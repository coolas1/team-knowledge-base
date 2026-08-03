#!/usr/bin/env bash
# Generate WeKnora's predictions for the 30-question eval suite — mirrors
# gbrain-files/qa/predictions/ so the two apps can be scored side by side.
#
# Reads each eval/qa/questions/NN-q.md, asks WeKnora (RAG chat over the bench
# KB), and writes weknora-files/qa/predictions/NN-p.md in the gbrain prediction
# format (frontmatter: id, knowledge_base, question_file).
#
# REQUIRES the weknora CLI (the REST chat endpoint shape varies; the CLI's
# `chat` is the stable surface). Install it with: bash scripts/weknora/install-cli.sh
#
# Usage:
#   bash scripts/weknora/qa-predict.sh
#   bash scripts/weknora/qa-predict.sh 5            # only Q05
#   WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/qa-predict.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

if ! command -v weknora >/dev/null 2>&1; then
  write_step_fail "weknora CLI required for predictions. Run: bash scripts/weknora/install-cli.sh"
  exit 2
fi
kb_id="$(state_get kb_id)"
if [[ -z "$kb_id" ]]; then
  write_step_fail "no kb_id in state. Run bootstrap.sh + ingest.sh first."; exit 3
fi

repo="$(get_repo_root)"
q_dir="$repo/eval/qa/questions"
out_dir="$(get_weknora_files_dir)/qa/predictions"
mkdir -p "$out_dir"

only="${1:-}"
for q in "$q_dir"/*-q.md; do
  [[ -f "$q" ]] || continue
  base="$(basename "$q")"          # NN-q.md
  nn="${base%%-*}"                 # NN
  [[ -n "$only" && "$only" != "$nn" ]] && continue
  out="$out_dir/${nn}-p.md"
  write_step "Q$nn -> $out"

  # Strip YAML frontmatter + the first "# Qxx —" heading; the question is the
  # remaining body. Collapse to one line for the CLI.
  qtext="$(awk 'BEGIN{fm=0} /^---[[:space:]]*$/ {fm++; next} fm<2 {next} /^# /{next} NF{print}' "$q" \
           | tr '\n' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')"
  if [[ -z "$qtext" ]]; then
    write_step_warn "  could not extract question text from $q — skipping"; continue
  fi

  set +e
  ans="$(weknora chat "$qtext" --kb "$kb_id" 2>/dev/null \
          | sed -r 's/\x1B\[[0-9;]*[mK]//g' | tr -s ' \n' ' ')"
  rc=$?
  set -e
  [[ $rc -ne 0 ]] && ans="(weknora chat exited $rc; see logs). Question was: $qtext"

  {
    echo "---"
    echo "id: $nn"
    echo "knowledge_base: weknora"
    echo "question_file: eval/qa/questions/${base}"
    echo "---"
    echo ""
    echo "# P$nn"
    echo ""
    echo "## Answer"
    echo ""
    echo "$ans"
  } > "$out"
  write_step_ok "  wrote $out"
done

write_step_ok "predictions written to $out_dir"
echo "Score against eval/qa/answers/ per eval/qa/EVAL_INSTRUCTIONS.md."
