#!/usr/bin/env bash
# Print the overall health of the brain. Runs gbrain doctor + a few summary
# probes pulled from the doctor JSON (page count, brain-score breakdown, sample
# link graph from a hub page).
#
# Output:
#   - Console: gbrain doctor (text) + summary probes + a one-paragraph recap.
#   - File:    gbrain-files/logs/doctor-<ts>.json — UTF-8 JSON snapshot for
#              diffing across runs (e.g. before/after a corpus change).
#
# Usage:
#   bash scripts/gbrain/health.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

log_dir="$(get_log_dir)"
timestamp="$(get_timestamp)"

if test_pglite_lock; then
  write_step_fail "PGLite lock held. Exit Claude Code (which hosts the MCP server) before running this script."
  exit 2
fi

log_file="$log_dir/health-$timestamp.log"
doctor_json="$log_dir/doctor-$timestamp.json"

# --- doctor (text) -----------------------------------------------------------

write_step "gbrain doctor (text)"
# gbrain doctor exits 1 when any check fails — that's diagnostic, not a script
# failure. Capture the exit code but don't let `set -e` abort the run.
set +e
gbrain doctor 2>&1 | tee -a "$log_file"
doctor_text_rc=${PIPESTATUS[0]}
set -e

# --- doctor (JSON snapshot) --------------------------------------------------

write_step "doctor --json -> $doctor_json"
set +e
gbrain doctor --json 2> >(tee -a "$log_file" >&2) > "$doctor_json"
doctor_json_rc=$?
set -e
if [[ $doctor_json_rc -ne 0 ]]; then
  write_step_warn "doctor --json exited $doctor_json_rc (continuing)"
fi

# --- summary probes from JSON ------------------------------------------------

page_count=""
brain_score_message=""
health_score=""
if [[ -s "$doctor_json" ]] && command -v jq >/dev/null 2>&1; then
  health_score=$(jq -r '.health_score // .brain_checks_score // empty' "$doctor_json" 2>/dev/null || true)
  page_count=$(jq -r '.checks[] | select(.name=="connection") | .message' "$doctor_json" 2>/dev/null \
    | grep -oE '[0-9]+ pages?' | head -1 | grep -oE '[0-9]+' || true)
  brain_score_message=$(jq -r '.checks[] | select(.name=="brain_score") | .message' "$doctor_json" 2>/dev/null || true)
fi

# --- sample link graph from a hub page ---------------------------------------
#
# `gbrain import raw` derives slugs from path-relative file paths, so the hub
# page raw/notes/ideas.md is most likely `notes/ideas`. Try a few patterns in
# order; the first that resolves to an actual page wins.

hub_slug=""
graph_edge_count=0
candidate_slugs=("notes/ideas" "ideas" "notes-ideas" "research/coral-resilience-paper")
for slug in "${candidate_slugs[@]}"; do
  set +e
  probe=$(gbrain graph "$slug" 2>/dev/null || true)
  set -e
  if [[ -n "$probe" && "$probe" =~ [^[:space:]] ]]; then
    hub_slug="$slug"
    write_step "gbrain graph $hub_slug"
    echo "$probe" | tee -a "$log_file"
    if command -v jq >/dev/null 2>&1; then
      graph_json=$(gbrain graph "$hub_slug" --json 2>/dev/null || true)
      if [[ -n "$graph_json" ]]; then
        # Current format: top-level ARRAY of graph nodes, each carrying a
        # .links[] of {to_slug,link_type} edges. Count the hub node's links
        # (the node at depth 0 / matching slug). Legacy fallback: top-level
        # object with .links/.children/.edges.
        graph_edge_count=$(echo "$graph_json" \
          | jq -r --arg slug "$hub_slug" \
              'if type == "array"
               then ( [.[] | select(.slug == $slug or .depth == 0) | ((.links // []) | .[])] | length )
               else ( ((.links // .children // .edges) // []) | length )
               end' 2>/dev/null || echo 0)
      fi
    fi
    break
  fi
done
if [[ -z "$hub_slug" ]]; then
  write_step_warn "could not resolve any hub slug - skipped sample graph"
fi

# --- summary -----------------------------------------------------------------

echo ""
echo "----- summary -----"
[[ -n "$health_score" ]] && echo "overall health score: $health_score"
if [[ $doctor_text_rc -ne 0 ]]; then
  write_step_warn "doctor text exited $doctor_text_rc (non-zero means failed checks - see top issues above)"
fi
[[ -n "$page_count" ]] && echo "page count:            $page_count (expected ~22 .md files from raw/)"
[[ -n "$brain_score_message" ]] && echo "brain_score:           $brain_score_message"
if [[ -n "$hub_slug" ]]; then
  echo "hub page:              $hub_slug"
  echo "graph edges from hub:  $graph_edge_count (proves \`extract all --source fs --dir raw\` worked)"
fi
echo "JSON snapshot:         $doctor_json"
