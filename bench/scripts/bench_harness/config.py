"""Load bench.yaml and resolve PKM entries."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Require PyYAML — we use it only here, not in stdlib.
try:
    import yaml
except ImportError:
    sys.exit(
        "PyYAML is required. Install it with: pip install pyyaml\n"
        "Or: uv pip install pyyaml"
    )


def _repo_root() -> Path:
    """Return the git repository root (absolute)."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except Exception:
        # Fallback: walk up from this file until we find .git
        p = Path(__file__).resolve().parent
        while p != p.parent:
            if (p / ".git").exists():
                return p
            p = p.parent
        return Path.cwd()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the bench YAML config and return it as a dict.

    If *path* is None, looks for ``scripts/bench.yaml`` relative to the repo root.
    """
    if path is None:
        path = _repo_root() / "scripts" / "bench.yaml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"bench config not found: {path}")

    with open(path) as fh:
        cfg = yaml.safe_load(fh)

    if cfg is None or "pkms" not in cfg:
        raise ValueError(f"{path}: missing top-level 'pkms' key")

    return cfg


def resolve_pkm(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the config block for a single PKM, or raise KeyError."""
    if cfg is None:
        cfg = load_config()
    pkms = cfg["pkms"]
    if name not in pkms:
        raise KeyError(f"unknown PKM '{name}'. Known: {', '.join(pkms)}")
    return pkms[name]


def list_pkm_names(cfg: dict[str, Any] | None = None) -> list[str]:
    """Return all PKM names defined in the config."""
    if cfg is None:
        cfg = load_config()
    return list(cfg["pkms"].keys())


def pkm_output_dir(pkm_name: str) -> Path:
    """Return ``<pkm_name>-files/`` under the repo root."""
    return _repo_root() / f"{pkm_name}-files"


def ensure_dir(path: Path) -> Path:
    """Create *path* (and parents) if it doesn't exist, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
