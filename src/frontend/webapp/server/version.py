"""Runtime version reporting.

The semantic version's single source of truth is the repo-root ``VERSION``
file. The image runs the app from source (``uv sync --no-install-project``),
so the project is never installed as a distribution and
``importlib.metadata.version()`` has nothing to read; ``pyproject.toml``'s
``version`` is packaging metadata, bumped alongside ``VERSION`` at release.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root (this file is src/frontend/webapp/server/version.py). The image
# mirrors the layout: /app/VERSION beside /app/src/.
_REPO_ROOT = Path(__file__).resolve().parents[4]

UNKNOWN_VERSION = "unknown"


def load_version(root: Path = _REPO_ROOT) -> str:
    """Read the semantic version from ``<root>/VERSION``.

    Absent or unreadable file degrades to ``"unknown"`` so a broken packaging
    step never prevents the app from starting.
    """
    try:
        text = (root / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN_VERSION
    return text.strip() or UNKNOWN_VERSION


def load_commit() -> str | None:
    """The source commit the running build was produced from.

    Injected as ``GIT_COMMIT`` at image build time (Containerfile build arg ->
    env var; the pipeline passes the deployed short SHA). Local dev runs have
    none, reported as ``None``.
    """
    return os.getenv("GIT_COMMIT") or None
