"""Retain: turn extracted document text into atomic, linked memories."""

from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from src.engine.components.chunker import Chunk, chunk_text

from .config import HindsightOptions
from .protocols import HindsightProviders, MemoryRepository
from .types import (
    ExtractedFact,
    MemoryDraft,
    MemoryLinkDraft,
    RetainInput,
    RetainPlan,
    RetainResult,
)
from .utils import cosine, normalize_entity, parse_datetime, valid_indexes


class RetainEngine:
    def __init__(
        self,
        repository: MemoryRepository,
        providers: HindsightProviders,
        options: HindsightOptions,
    ) -> None:
        self._repository = repository
        self._providers = providers
        self._options = options

    async def retain(self, retain_input: RetainInput) -> RetainResult:
        chunks = chunk_text(
            retain_input.content,
            chunk_size=self._options.chunk_tokens,
            overlap=self._options.chunk_overlap_tokens,
        )
        if not chunks:
            raise ValueError("cannot retain empty content")

        facts_by_chunk = await self._extract_facts(retain_input, chunks)
        facts = [fact for group in facts_by_chunk for fact in group]
        observations = await self._consolidate(facts)
        plan = await self._build_plan(
            retain_input=retain_input,
            chunks=chunks,
            facts_by_chunk=facts_by_chunk,
            observations=observations,
        )
        await self._repository.replace_document(plan)
        return RetainResult(
            document_id=retain_input.document_id,
            chunks=len(chunks),
            facts=len(facts),
            observations=sum(
                memory.memory_type == "observation" for memory in plan.memories
            ),
            memories=len(plan.memories),
            links=len(plan.links),
        )

    async def _extract_facts(
        self, retain_input: RetainInput, chunks: list[Chunk]
    ) -> list[list[ExtractedFact]]:
        results: list[list[ExtractedFact]] = []
        for chunk in chunks:
            try:
                payload = await self._providers.json(
                    "You extract exhaustive atomic memories. Preserve exact names, numbers, units, dates, "
                    "contradictions and cross-source references. Classify each as world or experience. "
                    "For conversations, preserve speaker attribution and do not turn assistant questions "
                    "or suggestions into user facts.",
                    f"SOURCE TYPE: {retain_input.source_type}\n"
                    f"TITLE: {retain_input.title}\n"
                    f"CONTEXT: {retain_input.context or ''}\n"
                    f"CHUNK INDEX: {chunk.index}\nTEXT:\n{chunk.text}\n\n"
                    'Return {"facts":[{"text":"self-contained fact","type":"world|experience",'
                    '"entities":["canonical names"],"occurred_start":"ISO or null",'
                    '"occurred_end":"ISO or null","where":"place or null",'
                    '"caused_by":[zero-based fact indexes],"confidence":0..1}]}.',
                )
                facts = self._parse_facts(payload)
                results.append(facts or [ExtractedFact(text=chunk.text)])
            except Exception:
                results.append([ExtractedFact(text=chunk.text)])
        return results

    @staticmethod
    def _parse_facts(payload: dict) -> list[ExtractedFact]:
        facts: list[ExtractedFact] = []
        for raw in payload.get("facts", []):
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            fact_type = str(raw.get("type", "world"))
            if fact_type not in {"world", "experience"}:
                fact_type = "world"
            try:
                confidence = float(raw.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            facts.append(
                ExtractedFact(
                    text=text,
                    fact_type=fact_type,
                    entities=[
                        str(item).strip()
                        for item in raw.get("entities", [])
                        if str(item).strip()
                    ],
                    occurred_start=parse_datetime(raw.get("occurred_start")),
                    occurred_end=parse_datetime(raw.get("occurred_end")),
                    location=str(raw["where"]).strip() if raw.get("where") else None,
                    caused_by=[
                        int(item)
                        for item in raw.get("caused_by", [])
                        if str(item).isdigit()
                    ],
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )
        return facts

    async def _consolidate(self, facts: list[ExtractedFact]) -> list[dict]:
        if len(facts) < 2:
            return []
        numbered = "\n".join(
            f"[{index}] {fact.text}" for index, fact in enumerate(facts)
        )
        try:
            payload = await self._providers.json(
                "Consolidate only genuinely repeated or connected facts into objective observations. "
                "Every observation must cite at least two source indexes. Do not invent information.",
                numbered[:30000]
                + '\nReturn {"observations":[{"text":"...","source_indexes":[0,1],'
                '"entities":["..."],"confidence":0..1}]}.',
            )
        except Exception:
            return []
        observations = []
        for raw in payload.get("observations", []):
            indexes = valid_indexes(raw.get("source_indexes"), len(facts))
            if str(raw.get("text", "")).strip() and len(indexes) >= 2:
                observations.append({**raw, "source_indexes": indexes})
        return observations

    async def _build_plan(
        self,
        *,
        retain_input: RetainInput,
        chunks: list[Chunk],
        facts_by_chunk: list[list[ExtractedFact]],
        observations: list[dict],
    ) -> RetainPlan:
        memories: list[MemoryDraft] = []
        links: list[MemoryLinkDraft] = []
        facts: list[tuple[ExtractedFact, MemoryDraft]] = []
        causal_indexes: list[tuple[int, int]] = []
        entity_memories: dict[str, list[str]] = defaultdict(list)
        texts: list[str] = []
        specs: list[tuple[Chunk, int, ExtractedFact, bool, int | None]] = []
        flat_index = 0
        context = (
            retain_input.context.strip()
            if retain_input.context and retain_input.context.strip()
            else f"Knowledge-base document: {retain_input.title}"
        )
        tags = list(
            dict.fromkeys(
                (
                    "team-knowledge-base",
                    f"file-type:{retain_input.file_type}",
                    *retain_input.tags,
                )
            )
        )
        metadata = {
            **retain_input.metadata,
            "title": retain_input.title,
            "file_type": retain_input.file_type,
            "source_type": retain_input.source_type,
        }

        for chunk, chunk_facts in zip(chunks, facts_by_chunk, strict=True):
            specs.append((chunk, 0, ExtractedFact(text=chunk.text), True, None))
            texts.append(chunk.text)
            chunk_offset = flat_index
            for memory_index, fact in enumerate(chunk_facts, start=1):
                specs.append((chunk, memory_index, fact, False, flat_index))
                texts.append(fact.text)
                for target in fact.caused_by:
                    if 0 <= target < len(chunk_facts) and target != memory_index - 1:
                        causal_indexes.append((flat_index, chunk_offset + target))
                flat_index += 1

        embeddings = await self._providers.embed(texts)
        if len(embeddings) != len(specs):
            raise ValueError("embedding provider returned an unexpected row count")

        by_flat_index: dict[int, MemoryDraft] = {}
        for spec, embedding in zip(specs, embeddings, strict=True):
            chunk, memory_index, fact, is_source_chunk, fact_index = spec
            memory = MemoryDraft(
                id=str(uuid4()),
                document_id=retain_input.document_id,
                chunk_index=chunk.index,
                memory_index=memory_index,
                memory_type=fact.fact_type,
                text=fact.text,
                source_text=chunk.text,
                context=context,
                embedding=embedding,
                entities=list(fact.entities),
                occurred_start=fact.occurred_start,
                occurred_end=fact.occurred_end,
                confidence=fact.confidence,
                is_source_chunk=is_source_chunk,
                location=fact.location,
                tags=list(tags),
                metadata=dict(metadata),
            )
            memories.append(memory)
            if fact_index is not None:
                by_flat_index[fact_index] = memory
                facts.append((fact, memory))
                for entity in fact.entities:
                    normalized = normalize_entity(entity)
                    if normalized:
                        entity_memories[normalized].append(memory.id)

        for source_index, target_index in causal_indexes:
            source = by_flat_index.get(source_index)
            target = by_flat_index.get(target_index)
            if source and target and source.id != target.id:
                links.append(MemoryLinkDraft(source.id, target.id, "caused_by"))

        for offset, (_, left) in enumerate(facts):
            for _, right in facts[offset + 1 :]:
                similarity = cosine(left.embedding, right.embedding)
                if similarity >= self._options.semantic_link_threshold:
                    links.append(
                        MemoryLinkDraft(left.id, right.id, "semantic", similarity)
                    )

        for _, memory in facts:
            neighbors = await self._repository.semantic_neighbors(
                memory.embedding,
                exclude_document_id=retain_input.document_id,
                limit=self._options.semantic_neighbor_limit,
            )
            links.extend(
                MemoryLinkDraft(memory.id, target_id, "semantic", similarity)
                for target_id, similarity in neighbors
                if similarity >= self._options.semantic_link_threshold
                and target_id != memory.id
            )

        dated = sorted(
            [(fact, memory) for fact, memory in facts if fact.occurred_start],
            key=lambda item: item[0].occurred_start,  # type: ignore[arg-type]
        )
        for (left_fact, left), (right_fact, right) in zip(dated, dated[1:]):
            assert left_fact.occurred_start and right_fact.occurred_start
            days = (
                abs(
                    (
                        right_fact.occurred_start - left_fact.occurred_start
                    ).total_seconds()
                )
                / 86400
            )
            if days <= 365:
                links.append(
                    MemoryLinkDraft(left.id, right.id, "temporal", 1 / (1 + days))
                )

        for ids in entity_memories.values():
            links.extend(
                MemoryLinkDraft(left, right, "entity")
                for left, right in zip(ids, ids[1:])
                if left != right
            )

        observation_texts = [str(item["text"]) for item in observations]
        observation_embeddings = await self._providers.embed(observation_texts)
        if len(observation_embeddings) != len(observations):
            raise ValueError(
                "embedding provider returned an unexpected observation row count"
            )
        for index, (raw, embedding) in enumerate(
            zip(observations, observation_embeddings, strict=True), start=1
        ):
            sources = [
                by_flat_index[source_index]
                for source_index in raw["source_indexes"]
                if source_index in by_flat_index
            ]
            if len(sources) < 2:
                continue
            observation = MemoryDraft(
                id=str(uuid4()),
                document_id=retain_input.document_id,
                chunk_index=-1,
                memory_index=index,
                memory_type="observation",
                text=str(raw["text"]),
                source_text="\n".join(source.text for source in sources),
                context=f"Consolidated observation; {context}",
                embedding=embedding,
                entities=[str(item) for item in raw.get("entities", [])],
                confidence=float(raw.get("confidence", 1.0)),
                source_memory_ids=[source.id for source in sources],
                tags=list(tags),
                metadata={**metadata, "derived": True},
            )
            memories.append(observation)
            links.extend(
                MemoryLinkDraft(observation.id, source.id, "evidence")
                for source in sources
            )

        unique_links = list(
            {
                (link.source_memory_id, link.target_memory_id, link.link_type): link
                for link in links
            }.values()
        )
        return RetainPlan(
            document_id=retain_input.document_id,
            title=retain_input.title,
            file_type=retain_input.file_type,
            source_type=retain_input.source_type,
            memories=memories,
            links=unique_links,
        )
