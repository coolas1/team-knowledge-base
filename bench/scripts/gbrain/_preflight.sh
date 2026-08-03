#!/usr/bin/env bash
# Shared helpers for scripts/gbrain/*.sh. Source this from each script:
#   source "$(dirname "$0")/_preflight.sh"
#
# Provides: get_repo_root, get_log_dir, get_timestamp,
# test_pglite_lock, test_ollama_reachable,
# write_step, write_step_ok, write_step_fail, write_step_warn.
#
# Why invoke gbrain as a bare command (not `bun run src/cli.ts`)? On Linux,
# `bun install -g github:garrytan/gbrain` produces a working compiled binary
# at ~/.bun/bin/gbrain — the bunfs vfs is writable, so PGLite's WASM payloads
# extract cleanly. No source checkout, no uncompiled workaround needed. The
# Windows-only bunfs read-only bug does not apply here.

set -euo pipefail

get_repo_root() {
  git rev-parse --show-toplevel
}

get_log_dir() {
  local dir
  dir="$(get_repo_root)/gbrain-files/logs"
  mkdir -p "$dir"
  echo "$dir"
}

get_timestamp() {
  date +%Y%m%d-%H%M%S
}

# Probe whether the PGLite single-writer lock is currently held (typically by
# Claude Code's MCP server). Returns 0 (true) if a `gbrain doctor --json` does
# not finish within the timeout — caller should tell the user to exit Claude
# Code before retrying. Returns 1 (false) otherwise.
test_pglite_lock() {
  if timeout 8 gbrain doctor --json >/dev/null 2>&1; then
    return 1
  fi
  # Distinguish "timed out" (lock held) from a real gbrain error. A broken
  # gbrain install also returns non-zero, so check stderr for the lock message.
  local err
  err=$(timeout 8 gbrain doctor --json 2>&1 >/dev/null || true)
  if echo "$err" | grep -q "PGLite lock\|Timed out waiting"; then
    return 0
  fi
  # If `timeout` itself reports 124 (the command hit the deadline), treat as
  # lock held — the most common cause by far.
  if ! timeout 8 gbrain doctor --json >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

# Read the configured Ollama base URL from ~/.gbrain/config.json and probe it.
# Returns 0 (true) if reachable. Advisory only — callers warn but do not abort,
# since `gbrain import --no-embed` still produces a queryable brain and the
# post-ingest `gbrain doctor` surfaces `embedding_provider_reach` on its own.
test_ollama_reachable() {
  local config_path="$HOME/.gbrain/config.json"
  [[ -f "$config_path" ]] || return 1
  local base
  # Prefer jq if available; fall back to grep + sed.
  if command -v jq >/dev/null 2>&1; then
    base=$(jq -r '.provider_base_urls.ollama // empty' "$config_path" 2>/dev/null || true)
  else
    base=$(grep -oE '"ollama"\s*:\s*"[^"]+"' "$config_path" 2>/dev/null \
      | head -1 | sed -E 's/.*"([^"]+)"$/\1/')
  fi
  [[ -n "$base" ]] || return 1
  local tags_url="${base%/v1}/api/tags"
  curl -fsS --max-time 3 "$tags_url" >/dev/null 2>&1
}

write_step()    { printf '\033[1;37m[step]\033[0m %s\n' "$*"; }
write_step_ok() { printf '\033[1;32m[ok]\033[0m   %s\n' "$*"; }
write_step_fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; }
write_step_warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
