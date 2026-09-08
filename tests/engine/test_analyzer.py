from src.engine.components.analyzer import (
    Analyzer, AnalysisResult, ChunkAnalysisResult, Entity, FileRelation, Relation,
)
import pytest


def test_parse_response_extracts_entities_relations_file_relations():
    raw = """```json
{"overview": "doc summary",
 "entities": [{"name": "Acme", "type": "Company", "description": "d"}],
 "relations": [{"from_name": "Acme", "to_name": "B", "type": "OWNS", "description": "x"}],
 "file_relations": [{"related_doc_title": "other.md", "type": "REFERENCES", "reason": "r"}]}
```"""
    result = Analyzer._parse_response(raw)
    assert isinstance(result, AnalysisResult)
    assert result.overview == "doc summary"
    assert len(result.entities) == 1
    assert isinstance(result.entities[0], Entity)
    assert len(result.relations) == 1
    assert isinstance(result.relations[0], Relation)
    assert len(result.file_relations) == 1
    assert isinstance(result.file_relations[0], FileRelation)


def test_parse_response_bad_json_returns_placeholder():
    result = Analyzer._parse_response("not json at all")
    assert result.overview.startswith("[LLM 返回解析失败]")


def test_parse_chunk_response():
    raw = '{"entities": [{"name": "E", "type": "T"}], "relations": []}'
    ca = Analyzer._parse_chunk_response(raw, chunk_index=3)
    assert isinstance(ca, ChunkAnalysisResult)
    assert ca.chunk_index == 3
    assert len(ca.entities) == 1


def test_parse_chunk_response_bad_json_empty():
    ca = Analyzer._parse_chunk_response("xxx", chunk_index=0)
    assert ca.entities == []
    assert ca.relations == []


async def test_analyze_disabled_llm_returns_placeholder(tmp_path, monkeypatch):
    from config.settings import settings
    from src.engine.components import analyzer as analyzer_module

    monkeypatch.setattr(settings.llm, "base_url", "")

    class _NoNetwork:
        def __init__(self, *a, **kw):
            raise AssertionError("network call attempted while LLM disabled")

    monkeypatch.setattr(analyzer_module.httpx, "AsyncClient", _NoNetwork)

    schema = tmp_path / "entity_schema.yaml"
    schema.write_text("entity_types:\n  core: [Person]\n  open: true\n"
                      "relation_types:\n  core: [WORKS_AT]\n  open: true\n")
    a = Analyzer(schema_path=schema)

    res = await a.analyze_overview("some text", "title")
    assert res.overview.startswith("[待 LLM 生成]")
    assert res.file_relations == []


def test_build_overview_prompt_contains_title():
    p = Analyzer._build_overview_prompt("My Title", "body text here")
    assert "My Title" in p
    assert "body text here" in p


async def test_openai_call_uses_llm_base_url(monkeypatch):
    from config.settings import settings
    from src.engine.components import analyzer as analyzer_module

    requested: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"overview": "ok"}'}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            requested["url"] = url
            requested["json"] = kwargs["json"]
            requested["headers"] = kwargs["headers"]
            return FakeResponse()

    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1/")
    monkeypatch.setattr(settings.llm, "model", "remote-model")
    monkeypatch.setattr(settings.llm, "api_key", "secret")
    monkeypatch.setattr(
        analyzer_module.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    result = await Analyzer()._call_openai_compatible("test prompt")

    assert result == '{"overview": "ok"}'
    assert requested["url"] == "https://llm.example/v1/chat/completions"
    assert requested["json"]["model"] == "remote-model"
    assert requested["headers"]["Authorization"] == "Bearer secret"


async def test_enabled_llm_without_model_raises(monkeypatch):
    from config.settings import settings
    from src.engine.components import analyzer as analyzer_module

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            return FakeResponse()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "x"}}]}

    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "")
    monkeypatch.setattr(
        analyzer_module.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    with pytest.raises(ValueError, match="LLM_MODEL"):
        await Analyzer()._call_openai_compatible("test prompt")
