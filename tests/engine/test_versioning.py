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
from src.engine.graphrag._version_match import (
    SIMILARITY_THRESHOLD,
    combined_similarity,
    content_similarity,
    find_version_candidate,
    _strip_version_markers,
)

# ── analyzer: 版本 diff 解析 ─────────────────────────────────────


def test_parse_changes_response_plain_json():
    raw = json.dumps(
        {
            "summary": "更新了交付范围",
            "changes": [
                {
                    "name": "新增无人机配送",
                    "description": "增加无人机章节",
                    "status": "added",
                },
                {
                    "name": "移除人工配送",
                    "description": "删除旧章节",
                    "status": "removed",
                },
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
    from src.engine.components import analyzer as analyzer_module

    monkeypatch.setattr(analyzer_module.settings.llm, "base_url", "")
    monkeypatch.setattr(analyzer_module.settings.llm, "model", "")
    result = await Analyzer().analyze_changes("old", "new", "t")
    assert result.summary.startswith("[待 LLM 生成]")


# ── Neo4j: 版本链投影 ────────────────────────────────────────────


class Result:
    async def data(self):
        return []

    async def single(self):
        return None


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
        doc_id="d1",
        title="t",
        file_type="markdown",
        overview="o",
        version_number=3,
        is_current=False,
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

    assert any("MATCH (c:Change {doc_id: $doc_id})" in q for q in session.queries)


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
        doc_id="prev-doc",
        raw_text="旧文本",
        from_version=1,
        to_version=2,
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
        "d",
        "r",
        1,
        2,
    )


# ── backend / MCP 协议面 ─────────────────────────────────────────


def test_backend_implements_version_methods():
    from src.engine.graphrag.backend import GraphRAGBackend

    for name in ["list_versions", "diff_versions"]:
        assert hasattr(GraphRAGBackend, name), f"missing {name}"


@pytest.mark.asyncio
async def test_mcp_version_tools_delegate_to_kb():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

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
        assert res == {
            "doc_id": "abc",
            "versions": [{"id": "abc", "version_number": 1}],
        }

        res = await mcp_mod.tkb_diff_versions("abc", 1, 2)
        assert res == {"doc_id": "abc", "changes": []}
    finally:
        mcp_mod._kb = None


@pytest.mark.asyncio
async def test_mcp_version_tools_report_missing_doc():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

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


# ── 版本化编辑（edit_document_content）────────────────────────────


@pytest.mark.asyncio
async def test_mcp_edit_document_content_reports_versioned_result():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod
    from src.engine.interface import DocumentRef

    kb = FakeKnowledgeBase()

    async def edit_content(doc_id, new_text):
        return DocumentRef(
            id="new-1",
            title="t",
            file_type="markdown",
            status="indexed",
            version_number=2,
            is_current=True,
        )

    kb.edit_content = edit_content  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.edit_document_content("old-1", "new content")
        assert res["id"] == "new-1"
        assert res["version_number"] == 2
        assert res["is_current"] is True
    finally:
        mcp_mod._kb = None


@pytest.mark.asyncio
async def test_mcp_edit_document_content_missing_doc_returns_error():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

    kb = FakeKnowledgeBase()

    async def edit_content(doc_id, new_text):
        raise ValueError(f"文档不存在: {doc_id}")

    kb.edit_content = edit_content  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.edit_document_content("missing", "x")
        assert "error" in res
    finally:
        mcp_mod._kb = None


def test_reindex_document_accepts_previous_version():
    import inspect

    sig = inspect.signature(Pipeline.reindex_document)
    assert "previous_version" in sig.parameters


def test_backend_implements_edit_document():
    from src.engine.graphrag.backend import GraphRAGBackend

    assert hasattr(GraphRAGBackend, "edit_document")


# ── P2: 编辑提议（propose-edit）──────────────────────────────────


def test_parse_edit_proposal_response_plain():
    from src.engine.components.analyzer import EditProposalResult

    raw = json.dumps(
        {"new_text": "# 改后全文", "notes": ["改了标题", "无冲突"]},
        ensure_ascii=False,
    )
    result = Analyzer._parse_edit_proposal_response(raw)

    assert isinstance(result, EditProposalResult)
    assert result.proposed_text == "# 改后全文"
    assert result.notes == ["改了标题", "无冲突"]


def test_parse_edit_proposal_normalizes_string_notes():
    raw = '{"new_text": "t", "notes": "single note"}'
    result = Analyzer._parse_edit_proposal_response(raw)

    assert result.notes == ["single note"]


def test_parse_edit_proposal_bad_json_returns_placeholder():
    result = Analyzer._parse_edit_proposal_response("not json")

    assert result.proposed_text == ""
    assert result.notes[0].startswith("[LLM 返回解析失败]")


def test_build_edit_proposal_prompt_contains_request_and_doc():
    prompt = Analyzer._build_edit_proposal_prompt(
        "文档内容", "把价格改为10元", "价目表"
    )
    assert "文档内容" in prompt
    assert "把价格改为10元" in prompt
    assert "价目表" in prompt


@pytest.mark.asyncio
async def test_propose_edit_with_todo_provider_returns_original(monkeypatch):
    from src.engine.components import analyzer as analyzer_module

    monkeypatch.setattr(analyzer_module.settings.llm, "base_url", "")
    monkeypatch.setattr(analyzer_module.settings.llm, "model", "")
    result = await Analyzer().propose_edit("原文", "改一下", "t")
    assert result.proposed_text == "原文"


# ── P2: 跨文档一致性检查 / 图谱存活过滤 ──────────────────────────


async def test_find_related_docs_via_entities_filters_current():
    client, session = _client()

    await client.find_related_docs_via_entities("d1", limit=5)

    query = session.queries[0]
    assert "coalesce(d.is_current, true) <> false" in query
    assert "shared_entities" in query
    assert session.parameters[0] == {"doc_id": "d1", "limit": 5}


async def test_get_full_graph_filters_stale_entities():
    client, session = _client()

    await client.get_full_graph()

    node_query = session.queries[0]
    assert "coalesce(ld.is_current, true) <> false" in node_query
    link_query = session.queries[1]
    # 两端实体 + 关系边都要求存活来源（lda/ldb/ldr 三个存在性子查询）
    assert link_query.count("is_current, true) <> false") == 3


async def test_query_neighbors_keeps_entities_without_sources():
    client, session = _client()

    await client.query_neighbors("e1", hops=2)

    query = session.queries[0]
    assert "neighbor.sources IS NULL" in query  # Hindsight 实体无 sources，保留


async def test_get_entity_details_filters_stale_graphrag_entities():
    client, session = _client()

    await client.get_entity_details("Shared")

    query = session.queries[0]
    # 无 sources 的实体（Hindsight）保留；有 sources 的按存活过滤
    assert "n.sources IS NULL OR EXISTS" in query


def test_backend_implements_propose_edit():
    from src.engine.graphrag.backend import GraphRAGBackend

    assert hasattr(GraphRAGBackend, "propose_edit")


@pytest.mark.asyncio
async def test_mcp_propose_edit_delegates_to_kb():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

    kb = FakeKnowledgeBase()
    seen: list[tuple[str, str]] = []

    async def propose_edit(doc_id, edit_request):
        seen.append((doc_id, edit_request))
        return {"doc_id": doc_id, "proposed_text": "new", "notes": []}

    kb.propose_edit = propose_edit  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.tkb_propose_edit("d1", "把A改成B")
        assert res["proposed_text"] == "new"
        assert seen == [("d1", "把A改成B")]
    finally:
        mcp_mod._kb = None


@pytest.mark.asyncio
async def test_mcp_propose_edit_missing_doc_returns_error():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

    kb = FakeKnowledgeBase()

    async def propose_edit(doc_id, edit_request):
        raise ValueError(f"文档不存在: {doc_id}")

    kb.propose_edit = propose_edit  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.tkb_propose_edit("missing", "x")
        assert "error" in res
    finally:
        mcp_mod._kb = None


# ── 改名识别（rename detection）──────────────────────────────────


def test_strip_version_markers_removes_common_suffixes():
    assert _strip_version_markers("报告_v2.md") == _strip_version_markers(
        "报告_final.md"
    )
    assert _strip_version_markers("规范v1.md") != _strip_version_markers("手册.md")


def test_content_similarity_identical_text():
    assert content_similarity("同一文档内容", "同一文档内容") == 1.0


def test_content_similarity_disjoint_text():
    assert content_similarity("甲乙丙丁", "子丑寅卯") == 0.0


def test_combined_similarity_detects_revised_renamed_doc():
    # 真实修订：只改数值，保留大部分文本
    base = "# 配送规范\n\n- 基础费5元\n- 时段9-18点\n- 人工配送为主，覆盖A栋B栋C栋"
    revised = "# 配送规范\n\n- 基础费8元\n- 时段9-18点\n- 人工配送为主，覆盖A栋B栋C栋"

    sim = combined_similarity("规范v1.md", base, "规范v2.md", revised)
    assert sim >= SIMILARITY_THRESHOLD, sim


def test_combined_similarity_rejects_different_docs():
    doc_a = "# 联邦学习调研\n\n联邦学习是分布式机器学习范式，数据不出本地"
    doc_b = "# 园区配送服务\n\n配送范围覆盖A栋B栋，收费标准五元起"

    sim = combined_similarity("调研.md", doc_a, "配送.md", doc_b)
    assert sim < SIMILARITY_THRESHOLD, sim


def test_find_version_candidate_exact_content_rename():
    base = "完全相同的内容主体"
    candidate = find_version_candidate("新名字.md", base, [("d1", "旧名字.md", base)])
    assert candidate is not None
    assert candidate.exact_content is True
    assert candidate.similarity == 1.0
    assert candidate.doc_id == "d1"


def test_find_version_candidate_similar_content_flagged():
    # 真实修订形态：多样文本 + 数值修改 + 少量新增
    base = (
        "# 园区配送服务规范\n\n"
        "配送范围覆盖园区A栋B栋C栋。配送时段为工作日上午九点至下午六点。\n"
        "基础配送费五元，加急费三元。人工配送为主要方式。\n"
        "用户可通过小程序下单，支持货到付款与月结两种结算方式。\n"
        "异常件由客服专线统一处理，响应时效为一个工作日。"
    )
    revised = (
        "# 园区配送服务规范\n\n"
        "配送范围覆盖园区A栋B栋C栋。配送时段为工作日上午九点至下午六点。\n"
        "基础配送费八元，加急费五元。人工配送为主要方式。\n"
        "用户可通过小程序下单，支持货到付款与月结两种结算方式。\n"
        "异常件由客服专线统一处理，响应时效为一个工作日。"
    )

    candidate = find_version_candidate(
        "规范_新版.md", revised, [("d1", "规范.md", base)]
    )
    assert candidate is not None
    assert candidate.exact_content is False
    assert candidate.similarity >= SIMILARITY_THRESHOLD


def test_find_version_candidate_no_match_returns_none():
    assert (
        find_version_candidate(
            "a.md", "内容甲", [("d1", "b.md", "完全不同的内容乙丙丁")]
        )
        is None
    )


def test_find_version_candidate_picks_highest_similarity():
    # 近邻：小修订的真实文本；远邻：无关文档
    base = (
        "# 财务报销制度\n\n报销单需附发票原件，邮寄至财务部统一处理。"
        "差旅住宿标准为每晚三百元，超出部分自理。"
    )
    near = (
        "# 财务报销制度\n\n报销单需附发票原件，邮寄至财务部统一处理。"
        "差旅住宿标准为每晚四百元，超出部分自理。"
    )
    far = "# 前端开发规范\n\n组件命名使用PascalCase，状态用hooks管理。"

    candidate = find_version_candidate(
        "报销制度.md", near, [("d1", "开发.md", far), ("d2", "报销.md", base)]
    )
    assert candidate is not None
    assert candidate.doc_id == "d2"


# ── 改名确认挂链（confirm_version_match）────────────────────────


@pytest.mark.asyncio
async def test_mcp_confirm_version_match_delegates_to_kb():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

    kb = FakeKnowledgeBase()
    seen: list[tuple[str, str]] = []

    async def confirm(doc_id, parent_doc_id):
        seen.append((doc_id, parent_doc_id))
        return {"doc_id": doc_id, "already_linked": False, "version_number": 2}

    kb.confirm_version_match = confirm  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.tkb_confirm_version_match("new", "parent")
        assert res["version_number"] == 2
        assert seen == [("new", "parent")]
    finally:
        mcp_mod._kb = None


@pytest.mark.asyncio
async def test_mcp_confirm_version_match_error_passthrough():
    from tests.conftest import FakeKnowledgeBase

    from src.agent.tkb.mcp import server as mcp_mod

    kb = FakeKnowledgeBase()

    async def confirm(doc_id, parent_doc_id):
        raise ValueError(f"文档不存在: {doc_id}")

    kb.confirm_version_match = confirm  # type: ignore[method-assign]
    mcp_mod.set_kb(kb)
    try:
        res = await mcp_mod.tkb_confirm_version_match("missing", "p")
        assert "error" in res
    finally:
        mcp_mod._kb = None


def test_backend_implements_confirm_version_match():
    from src.engine.graphrag.backend import GraphRAGBackend

    assert hasattr(GraphRAGBackend, "confirm_version_match")


def test_pipeline_record_version_change_is_public():
    assert hasattr(Pipeline, "record_version_change")


# ── 删除并发修复：批量清理 + 瞬态重试 + 孤儿兜底 ────────────────


class BatchRecordingSession(Session):
    """记录 UNWIND 批量写参数的假会话。"""


class RecordsResult(Result):
    """返回一条实体记录，触发 UNWIND 批量写路径。"""

    async def data(self):
        return [{"name": "E1", "sources": json.dumps([{"doc_id": "d1"}])}]


class RecordsSession(Session):
    async def run(self, query, **parameters):
        self.queries.append(query)
        self.parameters.append(parameters)
        return RecordsResult()


async def test_delete_document_graph_batches_source_updates():
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = Driver()
    client._driver.value = RecordsSession()
    session = client._driver.value

    await client.delete_document_graph("d1")

    queries = session.queries
    assert any("ORDER BY name" in q for q in queries), (
        "读取应按 name 排序（确定加锁顺序）"
    )
    assert any("UNWIND $updates" in q for q in queries), (
        "sources 更新应合并为单条批量写"
    )
    # 批量写参数包含移除了 doc_id 的 sources
    unwind_params = next(p for p in session.parameters if "updates" in p)
    assert unwind_params["updates"] == [{"name": "E1", "sources": "[]"}]


async def test_delete_document_graph_retries_transient_errors(monkeypatch):
    from src.engine.components.store import neo4j as neo4j_mod

    attempts = {"count": 0}

    async def flaky_delete(doc_id):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise neo4j_mod.Neo4jError("deadlock detected: fake")

    class FakeDriverObj:
        value = Session()

        def session(self):
            return self.value

    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = FakeDriverObj()
    client._delete_document_graph_once = flaky_delete  # type: ignore[method-assign]

    # 跳过指数退避的真实等待，加速测试
    async def fast_sleep(seconds):
        return None

    monkeypatch.setattr(neo4j_mod.asyncio, "sleep", fast_sleep)

    await client.delete_document_graph("d1")

    assert attempts["count"] == 3, "瞬态错误应重试至成功"


async def test_remove_cleans_graph_even_when_postgres_row_missing():
    """孤儿兜底：Postgres 行不存在时也要执行图谱清理。"""
    from src.engine.graphrag.backend import GraphRAGBackend

    cleaned: list[str] = []

    class FakeNeo4j:
        async def delete_document_graph(self, doc_id):
            cleaned.append(doc_id)

    class FakePipeline:
        async def before_remove(self, document_id):
            return None

    class MissingDocSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, uid):
            return None

    import src.engine.graphrag.backend as backend_mod

    original_factory = backend_mod.async_session_factory
    backend_mod.async_session_factory = MissingDocSession  # type: ignore[assignment]
    try:
        backend = GraphRAGBackend(FakeNeo4j(), FakePipeline())
        await backend.remove("00000000-0000-0000-0000-000000000001")
    finally:
        backend_mod.async_session_factory = original_factory  # type: ignore[assignment]

    assert cleaned == ["00000000-0000-0000-0000-000000000001"]
