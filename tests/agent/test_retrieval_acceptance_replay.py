from __future__ import annotations

import json
from pathlib import Path

from src.agent.tkb.mcp import server as mcp_mod
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource


CASES = json.loads(
    Path("benchmark/eval/retrieval-refine/replay_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def _document(doc_id: str) -> KnowledgeSource:
    return KnowledgeSource(
        memory_id=f"memory-{doc_id}",
        memory_type="chunk",
        doc_id=doc_id,
        title=doc_id.replace("doc-", ""),
        chunk_text=f"grounded evidence for {doc_id}",
        authority="document",
        source_group="document_evidence",
    )


def _conversation(turn_id: str) -> KnowledgeSource:
    return KnowledgeSource(
        memory_id=turn_id,
        memory_type="experience",
        doc_id="",
        title="conversation",
        chunk_text=f"bounded context for {turn_id}",
        authority="conversation",
        source_group="conversation_context",
    )


async def test_replay_cases_cross_mcp_with_separate_evidence_groups():
    by_prompt = {case["prompt"]: case for case in CASES}

    class ReplayQueryService:
        async def query(self, request):
            case = by_prompt[request.query]
            documents = [_document(value) for value in case["document_ids"]]
            conversations = [
                _conversation(value) for value in case["conversation_ids"]
            ]
            return KnowledgeQueryResult(
                strategy_used="recall",
                answer=case["answer"],
                sources=documents,
                route_used=case["route"],
                document_evidence=documents,
                conversation_context=conversations,
                trace={"outcome": "empty" if not documents else "success"},
            )

    mcp_mod.set_query_service(ReplayQueryService())
    try:
        for case in CASES:
            payload = await mcp_mod.query_knowledge(
                case["prompt"], route=case["route"], correlation_id=case["id"]
            )
            assert [item["doc_id"] for item in payload["sources"]] == case[
                "document_ids"
            ]
            assert payload["document_evidence"] == payload["sources"]
            assert [
                item["memory_id"] for item in payload["conversation_context"]
            ] == case["conversation_ids"]
            assert all(
                item["authority"] == "document" for item in payload["sources"]
            )
            assert all(
                item["authority"] == "conversation"
                for item in payload["conversation_context"]
            )
    finally:
        mcp_mod._query_service = None
