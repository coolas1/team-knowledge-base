#!/usr/bin/env python3
"""Script A — QA retrieval benchmark orchestrator.

Automates the full QA eval pipeline for one or more PKMs:

    ingest  →  predict (40 questions)  →  eval

Usage:
    python scripts/run-qa-bench.py
    python scripts/run-qa-bench.py --pkm gbrain
    python scripts/run-qa-bench.py --pkm gbrain --skip-ingest --parallel 8
    python scripts/run-qa-bench.py --skip-predict    # score existing predictions only
    python scripts/run-qa-bench.py --skip-eval       # generate predictions only

Flags:
    --pkm NAME        Run only one PKM (default: all in bench.yaml)
    --skip-ingest     Skip reset+ingest; assume KB is already populated
    --skip-predict    Skip prediction stage; only score existing predictions
    --skip-eval       Skip evaluation stage; only generate predictions
    --parallel N      Max concurrent prediction agents (default: 4)
    --timeout SECS    Per-agent timeout (default: 600)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path so ``from bench_harness import ...`` works.
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from bench_harness.config import (
    ensure_dir,
    list_pkm_names,
    load_config,
    pkm_output_dir,
    resolve_pkm,
)
from bench_harness.entrypoint import invoke
from bench_harness.prompt import build_prompt


# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
QA_DIR = REPO_ROOT / "bench" / "eval" / "qa"
QUESTIONS_DIR = QA_DIR / "questions"
PREDICT_RUNBOOK = QA_DIR / "PREDICT_INSTRUCTIONS.md"
EVAL_RUNBOOK = QA_DIR / "EVAL_INSTRUCTIONS.md"


# ---------------------------------------------------------------------------
# Stage: ingest
# ---------------------------------------------------------------------------

def run_ingest(pkm_name: str, pkm_cfg: dict) -> int:
    """Spawn the PKM's ingest command and wait for it."""
    ingest_cmd = pkm_cfg.get("ingest", "")
    if not ingest_cmd:
        print(f"[{pkm_name}] no ingest command configured — skipping")
        return 0

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] INGEST: {ingest_cmd}")
    print(f"{'='*60}")

    merged_env = dict(os.environ)
    merged_env.update(pkm_cfg.get("env", {}))

    log_dir = ensure_dir(pkm_output_dir(pkm_name) / "qa" / "logs")
    log_file = log_dir / "ingest.log"

    with open(log_file, "w") as lf:
        proc = subprocess_run(
            ["bash", "-c", ingest_cmd],
            env=merged_env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            timeout=None,  # ingest can take a long time
        )
    if proc.returncode == 0:
        print(f"[{pkm_name}] ingest OK  (log: {log_file})")
    else:
        print(f"[{pkm_name}] ingest FAILED (exit {proc.returncode}) — log: {log_file}")
    return proc.returncode


# ---------------------------------------------------------------------------
# Stage: predict
# ---------------------------------------------------------------------------

def _predict_one(
    q_id: str, q_file: Path, pkm_name: str, pkm_cfg: dict, timeout: int
) -> tuple[str, bool]:
    """Predict one question. Returns (q_id, success)."""
    output_file = pkm_output_dir(pkm_name) / "qa" / "predictions" / f"{q_id}-p.md"
    prompt = build_prompt(
        PREDICT_RUNBOOK,
        q_file,
        output_file,
        pkm_name,
    )
    label = f"{q_id}-predict"
    proc = invoke(
        pkm_name, pkm_cfg, prompt,
        output_file=output_file,
        timeout=timeout,
        bench_name="qa",
        log_label=label,
    )
    ok = proc.returncode == 0 and output_file.exists()
    return q_id, ok


def run_predict(pkm_name: str, pkm_cfg: dict, parallel: int, timeout: int) -> int:
    """Run prediction agents for all 40 questions."""
    q_files = sorted(QUESTIONS_DIR.glob("*-q.md"))
    if not q_files:
        print(f"[{pkm_name}] no question files found in {QUESTIONS_DIR}")
        return 1

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] PREDICT — {len(q_files)} questions (parallel={parallel})")
    print(f"{'='*60}")

    # Build task list: (q_id, q_file)
    tasks = [(qf.stem.replace("-q", ""), qf) for qf in q_files]

    ok = 0
    fail = 0

    if parallel <= 1:
        for q_id, qf in tasks:
            _, success = _predict_one(q_id, qf, pkm_name, pkm_cfg, timeout)
            if success:
                ok += 1
            else:
                fail += 1
            print(f"  [{pkm_name}] Q{q_id}: {'OK' if success else 'FAIL'}")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(_predict_one, q_id, qf, pkm_name, pkm_cfg, timeout): q_id
                for q_id, qf in tasks
            }
            for fut in concurrent.futures.as_completed(futures):
                q_id, success = fut.result()
                if success:
                    ok += 1
                else:
                    fail += 1
                print(f"  [{pkm_name}] Q{q_id}: {'OK' if success else 'FAIL'}  ({ok+fall}/{len(tasks)})")

    print(f"[{pkm_name}] predict done: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# Stage: eval
# ---------------------------------------------------------------------------

def run_eval(pkm_name: str, pkm_cfg: dict, timeout: int) -> int:
    """Run the evaluation agent to score predictions."""
    output_file = pkm_output_dir(pkm_name) / "qa" / "RESULTS.md"

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] EVAL — scoring predictions → {output_file}")
    print(f"{'='*60}")

    # The eval runbook references the predictions and answers directories.
    # We pass the runbook as the prompt; the agent reads predictions from
    # <pkm>-files/qa/predictions/ per the runbook instructions.
    prompt = build_prompt(
        EVAL_RUNBOOK,
        EVAL_RUNBOOK,   # the runbook IS the input — it tells the agent what to do
        output_file,
        pkm_name,
    )
    proc = invoke(
        pkm_name, pkm_cfg, prompt,
        output_file=output_file,
        timeout=timeout,
        bench_name="qa",
        log_label="eval",
    )
    if proc.returncode == 0:
        print(f"[{pkm_name}] eval OK → {output_file}")
        return 0
    else:
        print(f"[{pkm_name}] eval FAILED (exit {proc.returncode})")
        return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="QA retrieval benchmark orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--pkm", default=None, help="Run only this PKM (default: all)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingest stage")
    parser.add_argument("--skip-predict", action="store_true", help="Skip prediction stage")
    parser.add_argument("--skip-eval", action="store_true", help="Skip evaluation stage")
    parser.add_argument("--parallel", type=int, default=4, help="Max concurrent predictions (default: 4)")
    parser.add_argument("--timeout", type=int, default=600, help="Per-agent timeout in seconds (default: 600)")
    args = parser.parse_args()

    cfg = load_config()
    pkm_names = [args.pkm] if args.pkm else list_pkm_names(cfg)

    print(f"QA benchmark — PKMs: {', '.join(pkm_names)}")
    print(f"  ingest:  {'skip' if args.skip_ingest else 'yes'}")
    print(f"  predict: {'skip' if args.skip_predict else 'yes (40 questions)'}")
    print(f"  eval:    {'skip' if args.skip_eval else 'yes'}")
    print(f"  parallel: {args.parallel}  timeout: {args.timeout}s")

    overall_rc = 0

    for pkm_name in pkm_names:
        try:
            pkm_cfg = resolve_pkm(pkm_name, cfg)
        except KeyError as e:
            print(f"ERROR: {e}")
            overall_rc = 1
            continue

        # Ensure output directories exist
        ensure_dir(pkm_output_dir(pkm_name) / "qa" / "predictions")
        ensure_dir(pkm_output_dir(pkm_name) / "qa" / "logs")

        # --- Ingest ---
        if not args.skip_ingest:
            rc = run_ingest(pkm_name, pkm_cfg)
            if rc != 0:
                print(f"[{pkm_name}] ingest failed — skipping remaining stages")
                overall_rc = 1
                continue

        # --- Predict ---
        if not args.skip_predict:
            rc = run_predict(pkm_name, pkm_cfg, args.parallel, args.timeout)
            if rc != 0:
                overall_rc = 1

        # --- Eval ---
        if not args.skip_eval:
            rc = run_eval(pkm_name, pkm_cfg, args.timeout)
            if rc != 0:
                overall_rc = 1

    print(f"\n{'='*60}")
    print(f"QA benchmark complete (exit {overall_rc})")
    return overall_rc


# Use a wrapper so we can reference subprocess without a top-level import
# that shadows the module name.
import subprocess as _subprocess_mod

def subprocess_run(cmd, **kwargs):
    """Thin wrapper so the module can call subprocess.run without name collision."""
    return _subprocess_mod.run(cmd, **kwargs)


if __name__ == "__main__":
    sys.exit(main())
