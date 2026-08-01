"""Reflect: plan additional recalls and synthesize a grounded answer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

from .config import HindsightOptions
from .protocols import HindsightProviders, MemoryRepository
from .types import RecallResult, ReflectResult
from .utils import cosine


class RecallProvider(Protocol):
    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult: ...


class ReflectEngine:
    def __init__(
        self,
        recall: RecallProvider,
        repository: MemoryRepository,
        providers: HindsightProviders,
        options: HindsightOptions,
    ) -> None:
        self._recall = recall
        self._repository = repository
        self._providers = providers
        self._options = options

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
    ) -> ReflectResult:
        initial = await self._recall.recall(query, mode=mode, top_k=top_k)
        evidence = {item.id: item for item in initial.results}
        try:
            plan = await self._providers.json(
                "Plan evidence retrieval for a complex question. Add subqueries only for missing hops.",
                f"QUESTION: {query}\nINITIAL EVIDENCE:\n"
                + "\n".join(f"[{item.id}] {item.text}" for item in evidence.values())
                + '\nReturn {"subqueries":[]}.',
            )
        except Exception:
            plan = {"subqueries": []}

        tool_trace: list[dict[str, Any]] = [
            {
                "tool": "recall",
                "input": {"query": query, "mode": mode},
                "output_count": len(evidence),
                "iteration": 1,
            }
        ]
        for iteration, subquery in enumerate(
            plan.get("subqueries", [])[: self._options.reflect_subquery_limit], start=2
        ):
            recalled = await self._recall.recall(str(subquery), mode=mode, top_k=top_k)
            evidence.update({item.id: item for item in recalled.results})
            tool_trace.append(
                {
                    "tool": "recall",
                    "input": {"query": str(subquery), "mode": mode},
                    "output_count": len(recalled.results),
                    "iteration": iteration,
                }
            )

        embeddings = await self._providers.embed([query])
        if not embeddings:
            raise ValueError("embedding provider returned no reflection embedding")
        context = await self._repository.reflection_context(query, embeddings[0])
        relevant_models = sorted(
            (
                model
                for model in context.mental_models
                if not model.is_directive and (model.summary or model.description)
            ),
            key=lambda model: cosine(embeddings[0], model.embedding),
            reverse=True,
        )[: self._options.reflect_model_limit]
        directives = [
            model
            for model in context.mental_models
            if model.is_directive
            and (not model.trigger or model.trigger.casefold() in query.casefold())
        ]
        answer = await self._providers.text(
            "Answer strictly from cited memories. Cite memory ids in square brackets, expose contradictions, "
            "and state when evidence is insufficient. Obey supplied directives.",
            self._prompt(query, evidence, relevant_models, directives, context.profile),
        )

        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence.values():
            grouped[item.memory_type].append(item.as_evidence())
        grouped["directives"] = [
            {
                "id": model.id,
                "name": model.name,
                "content": model.summary or model.description,
            }
            for model in directives
        ]
        grouped["mental_models"] = [
            {
                "id": model.id,
                "name": model.name,
                "content": model.summary or model.description,
                "source_memory_ids": list(model.source_memory_ids),
            }
            for model in relevant_models
        ]
        return ReflectResult(text=answer, based_on=dict(grouped), tool_trace=tool_trace)

    @staticmethod
    def _prompt(query, evidence, models, directives, profile) -> str:
        return (
            f"QUESTION: {query}\nMEMORY PROFILE: background={profile.background}; "
            f"skepticism={profile.skepticism}; literalism={profile.literalism}; "
            f"empathy={profile.empathy}\nDIRECTIVES:\n"
            + "\n".join(model.summary or model.description for model in directives)
            + "\nMENTAL MODELS:\n"
            + "\n".join(
                f"[{model.id}] {model.name}: {model.summary or model.description}"
                for model in models
            )
            + "\nEVIDENCE:\n"
            + "\n".join(
                f"[{item.id}] {item.text} (source: {item.title})"
                for item in evidence.values()
            )
        )
