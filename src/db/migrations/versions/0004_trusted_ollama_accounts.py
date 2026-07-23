"""Add administrator-approved Ollama account memberships.

Revision ID: 0004_ollama_accounts
Revises: 0003_team_tokens
"""

from alembic import op

revision = "0004_ollama_accounts"
down_revision = "0003_team_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trusted_ollama_accounts (
            id uuid PRIMARY KEY,
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            username varchar(255) NOT NULL,
            display_name varchar(255) NOT NULL DEFAULT '',
            roles jsonb NOT NULL DEFAULT '["viewer"]'::jsonb,
            active boolean NOT NULL DEFAULT true,
            last_used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_trusted_ollama_team_username UNIQUE (team_id, username)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trusted_ollama_username_active "
        "ON trusted_ollama_accounts(username, active)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_trusted_ollama_team_created "
        "ON trusted_ollama_accounts(team_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trusted_ollama_accounts")
