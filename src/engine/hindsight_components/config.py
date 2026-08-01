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
    rrf_k: int = 60
    semantic_link_threshold: float = 0.78
    semantic_neighbor_limit: int = 3
    mmr_redundancy_penalty: float = 0.2
    reflect_subquery_limit: int = 3
    reflect_model_limit: int = 5
