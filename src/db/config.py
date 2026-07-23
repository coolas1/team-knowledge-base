from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "knowledge_base"
    postgres_user: str = "kb_user"
    postgres_password: str = "kb_pass"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # LLM (DashScope)
    llm_api_key: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Tenant/auth compatibility. Production multi-team deployments should map
    # trusted bearer tokens to teams via API_TOKENS_JSON and keep the untrusted
    # header disabled.
    default_team_id: str = "default"
    allow_anonymous_default_team: bool = True
    allow_untrusted_team_header: bool = False
    api_tokens_json: str = "{}"
    # A server-owned token selecting the trusted team identity for MCP calls.
    # Agents cannot override its team_id through tool arguments.
    mcp_api_token: str = ""
    # Requests without a bearer token may identify an administrator-approved
    # Ollama username only when their source IP belongs to one of these CIDRs.
    trusted_ollama_networks: str = "127.0.0.1/32,::1/128"

    # Durable workers
    operation_poll_interval: float = 1.0
    operation_lease_seconds: int = 300
    projector_poll_interval: float = 1.0
    worker_max_attempts: int = 3

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
