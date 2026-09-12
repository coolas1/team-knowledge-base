"""Validate pinned upstream bytes at build time, without downloading code."""

import hashlib
import json
from pathlib import Path


def verify(root: Path | None = None) -> None:
    root = root or Path(__file__).parent / "vendor" / "codex_ppt"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Invalid upstream manifest path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Upstream checksum mismatch: {name}")


if __name__ == "__main__":
    verify()
