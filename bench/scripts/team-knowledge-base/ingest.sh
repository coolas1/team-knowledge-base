#!/usr/bin/env bash
# Ingest raw/ into the team-knowledge-base via the REST API.
#
# Walks raw/ and uploads every file (default) — or only .md with --only-md for
# gbrain parity — then polls each document until status reaches "indexed" or
# "failed". tkb's pipeline extracts text from md/pdf/docx/pptx/images,
# chunks, LLM-analyzes, embeds, and writes to pgvector + Neo4j.
#
# raw/ is read-only: the only operation that touches it is the multipart upload
# (a read). No write/remove targets raw/.
#
# Per-run output lands in team-knowledge-base-files/:
#   manifest-<ts>.json  — {path, doc_id, status} per file
#   manifest.json       — latest copy (stable name)
#   logs/ingest-<ts>.log
#
# Usage:
#   bash scripts/team-knowledge-base/ingest.sh
#   bash scripts/team-knowledge-base/ingest.sh --only-md
#   TKB_BASE_URL=http://localhost:8000 bash scripts/team-knowledge-base/ingest.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

raw_dir="$(get_repo_root)/bench/raw"
files_dir="$(get_tkb_files_dir)"
log_dir="$(get_log_dir)"
ts="$(get_timestamp)"
log_file="$log_dir/ingest-$ts.log"
ndjson="$(mktemp)"
trap 'rm -f "$ndjson"' EXIT

ONLY_MD=0
for arg in "$@"; do
  case "$arg" in
    --only-md) ONLY_MD=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 64 ;;
  esac
done

# --- preflight ---------------------------------------------------------------

if [[ ! -d "$raw_dir" ]]; then
  write_step_fail "raw/ not found at $raw_dir"; exit 3
fi
if ! test_tkb_app_reachable; then
  write_step_fail "tkb app not reachable ($(get_tkb_base_url)/health). Start the app first."; exit 2
fi
if test_ollama_reachable; then
  write_step_ok "Ollama reachable — embeddings will be generated."
else
  write_step_warn "Ollama NOT reachable — files will upload but embedding will fail."
fi

# --- file list ---------------------------------------------------------------

mapfile -t files < <(cd "$raw_dir" && find . -type f | sed 's|^\./||' | sort)
total=${#files[@]}
scope_label="all files"
if [[ $ONLY_MD -eq 1 ]]; then
  md=(); for f in "${files[@]}"; do [[ "$f" == *.md ]] && md+=("$f"); done
  files=("${md[@]}"); total=${#files[@]}; scope_label="markdown only (.md)"
fi
write_step_ok "queued $total file(s) from raw/ ($scope_label) -> tkb"

# --- helpers -----------------------------------------------------------------

ftype_of() {  # $1 = filename -> coarse type from extension
  case "$1" in
    *.md)   echo markdown ;; *.pdf)  echo pdf ;;
    *.xlsx) echo xlsx ;; *.xls) echo xls ;; *.csv) echo csv ;;
    *.png)  echo png ;; *.jpg|*.jpeg) echo jpg ;;
    *.docx) echo docx ;; *.txt) echo txt ;; *.json) echo json ;;
    *) echo other ;;
  esac
}

# Poll a doc until status is terminal (indexed/failed) or timeout.
# Echoes the terminal status (indexed|failed|timeout).
wait_indexed() {
  local id="$1" deadline=$(( $(date +%s) + ${DOC_TIMEOUT:-120} )) st=""
  while :; do
    st="$(tkb_api GET "/documents/$id" 2>/dev/null | jq -r '.status // empty' 2>/dev/null || true)"
    case "$st" in indexed|failed) echo "$st"; return 0 ;; esac
    if [[ $(date +%s) -ge $deadline ]]; then echo "timeout"; return 0; fi
    sleep 5
  done
}

# --- upload loop -------------------------------------------------------------

ok=0; failed=0; timedout=0; i=0
for rel in "${files[@]}"; do
  i=$((i+1))
  abs="$raw_dir/$rel"
  ft="$(ftype_of "$rel")"
  printf '\033[1;37m[step]\033[0m [%d/%d] %s (%s)\n' "$i" "$total" "$rel" "$ft" | tee -a "$log_file"

  doc_id=""; status=""; err=""
  set +e
  resp="$(tkb_api POST "/documents/upload" -F "file=@$abs" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    err="$(echo "$resp" | tail -1)"; status="upload_failed"
    write_step_fail "  upload failed: $err" | tee -a "$log_file"
  else
    doc_id="$(echo "$resp" | jq -r '.doc_id // .id // empty' 2>/dev/null || true)"
    if [[ -z "$doc_id" ]]; then
      err="$(echo "$resp" | jq -r '.detail // .message // .' 2>/dev/null || echo "$resp" | tail -1)"
      status="no_doc_id"
      write_step_fail "  no doc id returned: $err" | tee -a "$log_file"
    else
      status="$(wait_indexed "$doc_id")"
      case "$status" in
        indexed) ok=$((ok+1)); write_step_ok "  indexed" | tee -a "$log_file" ;;
        failed)  failed=$((failed+1));
                 err="$(tkb_api GET "/documents/$doc_id" 2>/dev/null | jq -r '.error // .detail // empty' 2>/dev/null || true)"
                 write_step_fail "  indexing failed: ${err:-<no detail>}" | tee -a "$log_file" ;;
        *)       timedout=$((timedout+1)); write_step_warn "  still indexing after timeout (status=$status)" | tee -a "$log_file" ;;
      esac
    fi
  fi

  jq -n --arg p "$rel" --arg t "$ft" --arg id "$doc_id" --arg st "$status" --arg e "$err" \
    '{path:$p, type:$t, doc_id:$id, status:$st, error:$e}' >> "$ndjson"
done

# --- manifest ----------------------------------------------------------------

manifest="$files_dir/manifest-$ts.json"
jq -s '.' "$ndjson" > "$manifest"
cp "$manifest" "$files_dir/manifest.json"

echo "" | tee -a "$log_file"
echo "----- ingest summary -----" | tee -a "$log_file"
echo "files:     $total" | tee -a "$log_file"
echo "indexed:   $ok" | tee -a "$log_file"
[[ $failed   -gt 0 ]] && echo "failed:    $failed"   | tee -a "$log_file"
[[ $timedout -gt 0 ]] && echo "timed out: $timedout" | tee -a "$log_file"
echo "manifest:  $manifest" | tee -a "$log_file"

write_step_ok "ingest done. Next: bash scripts/team-knowledge-base/health.sh"
if [[ $failed -gt 0 || $timedout -gt 0 ]]; then exit 1; fi
