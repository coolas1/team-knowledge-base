#!/usr/bin/env python3
"""Script B — File-update benchmark orchestrator.

Automates the full file-update eval pipeline for one or more PKMs:

    ingest  →  edit (Agent A, 20 entries)  →  verify (Agent B)  →  eval

Usage:
    python scripts/run-file-update-bench.py
    python scripts/run-file-update-bench.py --pkm gbrain
    python scripts/run-file-update-bench.py --pkm gbrain --skip-ingest --skip-edit
    python scripts/run-file-update-bench.py --skip-verify   # edit only, skip verify
    python scripts/run-file-update-bench.py --skip-eval     # no scoring

Flags:
    --pkm NAME        Run only one PKM (default: all in bench.yaml)
    --skip-ingest     Skip reset+ingest; assume KB is already populated
    --skip-edit       Skip Agent A (edit) stage
    --skip-verify     Skip Agent B (verify) stage
    --skip-eval       Skip scoring stage
    --timeout SECS    Per-agent timeout (default: 600)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path.
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
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FU_DIR = REPO_ROOT / "bench" / "eval" / "file-update"
ENTRIES_DIR = FU_DIR / "entries"
EDIT_RUNBOOK = FU_DIR / "EDIT_INSTRUCTIONS.md"
VERIFY_RUNBOOK = FU_DIR / "VERIFY_INSTRUCTIONS.md"
EVAL_RUNBOOK = FU_DIR / "EVAL_INSTRUCTIONS.md"


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

    log_dir = ensure_dir(pkm_output_dir(pkm_name) / "file-update" / "logs")
    log_file = log_dir / "ingest.log"

    with open(log_file, "w") as lf:
        proc = subprocess.run(
            ["bash", "-c", ingest_cmd],
            env=merged_env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            timeout=None,
        )
    if proc.returncode == 0:
        print(f"[{pkm_name}] ingest OK  (log: {log_file})")
    else:
        print(f"[{pkm_name}] ingest FAILED (exit {proc.returncode}) — log: {log_file}")
    return proc.returncode


# ---------------------------------------------------------------------------
# Stage: edit (Agent A)
# ---------------------------------------------------------------------------

def run_edit(pkm_name: str, pkm_cfg: dict, timeout: int) -> int:
    """Run Agent A (editor) for all 20 entries, sequentially."""
    entries = sorted(ENTRIES_DIR.glob("*"))
    entries = [e for e in entries if e.is_dir()]
    if not entries:
        print(f"[{pkm_name}] no file-update entries found in {ENTRIES_DIR}")
        return 1

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] EDIT (Agent A) — {len(entries)} entries (sequential)")
    print(f"{'='*60}")

    ok = 0
    fail = 0

    for entry_dir in entries:
        entry_id = entry_dir.name[:2]  # "01", "02", ...
        prompt_file = entry_dir / "prompt.md"
        if not prompt_file.exists():
            print(f"  [{pkm_name}] {entry_dir.name}: SKIP (no prompt.md)")
            continue

        output_file = (
            pkm_output_dir(pkm_name)
            / "file-update"
            / "edit-trails"
            / f"{entry_id}-log.md"
        )
        prompt = build_prompt(
            EDIT_RUNBOOK,
            prompt_file,
            output_file,
            pkm_name,
        )
        label = f"{entry_id}-edit"
        proc = invoke(
            pkm_name, pkm_cfg, prompt,
            output_file=output_file,
            timeout=timeout,
            bench_name="file-update",
            log_label=label,
        )
        if proc.returncode == 0:
            ok += 1
            print(f"  [{pkm_name}] {entry_dir.name}: OK")
        else:
            fail += 1
            print(f"  [{pkm_name}] {entry_dir.name}: FAIL (exit {proc.returncode})")

    print(f"[{pkm_name}] edit done: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# Stage: verify (Agent B)
# ---------------------------------------------------------------------------

def run_verify(pkm_name: str, pkm_cfg: dict, timeout: int) -> int:
    """Run Agent B (verifier) for all 20 entries, sequentially.

    Each invocation is a FRESH process with no memory of Stage 1.
    """
    entries = sorted(ENTRIES_DIR.glob("*"))
    entries = [e for e in entries if e.is_dir()]
    if not entries:
        print(f"[{pkm_name}] no file-update entries found in {ENTRIES_DIR}")
        return 1

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] VERIFY (Agent B) — {len(entries)} entries (sequential)")
    print(f"{'='*60}")

    ok = 0
    fail = 0

    for entry_dir in entries:
        entry_id = entry_dir.name[:2]

        # Collect verify questions
        vq_dir = entry_dir / "verify-q"
        if not vq_dir.is_dir():
            print(f"  [{pkm_name}] {entry_dir.name}: SKIP (no verify-q/)")
            continue

        vq_files = sorted(vq_dir.glob("*-vq.md"))
        if not vq_files:
            print(f"  [{pkm_name}] {entry_dir.name}: SKIP (no verification questions)")
            continue

        output_file = (
            pkm_output_dir(pkm_name)
            / "file-update"
            / "verify-predictions"
            / f"{entry_id}-vp.md"
        )

        # Build prompt: runbook + first verify-q file as main input + rest as extras
        prompt = build_prompt(
            VERIFY_RUNBOOK,
            vq_files[0],
            output_file,
            pkm_name,
            extra_input_paths=vq_files[1:],
        )
        label = f"{entry_id}-verify"
        proc = invoke(
            pkm_name, pkm_cfg, prompt,
            output_file=output_file,
            timeout=timeout,
            bench_name="file-update",
            log_label=label,
        )
        if proc.returncode == 0:
            ok += 1
            print(f"  [{pkm_name}] {entry_dir.name}: OK")
        else:
            fail += 1
            print(f"  [{pkm_name}] {entry_dir.name}: FAIL (exit {proc.returncode})")

    print(f"[{pkm_name}] verify done: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# Stage: eval
# ---------------------------------------------------------------------------

def run_eval(pkm_name: str, pkm_cfg: dict, timeout: int) -> int:
    """Run the scoring agent to evaluate edit trails + verify predictions."""
    output_file = pkm_output_dir(pkm_name) / "file-update" / "RESULTS.md"

    print(f"\n{'='*60}")
    print(f"[{pkm_name}] EVAL — scoring entries → {output_file}")
    print(f"{'='*60}")

    prompt = build_prompt(
        EVAL_RUNBOOK,
        EVAL_RUNBOOK,
        output_file,
        pkm_name,
    )
    proc = invoke(
        pkm_name, pkm_cfg, prompt,
        output_file=output_file,
        timeout=timeout,
        bench_name="file-update",
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
        description="File-update benchmark orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--pkm", default=None, help="Run only this PKM (default: all)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingest stage")
    parser.add_argument("--skip-edit", action="store_true", help="Skip edit (Agent A) stage")
    parser.add_argument("--skip-verify", action="store_true", help="Skip verify (Agent B) stage")
    parser.add_argument("--skip-eval", action="store_true", help="Skip evaluation stage")
    parser.add_argument("--timeout", type=int, default=600, help="Per-agent timeout in seconds (default: 600)")
    args = parser.parse_args()

    cfg = load_config()
    pkm_names = [args.pkm] if args.pkm else list_pkm_names(cfg)

    print(f"File-update benchmark — PKMs: {', '.join(pkm_names)}")
    print(f"  ingest: {'skip' if args.skip_ingest else 'yes'}")
    print(f"  edit:   {'skip' if args.skip_edit else 'yes (20 entries)'}")
    print(f"  verify: {'skip' if args.skip_verify else 'yes (20 entries)'}")
    print(f"  eval:   {'skip' if args.skip_eval else 'yes'}")
    print(f"  timeout: {args.timeout}s")

    overall_rc = 0

    for pkm_name in pkm_names:
        try:
            pkm_cfg = resolve_pkm(pkm_name, cfg)
        except KeyError as e:
            print(f"ERROR: {e}")
            overall_rc = 1
            continue

        # Ensure output directories
        for sub in ["edit-trails", "edit-outputs", "verify-predictions", "logs"]:
            ensure_dir(pkm_output_dir(pkm_name) / "file-update" / sub)

        # --- Ingest ---
        if not args.skip_ingest:
            rc = run_ingest(pkm_name, pkm_cfg)
            if rc != 0:
                print(f"[{pkm_name}] ingest failed — skipping remaining stages")
                overall_rc = 1
                continue

        # --- Edit (Agent A) ---
        if not args.skip_edit:
            rc = run_edit(pkm_name, pkm_cfg, args.timeout)
            if rc != 0:
                overall_rc = 1

        # --- Verify (Agent B) ---
        if not args.skip_verify:
            rc = run_verify(pkm_name, pkm_cfg, args.timeout)
            if rc != 0:
                overall_rc = 1

        # --- Eval ---
        if not args.skip_eval:
            rc = run_eval(pkm_name, pkm_cfg, args.timeout)
            if rc != 0:
                overall_rc = 1

    print(f"\n{'='*60}")
    print(f"File-update benchmark complete (exit {overall_rc})")
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
