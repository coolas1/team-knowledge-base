#!/bin/sh
set -e

mkdir -p "${GBRAIN_HOME:-/data/gbrain}"

# Map shared env vars into gbrain config
# gbrain uses an AI gateway with provider:model format (e.g. "openai:gpt-4o")
LLM_GATEWAY="${LLM_PROVIDER:-openai}:${LLM_MODEL_NAME:-gpt-4o}"
EMBED_GATEWAY="${EMBEDDING_PROVIDER:-openai}:${EMBEDDING_MODEL_NAME:-nomic-embed-text}"

cat > "${GBRAIN_HOME:-/data/gbrain}/config.json" << EOF
{
  "engine": "pglite",
  "database_path": "${GBRAIN_HOME:-/data/gbrain}/brain.db",
  "embedding_model": "${EMBED_GATEWAY}",
  "embedding_dimensions": 768,
  "expansion_model": "${LLM_GATEWAY}"
}
EOF

# Export API keys so the AI gateway can find them
# gbrain's buildGatewayConfig picks up these env vars
if [ -n "${LLM_API_KEY}" ]; then
  case "${LLM_PROVIDER:-openai}" in
    openai|azure)
      export OPENAI_API_KEY="${LLM_API_KEY}"
      export OPENAI_BASE_URL="${LLM_BASE_URL:-https://api.openai.com/v1}"
      ;;
    anthropic)
      export ANTHROPIC_API_KEY="${LLM_API_KEY}"
      export ANTHROPIC_BASE_URL="${LLM_BASE_URL:-https://api.anthropic.com/v1}"
      ;;
    google)
      export GOOGLE_GENERATIVE_AI_API_KEY="${LLM_API_KEY}"
      ;;
  esac
fi

if [ -n "${EMBEDDING_API_KEY}" ]; then
  case "${EMBEDDING_PROVIDER:-openai}" in
    openai)
      export OPENAI_API_KEY="${EMBEDDING_API_KEY}"
      ;;
  esac
fi

exec gbrain serve
