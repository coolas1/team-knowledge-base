"""Runtime-neutral options for the Hindsight core."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HindsightOptions:
    chunk_tokens: int = 500
    chunk_overlap_tokens: int = 50
    recall_limit: int = 20
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

    def __post_init__(self) -> None:
        positive = {
            "deep_total_timeout_seconds": self.deep_total_timeout_seconds,
            "query_analysis_timeout_seconds": self.query_analysis_timeout_seconds,
            "query_embedding_timeout_seconds": self.query_embedding_timeout_seconds,
            "retrieval_arm_timeout_seconds": self.retrieval_arm_timeout_seconds,
            "rerank_timeout_seconds": self.rerank_timeout_seconds,
            "rerank_candidate_limit": self.rerank_candidate_limit,
            "rerank_text_limit_chars": self.rerank_text_limit_chars,
            "rerank_total_chars": self.rerank_total_chars,
            "keyword_candidate_limit": self.keyword_candidate_limit,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(
                f"Hindsight timeout and bound settings must be positive: {', '.join(invalid)}"
            )
