from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod
from src.frontend.webapp.server import deps
from src.frontend.webapp.server.version import load_commit, load_version


def test_load_version_reads_the_version_file(tmp_path: Path):
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert load_version(tmp_path) == "1.2.3"


def test_load_version_defaults_to_unknown_when_file_absent(tmp_path: Path):
    assert load_version(tmp_path) == "unknown"


def test_load_version_defaults_to_unknown_when_blank(tmp_path: Path):
    (tmp_path / "VERSION").write_text("  \n", encoding="utf-8")
    assert load_version(tmp_path) == "unknown"


def test_load_commit_reads_git_commit_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    assert load_commit() == "abc1234"


def test_load_commit_is_none_when_unset_or_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert load_commit() is None
    monkeypatch.setenv("GIT_COMMIT", "")
    assert load_commit() is None


@pytest.fixture
def client(monkeypatch):
    # Bypass real engine build: no-op lifespan (version needs no engine).
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    with TestClient(app_mod.app) as c:
        yield c


def test_app_reports_the_version_file_contents(client):
    # The FastAPI app version comes from VERSION, not a hardcoded literal.
    assert app_mod.app.version == "0.2.0"


def test_version_endpoint_reports_version_and_commit(client, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    res = client.get("/version")
    assert res.status_code == 200
    assert res.json() == {"version": "0.2.0", "commit": "abc1234"}


def test_version_endpoint_reports_null_commit_when_unset(client, monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    res = client.get("/version")
    assert res.status_code == 200
    assert res.json() == {"version": "0.2.0", "commit": None}
