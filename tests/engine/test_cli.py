import json
from pathlib import Path

import pytest

from src.engine import cli as cli_mod
from src.engine.config import EngineConfig
from src.engine.interface import GraphData, GraphNode, IngestSource, RecallRequest, RecallResult
from tests.conftest import FakeKnowledgeBase


def _install_fake(monkeypatch, fake: FakeKnowledgeBase):
    monkeypatch.setattr(
        cli_mod, "build_engine", lambda cfg: fake, raising=True
    )


def test_cli_ingest_prints_doc_ref(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["ingest", "--name", "x.md", "--data", "hello"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["title"] == "x.md"
    assert out["status"] == "indexed"
    assert list(fake.raw.values())[0] == b"hello"


def test_cli_recall_prints_result(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    fake.recall_calls.clear()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["recall", "--query", "find acme", "--top-k", "5"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"chunks": [], "related_entities": [], "related_docs": []}
    assert fake.recall_calls == ["find acme"]


def test_cli_graph_full(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    fake.graph = GraphData(nodes=[GraphNode(name="n", type="T")])
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["graph"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["nodes"][0]["name"] == "n"


def test_cli_list(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["list"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["total"] == 0


def test_cli_remove(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["remove", "--doc-id", "abc"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == '{"removed": "abc"}'
