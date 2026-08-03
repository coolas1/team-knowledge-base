#!/usr/bin/env bash
# Health snapshot for the team-knowledge-base.
#
# Checks: app reachability, Ollama reachability, document counts by status,
# raw/ file parity, Neo4j entity + relationship counts (if Neo4j is
# reachable).
#
# Output written to team-knowledge-base-files/logs/health-<ts>.json.
#
# Usage:
#   bash scripts/team-knowledge-base/health.sh
#   TKB_BASE_URL=http://localhost:8000 bash scripts/team-knowledge-base/health.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

log_dir="$(get_log_dir)"
ts="$(get_timestamp)"
out="$log_dir/health-$ts.json"

# --- health probes -----------------------------------------------------------

app_ok="false"
ollama_ok="false"
neo4j_ok="false"

if test_tkb_app_reachable; then
  app_ok="true"
fi

if test_ollama_reachable; then
  ollama_ok="true"
fi

# Neo4j reachability check (best-effort — bolt port may be on the host)
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 3 http://localhost:7474 >/dev/null 2>&1; then
    neo4j_ok="true"
  fi
fi

# --- document stats ----------------------------------------------------------

doc_indexed=0
doc_failed=0
doc_pending=0
doc_other=0

if [[ "$app_ok" == "true" ]]; then
  # Paginate through all documents
  page=1
  while :; do
    page_json="$(tkb_api GET "/documents?page=$page&page_size=100" 2>/dev/null || echo '{}')"
    # Sum status counts across pages
    idx=$(echo "$page_json" | jq -r '[.documents[]? | select(.status=="indexed")] | length' 2>/dev/null || echo 0)
    fail=$(echo "$page_json" | jq -r '[.documents[]? | select(.status=="failed")] | length' 2>/dev/null || echo 0)
    pend=$(echo "$page_json" | jq -r '[.documents[]? | select(.status=="pending" or .status=="processing")] | length' 2>/dev/null || echo 0)
    oth=$(echo "$page_json" | jq -r '[.documents[]? | select(.status!="indexed" and .status!="failed" and .status!="pending" and .status!="processing")] | length' 2>/dev/null || echo 0)
    doc_indexed=$((doc_indexed + idx))
    doc_failed=$((doc_failed + fail))
    doc_pending=$((doc_pending + pend))
    doc_other=$((doc_other + oth))
    # Check if there are more pages
    total=$(echo "$page_json" | jq -r '.total // 0' 2>/dev/null || echo 0)
    if [[ $((page * 100)) -ge $total ]]; then break; fi
    page=$((page + 1))
    if [[ $page -gt 50 ]]; then break; fi
  done
fi

# --- raw/ parity -------------------------------------------------------------

raw_count=$(find "$(get_repo_root)/bench/raw" -type f | wc -l)
doc_total=$((doc_indexed + doc_failed + doc_pending + doc_other))

# --- Neo4j stats (best-effort) -----------------------------------------------

neo4j_entities="N/A"
neo4j_relationships="N/A"

if command -v cypher-shell >/dev/null 2>&1 && [[ "$neo4j_ok" == "true" ]]; then
  neo4j_entities=$(cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-password}" \
    "MATCH (n) RETURN count(n) AS c" 2>/dev/null | tail -1 | tr -d '"' || echo "N/A")
  neo4j_relationships=$(cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-password}" \
    "MATCH ()-[r]->() RETURN count(r) AS c" 2>/dev/null | tail -1 | tr -d '"' || echo "N/A")
fi

# --- write snapshot ----------------------------------------------------------

jq -n \
  --arg ts "$ts" \
  --arg app_ok "$app_ok" \
  --arg ollama_ok "$ollama_ok" \
  --arg neo4j_ok "$neo4j_ok" \
  --argjson doc_indexed "$doc_indexed" \
  --argjson doc_failed "$doc_failed" \
  --argjson doc_pending "$doc_pending" \
  --argjson doc_other "$doc_other" \
  --argjson doc_total "$doc_total" \
  --argjson raw_count "$raw_count" \
  --arg neo4j_entities "$neo4j_entities" \
  --arg neo4j_relationships "$neo4j_relationships" \
  '{
    timestamp: $ts,
    app_reachable: $app_ok,
    ollama_reachable: $ollama_ok,
    neo4j_reachable: $neo4j_ok,
    documents: {
      indexed: $doc_indexed,
      failed: $doc_failed,
      pending: $doc_pending,
      other: $doc_other,
      total: $doc_total
    },
    raw_files: $raw_count,
    neo4j: {
      entities: $neo4j_entities,
      relationships: $neo4j_relationships
    }
  }' > "$out"

# --- console summary ---------------------------------------------------------

echo ""
echo "----- tkb health ($ts) -----"
echo "app:    $app_ok"
echo "ollama: $ollama_ok"
echo "neo4j:  $neo4j_ok"
echo "docs:   $doc_total total ($doc_indexed indexed, $doc_failed failed, $doc_pending pending)"
echo "raw/:   $raw_count files"
echo "neo4j:  $neo4j_entities entities, $neo4j_relationships relationships"
echo "snapshot: $out"

write_step_ok "health done — snapshot: $out"
