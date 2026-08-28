"""版本链（纵向迭代管理）单元测试。

覆盖：analyzer 变更解析、Neo4j 版本投影 Cypher、pipeline 版本入库
编排、MCP 工具与 backend 协议面。全链路行为由 integration 标记的
测试在真实服务上验证。
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.engine.components.analyzer import Analyzer, ChangeAnalysisResult
from src.engine.components.store.models import DocumentChange
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.graphrag.pipeline import Pipeline, VersionParent

# ── analyzer: 版本 diff 解析 ─────────────────────────────────────


def test_parse_changes_response_plain_json():
    raw = json.dumps(
        {
            "summary": "更新了交付范围",
            "changes": [
                {"name": "新增无人机配送", "description": "增加无人机章节", "status": "added"},
                {"name": "移除人工配送", "description": "删除旧章节", "status": "removed"},
            ],
        },
        ensure_ascii=False,
    )
    result = Analyzer._parse_changes_response(raw)

    assert isinstance(result, ChangeAnalysisResult)
    assert result.summary == "更新了交付范围"
    assert len(result.changes) == 2
    assert result.changes[0]["status"] == "added"
    assert result.changes[1]["status"] == "removed"


def test_parse_changes_response_tolerates_fenced_json():
    raw = (
        "以下是版本变更：\n```json\n"
        '{"summary": "s", "changes": [{"name": "a", "description": "d", "status": "added"}]}'
        "\n```"
    )
    result = Analyzer._parse_changes_response(raw)

    assert result.summary == "s"
    assert result.changes == [{"name": "a", "description": "d", "status": "added"}]


def test_parse_changes_response_normalizes_unknown_status():
    raw = '{"summary": "s", "changes": [{"name": "a", "description": "d", "status": "unknown"}]}'
    result = Analyzer._parse_changes_response(raw)

    assert result.changes[0]["status"] == "modified"


def test_parse_changes_response_bad_json_returns_placeholder():
    result = Analyzer._parse_changes_response("not json")

    assert result.summary.startswith("[LLM 返回解析失败]")
    assert result.changes == []


def test_parse_changes_response_skips_non_dict_entries():
    raw = '{"summary": "s", "changes": ["bad", {"name": "a", "description": "d", "status": "added"}]}'
    result = Analyzer._parse_changes_response(raw)

    assert len(result.changes) == 1


def test_build_changes_prompt_contains_both_versions():
    prompt = Analyzer._build_changes_prompt("旧内容", "新内容", "规格书")
    assert "旧内容" in prompt
    assert "新内容" in prompt
    assert "规格书" in prompt
    assert "status" in prompt


@pytest.mark.asyncio
async def test_analyze_changes_with_todo_provider_returns_placeholder(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "llm_provider", "todo")
    result = await Analyzer().analyze_changes("old", "new", "t")
    assert result.summary.startswith("[待 LLM 生成]")


# ── Neo4j: 版本链投影 ────────────────────────────────────────────


class Result:
    async def data(self):
        return []


class Session:
    def __init__(self):
        self.queries: list[str] = []
        self.parameters: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def run(self, query, **parameters):
        self.queries.append(query)
        self.parameters.append(parameters)
        return Result()


class Driver:
    def __init__(self):
        self.value = Session()

    def session(self):
        return self.value


def _client() -> tuple[Neo4jClient, Session]:
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = Driver()
    return client, client._driver.value


async def test_upsert_document_node_includes_version_properties():
    client, session = _client()

    await client.upsert_document_node(
        doc_id="d1", title="t", file_type="markdown",
        overview="o", version_number=3, is_current=False,
    )

    query = session.queries[0]
    assert "d.version_number = $version_number" in query
    assert "d.is_current = $is_current" in query
    assert session.parameters[0]["version_number"] == 3
    assert session.parameters[0]["is_current"] is False


async def test_link_next_version_merges_directed_edge():
    client, session = _client()

    await client.link_next_version("prev", "next")

    query = session.queries[0]
    assert "MERGE (prev)-[r:NEXT_VERSION]->(next)" in query
    assert session.parameters[0] == {"from_doc_id": "prev", "to_doc_id": "next"}


async def test_upsert_changes_writes_change_node():
    client, session = _client()
    changes = [{"name": "a", "description": "d", "status": "added"}]

    await client.upsert_changes("d1", 1, 2, "摘要", changes)

    query = session.queries[0]
    assert "MERGE (c:Change {doc_id: $doc_id})" in query
    assert "MERGE (c)-[:CHANGE_OF]->(d)" in query
    assert json.loads(session.parameters[0]["changes"]) == changes
    assert session.parameters[0]["from_version"] == 1
    assert session.parameters[0]["to_version"] == 2


async def test_delete_document_graph_cleans_change_nodes():
    client, session = _client()

    await client.delete_document_graph("d1")

    assert any(
        "MATCH (c:Change {doc_id: $doc_id})" in q for q in session.queries
    )


# ── pipeline: 版本链入库编排 ─────────────────────────────────────


class FakeSession:
    """SQLAlchemy AsyncSession 替身：记录写入的对象。"""

    def __init__(self):
        self.added: list = []
        self.executed: list = []

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt):
        self.executed.append(stmt)

    async def commit(self):
        return None


class RecordingNeo4j:
    def __init__(self):
        self.links: list[tuple[str, str]] = []
        self.changes: list[dict] = []

    async def link_next_version(self, from_doc_id, to_doc_id):
        self.links.append((from_doc_id, to_doc_id))

    async def upsert_changes(self, **kwargs):
        self.changes.append(kwargs)


class RecordingAnalyzer:
    def __init__(self, result: ChangeAnalysisResult):
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    async def analyze_changes(self, old_text, new_text, title):
        self.calls.append((old_text, new_text, title))
        return self.result


@pytest.mark.asyncio
async def test_process_version_change_persists_diff_and_projects_graph():
    analysis = ChangeAnalysisResult(
        summary="v2 修改了交付范围",
        changes=[{"name": "x", "description": "y", "status": "modified"}],
    )
    analyzer = RecordingAnalyzer(analysis)
    neo4j = RecordingNeo4j()
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._analyzer = analyzer
    pipeline._neo4j = neo4j

    doc_id = uuid4()
    session = FakeSession()
    parent = VersionParent(
        doc_id="prev-doc", raw_text="旧文本",
        from_version=1, to_version=2,
    )

    await pipeline._process_version_change(doc_id, "标题", "新文本", parent, session)

    # LLM diff 收到旧/新文本
    assert analyzer.calls == [("旧文本", "新文本", "标题")]
    # document_changes 行已写入
    added = [a for a in session.added if isinstance(a, DocumentChange)]
    assert len(added) == 1
    assert added[0].doc_id == doc_id
    assert added[0].from_version == 1
    assert added[0].to_version == 2
    assert added[0].summary == "v2 修改了交付范围"
    assert added[0].changes == analysis.changes
    # Neo4j 投影
    assert neo4j.links == [("prev-doc", str(doc_id))]
    assert neo4j.changes[0]["summary"] == "v2 修改了交付范围"


@pytest.mark.asyncio
async def test_process_version_change_failure_does_not_raise():
    class FailingAnalyzer:
        async def analyze_changes(self, *args, **kwargs):
            raise RuntimeError("LLM down")

    pipeline = Pipeline.__new__(Pipeline)
    pipeline._analyzer = FailingAnalyzer()
    pipeline._neo4j = RecordingNeo4j()

    # 版本元数据失败只记日志，不抛出（不影响文档 indexed 状态）
    await pipeline._process_version_change(
        uuid4(), "t", "new", VersionParent("p", "old", 1, 2), FakeSession()
    )


def test_version_parent_dataclass_fields():
    parent = VersionParent(doc_id="d", raw_text="r", from_version=1, to_version=2)
    assert (parent.doc_id, parent.raw_text, parent.from_version, parent.to_version) == (
        "d", "r", 1, 2,
    )


# ── backend / MCP 协议面 ─────────────────────────────────────────


def test_backend_implements_version_methods():
    from src.engine.graphrag.backend import GraphRAGBackend

    for name in ["list_versions", "diff_versions"]:
        assert hasattr(GraphRAGBackend, name), f"missing {name}"


@pytest.mark.asyncio
async def test_mcp_version_tools_delegate_to_kb():
    from tests.conftest import FakeKnowledgeBase

    from src.engine import mcp as mcp_mod

    kb = FakeKnowledgeBase()
    mcp_mod.set_kb(kb)
    try:
        # FakeKnowledgeBase 未实现版本方法时按协议补齐
        async def list_versions(doc_id):
            return [{"id": doc_id, "version_number": 1}]

        async def diff_versions(doc_id, from_version, to_version):
            return {"doc_id": doc_id, "changes": []}

        kb.list_versions = list_versions  # type: ignore[method-assign]
        kb.diff_versions = diff_versions  # type: ignore[method-assign]

        res = await mcp_mod.tkb_list_versions("abc")
        assert res == {"doc_id": "abc", "versions": [{"id": "abc", "version_number": 1}]}

        res = await mcp_mod.tkb_diff_versions("abc", 1, 2)
        assert res == {"doc_id": "abc", "changes": []}
    finally:
        mcp_mod._kb = None


@pytest.mark.asyncio
async def test_mcp_version_tools_report_missing_doc():
    from tests.conftest import FakeKnowledgeBase

    from src.engine import mcp as mcp_mod

    kb = FakeKnowledgeBase()

    async def list_versions(doc_id):
        raise ValueError(f"文档不存在: {doc_id}")

    kb.list_versions = list_versions  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.tkb_list_versions("missing")
        assert "error" in res
    finally:
        mcp_mod._kb = None
