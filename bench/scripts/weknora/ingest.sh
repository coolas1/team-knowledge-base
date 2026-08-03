#!/usr/bin/env bash
# Ingest raw/ into the WeKnora knowledge base via the REST API.
#
# Walks raw/ and uploads every file (default) — or only .md with --only-md for
# gbrain parity — as knowledge documents, then polls each until parsing reaches
# a terminal status. WeKnora's docreader parses PDF/Word/Excel/CSV/images
# natively, so the full run includes the binary "leaf node" attachments gbrain
# leaves un-parsed (e.g. coral-resilience-paper.pdf).
#
# raw/ is read-only by construction: the only command that touches it is the
# multipart `file=@<path>` upload (a read). No write/remove targets raw/.
#
# Per-run intermediate output lands in weknora-files/:
#   manifest-<ts>.json  — {path,type,doc_id,parse_status,error} per file
#   manifest.json       — latest copy (stable name for health/qa)
#   logs/ingest-<ts>.log
#
# Usage:
#   bash scripts/weknora/ingest.sh
#   bash scripts/weknora/ingest.sh --only-md        # gbrain parity (~22 .md)
#   WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/ingest.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

raw_dir="$(get_repo_root)/raw"
files_dir="$(get_weknora_files_dir)"
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
kb_id="$(state_get kb_id)"
if [[ -z "$kb_id" ]]; then
  write_step_fail "no kb_id in state. Run bootstrap.sh first: bash scripts/weknora/bootstrap.sh"; exit 3
fi
if ! test_weknora_app_reachable; then
  write_step_fail "WeKnora app not reachable ($(get_weknora_base_url)/health). Bring the stack up first."; exit 2
fi
if ! test_ollama_reachable; then
  write_step_warn "Ollama NOT reachable ($(get_ollama_url)) — files will upload but embedding will fail (parse_status=failed)."
fi

# --- file list ---------------------------------------------------------------

mapfile -t files < <(cd "$raw_dir" && find . -type f | sed 's|^\./||' | sort)
total=${#files[@]}
scope_label="all files"
if [[ $ONLY_MD -eq 1 ]]; then
  md=(); for f in "${files[@]}"; do [[ "$f" == *.md ]] && md+=("$f"); done
  files=("${md[@]}"); total=${#files[@]}; scope_label="markdown only (.md)"
fi
write_step_ok "queued $total file(s) from raw/ ($scope_label) -> KB $kb_id"

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

# Poll a doc until parse_status is terminal (completed/failed) or timeout.
# Echoes the terminal status (completed|failed|timeout).
wait_parse() {
  local id="$1" deadline=$(( $(date +%s) + ${DOC_TIMEOUT:-420} )) st=""
  while :; do
    st="$(wk_api GET "/knowledge/$id" 2>/dev/null | jq -r '.data.parse_status // empty' 2>/dev/null || true)"
    case "$st" in completed|failed) echo "$st"; return 0 ;; esac
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
  resp="$(wk_api POST "/knowledge-bases/$kb_id/knowledge/file" \
            -F "file=@$abs" -F "enable_multimodel=true" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    err="$(echo "$resp" | tail -1)"; status="upload_failed"
    write_step_fail "  upload failed: $err" | tee -a "$log_file"
  else
    doc_id="$(echo "$resp" | jq -r '.data.id // empty' 2>/dev/null || true)"
    if [[ -z "$doc_id" ]]; then
      err="$(echo "$resp" | jq -r '.error.message // .' 2>/dev/null || echo "$resp" | tail -1)"
      status="no_doc_id"
      write_step_fail "  no doc id returned: $err" | tee -a "$log_file"
    else
      write_step "  uploaded -> $doc_id; waiting for parse..." | tee -a "$log_file"
      status="$(wait_parse "$doc_id")"
      case "$status" in
        completed) ok=$((ok+1)); write_step_ok "  parsed (completed)" | tee -a "$log_file" ;;
        failed)    failed=$((failed+1));
                   err="$(wk_api GET "/knowledge/$doc_id" 2>/dev/null | jq -r '.data.error_message // empty' 2>/dev/null || true)"
                   write_step_fail "  parse failed: ${err:-<no detail>}" | tee -a "$log_file" ;;
        *)         timedout=$((timedout+1)); write_step_warn "  parse still running after timeout (status=$status)" | tee -a "$log_file" ;;
      esac
    fi
  fi

  jq -n --arg p "$rel" --arg t "$ft" --arg id "$doc_id" --arg st "$status" --arg e "$err" \
    '{path:$p, type:$t, doc_id:$id, parse_status:$st, error:$err}' >> "$ndjson"
done

# --- manifest ----------------------------------------------------------------

manifest="$files_dir/manifest-$ts.json"
jq -s '.' "$ndjson" > "$manifest"
cp "$manifest" "$files_dir/manifest.json"
state_set last_manifest "$manifest"

echo "" | tee -a "$log_file"
echo "----- ingest summary -----" | tee -a "$log_file"
echo "files:     $total" | tee -a "$log_file"
echo "completed: $ok" | tee -a "$log_file"
[[ $failed   -gt 0 ]] && echo "failed:    $failed"   | tee -a "$log_file"
[[ $timedout -gt 0 ]] && echo "timed out: $timedout" | tee -a "$log_file"
echo "manifest:  $manifest" | tee -a "$log_file"

write_step_ok "ingest done. Next: bash scripts/weknora/health.sh"
if [[ $failed -gt 0 || $timedout -gt 0 ]]; then exit 1; fi
