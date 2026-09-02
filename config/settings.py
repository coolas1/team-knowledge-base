"""Infra connection settings, loaded from .env via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InfraSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "knowledge_base"
    postgres_user: str = "kb_user"
    postgres_password: str = "kb_pass"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    ollama_base_url: str = "http://localhost:11434"

    # Chat/analysis LLM (OpenAI-compatible or Ollama). provider="todo" disables it.
    llm_provider: str = "todo"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""

    # Reranker (search gatekeeper). provider: local|http|none.
    #   local = CrossEncoder (needs the `reranker` extra / torch);
    #   http  = external /v1/rerank API (Cohere/Jina/OpenAI-compatible);
    #   none  = disabled (vector-only ranking, no torch).
    reranker_provider: str = "none"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_base_url: str = ""
    reranker_api_key: str = ""

    # Disposable Neo4j projection worker. It remains opt-in so PostgreSQL-only
    # Hindsight deployments keep their current runtime behaviour.
    hindsight_graph_worker_enabled: bool = False
    hindsight_graph_worker_poll_seconds: float = Field(default=1.0, gt=0)
    hindsight_graph_worker_lease_seconds: int = Field(default=300, ge=1)
    hindsight_graph_worker_max_attempts: int = Field(default=10, ge=1)

    # Automatic conversation memory remains separately gated from file-memory
    # retention so deployments can upgrade the engine contract before enabling it.
    hindsight_conversation_memory_enabled: bool = False
    hindsight_conversation_recall_limit: int = Field(default=20, ge=1, le=100)
    hindsight_conversation_worker_poll_seconds: float = Field(default=1.0, gt=0)
    hindsight_conversation_worker_lease_seconds: int = Field(default=300, ge=1)
    hindsight_conversation_worker_max_attempts: int = Field(default=10, ge=1)
    hindsight_conversation_worker_max_concurrent: int = Field(default=1, ge=1)
    hindsight_conversation_worker_retry_seconds: float = Field(default=1.0, gt=0)
    hindsight_conversation_worker_max_retry_seconds: float = Field(
        default=300.0, gt=0
    )
    hindsight_conversation_retention_context: str = Field(
        default="Completed team conversation turn", min_length=1
    )

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = InfraSettings()
