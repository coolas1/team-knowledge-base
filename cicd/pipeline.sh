#!/usr/bin/env bash
# Local CI/CD pipeline for the LAN deployment of team-knowledge-base.
#
# Invoked every 5 minutes by the systemd user timer (team-kb-cicd.timer) via
# the static bootstrap run.sh in the stable dir (<repo>/.deploy/). Stages:
#
#   watch  - compare git ls-remote origin/main against the last deployed SHA;
#            exit 0 without doing anything when unchanged
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
TKB_CICD_BRANCH="${TKB_CICD_BRANCH:-main}"
TKB_CICD_VENV="${TKB_CICD_VENV:-/var/tmp/tkb-venvs/cicd}"
TKB_NODE22_BIN="${TKB_NODE22_BIN:-/var/tmp/node22/bin}"
# SPA tests need Node 22 (system Node is 18 and fails 3 upload tests).
TKB_HEALTH_TIMEOUT="${TKB_HEALTH_TIMEOUT:-180}"
# Regional PyPI mirror for LAN builds (Containerfile's PYPI_MIRROR build arg;
# empty = upstream PyPI). Overridable from deploy.env / the environment.
PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.aliyun.com/pypi/simple}"
# Backups: how many dated backup sets to keep in <stable-dir>/backups/.
TKB_BACKUP_KEEP="${TKB_BACKUP_KEEP:-5}"

repo_dir="$TKB_CICD_HOME/repo"
deploy_env="$TKB_CICD_HOME/deploy.env"
state_file="$TKB_CICD_HOME/last-deployed"
history_file="$TKB_CICD_HOME/deployed-shas"
# Images with fixed names in docker-compose.yml; each is tagged :<short-sha>
# at build time so any deployed SHA stays available for rollback.
compose_images=(team-kb-webapp team-kb-pi-agent)

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

# Health-check port comes from deploy.env too, read without exporting.
app_port=""
if [[ -f "$deploy_env" ]]; then
  app_port="$(grep -E '^APP_PORT=' "$deploy_env" | tail -1 | cut -d= -f2-)"
fi
health_url="http://127.0.0.1:${app_port:-8000}/health"

for tool in git uv podman curl npm node; do
  command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done
[[ -x "$TKB_NODE22_BIN/node" ]] || die "Node 22 not found at $TKB_NODE22_BIN"

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
  SHORT_SHA="$(git -C "$repo_dir" rev-parse --short=7 "$HEAD_SHA")"
  export GIT_COMMIT="$SHORT_SHA"
  log "build: podman compose build (PYPI_MIRROR=${PYPI_MIRROR:-<upstream PyPI>}, GIT_COMMIT=$SHORT_SHA)"
  PYPI_MIRROR="${PYPI_MIRROR:-}" \
    podman compose --env-file "$deploy_env" build || die "compose build failed"

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
  # the uploads volume into <stable-dir>/backups/ (keep-last-N).
  cd "$repo_dir"
  TKB_CICD_HOME="$TKB_CICD_HOME" TKB_BACKUP_KEEP="$TKB_BACKUP_KEEP" \
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

# --- run --------------------------------------------------------------------
if ! stage_sync; then
  exit 0 # unchanged head: no gate, no build, no deploy
fi

# Sync may have checked out a newer pipeline.sh than the copy running now.
# Hand over to the fresh version before gating, so a pushed pipeline change
# takes effect on the very run that pulls it.
if [[ -z "${TKB_CICD_FRESH:-}" ]] \
  && ! cmp -s "${BASH_SOURCE[0]}" "$repo_dir/cicd/pipeline.sh"; then
  log "sync: pipeline.sh changed on main; re-execing the fresh version"
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
log "deployed $SHORT_SHA ($HEAD_SHA)"
