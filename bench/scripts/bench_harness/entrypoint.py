"""Spawn an agentic CLI entrypoint and collect its output."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import ensure_dir, pkm_output_dir


def invoke(
    pkm_name: str,
    pkm_config: dict[str, Any],
    prompt: str,
    *,
    output_file: str | Path | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 600,
    bench_name: str = "qa",
    log_label: str = "",
) -> subprocess.CompletedProcess:
    """Spawn the PKM's entrypoint CLI with *prompt* and collect stdout.

    Parameters
    ----------
    pkm_name:
        PKM name (e.g. ``"gbrain"``). Used for log/output directory paths.
    pkm_config:
        A single PKM's config block from bench.yaml. Must contain ``entrypoint``,
        ``entrypoint_args``, and optionally ``env`` and ``entrypoint_stdin``.
    prompt:
        The full prompt text to send to the agent.
    output_file:
        If given, write the agent's stdout here (parent dirs created as needed).
    extra_env:
        Additional environment variables merged on top of pkm_config.env.
    timeout:
        Subprocess timeout in seconds (default 10 min).
    bench_name:
        Label for log directories (``"qa"`` or ``"file-update"``).
    log_label:
        Short identifier for the log filename (e.g. ``"01-predict"``).

    Returns
    -------
    subprocess.CompletedProcess
    """
    entrypoint = pkg_config_get(pkm_config, "entrypoint")
    entrypoint_args = pkg_config_get(pkm_config, "entrypoint_args", [])
    use_stdin = pkg_config_get(pkm_config, "entrypoint_stdin", False)
    pkm_env = dict(pkg_config_get(pkm_config, "env", {}))

    # Merge environments: pkm config < extra_env
    merged_env = dict(os.environ)
    merged_env.update(pkm_env)
    if extra_env:
        merged_env.update(extra_env)

    # Set up log directory
    log_dir = ensure_dir(pkm_output_dir(pkm_name) / bench_name / "logs")
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_label = log_label.replace("/", "-").replace(" ", "_") if log_label else "run"
    log_file = log_dir / f"{ts}-{safe_label}.log"

    # Write prompt to a temp file (so we don't blow up the command line)
    prompt_file = log_dir / f"{ts}-{safe_label}-prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Build command
    cmd = [entrypoint] + list(entrypoint_args)

    if not use_stdin:
        # Default: pass prompt via -p flag
        cmd.extend(["-p", prompt])
    # If use_stdin: pass prompt via stdin below

    # Ensure output directory exists
    if output_file:
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

    # Log what we're about to run
    log_lines = [
        f"=== entrypoint invoke ===",
        f"timestamp: {ts}",
        f"entrypoint: {entrypoint}",
        f"args: {entrypoint_args}",
        f"timeout: {timeout}s",
        f"prompt_file: {prompt_file}",
        f"output_file: {output_file}",
        f"env extras: {list(pkm_env.keys())}",
        "",
        f"=== prompt ===",
        prompt,
        "",
        f"=== stdout ===",
    ]
    log_file.write_text("\n".join(log_lines), encoding="utf-8")

    try:
        if use_stdin:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
            )
        else:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
            )
    except subprocess.TimeoutExpired:
        # Synthesize a failed result
        proc = subprocess.CompletedProcess(
            args=cmd, returncode=-1,
            stdout="", stderr=f"timeout after {timeout}s",
        )
    except FileNotFoundError:
        proc = subprocess.CompletedProcess(
            args=cmd, returncode=-2,
            stdout="", stderr=f"entrypoint not found: {entrypoint}",
        )

    # Append stdout + stderr to log
    with open(log_file, "a", encoding="utf-8") as lf:
        lf.write(proc.stdout or "")
        lf.write("\n\n=== stderr ===\n")
        lf.write(proc.stderr or "")
        lf.write(f"\n\n=== exit code: {proc.returncode} ===\n")

    # Write output if requested
    if output_file and proc.stdout:
        output_file.write_text(proc.stdout.strip(), encoding="utf-8")

    return proc


def pkg_config_get(cfg: dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a key from a PKM config block, with a default."""
    return cfg.get(key, default)
