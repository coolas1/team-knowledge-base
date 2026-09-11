"""Runtime-neutral options for the Hindsight core."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HindsightOptions:
    file_summary_enabled: bool = False
    adaptive_reflect_enabled: bool = False
    consolidation_enabled: bool = False
    entity_resolution_enabled: bool = False
    entity_candidate_limit: int = 10
    entity_resolution_timeout_seconds: float = 60.0
    entity_resolution_max_concurrent: int = 8
    chunk_tokens: int = 500
    chunk_overlap_tokens: int = 50
    retain_chunk_concurrency: int = 4
    recall_limit: int = 20
    recall_max_results: int = 100
    recall_max_candidates: int = 300
    recall_max_tokens: int = 4096
    retrieval_arm_minimum: int = 30
    rerank_limit: int = 40
    deep_total_timeout_seconds: float = 45.0
    query_analysis_timeout_seconds: float = 8.0
    query_embedding_timeout_seconds: float = 10.0
    retrieval_arm_timeout_seconds: float = 5.0
    rerank_timeout_seconds: float = 12.0
    rerank_candidate_limit: int = 40
    rerank_text_limit_chars: int = 4_000
    rerank_total_chars: int = 60_000
    keyword_candidate_limit: int = 300
    rrf_k: int = 60
    semantic_link_threshold: float = 0.78
    semantic_neighbor_limit: int = 3
    mmr_redundancy_penalty: float = 0.2
    # Relevance gates: queries without KB coverage must not surface unrelated
    # memories. semantic gate applies to every mode; score gate applies to the
    # neural-rerank path (deep mode).
    recall_min_semantic: float = 0.45
    recall_min_score: float = 0.4
    # Cap neural reranker scores with vector similarity to stop the LLM from
    # "hallucinating" high scores for semantically unrelated chunks.
    rerank_semantic_margin: float = 0.25
    reflect_subquery_limit: int = 3
    reflect_model_limit: int = 5
    reflect_max_iterations: int = 8
    reflect_max_tokens: int = 8192
    reflect_total_timeout_seconds: float = 60

    def __post_init__(self) -> None:
        if self.entity_candidate_limit > 100:
            raise ValueError("entity_candidate_limit cannot exceed 100")
        positive = {
            "entity_candidate_limit": self.entity_candidate_limit,
            "entity_resolution_timeout_seconds": self.entity_resolution_timeout_seconds,
            "entity_resolution_max_concurrent": self.entity_resolution_max_concurrent,
            "retain_chunk_concurrency": self.retain_chunk_concurrency,
            "deep_total_timeout_seconds": self.deep_total_timeout_seconds,
            "query_analysis_timeout_seconds": self.query_analysis_timeout_seconds,
            "query_embedding_timeout_seconds": self.query_embedding_timeout_seconds,
            "retrieval_arm_timeout_seconds": self.retrieval_arm_timeout_seconds,
            "rerank_timeout_seconds": self.rerank_timeout_seconds,
            "rerank_candidate_limit": self.rerank_candidate_limit,
            "rerank_text_limit_chars": self.rerank_text_limit_chars,
            "rerank_total_chars": self.rerank_total_chars,
            "keyword_candidate_limit": self.keyword_candidate_limit,
            "recall_max_results": self.recall_max_results,
            "recall_max_candidates": self.recall_max_candidates,
            "recall_max_tokens": self.recall_max_tokens,
            "reflect_max_iterations": self.reflect_max_iterations,
            "reflect_max_tokens": self.reflect_max_tokens,
            "reflect_total_timeout_seconds": self.reflect_total_timeout_seconds,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(
                f"Hindsight timeout and bound settings must be positive: {', '.join(invalid)}"
            )
