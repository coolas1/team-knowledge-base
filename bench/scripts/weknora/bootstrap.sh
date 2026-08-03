#!/usr/bin/env bash
# One-time headless bootstrap of the WeKnora bench tenant + knowledge base.
#
#   1. POST /tenants -> mint a tenant whose response carries api_key (no auth
#      header required for the first tenant). Idempotent: reuse an existing key
#      from state.json.
#   2. Resolve an embedding model + a chat/LLM model from the running instance
#      (these point at the remote Ollama: nomic-embed-text / qwen3.5). WeKnora
#      usually seeds defaults on first boot; if not, we try to register them.
#   3. POST /knowledge-bases -> create "wren-adachi-corpus" referencing those
#      model IDs. Idempotent: reuse an existing KB of the same name.
#   4. Persist {tenant_id, api_key, kb_id, model ids} to weknora-files/state/
#      and (if the weknora CLI is installed) configure its profile.
#
# Usage:
#   bash scripts/weknora/bootstrap.sh
#   WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/bootstrap.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"
require_jq

KB_NAME="${WEKNORA_KB_NAME:-wren-adachi-corpus}"
TENANT_NAME="${WEKNORA_TENANT_NAME:-wren-adachi-bench}"
base_url="$(get_weknora_base_url)"

# --- preflight ---------------------------------------------------------------

if ! test_weknora_app_reachable; then
  write_step_fail "WeKnora app not reachable at $base_url/health. Bring it up first: bash scripts/weknora/install-stack.sh"
  exit 2
fi
write_step_ok "app reachable at $base_url"

if test_ollama_reachable; then
  write_step_ok "Ollama reachable — embeddings + LLM available"
else
  write_step_warn "Ollama NOT reachable ($(get_ollama_url)); document upload/parse will likely fail."
fi

# --- 1. tenant (+ api_key) ---------------------------------------------------

api_key="$(get_api_key)"
if [[ -n "$api_key" ]]; then
  write_step_ok "reusing api_key from state/env"
else
  write_step "POST /tenants ($TENANT_NAME)"
  body="$(jq -n \
    --arg name "$TENANT_NAME" \
    --arg desc "Bench tenant for the synthetic Wren Adachi multi-modal corpus" \
    '{name:$name, description:$desc, business:"personal",
      retriever_engines:{engines:[
        {retriever_type:"keywords", retriever_engine_type:"postgres"},
        {retriever_type:"vector",   retriever_engine_type:"postgres"}]}}')"
  resp="$(wk_api POST /tenants --data "$body" -H "Content-Type: application/json")"
  tenant_id="$(echo "$resp" | jq -r '.data.id // empty')"
  api_key="$(echo "$resp" | jq -r '.data.api_key // empty')"
  if [[ -z "$api_key" ]]; then
    write_step_fail "tenant create did not return an api_key. Response:" >&2
    echo "$resp" >&2
    write_step_fail "(if POST /tenants is auth-gated here, register a user via the Web UI at $base_url, then set WEKNORA_API_KEY and re-run.)"
    exit 3
  fi
  state_set tenant_id "${tenant_id:-}"
  state_set api_key "$api_key"
  write_step_ok "tenant minted (id=${tenant_id:-unknown}); api_key saved to state.json"
fi

# --- 2. resolve models (embedding + chat) ------------------------------------
#
# Field names vary across WeKnora versions, so match leniently. WeKnora seeds
# default Ollama models on first boot when OLLAMA_BASE_URL is set; we prefer
# those and only attempt registration if none match.

resolve_model() {  # $1 = regex over model_type/type/name
  wk_api GET /models 2>/dev/null \
    | jq -r --arg re "$1" \
        '.data[]? | select((.model_type//.type//.engine_type//.name//"") | test($re;"i")) | .id' \
    | head -1
}

emb_id="$(state_get embedding_model_id)"
chat_id="$(state_get chat_model_id)"
[[ -n "$emb_id" ]]  || emb_id="$(resolve_model 'embed')"
[[ -n "$chat_id" ]] || chat_id="$(resolve_model 'chat|llm|language|qwen')"

# Best-effort registration if still empty. The POST /models body shape follows
# the OpenAI-compatible provider convention; if the version differs this just
# logs and we continue (KB create will surface a clear error if it needs them).
if [[ -z "$emb_id" || -z "$chat_id" ]]; then
  write_step_warn "default models not found via GET /models — attempting to register Ollama models"
  ollama="$(get_ollama_url)"
  for pair in "embedding:nomic-embed-text" "chat:qwen3.5"; do
    kind="${pair%%:*}"; mname="${pair##*:}"
    if [[ "$kind" == "embedding" && -n "$emb_id" ]]; then continue; fi
    if [[ "$kind" == "chat"      && -n "$chat_id" ]]; then continue; fi
    reg="$(jq -n --arg n "$mname" --arg t "$kind" --arg u "$ollama/v1" \
      '{name:$n, model_type:$t, provider:"ollama", base_url:$u, api_key:"ollama", model_name:$n}' 2>/dev/null || true)"
    set +e
    r="$(wk_api POST /models --data "$reg" -H "Content-Type: application/json" 2>&1)" || true
    set -e
    id="$(echo "$r" | jq -r '.data.id // empty' 2>/dev/null || true)"
    [[ -n "$id" ]] && { [[ "$kind" == "embedding" ]] && emb_id="$id" || chat_id="$id"; write_step_ok "registered $kind model $mname -> $id"; } \
      || write_step_warn "could not auto-register $kind ($mname); configure it in the Web UI if KB create fails."
  done
fi
state_set embedding_model_id "${emb_id:-}"
state_set chat_model_id "${chat_id:-}"
[[ -n "$emb_id" ]]  && write_step_ok "embedding model: $emb_id"  || write_step_warn "no embedding model resolved"
[[ -n "$chat_id" ]] && write_step_ok "chat model:      $chat_id" || write_step_warn "no chat model resolved"

# --- 3. knowledge base -------------------------------------------------------

kb_id="$(state_get kb_id)"
# Validate a stored kb_id still exists; else look up by name; else create.
if [[ -n "$kb_id" ]] && wk_api GET "/knowledge-bases/$kb_id" >/dev/null 2>&1; then
  write_step_ok "reusing KB $kb_id"
else
  kb_id=""
  existing="$(wk_api GET /knowledge-bases 2>/dev/null \
    | jq -r --arg name "$KB_NAME" '.data[]? | select(.name==$name) | .id' | head -1 || true)"
  if [[ -n "$existing" ]]; then
    kb_id="$existing"; write_step_ok "found existing KB '$KB_NAME' -> $kb_id"
  else
    write_step "POST /knowledge-bases ($KB_NAME)"
    kbb="$(jq -n --arg name "$KB_NAME" \
      --arg emb "$emb_id" --arg chat "$chat_id" \
      '{name:$name, description:"Wren Adachi personal archive (synthetic) — multi-modal RAG bench corpus",
        chunking_config:{chunk_size:1000, chunk_overlap:200,
          separators:["\n\n","\n",". ","! ","? ","; ","。","！","？","；"],
          enable_multimodal:true},
        embedding_model_id:$emb, summary_model_id:$chat,
        vlm_config:{enabled:true, model_id:$chat},
        rerank_model_id:""}')"
    # Drop empty model ids (server may reject empty UUIDs); only keep what we have.
    [[ -z "$emb_id" ]]  && kbb="$(echo "$kbb" | jq 'del(.embedding_model_id)')"
    [[ -z "$chat_id" ]] && kbb="$(echo "$kbb" | jq 'del(.summary_model_id, .vlm_config)')"
    resp="$(wk_api POST /knowledge-bases --data "$kbb" -H "Content-Type: application/json")"
    kb_id="$(echo "$resp" | jq -r '.data.id // empty')"
    if [[ -z "$kb_id" ]]; then
      write_step_fail "KB create failed. Response:" >&2; echo "$resp" >&2
      write_step_fail "If it complains about model ids, configure embedding/chat models in the Web UI ($base_url) and re-run." >&2
      exit 4
    fi
    write_step_ok "KB created -> $kb_id"
  fi
fi
state_set kb_id "$kb_id"

# --- 4. CLI profile (optional) ----------------------------------------------

if command -v weknora >/dev/null 2>&1; then
  weknora profile add bench --host "$base_url" --use >/dev/null 2>&1 || true
  echo "$api_key" | weknora auth login --with-token >/dev/null 2>&1 \
    && write_step_ok "weknora CLI profile 'bench' configured" \
    || write_step_warn "weknora CLI login failed (non-fatal; core pipeline uses curl)"
else
  write_step_warn "weknora CLI not installed — core pipeline still works via curl. (Optional: bash scripts/weknora/install-cli.sh)"
fi

cat <<EOF

Bootstrap done. State: $(get_state_file)
  tenant_id:    $(state_get tenant_id)
  kb_id:        $kb_id  (name: $KB_NAME)
  base_url:     $base_url

Next:
  bash scripts/weknora/ingest.sh          # upload raw/ -> KB
  bash scripts/weknora/health.sh          # verify
EOF
