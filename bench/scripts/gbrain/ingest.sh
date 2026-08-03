#!/usr/bin/env bash
# Ingest raw/ into gbrain and establish links automatically.
#
# Pipeline:
#   1. gbrain import raw --no-embed --workers 1 [--fresh]
#      Chunks .md files only (binary attachments — pdf/xlsx/csv/png/jpg — are
#      NOT parsed; they are leaf nodes described in prose by surrounding .md).
#      Embedding deferred to step 3 so an Ollama outage does not taint the run.
#   2. gbrain extract all --source fs --dir raw
#      Walks raw/ and extracts inline `[text](path.md)` markdown links +
#      `[[wikilink]]` references. This is the right call for an UNTYPED corpus
#      (no entity-typed slugs). Explicit `--dir raw` avoids the bare
#      `extract all` footgun (defaults to `--dir .` which walks the whole repo
#      tree).
#   3. gbrain embed --stale (unless --skip-embed)
#      Embeds chunks missing vectors. Idempotent.
#
# raw/ is read-only by construction: the only command that touches it is
# `gbrain import raw` and `gbrain extract all --source fs --dir raw` (both read
# paths). No write/remove in this script targets anything under raw/.
#
# Intermediate logs land in gbrain-files/logs/ (already .gitignore'd).
#
# Usage:
#   bash scripts/gbrain/ingest.sh
#   bash scripts/gbrain/ingest.sh --fresh
#   bash scripts/gbrain/ingest.sh --skip-embed

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

repo_root="$(get_repo_root)"
log_dir="$(get_log_dir)"
timestamp="$(get_timestamp)"

FRESH=0
SKIP_EMBED=0
for arg in "$@"; do
  case "$arg" in
    --fresh)     FRESH=1 ;;
    --skip-embed) SKIP_EMBED=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

# --- preflight ---------------------------------------------------------------

if test_pglite_lock; then
  write_step_fail "PGLite lock held. Exit Claude Code (which hosts the MCP server) before running this script."
  exit 2
fi

raw_dir="$repo_root/raw"
if [[ ! -d "$raw_dir" ]]; then
  write_step_fail "raw/ not found at $raw_dir"
  exit 3
fi

if test_ollama_reachable; then
  write_step_ok "Ollama reachable - embeddings will be generated."
else
  write_step_warn "Ollama NOT reachable. \`gbrain import --no-embed\` will still succeed, but \`gbrain embed --stale\` will leave NULL vectors (the \"embedded_count lies\" gotcha). \`gbrain doctor\` will flag this as \`embedding_provider_reach\`."
fi

# --- helper ------------------------------------------------------------------

run_gbrain_step() {
  local name="$1"; shift
  local log_file="$1"; shift
  write_step "$name -> $log_file"
  : > "$log_file"
  # Pipe stdout+stderr through tee so the console sees progress AND the log
  # captures it. `set -e` is disabled inside the pipe so we can catch gbrain's
  # exit code without aborting mid-stream.
  set +e
  gbrain "$@" 2>&1 | tee -a "$log_file"
  local rc=${PIPESTATUS[0]}
  set -e
  if [[ $rc -ne 0 ]]; then
    write_step_fail "gbrain exited $rc during step: $name (log: $log_file)"
    exit $rc
  fi
}

# --- step 1: import ----------------------------------------------------------

import_args=(import raw --no-embed --workers 1)
if [[ $FRESH -eq 1 ]]; then import_args+=(--fresh); fi
run_gbrain_step "import (chunk only, no embed)" \
  "$log_dir/import-$timestamp.log" \
  "${import_args[@]}"
write_step_ok "import complete"

# --- step 2: extract links from raw/ markdown (fs-source) --------------------

run_gbrain_step "extract all --source fs --dir raw (markdown links)" \
  "$log_dir/extract-$timestamp.log" \
  extract all --source fs --dir raw
write_step_ok "link extraction complete"

# --- step 3: embed (unless skipped) ------------------------------------------

if [[ $SKIP_EMBED -eq 1 ]]; then
  write_step_warn "--skip-embed set - skipping embed step."
else
  run_gbrain_step "embed --stale (chunks missing vectors)" \
    "$log_dir/embed-$timestamp.log" \
    embed --stale
  write_step_ok "embed complete"
fi

write_step_ok "ingest pipeline complete. Logs: $log_dir/{$timestamp,...}"
