from src.engine.components.analyzer import (
    Analyzer, AnalysisResult, ChunkAnalysisResult, Entity, FileRelation, Relation,
)


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


def test_analyzer_with_todo_provider_returns_placeholder(tmp_path, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "llm_provider", "todo")
    schema = tmp_path / "entity_schema.yaml"
    schema.write_text("entity_types:\n  core: [Person]\n  open: true\n"
                      "relation_types:\n  core: [WORKS_AT]\n  open: true\n")
    a = Analyzer(schema_path=schema)

    async def go():
        return await a.analyze_overview("some text", "title")

    import asyncio
    res = asyncio.run(go())
    assert res.overview.startswith("[待 LLM 生成]")
    assert res.file_relations == []


def test_build_overview_prompt_contains_title():
    p = Analyzer._build_overview_prompt("My Title", "body text here")
    assert "My Title" in p
    assert "body text here" in p


async def test_ollama_call_uses_native_ollama_base_url(monkeypatch):
    from config.settings import settings
    from src.engine.components import analyzer as analyzer_module

    requested: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"overview": "ok"}'}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            requested["url"] = url
            requested["json"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama:11434/")
    monkeypatch.setattr(settings, "llm_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen3:14b")
    monkeypatch.setattr(
        analyzer_module.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    result = await Analyzer()._call_ollama("test prompt")

    assert result == '{"overview": "ok"}'
    assert requested["url"] == "http://ollama:11434/api/generate"
    assert requested["json"]["model"] == "qwen3:14b"
