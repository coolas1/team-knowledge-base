"""Run the backend with any compatible Python 3.12 interpreter.

This also loads dependencies from the repository-local virtual environment. It
is useful on Windows hosts where application-control policy blocks the Python
runtime referenced by ``.venv\\Scripts\\python.exe``.
"""

from pathlib import Path
import site
import sys


ROOT = Path(__file__).resolve().parents[1]
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8001)
