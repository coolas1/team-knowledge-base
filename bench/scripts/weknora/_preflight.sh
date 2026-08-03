#!/usr/bin/env bash
# Shared helpers for scripts/weknora/*.sh. Source this from each script:
#   source "$(dirname "$0")/_preflight.sh"
#
# WeKnora runs as a Docker/Podman stack: app on :8080, ParadeDB pgvector for
# both DB + vectors, redis for the asynq pipeline. Its source is vendored in
# this repo as a git submodule at vendors/WeKnora (init: `git submodule update
# --init --recursive vendors/WeKnora`). The reset/ingest/health scripts drive the
# running server over its REST API (curl + jq). All bench artifacts go under
# weknora-files/ (gitignored).
#
# Connectivity knob: WEKNORA_BASE_URL (env, or weknora-files/env/url, or
# http://localhost:8080). On the host that's right; from a sibling container set
# it to the host's published port (e.g. http://172.17.0.1:8080).

set -euo pipefail

# --- repo / paths -----------------------------------------------------------

get_repo_root() {
  git rev-parse --show-toplevel
}

get_weknora_files_dir() {
  echo "$(get_repo_root)/weknora-files"
}

get_weknora_src() {
  echo "${WEKNORA_SRC:-$(get_repo_root)/vendors/WeKnora}"
}

get_log_dir() {
  local dir
  dir="$(get_weknora_files_dir)/logs"
  mkdir -p "$dir"
  echo "$dir"
}

get_state_file() {
  echo "$(get_weknora_files_dir)/state/state.json"
}

get_state_dir() {
  local dir
  dir="$(get_weknora_files_dir)/state"
  mkdir -p "$dir"
  echo "$dir"
}

get_timestamp() {
  date +%Y%m%d-%H%M%S
}

# --- connectivity -----------------------------------------------------------

# Where the WeKnora app is reachable. Precedence: env > weknora-files/env/url >
# http://localhost:8080.
get_weknora_base_url() {
  local url="${WEKNORA_BASE_URL:-}"
  if [[ -z "$url" ]]; then
    local hint; hint="$(get_weknora_files_dir)/env/url"
    [[ -f "$hint" ]] && url="$(head -1 "$hint" | tr -d '[:space:]')"
  fi
  [[ -z "$url" ]] && url="http://localhost:8080"
  echo "$url"
}

# Remote Ollama serving nomic-embed-text (embeddings) + qwen3.5 (chat LLM).
get_ollama_url() {
  echo "${WEKNORA_OLLAMA_URL:-http://10.201.186.15:11434}"
}

# Pick an available compose implementation: podman compose > docker compose >
# docker-compose. Echoes the command string; returns 1 if none.
detect_compose_cmd() {
  if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    echo "podman compose"; return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "docker compose"; return 0
  fi
  if command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    echo "docker-compose"; return 0
  fi
  return 1
}

test_compose_present() {
  detect_compose_cmd >/dev/null 2>&1
}

# Is the WeKnora app up and healthy? Advisory — callers warn but may proceed
# (e.g. install-stack brings it up; bootstrap needs it up).
test_weknora_app_reachable() {
  local base; base="$(get_weknora_base_url)"
  curl -fsS --max-time 5 "$base/health" >/dev/null 2>&1
}

# Is the configured Ollama reachable? Advisory only.
test_ollama_reachable() {
  local base; base="$(get_ollama_url)"
  curl -fsS --max-time 5 "${base%/v1}/api/tags" >/dev/null 2>&1
}

# --- state (weknora-files/state/state.json) ---------------------------------

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    write_step_fail "jq is required (not on PATH). Install jq and re-run."
    exit 2
  }
}

# state_get <key> -> echoes value (empty if missing/no file).
state_get() {
  local f; f="$(get_state_file)"
  [[ -f "$f" ]] || { echo ""; return 0; }
  jq -r --arg k "$1" '.[$k] // empty' "$f" 2>/dev/null || echo ""
}

# state_set <key> <value> -> upserts into state.json (creates if absent).
state_set() {
  require_jq
  local f; f="$(get_state_file)"
  mkdir -p "$(dirname "$f")"
  if [[ ! -f "$f" ]]; then echo "{}" > "$f"; chmod 600 "$f"; fi
  local tmp; tmp="$(mktemp)"
  jq --arg k "$1" --arg v "$2" '.[$k]=$v' "$f" > "$tmp"
  mv "$tmp" "$f"
  chmod 600 "$f"
}

# Active API key: explicit env wins, else the one bootstrap minted into state.
get_api_key() {
  echo "${WEKNORA_API_KEY:-$(state_get api_key)}"
}

# wk_api <method> <path> [curl-extra-args...]
# Wraps curl against $WEKNORA_BASE_URL/api/v1 with the X-API-Key header (when we
# have one). Uses -fsS so HTTP errors surface under `set -e`; pass curl overrides
# (e.g. -F file=@x) as trailing args.
wk_api() {
  local method="$1" path="$2"; shift 2
  local base; base="$(get_weknora_base_url)"
  local key; key="$(get_api_key)"
  local hdr=()
  [[ -n "$key" ]] && hdr+=(-H "X-API-Key: $key")
  curl -fsS -X "$method" "${hdr[@]}" "$@" "$base/api/v1$path"
}

# --- console printers (mirror scripts/gbrain/_preflight.sh) -----------------

write_step()      { printf '\033[1;37m[step]\033[0m %s\n' "$*"; }
write_step_ok()   { printf '\033[1;32m[ok]\033[0m   %s\n' "$*"; }
write_step_warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
write_step_fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; }
