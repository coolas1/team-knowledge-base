#!/usr/bin/env bash
# Bulk-ingest documents into the TKB engine via POST /api/documents/upload.
#
# Each file is uploaded as multipart; its stored title is its path relative to
# the source dir (e.g. "research/coral-resilience-paper.md"), so documents keep
# their category grouping and names stay unique. Files whose extension has no
# extractor are skipped.
#
# Usage:
#   benchmark/ingest.sh                       # ingest benchmark/raw → localhost:8000
#   benchmark/ingest.sh ./benchmark/raw       # ...from a different source dir
#   ENGINE_URL=http://host:8000 benchmark/ingest.sh
#
# Requires: bash 4+, curl, find. The engine must be reachable (the engine
# webapp publishes :8000). Keep SUPPORTED below in sync with
# src/engine/components/extractors/registry.py (SUPPORTED_EXTENSIONS).
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-http://localhost:8000}"
SRC_DIR="${1:-benchmark/raw}"
ENDPOINT="${ENGINE_URL%/}/api/documents/upload"

# Extensions backed by an extractor. Sync with registry.SUPPORTED_EXTENSIONS.
SUPPORTED=(md markdown txt pdf docx pptx png jpg jpeg tiff bmp webp csv)

if [[ ! -d "$SRC_DIR" ]]; then
  echo "no such source dir: $SRC_DIR" >&2
  exit 2
fi

# Preflight: fail fast with a clear message instead of N upload errors.
if ! curl -fsS -o /dev/null --max-time 5 "${ENGINE_URL%/}/openapi.json"; then
  echo "engine not reachable at ${ENGINE_URL} (is the app up on :8000?)" >&2
  exit 2
fi

pattern=""
for e in "${SUPPORTED[@]}"; do pattern="${pattern:+$pattern|}$e"; done

ok=0; fail=0; skip=0; failed=()
while IFS= read -r -d '' f; do
  rel="${f#"$SRC_DIR"}"; rel="${rel#/}"
  ext="${f##*.}"; ext="${ext,,}"
  if [[ ! "$ext" =~ ^($pattern)$ ]]; then
    printf 'skip  (.%s): %s\n' "$ext" "$rel"; skip=$((skip + 1)); continue
  fi
  if curl -fsS -o /dev/null -F "file=@${f};filename=${rel}" "$ENDPOINT"; then
    printf 'ok    : %s\n' "$rel"; ok=$((ok + 1))
  else
    printf 'FAIL  : %s\n' "$rel" >&2; fail=$((fail + 1)); failed+=("$rel")
  fi
done < <(find "$SRC_DIR" -type f -print0)

echo "----"
echo "ingested=$ok failed=$fail skipped=$skip  ($SRC_DIR -> $ENDPOINT)"
if (( fail > 0 )); then
  printf '  failed:\n' >&2
  printf '    %s\n' "${failed[@]}" >&2
  exit 1
fi
