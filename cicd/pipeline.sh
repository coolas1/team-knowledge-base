#!/usr/bin/env bash
# Local CI/CD pipeline for the LAN deployment of team-knowledge-base.
#
# Invoked every 5 minutes by a systemd user timer (team-kb-cicd.timer for
# production, team-kb-cicd-dev.timer for staging) via the static bootstrap
# run.sh in the stack's stable dir (<repo>/.deploy/main/ or .deploy/develop/).
# One pipeline instance per stable dir; the watched branch, stack namespace,
# and gate venv all come from that stable dir's deploy.env. Stages:
#
#   watch  - compare git ls-remote origin/$TKB_CICD_BRANCH against the last
#            deployed SHA; exit 0 without doing anything when unchanged
#   sync   - git fetch + reset --hard (disposable clone under the stable dir)
#   gate   - ruff check, uv run pytest, SPA npm test (Node 22)
#   build  - podman compose build; tag images :<short-sha> alongside :latest
#   deploy - podman compose up -d --remove-orphans --env-file deploy.env
#   verify - poll GET /health with a bounded timeout
#
# Deploy credentials live in the stable dir's deploy.env (never in the clone),
# so wiping the clone never touches credentials or data volumes. A failed gate
# or build exits non-zero before `deploy`, leaving the running stack untouched.
#
# Usage: pipeline.sh [--dry-run] [--force]
#   --dry-run  run watch+sync+gate+build but withhold deploy
#   --force    run the full flow even when the branch head is unchanged
set -Eeuo pipefail

# --- configuration ----------------------------------------------------------
# TKB_CICD_HOME etc. are overridable for sandbox testing. The default derives
# from this script's location (clone layout <home>/repo/cicd/pipeline.sh, so
# home is two levels up), which keeps a relocated stable dir working without
# env setup. Exported because the self-location guard below re-execs a
# snapshot from a different path — there, location-based derivation would be
# wrong.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TKB_CICD_HOME="${TKB_CICD_HOME:-$(cd "$script_dir/../.." && pwd)}"
export TKB_CICD_HOME
TKB_CICD_REMOTE="${TKB_CICD_REMOTE:-https://github.com/coolas1/team-knowledge-base.git}"
# TKB_CICD_BRANCH/COMPOSE_PROJECT_NAME/TKB_CICD_VENV: the branch, stack
# namespace, and gate venv come from the stable dir's deploy.env (read below,
# non-exported — same discipline as APP_PORT) so the two pipeline instances
# differ only by their stable dir. Environment overrides still win for sandbox
# testing.
TKB_CICD_BRANCH="${TKB_CICD_BRANCH:-main}"
TKB_CICD_VENV="${TKB_CICD_VENV:-}"
TKB_NODE22_BIN="${TKB_NODE22_BIN:-/var/tmp/node22/bin}"
# SPA tests need Node 22 (system Node is 18 and fails 3 upload tests).
TKB_HEALTH_TIMEOUT="${TKB_HEALTH_TIMEOUT:-180}"
# Regional PyPI mirror for LAN builds (Containerfile's PYPI_MIRROR build arg;
# empty = upstream PyPI). Overridable from deploy.env / the environment.
PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.aliyun.com/pypi/simple}"
# npm registries (Containerfile build args). Install defaults to a regional
# mirror because direct registry.npmjs.org fetches stall from this host
# (npm ignores HTTP(S)_PROXY). The audit stays on upstream npmjs: mirrors do
# not implement npm's audit API and the security gate fails closed.
# NPM_PROXY is deliberately NOT defaulted here - a local-only opt-in,
# settable from deploy.env / the environment, never a committed default.
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
NPM_AUDIT_REGISTRY="${NPM_AUDIT_REGISTRY:-https://registry.npmjs.org}"
# Backups: how many dated backup sets to keep in <stable-dir>/backups/.
TKB_BACKUP_KEEP="${TKB_BACKUP_KEEP:-5}"
# Bounded image retention: how many of the most recent :<short-sha> image
# tags to keep per stack (0 disables pruning). Every build tags :<short-sha>,
# so two stacks would accumulate unbounded without this.
TKB_IMAGE_KEEP="${TKB_IMAGE_KEEP:-10}"

repo_dir="$TKB_CICD_HOME/repo"
deploy_env="$TKB_CICD_HOME/deploy.env"
state_file="$TKB_CICD_HOME/last-deployed"
history_file="$TKB_CICD_HOME/deployed-shas"

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) echo "usage: pipeline.sh [--dry-run] [--force]" >&2; exit 2 ;;
  esac
done

log() { printf '[cicd] %s\n' "$*"; }
die() { log "FAILED: $*"; exit 1; }

# --- environment ------------------------------------------------------------
# systemd user units get a minimal PATH; make the pipeline self-sufficient.
export PATH="$HOME/.local/bin:$TKB_NODE22_BIN:/usr/local/bin:/usr/bin:/bin"

# deploy.env holds compose substitution values plus the proxy settings
# git/uv/npm/podman need. Only the PROXY keys and npm_config_cache are
# exported into this environment: exporting app config (LLM_MODEL etc.)
# would leak into the gate's test processes (pydantic-settings reads real
# env vars) and change test behavior. npm_config_cache keeps the SPA
# install's cache on local disk even though the clone (node_modules) is on
# the slower home filesystem. Compose receives the full file via --env-file.
if [[ -f "$deploy_env" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || continue
    case "${BASH_REMATCH[1]}" in
      *_proxy|*_PROXY|npm_config_cache) export "$line" ;;
    esac
  done < "$deploy_env"
fi

# Health-check port, watched branch, stack namespace, and gate venv come from
# deploy.env too, read without exporting (app config must not leak into the
# gate's test environment).
app_port=""
cicd_branch=""
project_name=""
venv_from_env=""
image_keep=""
if [[ -f "$deploy_env" ]]; then
  # `|| true`: with pipefail, a key absent from deploy.env makes the grep
  # pipeline fail the assignment and set -e kills the run (backup.sh guards
  # the way). Optional keys rely on it.
  app_port="$(grep -E '^APP_PORT=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  cicd_branch="$(grep -E '^TKB_CICD_BRANCH=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  project_name="$(grep -E '^COMPOSE_PROJECT_NAME=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  venv_from_env="$(grep -E '^TKB_CICD_VENV=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
  image_keep="$(grep -E '^TKB_IMAGE_KEEP=' "$deploy_env" | tail -1 | cut -d= -f2- || true)"
fi
TKB_CICD_BRANCH="${cicd_branch:-$TKB_CICD_BRANCH}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-${project_name:-team-kb}}"
TKB_IMAGE_KEEP="${image_keep:-$TKB_IMAGE_KEEP}"
health_url="http://127.0.0.1:${app_port:-8000}/health"
# Images with names fixed in docker-compose.yml; each is tagged :<short-sha>
# at build time so any deployed SHA stays available for rollback. Includes the
# profile-gated tool images: the build stage activates their profiles so they
# exist for deployments that enable tool-authoring (harmless to build otherwise).
# Names derive from the stack's COMPOSE_PROJECT_NAME, mirroring the compose
# file's ${COMPOSE_PROJECT_NAME:-team-kb}-<service> image names.
compose_images=("${COMPOSE_PROJECT_NAME}-webapp" "${COMPOSE_PROJECT_NAME}-pi-agent"
  "${COMPOSE_PROJECT_NAME}-tool-job" "${COMPOSE_PROJECT_NAME}-tool-runner")
# Per-stack gate venv: the two stacks' lockfiles diverge freely, and a shared
# venv would uv-sync-thrash between two heads every 5 minutes (develop's
# default sibling is cicd-dev). deploy.env or the environment can override.
if [[ -z "$TKB_CICD_VENV" ]]; then
  if [[ -n "$venv_from_env" ]]; then
    TKB_CICD_VENV="$venv_from_env"
  elif [[ "$TKB_CICD_BRANCH" == "develop" ]]; then
    TKB_CICD_VENV="/var/tmp/tkb-venvs/cicd-dev"
  else
    TKB_CICD_VENV="/var/tmp/tkb-venvs/cicd"
  fi
fi

for tool in git uv podman curl npm node; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done
[[ -x "$TKB_NODE22_BIN/node" ]] || die "Node 22 not found at $TKB_NODE22_BIN"

log "pipeline: branch=$TKB_CICD_BRANCH project=$COMPOSE_PROJECT_NAME venv=$TKB_CICD_VENV"

mkdir -p "$TKB_CICD_HOME"

# --- self-location guard ----------------------------------------------------
# The pipeline lives inside the very clone it syncs: `git reset --hard` in
# stage_sync would rewrite the script bash is mid-executing (garbage or
# stale logic), and the invoked copy is always the PREVIOUS head's version.
# So when started from the clone, snapshot to the stable dir and re-exec
# the snapshot; after sync, hand over to the freshly checked-out copy if
# it differs (see the TKB_CICD_FRESH re-exec below).
if [[ -z "${TKB_CICD_SNAPSHOT:-}" && -z "${TKB_CICD_FRESH:-}" ]] \
  && [[ "${BASH_SOURCE[0]}" -ef "$repo_dir/cicd/pipeline.sh" ]]; then
  snap="$TKB_CICD_HOME/pipeline.exec.sh"
  cp "${BASH_SOURCE[0]}" "$snap"
  TKB_CICD_SNAPSHOT=1 exec bash "$snap" "$@"
fi

# --- stages -----------------------------------------------------------------
HEAD_SHA=""
SHORT_SHA=""

stage_sync() {
  local remote_head
  remote_head="$(git ls-remote "$TKB_CICD_REMOTE" "refs/heads/$TKB_CICD_BRANCH" | cut -f1)"
  [[ -n "$remote_head" ]] || die "ls-remote returned no head for $TKB_CICD_BRANCH"

  if [[ $FORCE -eq 0 && -f "$state_file" ]]; then
    local last
    last="$(<"$state_file")"
    if [[ "$last" == "$remote_head" ]]; then
      log "watch: $TKB_CICD_BRANCH unchanged at ${remote_head:0:7}; nothing to do"
      return 1 # sentinel: no-op run
    fi
  fi

  if [[ ! -d "$repo_dir/.git" ]]; then
    log "sync: cloning $TKB_CICD_REMOTE -> $repo_dir"
    git clone --branch "$TKB_CICD_BRANCH" "$TKB_CICD_REMOTE" "$repo_dir" \
      || die "clone failed"
  else
    log "sync: fetching $TKB_CICD_BRANCH"
    git -C "$repo_dir" fetch "$TKB_CICD_REMOTE" "$TKB_CICD_BRANCH" \
      || die "fetch failed"
    # Disposable clone: hard-reset to the branch head. clean -fd removes
    # untracked files but keeps ignored ones (SPA node_modules survives).
    git -C "$repo_dir" reset --hard FETCH_HEAD || die "reset failed"
    git -C "$repo_dir" clean -fd
  fi
  HEAD_SHA="$remote_head"
  log "sync: at $HEAD_SHA"
  return 0
}

stage_gate() {
  cd "$repo_dir"
  export UV_PROJECT_ENVIRONMENT="$TKB_CICD_VENV" # keep the venv off cephfs

  log "gate: venv=$TKB_CICD_VENV"
  log "gate: ruff check"
  uv run ruff check || die "ruff check failed"

  log "gate: pytest (unit/contract/BFF)"
  uv run pytest || die "pytest failed"

  log "gate: SPA npm test (Node $(node --version))"
  (
    cd src/frontend/webapp/client
    npm install --no-fund --no-audit || exit 1
    npm test
  ) || die "SPA tests failed"
}

stage_build() {
  cd "$repo_dir"
  [[ -f "$deploy_env" ]] || die "deploy.env missing at $deploy_env"
  # The LAN deployment builds from a regional PyPI mirror (opt-in build arg);
  # an unset PYPI_MIRROR means upstream PyPI. GIT_COMMIT is the source SHA
  # the built image reports through /version (Containerfile GIT_COMMIT arg).
  # npm registries mirror the Containerfile defaults (see the config block
  # above for why install and audit differ); NPM_PROXY reaches compose only
  # if deploy.env sets it (local-only opt-in).
  SHORT_SHA="$(git -C "$repo_dir" rev-parse --short=7 "$HEAD_SHA")"
  export GIT_COMMIT="$SHORT_SHA"
  log "build: podman compose build --profile tool-images --profile tool-authoring (PYPI_MIRROR=${PYPI_MIRROR:-<upstream PyPI>}, NPM_REGISTRY=$NPM_REGISTRY, NPM_AUDIT_REGISTRY=$NPM_AUDIT_REGISTRY, GIT_COMMIT=$SHORT_SHA)"
  PYPI_MIRROR="${PYPI_MIRROR:-}" \
    NPM_REGISTRY="$NPM_REGISTRY" \
    NPM_AUDIT_REGISTRY="$NPM_AUDIT_REGISTRY" \
    podman compose --env-file "$deploy_env" \
    --profile tool-images --profile tool-authoring build || die "compose build failed"

  # Fail closed on a stale build cache: buildah's classic builder caches
  # ENV-from-ARG layers without the arg value (see the Containerfile's
  # cache-bust RUN), so verify the freshly built image carries this run's
  # SHA before tagging/deploying it.
  local baked_commit
  baked_commit="$(podman image inspect "${COMPOSE_PROJECT_NAME}-webapp:latest" \
    --format '{{.Config.Env}}' | grep -o 'GIT_COMMIT=[0-9a-f]\{7,40\}' | cut -d= -f2- || true)"
  if [[ "$baked_commit" != "$SHORT_SHA" ]]; then
    die "built webapp reports GIT_COMMIT='$baked_commit', expected '$SHORT_SHA' (stale build-cache layer?)"
  fi

  local image
  for image in "${compose_images[@]}"; do
    if podman image exists "$image:latest"; then
      podman tag "$image:latest" "$image:$SHORT_SHA" \
        || die "failed to tag $image:$SHORT_SHA"
      log "build: tagged $image:$SHORT_SHA"
    fi
  done
}

stage_backup() {
  # Before the redeploy replaces the running stack: dump Postgres and snapshot
  # the uploads volume into <stable-dir>/backups/ (keep-last-N). The backup
  # targets THIS stack's objects: backup.sh reads TKB_POSTGRES_CONTAINER /
  # TKB_UPLOADS_VOLUME from the environment (not deploy.env), so pass the
  # COMPOSE_PROJECT_NAME-derived names explicitly — without them a develop
  # deploy would back up the production stack.
  # A stack's very first deploy (no state file, no postgres container) has
  # nothing to back up, so it skips the stage; backup.sh still dies when an
  # ESTABLISHED stack's container is missing (tripwire).
  cd "$repo_dir"
  if [[ ! -f "$state_file" ]] \
    && ! podman container exists "${COMPOSE_PROJECT_NAME}-postgres"; then
    log "backup: first deploy (no ${COMPOSE_PROJECT_NAME} stack yet); skipping"
    return 0
  fi
  TKB_CICD_HOME="$TKB_CICD_HOME" TKB_BACKUP_KEEP="$TKB_BACKUP_KEEP" \
    TKB_POSTGRES_CONTAINER="${COMPOSE_PROJECT_NAME}-postgres" \
    TKB_UPLOADS_VOLUME="${COMPOSE_PROJECT_NAME}_uploadsdata" \
    bash "$repo_dir/cicd/backup.sh" "$SHORT_SHA" \
    || die "pre-deploy backup failed"
}

stage_deploy() {
  cd "$repo_dir"
  log "deploy: podman compose up -d --remove-orphans"
  podman compose --env-file "$deploy_env" up -d --remove-orphans \
    || die "compose up failed"
}

stage_verify() {
  log "verify: polling $health_url (timeout ${TKB_HEALTH_TIMEOUT}s)"
  local deadline=$((SECONDS + TKB_HEALTH_TIMEOUT))
  while (( SECONDS < deadline )); do
    if curl -fsS --noproxy '*' --max-time 5 "$health_url" >/dev/null 2>&1; then
      log "verify: healthy"
      return 0
    fi
    sleep 5
  done
  die "health check did not pass within ${TKB_HEALTH_TIMEOUT}s"
}

stage_record() {
  printf '%s\n' "$HEAD_SHA" > "$state_file"
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$HEAD_SHA" >> "$history_file"
}

stage_prune() {
  # Bounded per-stack image retention: every build tags :<short-sha>, so two
  # stacks would double the accumulation. Drop SHA-tagged images older than
  # the newest TKB_IMAGE_KEEP entries of this stack's deploy history. :latest
  # is never SHA-like, so the tag the running containers use is untouched.
  # Unrecorded SHA tags (built but never deployed) are pruned too — they are
  # not rollback paths. TKB_IMAGE_KEEP=0 disables pruning.
  cd "$repo_dir"
  if [[ ! "$TKB_IMAGE_KEEP" =~ ^[0-9]+$ ]]; then
    log "prune: invalid TKB_IMAGE_KEEP='$TKB_IMAGE_KEEP'; skipping"
    return 0
  fi
  if (( TKB_IMAGE_KEEP == 0 )); then
    log "prune: TKB_IMAGE_KEEP=0; retention disabled"
    return 0
  fi

  # Protected shorts: the newest keep-set from deployed-shas plus the SHA this
  # run just deployed (stage_record appended it, so it is in the keep-set; the
  # explicit entry guards a truncated/missing history file).
  local -A protected=()
  if [[ -f "$history_file" ]]; then
    while read -r _ts sha _rest; do
      if [[ -n "$sha" ]]; then
        protected["${sha:0:7}"]=1
      fi
    done < <(tail -n "$TKB_IMAGE_KEEP" "$history_file")
  fi
  protected["$SHORT_SHA"]=1

  local image tag
  for image in "${compose_images[@]}"; do
    while IFS= read -r tag; do
      if [[ ! "$tag" =~ ^[0-9a-f]{7,40}$ ]]; then
        continue
      fi
      if [[ -n "${protected[$tag]:-}" ]]; then
        continue
      fi
      log "prune: removing $image:$tag"
      podman rmi "$image:$tag" || log "prune: rmi $image:$tag failed (continuing)"
    done < <(podman images --format '{{.Tag}}' "$image" 2>/dev/null || true)
  done
}

# --- run --------------------------------------------------------------------
if ! stage_sync; then
  exit 0 # unchanged head: no gate, no build, no deploy
fi

# Sync may have checked out a newer pipeline.sh than the copy running now.
# Hand over to the fresh version before gating, so a pushed pipeline change
# takes effect on the very run that pulls it.
if [[ -z "${TKB_CICD_FRESH:-}" ]] \
  && ! cmp -s "${BASH_SOURCE[0]}" "$repo_dir/cicd/pipeline.sh"; then
  log "sync: pipeline.sh changed on $TKB_CICD_BRANCH; re-execing the fresh version"
  TKB_CICD_FRESH=1 exec bash "$repo_dir/cicd/pipeline.sh" "$@"
fi

stage_gate
stage_build

if [[ $DRY_RUN -eq 1 ]]; then
  log "dry-run: deploy withheld (gate+build passed for ${HEAD_SHA:0:7})"
  exit 0
fi

# Backup before replacing the running stack (a --dry-run must not touch the
# deployment's data, so this runs only on a real deploy).
stage_backup
stage_deploy
stage_verify
stage_record
stage_prune
log "deployed $SHORT_SHA ($HEAD_SHA)"
