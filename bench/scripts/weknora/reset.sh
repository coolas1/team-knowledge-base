#!/usr/bin/env bash
# Reset the WeKnora knowledge base to empty (clean ingest).
#
# Deletes the bench KB and re-creates it via bootstrap.sh. Does NOT touch the
# Docker volumes or the running stack — only the KB (so a re-ingest starts from
# zero documents without re-pulling images or re-minting the tenant).
#
# Usage:
#   bash scripts/weknora/reset.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

kb_id="$(state_get kb_id)"
if [[ -z "$kb_id" ]]; then
  write_step_warn "no kb_id in state — nothing to delete. Run bootstrap.sh first."
  exit 0
fi

if ! test_weknora_app_reachable; then
  write_step_fail "WeKnora app not reachable ($(get_weknora_base_url)/health). Bring the stack up first."
  exit 2
fi

write_step "DELETE /knowledge-bases/$kb_id"
if wk_api DELETE "/knowledge-bases/$kb_id" >/dev/null 2>&1; then
  write_step_ok "KB $kb_id deleted"
else
  write_step_warn "delete returned non-zero (KB may already be gone) — continuing"
fi
state_set kb_id ""

# Re-create the KB (reuses tenant/api_key/models already in state).
bash "$(dirname "$0")/bootstrap.sh"

write_step_ok "reset complete. Empty KB ready. Next:"
echo "  bash scripts/weknora/ingest.sh"
