#!/usr/bin/env bash
# Print the health of the WeKnora bench: app reachability, CLI doctor (if the
# CLI is installed), KB doc counts by parse_status, and a parity check against
# the raw/ file count.
#
# Output:
#   - Console: probes + summary.
#   - File:    weknora-files/logs/health-<ts>.json (a snapshot for diffing).
#
# Usage:
#   bash scripts/weknora/health.sh
#   WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/health.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

log_dir="$(get_log_dir)"
ts="$(get_timestamp)"
snap="$log_dir/health-$ts.json"
base_url="$(get_weknora_base_url)"

# --- app reachability --------------------------------------------------------

if test_weknora_app_reachable; then
  write_step_ok "app reachable at $base_url"
else
  write_step_fail "app NOT reachable at $base_url/health. Bring the stack up: bash scripts/weknora/install-stack.sh"
  exit 2
fi

if test_ollama_reachable; then
  write_step_ok "Ollama reachable ($(get_ollama_url))"
else
  write_step_warn "Ollama NOT reachable — embeddings/parse will fail."
fi

# --- CLI doctor (optional) ---------------------------------------------------

if command -v weknora >/dev/null 2>&1; then
  write_step "weknora doctor"
  set +e; weknora doctor 2>&1 | tee -a "$snap" >/dev/null; set -e
else
  echo "(weknora CLI not installed — skipping doctor)" | tee -a "$snap"
fi

# --- KB doc inventory --------------------------------------------------------

kb_id="$(state_get kb_id)"
counts='{completed:0,failed:0,processing:0,pending:0,other:0}'
total_docs=0
if [[ -n "$kb_id" ]]; then
  # Page through all docs in the KB and tally parse_status.
  page=1
  while :; do
    set +e
    r="$(wk_api GET "/knowledge-bases/$kb_id/knowledge?page=$page&page_size=100" 2>/dev/null || true)"
    set -e
    n="$(echo "$r" | jq -r '.data | if type=="array" then length else (.items|length) end' 2>/dev/null || echo 0)"
    [[ "$n" -eq 0 ]] && break
    page_counts="$(echo "$r" | jq -r '[.data[]? // .data.items[]? | .parse_status // "other"]
      | group_by(.) | map({(.[0]):length}) | add // {}' 2>/dev/null || echo '{}')"
    counts="$(jq -n --argjson c "$counts" --argjson p "$page_counts" \
      '$c * $p | with_entries(.value //= 0) | to_entries | map(.value = (.value|tonumber? // 0)) | from_entries' 2>/dev/null || echo "$counts")"
    total_docs=$((total_docs + n))
    more="$(echo "$r" | jq -r 'if .total then (.total > (.page * (.page_size // 100))) else false end' 2>/dev/null || echo false)"
    [[ "$more" == "true" ]] && page=$((page+1)) || break
    [[ $page -gt 50 ]] && break   # hard cap
  done
else
  write_step_warn "no kb_id in state — run bootstrap.sh first."
fi

# --- raw/ parity -------------------------------------------------------------

raw_dir="$(get_repo_root)/raw"
raw_total="$(cd "$raw_dir" && find . -type f | wc -l | tr -d ' ')"
manifest="$(get_weknora_files_dir)/manifest.json"
manifest_total=""
[[ -f "$manifest" ]] && manifest_total="$(jq -r 'length' "$manifest" 2>/dev/null || true)"

# --- summary -----------------------------------------------------------------

echo "" | tee -a "$snap"
echo "----- WeKnora bench health -----" | tee -a "$snap"
echo "base_url:       $base_url" | tee -a "$snap"
echo "kb_id:          ${kb_id:-<none>}" | tee -a "$snap"
echo "docs in KB:     $total_docs" | tee -a "$snap"
echo "  completed:    $(jq -r '.completed // 0' <<<"$counts")" | tee -a "$snap"
echo "  failed:       $(jq -r '.failed // 0' <<<"$counts")" | tee -a "$snap"
echo "  processing:   $(jq -r '.processing // 0' <<<"$counts")" | tee -a "$snap"
echo "  pending:      $(jq -r '.pending // 0' <<<"$counts")" | tee -a "$snap"
echo "raw/ files:     $raw_total" | tee -a "$snap"
[[ -n "$manifest_total" ]] && echo "last manifest:  $manifest_total entries ($manifest)" | tee -a "$snap"
echo "snapshot:       $snap" | tee -a "$snap"

failed="$(jq -r '.failed // 0' <<<"$counts")"
if [[ -n "$kb_id" && "$total_docs" -ge "$raw_total" && "$failed" -eq 0 ]]; then
  write_step_ok "healthy: KB holds $total_docs docs, all parsed, matches raw/ ($raw_total files)."
else
  write_step_warn "incomplete: KB=$total_docs vs raw/=$raw_total, failed=$failed. Inspect with: bash scripts/weknora/ingest.sh"
fi
