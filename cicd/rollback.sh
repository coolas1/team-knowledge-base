#!/usr/bin/env bash
# Rollback the LAN deployment to a previously deployed commit SHA.
#
# Usage: rollback.sh <sha>          (full or short SHA, as recorded in
#                                    <repo>/.deploy/deployed-shas)
#
# Retags the SHA-tagged images (kept from every pipeline build) as :latest,
# checks the disposable clone out at that SHA so the compose file matches the
# images, and redeploys. The last-deployed marker is intentionally NOT
# rewritten: while origin/main stays at the SHA the pipeline last deployed,
# the timer no-ops and the rollback sticks; the next merge to main deploys
# forward again. Stop/disable team-kb-cicd.timer to pin a rollback longer.
#
# Rollback is manual by design (v1): the pipeline never rolls back on its own.
set -Eeuo pipefail

# Derive the stable dir from this script's location (clone layout
# <home>/repo/cicd/rollback.sh); TKB_CICD_HOME overrides for sandbox use.
# Exported for the snapshot re-exec below, same as pipeline.sh.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TKB_CICD_HOME="${TKB_CICD_HOME:-$(cd "$script_dir/../.." && pwd)}"
export TKB_CICD_HOME
TKB_CICD_VENV="${TKB_CICD_VENV:-/var/tmp/tkb-venvs/cicd}"
TKB_NODE22_BIN="${TKB_NODE22_BIN:-/var/tmp/node22/bin}"
TKB_HEALTH_TIMEOUT="${TKB_HEALTH_TIMEOUT:-180}"

repo_dir="$TKB_CICD_HOME/repo"
deploy_env="$TKB_CICD_HOME/deploy.env"
history_file="$TKB_CICD_HOME/deployed-shas"
compose_images=(team-kb-webapp team-kb-pi-agent)

log() { printf '[cicd-rollback] %s\n' "$*"; }
die() { log "FAILED: $*"; exit 1; }

export PATH="$HOME/.local/bin:$TKB_NODE22_BIN:/usr/local/bin:/usr/bin:/bin"

# --- self-location guard ----------------------------------------------------
# Like pipeline.sh: rollback resets the clone mid-run, which would rewrite
# the script bash is executing. Snapshot to the stable dir and re-exec.
if [[ -z "${TKB_CICD_SNAPSHOT:-}" ]] \
  && [[ "${BASH_SOURCE[0]}" -ef "$repo_dir/cicd/rollback.sh" ]]; then
  snap="$TKB_CICD_HOME/rollback.exec.sh"
  cp "${BASH_SOURCE[0]}" "$snap"
  TKB_CICD_SNAPSHOT=1 exec bash "$snap" "$@"
fi

# Only PROXY keys from deploy.env are exported (see pipeline.sh for why).
if [[ -f "$deploy_env" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || continue
    case "${BASH_REMATCH[1]}" in
      *_proxy|*_PROXY) export "$line" ;;
    esac
  done < "$deploy_env"
fi
app_port=""
if [[ -f "$deploy_env" ]]; then
  app_port="$(grep -E '^APP_PORT=' "$deploy_env" | tail -1 | cut -d= -f2-)"
fi
health_url="http://127.0.0.1:${app_port:-8000}/health"

[[ $# -eq 1 ]] || { echo "usage: rollback.sh <sha>" >&2; exit 2; }
[[ -d "$repo_dir/.git" ]] || die "disposable clone missing at $repo_dir"
[[ -f "$deploy_env" ]] || die "deploy.env missing at $deploy_env"

# Resolve the argument against the deployed-SHA history (full SHAs only).
target=""
if [[ -f "$history_file" ]]; then
  while read -r _ts sha _rest; do
    [[ -z "$sha" ]] && continue
    if [[ "$sha" == "$1" || "${sha:0:${#1}}" == "$1" ]]; then
      target="$sha"
    fi
  done < "$history_file"
fi
[[ -n "$target" ]] || die "$1 is not a recorded deployed SHA (see $history_file)"
short="${target:0:7}"

for image in "${compose_images[@]}"; do
  podman image exists "$image:$short" || die "$image:$short not found locally"
done

log "rolling back to $target"
for image in "${compose_images[@]}"; do
  podman tag "$image:$short" "$image:latest" || die "failed to retag $image"
done
# Compose definition must match the images being deployed.
git -C "$repo_dir" reset --hard "$target" || die "git reset to $target failed"

cd "$repo_dir"
podman compose --env-file "$deploy_env" up -d --remove-orphans \
  || die "compose up failed"

log "verifying $health_url"
deadline=$((SECONDS + TKB_HEALTH_TIMEOUT))
while (( SECONDS < deadline )); do
  if curl -fsS --noproxy '*' --max-time 5 "$health_url" >/dev/null 2>&1; then
    log "healthy on $short"
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$target (rollback)" >> "$history_file"
    exit 0
  fi
  sleep 5
done
die "health check did not pass within ${TKB_HEALTH_TIMEOUT}s"
