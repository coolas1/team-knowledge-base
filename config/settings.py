"""Infra connection settings, loaded from .env via pydantic-settings."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.engine.trusted_scope import ScopeBinding


class LLMSettings(BaseSettings):
    """Chat/analysis LLM — any OpenAI-compatible /chat/completions API.

    Empty base_url disables the LLM (Analyzer degrades to placeholders;
    Hindsight raises). Enabled with an empty model is a config error.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LLM_", extra="ignore"
    )

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    memory_thinking: Literal["auto", "disabled", "enabled"] = "auto"

    @property
    def enabled(self) -> bool:
        """True when an OpenAI-compatible endpoint is configured."""
        return bool(self.base_url)

    def require_model(self) -> str:
        """Model name, or a clear error when enabled without a model."""
        if not self.model:
            raise ValueError(
                "LLM_BASE_URL is set but LLM_MODEL is empty; "
                "configure LLM_MODEL or clear LLM_BASE_URL to disable the LLM"
            )
        return self.model


class ImageSettings(BaseSettings):
    """Explicit Agent Plan image route; never inherits text credentials."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="IMAGE_", extra="ignore"
    )
    provider: str = "ark"
    base_url: str = ""
    model: str = ""
    api_key: str = Field(default="", repr=False)

    def require_ready(self) -> None:
        if self.provider != "ark":
            raise ValueError("Unsupported IMAGE_PROVIDER")
        if not self.api_key or not self.model or not self.base_url:
            raise ValueError(
                "IMAGE_BASE_URL, IMAGE_MODEL and IMAGE_API_KEY are required"
            )
        if self.base_url.rstrip("/") != "https://ark.cn-beijing.volces.com/api/plan/v3":
            raise ValueError(
                "IMAGE_BASE_URL must use the approved Ark Agent Plan route"
            )
        if self.model != "doubao-seedream-5.0-lite":
            raise ValueError("IMAGE_MODEL has no verified Seedream capability profile")


class PPTSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="PPT_", extra="ignore"
    )
    enabled: bool = False
    max_pages: int = Field(default=20, ge=1, le=20)
    default_pages: int = Field(default=8, ge=1, le=20)


class EmbeddingSettings(BaseSettings):
    """Embeddings — any OpenAI-compatible /v1/embeddings API.

    No off switch: embeddings are load-bearing for search. The stored
    vector width is fixed at 768 (EMBEDDING_DIM in store/models.py); the
    Embedder fails fast if the model returns another width.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="EMBEDDING_", extra="ignore"
    )

    base_url: str = "http://localhost:11434/v1"
    model: str = "nomic-embed-text"
    api_key: str = ""


class RerankerSettings(BaseSettings):
    """Reranker — provider: none | http | local (see components/reranker.py)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="RERANKER_", extra="ignore"
    )

    provider: str = "none"
    base_url: str = ""
    model: str = "BAAI/bge-reranker-v2-m3"
    api_key: str = ""


class InfraSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    memory_scope_bindings: dict[str, ScopeBinding] = Field(
        default_factory=dict, repr=False
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "knowledge_base"
    postgres_user: str = "kb_user"
    postgres_password: str = "kb_pass"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    # Disposable Neo4j projection worker. Kill switch for deployments without
    # Neo4j; the primary control is the engine.memory.graph_worker app flag.
    hindsight_graph_worker_enabled: bool = True
    hindsight_graph_worker_poll_seconds: float = Field(default=1.0, gt=0)
    hindsight_graph_worker_lease_seconds: int = Field(default=300, ge=1)
    hindsight_graph_worker_max_attempts: int = Field(default=10, ge=1)

    # Recall relevance gates: drop results below these thresholds so queries
    # without knowledge-base coverage return "not found" instead of surfacing
    # unrelated memories.
    hindsight_recall_min_semantic: float = Field(default=0.45, ge=0.0, le=1.0)
    hindsight_recall_min_score: float = Field(default=0.4, ge=0.0, le=1.0)
    # Conversation-memory recall uses its own, lower semantic floor so the
    # public-corpus gate does not determine what memories are recalled.
    hindsight_conversation_recall_min_semantic: float = Field(
        default=0.25, ge=0.0, le=1.0
    )
    hindsight_rerank_semantic_margin: float = Field(default=0.25, ge=0.0)

    # Deep recall uses a total monotonic deadline plus shorter phase limits.
    hindsight_deep_total_timeout_seconds: float = Field(default=45.0, gt=0)
    hindsight_query_analysis_timeout_seconds: float = Field(default=8.0, gt=0)
    hindsight_query_embedding_timeout_seconds: float = Field(default=10.0, gt=0)
    hindsight_retrieval_arm_timeout_seconds: float = Field(default=5.0, gt=0)
    hindsight_rerank_timeout_seconds: float = Field(default=12.0, gt=0)
    hindsight_rerank_candidate_limit: int = Field(default=40, ge=1)
    hindsight_rerank_text_limit_chars: int = Field(default=4_000, ge=1)
    hindsight_rerank_total_chars: int = Field(default=60_000, ge=1)
    hindsight_keyword_candidate_limit: int = Field(default=300, ge=1)
    hindsight_keyword_index_enabled: bool = False

    # Automatic conversation memory remains separately gated from file-memory
    # retention so deployments can upgrade the engine contract before enabling it.
    hindsight_conversation_memory_enabled: bool = False
    hindsight_conversation_recall_limit: int = Field(default=20, ge=1, le=100)
    # 单轮留存内容上限（字符）：超出截断并附 [truncated] 标记。
    hindsight_conversation_max_turn_chars: int = Field(default=100_000, ge=1)
    hindsight_conversation_worker_poll_seconds: float = Field(default=1.0, gt=0)
    hindsight_conversation_worker_lease_seconds: int = Field(default=300, ge=1)
    hindsight_conversation_worker_max_attempts: int = Field(default=10, ge=1)
    hindsight_conversation_worker_max_concurrent: int = Field(default=1, ge=1)
    hindsight_conversation_worker_retry_seconds: float = Field(default=1.0, gt=0)
    hindsight_conversation_worker_max_retry_seconds: float = Field(default=300.0, gt=0)
    hindsight_conversation_retention_context: str = Field(
        default="Completed team conversation turn", min_length=1
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Uploaded document originals. The relative default keeps dev-checkout
    # behavior; the compose deployment sets the absolute volume mount
    # (/app/uploads) so uploads survive container recreation.
    uploads_dir: str = "uploads"

    # BFF upload size cap (single and batch, per file). Over-cap uploads
    # are rejected with 413 file_too_large; the read is bounded so the
    # body beyond the cap is never buffered. Env: KB_MAX_UPLOAD_BYTES.
    kb_max_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    # Model config groups (OpenAI-compatible endpoints). Sub-models each
    # read their own env prefix from .env; see the classes above.
    llm: LLMSettings = Field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    ppt: PPTSettings = Field(default_factory=PPTSettings)

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = InfraSettings()
