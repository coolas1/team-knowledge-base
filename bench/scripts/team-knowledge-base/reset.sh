#!/usr/bin/env bash
# Reset the team-knowledge-base to an empty knowledge base.
#
# Deletes all documents via the REST API. Does NOT wipe Postgres or Neo4j
# volumes — only the document/chunk/graph records belonging to this KB.
#
# Usage:
#   bash scripts/team-knowledge-base/reset.sh
#   TKB_BASE_URL=http://localhost:8000 bash scripts/team-knowledge-base/reset.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

# --- preflight ---------------------------------------------------------------

if ! test_tkb_app_reachable; then
  write_step_fail "tkb app not reachable at $(get_tkb_base_url)/health. Start the app first."
  exit 2
fi

# --- delete all documents ----------------------------------------------------

write_step "listing existing documents..."
docs_json="$(tkb_api GET "/documents?page_size=100" 2>/dev/null || echo '{}')"
doc_count=$(echo "$docs_json" | jq -r '.total // .documents | length // 0' 2>/dev/null || echo 0)

if [[ "$doc_count" -eq 0 ]]; then
  write_step_ok "no documents to delete — already clean."
  exit 0
fi

write_step "deleting $doc_count document(s)..."

deleted=0
failed=0

# The list endpoint returns paginated results. Iterate through all pages.
page=1
while :; do
  page_json="$(tkb_api GET "/documents?page=$page&page_size=100" 2>/dev/null || echo '{}')"
  ids=$(echo "$page_json" | jq -r '.documents[]?.id // empty' 2>/dev/null || true)
  if [[ -z "$ids" ]]; then
    break
  fi
  while IFS= read -r doc_id; do
    [[ -z "$doc_id" ]] && continue
    if tkb_api DELETE "/documents/$doc_id" >/dev/null 2>&1; then
      deleted=$((deleted + 1))
    else
      failed=$((failed + 1))
      write_step_warn "  failed to delete $doc_id"
    fi
  done <<< "$ids"
  page=$((page + 1))
  # Safety: don't loop forever
  if [[ $page -gt 50 ]]; then
    write_step_warn "  gave up after 50 pages — possible infinite loop"
    break
  fi
done

echo ""
if [[ $failed -eq 0 ]]; then
  write_step_ok "reset complete — deleted $deleted document(s)."
else
  write_step_fail "reset done — $deleted deleted, $failed failed."
  exit 1
fi
