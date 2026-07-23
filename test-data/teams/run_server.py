"""Test-only launcher using the trusted Codex Python runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ROOT / ".venv" / "Lib" / "site-packages"

for path in (
    SITE_PACKAGES,
    SITE_PACKAGES / "win32",
    SITE_PACKAGES / "win32" / "lib",
    SITE_PACKAGES / "pythonwin",
    ROOT,
):
    sys.path.insert(0, str(path))

os.add_dll_directory(str(SITE_PACKAGES / "pywin32_system32"))

import uvicorn  # noqa: E402

uvicorn.run("src.main:app", host="127.0.0.1", port=8001)
