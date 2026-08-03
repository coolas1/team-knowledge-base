#!/usr/bin/env bash
# Re-apply the minted WeKnora credentials to the weknora CLI profile in THIS
# context. Use after bootstrap.sh ran in a DIFFERENT context — e.g. the stack +
# bootstrap ran on the host, and you want the CLI (and its `mcp serve`) here in
# the bench container to reach the host's WeKnora at the bridge gateway.
#
# Reads api_key from state.json (or WEKNORA_API_KEY) and host from
# WEKNORA_BASE_URL (or weknora-files/env/url, or localhost:8080).
#
# Usage:
#   bash scripts/weknora/connect.sh
#   WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/connect.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

base_url="$(get_weknora_base_url)"
api_key="$(get_api_key)"

if ! command -v weknora >/dev/null 2>&1; then
  write_step_fail "weknora CLI not installed. Run: bash scripts/weknora/install-cli.sh"
  exit 2
fi

if [[ -z "$api_key" ]]; then
  write_step_fail "No api_key found. Run bootstrap.sh first (it writes state.json), or export WEKNORA_API_KEY=sk-..."
  exit 3
fi

if ! test_weknora_app_reachable; then
  write_step_warn "app not reachable at $base_url/health — profile will still be written, but tools will fail until the host stack is up."
fi

weknora profile add bench --host "$base_url" --use >/dev/null 2>&1 || true
printf '%s' "$api_key" | weknora auth login --with-token

write_step_ok "connected: profile 'bench' -> $base_url"
weknora doctor || write_step_warn "weknora doctor reported issues (see above)"
echo "MCP: 'weknora mcp serve' (registered in .mcp.json) will use this profile."
