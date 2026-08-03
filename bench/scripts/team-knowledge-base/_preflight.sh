#!/usr/bin/env bash
# Shared helpers for scripts/team-knowledge-base/*.sh. Source this from each script:
#   source "$(dirname "$0")/_preflight.sh"
#
# team-knowledge-base runs as a FastAPI monolith (Python) on APP_PORT (default 8000),
# backed by Postgres 16 + pgvector (:5433) and Neo4j 5 (:7687). Its source is
# located at the repo root. Bench artifacts go
# under team-knowledge-base-files/ (gitignored).
#
# Connectivity knob: TKB_BASE_URL (env, or http://127.0.0.1:8000).

set -euo pipefail

# --- repo / paths -----------------------------------------------------------

get_repo_root() {
  git rev-parse --show-toplevel
}

get_tkb_files_dir() {
  echo "$(get_repo_root)/team-knowledge-base-files"
}

get_log_dir() {
  local dir
  dir="$(get_tkb_files_dir)/logs"
  mkdir -p "$dir"
  echo "$dir"
}

get_timestamp() {
  date +%Y%m%d-%H%M%S
}

# --- connectivity -----------------------------------------------------------

# Where the tkb app is reachable. Precedence: env > http://127.0.0.1:8000.
get_tkb_base_url() {
  local url="${TKB_BASE_URL:-}"
  [[ -z "$url" ]] && url="http://127.0.0.1:8000"
  echo "$url"
}

# Remote Ollama serving nomic-embed-text (embeddings) + LLM.
get_ollama_url() {
  echo "${TKB_OLLAMA_URL:-http://10.201.186.15:11434}"
}

# Is the tkb app up and responding?
test_tkb_app_reachable() {
  local base; base="$(get_tkb_base_url)"
  curl -fsS --max-time 5 "$base/health" >/dev/null 2>&1
}

# Is Ollama reachable? Advisory only.
test_ollama_reachable() {
  local base; base="$(get_ollama_url)"
  curl -fsS --max-time 5 "${base%/v1}/api/tags" >/dev/null 2>&1
}

# --- API helpers ------------------------------------------------------------

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    write_step_fail "jq is required (not on PATH). Install jq and re-run."
    exit 2
  }
}

# tkb_api <method> <path> [curl-extra-args...]
# Wraps curl against $TKB_BASE_URL. Uses -fsS so HTTP errors surface under
# `set -e`; pass curl overrides (e.g. -F file=@x) as trailing args.
tkb_api() {
  local method="$1" path="$2"; shift 2
  local base; base="$(get_tkb_base_url)"
  curl -fsS -X "$method" "$@" "$base$path"
}

# --- console printers (mirror scripts/gbrain/_preflight.sh) -----------------

write_step()      { printf '\033[1;37m[step]\033[0m %s\n' "$*"; }
write_step_ok()   { printf '\033[1;32m[ok]\033[0m   %s\n' "$*"; }
write_step_warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
write_step_fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; }
