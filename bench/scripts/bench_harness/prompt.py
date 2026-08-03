"""Build agent prompts from runbooks + input files + PKM template vars."""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    """Read a text file, stripping trailing whitespace."""
    with open(path) as fh:
        return fh.read().strip()


def _resolve_template(text: str, pkm_name: str) -> str:
    """Replace template variables in runbook text.

    ``<knowledge-base>``      → pkm_name  (e.g. ``gbrain``)
    ``<knowledge-base>-files`` → ``{pkm_name}-files``
    """
    return text.replace("<knowledge-base>-files", f"{pkm_name}-files").replace(
        "<knowledge-base>", pkm_name
    )


def build_prompt(
    runbook_path: str | Path,
    input_path: str | Path,
    output_path: str | Path,
    pkm_name: str,
    *,
    extra_input_paths: list[str | Path] | None = None,
) -> str:
    """Build a complete agent prompt from a runbook + input file(s).

    Parameters
    ----------
    runbook_path:
        Path to the runbook markdown (PREDICT_INSTRUCTIONS.md, EDIT_INSTRUCTIONS.md,
        VERIFY_INSTRUCTIONS.md, or EVAL_INSTRUCTIONS.md).
    input_path:
        Path to the main input file (a question, a prompt, etc.).
    output_path:
        Where the agent should write its output. Appended as a final instruction.
    pkm_name:
        PKM name used to replace ``<knowledge-base>`` template variables.
    extra_input_paths:
        Additional files to append after the main input (e.g. verify-q/*.md).

    Returns
    -------
    str
        The complete prompt, ready to pass to an entrypoint.
    """
    runbook_text = _read(Path(runbook_path))
    runbook_text = _resolve_template(runbook_text, pkm_name)

    input_text = _read(Path(input_path))

    parts = [runbook_text, "", "---", "", input_text]

    if extra_input_paths:
        for ep in extra_input_paths:
            parts.append("")
            parts.append(_read(Path(ep)))

    parts.extend([
        "",
        "---",
        "",
        f"**Output instruction:** Write your final result to `{output_path}`.",
        "Create any parent directories if they don't exist. Exit when done.",
    ])

    return "\n".join(parts)
