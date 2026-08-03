#!/usr/bin/env bash
# Orchestrator: reset -> ingest -> health. The one-shot path for getting
# gbrain from empty to a fully populated, health-checked brain against raw/.
#
# Health always runs, even if reset or ingest failed, so the user sees the
# partial state instead of a bare error.
#
# Usage:
#   bash scripts/gbrain/run-all.sh
#   bash scripts/gbrain/run-all.sh --skip-reset
#   bash scripts/gbrain/run-all.sh --skip-embed --fresh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

SKIP_RESET=0
SKIP_EMBED=0
FRESH=0
for arg in "$@"; do
  case "$arg" in
    --skip-reset) SKIP_RESET=1 ;;
    --skip-embed) SKIP_EMBED=1 ;;
    --fresh)      FRESH=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

if test_pglite_lock; then
  write_step_fail "PGLite lock held. Exit Claude Code (which hosts the MCP server) before running this script."
  exit 2
fi

any_failed=0
script_dir="$(cd "$(dirname "$0")" && pwd)"

# --- reset -------------------------------------------------------------------

if [[ $SKIP_RESET -eq 1 ]]; then
  write_step_warn "--skip-reset set - skipping reset step."
else
  set +e
  bash "$script_dir/reset.sh"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    write_step_fail "reset.sh exited $rc - aborting ingest, will still run health."
    any_failed=1
  else
    write_step_ok "reset done"
  fi
fi

# --- ingest ------------------------------------------------------------------

if [[ $any_failed -eq 0 ]]; then
  ingest_args=()
  [[ $FRESH -eq 1 ]] && ingest_args+=(--fresh)
  [[ $SKIP_EMBED -eq 1 ]] && ingest_args+=(--skip-embed)
  set +e
  bash "$script_dir/ingest.sh" "${ingest_args[@]}"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    write_step_fail "ingest.sh exited $rc - will still run health."
    any_failed=1
  else
    write_step_ok "ingest done"
  fi
fi

# --- health (always) ---------------------------------------------------------

set +e
bash "$script_dir/health.sh"
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  write_step_fail "health.sh exited $rc"
  any_failed=1
else
  write_step_ok "health done"
fi

if [[ $any_failed -ne 0 ]]; then
  write_step_fail "one or more steps failed - see output above."
  exit 1
fi

write_step_ok "run-all complete."
