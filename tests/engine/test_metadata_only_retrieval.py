from types import SimpleNamespace
import json

import pytest

from src.agent.interface import SkillContext
from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.file_chunk_recall import reliable_passage
from src.engine.hindsight_components.reflect import ReflectEngine
from src.engine.hindsight_components.recall import RecallEngine
from src.engine.hindsight_components.types import (
    RecallCandidate,
    RecallResult,
)
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource


def _metadata_candidate() -> RecallCandidate:
    return RecallCandidate(
        id="parent:doc-1",
        document_id="doc-1",
        title="自动驾驶综述.pdf",
        text="",
        source_text="",
        chunk_index=-1,
        source_type="upload",
        context="Document metadata matched; no passage is available",
        metadata={
            "metadata_only": True,
            "passage_confidence": "unavailable",
        },
        semantic_score=0.9,
    )


def test_passage_reliability_threshold_is_inclusive():
    assert reliable_passage(0.35, 0.35) is True
    assert reliable_passage(0.349, 0.35) is False


@pytest.mark.asyncio
async def test_reflection_discloses_metadata_only_without_generating_body():
    candidate = _metadata_candidate()

    class Recall:
        async def recall(self, *_args, **_kwargs):
            return RecallResult([candidate], {}, {}, {})

    class Providers:
        async def json(self, *_args, **_kwargs):
            return {"subqueries": []}

        async def embed(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("metadata-only evidence must not reach synthesis")

        async def text(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("metadata-only evidence must not reach synthesis")

    engine = ReflectEngine(
        Recall(), SimpleNamespace(), Providers(), HindsightOptions()
    )
    result = await engine.reflect("自动驾驶用了什么算法？")

    assert "仅文档元数据" in result.text
    assert "未找到可靠正文段落" in result.text
    assert result.based_on["world"][0]["text"] == ""
    assert result.based_on["world"][0]["metadata"]["metadata_only"] is True


@pytest.mark.asyncio
async def test_agent_skill_does_not_ask_llm_to_invent_metadata_only_body():
    from src.agent.tkb.skills.reflective_search.skill import run

    class Query:
        async def query(self, _request):
            return KnowledgeQueryResult(
                strategy_used="recall",
                sources=[
                    KnowledgeSource(
                        memory_id="parent:doc-1",
                        memory_type="world",
                        doc_id="doc-1",
                        title="自动驾驶综述.pdf",
                        chunk_text="",
                        metadata={
                            "metadata_only": True,
                            "passage_confidence": "low",
                        },
                    )
                ],
            )

    class LLM:
        async def complete(self, _prompt):  # pragma: no cover
            raise AssertionError("metadata-only evidence must not reach the LLM")

    result = await run(
        SkillContext(
            kb=SimpleNamespace(),
            query=Query(),
            llm=LLM(),
            params={"query": "自动驾驶用了什么算法？", "needs_answer": True},
        )
    )

    assert "没有可靠正文段落" in result.output["answer"]
    assert result.output["sources"][0]["chunk_text"] == ""
    assert result.output["sources"][0]["metadata"]["metadata_only"] is True


def test_hierarchical_trace_is_bounded_and_contains_no_source_content():
    candidates = []
    for index in range(5):
        item = RecallCandidate(
            id=f"candidate-{index}",
            document_id=f"document-{index}",
            title="SECRET TITLE",
            text="SECRET BODY",
            source_text="SECRET SOURCE",
            chunk_index=index,
            metadata={
                "parent_score": 0.9,
                "passage_score": 0.8,
                "passage_confidence": "reliable",
                "safety_lane": index == 4,
                "lexical_field_scores": {
                    "title": 1.2,
                    "body": 0.4,
                    "SECRET FIELD": 999,
                },
            },
            final_score=0.7,
        )
        candidates.append(item)

    trace = RecallEngine._hierarchical_trace(
        candidates,
        candidates[:2],
        duplicate_collapsed=3,
        max_records=2,
        configured_per_document_cap=3,
    )

    assert trace["parent_candidate_count"] == 5
    assert trace["passage_candidate_count"] == 5
    assert len(trace["parent_candidates"]) == 2
    assert len(trace["passage_candidates"]) == 2
    assert trace["trace_truncated"] is True
    assert trace["safety_lane_used"] is True
    assert trace["safety_lane_count"] == 1
    assert trace["configured_per_document_cap"] == 3
    assert trace["collapsed_count"] == 3
    serialized = json.dumps(trace)
    assert "SECRET" not in serialized
