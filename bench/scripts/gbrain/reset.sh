#!/usr/bin/env bash
# Reset gbrain to an empty brain.
#
# Wipes ~/.gbrain/brain.pglite and re-creates an empty brain via
# `gbrain init --pglite`. No ingest, no embedding — use ingest.sh (or
# run-all.sh) for that. Pre-checks the PGLite lock so the user gets a clear
# message instead of a 30s hang when Claude Code is still running.
#
# Usage:
#   bash scripts/gbrain/reset.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

if test_pglite_lock; then
  write_step_fail "PGLite lock held. Exit Claude Code (which hosts the MCP server) before running this script."
  exit 2
fi

gbrain_home="${GBRAIN_HOME:-$HOME/.gbrain}"
brain="$gbrain_home/brain.pglite"

echo "[wipe] removing $brain"
rm -rf "$brain"

echo "[init] gbrain init --pglite"
gbrain init --pglite

echo "[verify] gbrain doctor (brief)"
gbrain doctor 2>&1 | tail -5

write_step_ok "reset complete. Empty brain ready. Next:"
echo "  bash scripts/gbrain/ingest.sh"
echo "  bash scripts/gbrain/health.sh"
echo "  # or all-in-one (skipping the reset you just ran):"
echo "  bash scripts/gbrain/run-all.sh --skip-reset"
