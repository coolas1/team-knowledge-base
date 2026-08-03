#!/usr/bin/env bash
# Orchestrator: install-stack -> install-cli -> bootstrap -> reset -> ingest ->
# health. The one-shot path from empty to a populated, health-checked WeKnora
# KB against raw/.
#
# Health always runs, even if an earlier step failed, so the user sees partial
# state instead of a bare error (mirrors scripts/gbrain/run-all.sh).
#
# install-stack needs podman/docker (host-side). If no compose is present it is
# skipped with a warning — useful when running the rest of the pipeline from a
# sibling container against an already-up host stack.
#
# Usage:
#   bash scripts/weknora/run-all.sh
#   bash scripts/weknora/run-all.sh --skip-stack --skip-reset
#   bash scripts/weknora/run-all.sh --only-md          # gbrain-parity ingest

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

SKIP_STACK=0; SKIP_CLI=0; SKIP_RESET=0; ONLY_MD=0
for arg in "$@"; do
  case "$arg" in
    --skip-stack) SKIP_STACK=1 ;;
    --skip-cli)   SKIP_CLI=1 ;;
    --skip-reset) SKIP_RESET=1 ;;
    --only-md)    ONLY_MD=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

any_failed=0
script_dir="$(cd "$(dirname "$0")" && pwd)"
run() {  # <label> <script...>
  local label="$1"; shift
  write_step "=== $label ==="
  set +e; bash "$@"; local rc=$?; set -e
  if [[ $rc -ne 0 ]]; then
    write_step_fail "$label exited $rc"
    any_failed=1
  else
    write_step_ok "$label done"
  fi
}

# --- install-stack (host-side; needs compose) --------------------------------

if [[ $SKIP_STACK -eq 1 ]]; then
  write_step_warn "--skip-stack set — skipping stack bring-up."
elif test_compose_present; then
  run "install-stack" "$script_dir/install-stack.sh"
else
  write_step_warn "no compose (podman/docker) here — skipping install-stack. Assuming the WeKnora stack is already up (set WEKNORA_BASE_URL if it's not localhost:8080)."
fi

# --- install-cli (optional) --------------------------------------------------

if [[ $SKIP_CLI -eq 1 ]]; then
  write_step_warn "--skip-cli set — skipping CLI install."
else
  if command -v weknora >/dev/null 2>&1; then
    write_step_ok "weknora CLI already present — skipping install-cli."
  else
    run "install-cli" "$script_dir/install-cli.sh"
  fi
fi

# --- bootstrap (tenant + KB) -------------------------------------------------

run "bootstrap" "$script_dir/bootstrap.sh"

# --- reset (clean KB) --------------------------------------------------------

if [[ $SKIP_RESET -eq 1 ]]; then
  write_step_warn "--skip-reset set — keeping existing KB contents."
else
  run "reset" "$script_dir/reset.sh"
fi

# --- ingest ------------------------------------------------------------------

ingest_args=()
[[ $ONLY_MD -eq 1 ]] && ingest_args+=(--only-md)
run "ingest" "$script_dir/ingest.sh" "${ingest_args[@]}"

# --- health (always) ---------------------------------------------------------

run "health" "$script_dir/health.sh"

if [[ $any_failed -ne 0 ]]; then
  write_step_fail "one or more steps failed — see output above."
  exit 1
fi
write_step_ok "run-all complete."
