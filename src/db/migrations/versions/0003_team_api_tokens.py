"""Add database-backed team API tokens.

Revision ID: 0003_team_tokens
Revises: 0002_legacy
"""

from alembic import op

revision = "0003_team_tokens"
down_revision = "0002_legacy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_api_tokens (
            id uuid PRIMARY KEY,
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            name varchar(255) NOT NULL,
            subject varchar(255) NOT NULL,
            token_hash varchar(64) NOT NULL UNIQUE,
            token_prefix varchar(32) NOT NULL,
            roles jsonb NOT NULL DEFAULT '["member"]'::jsonb,
            active boolean NOT NULL DEFAULT true,
            expires_at timestamptz NULL,
            last_used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_api_tokens_team_created "
        "ON team_api_tokens(team_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_api_tokens_hash_active "
        "ON team_api_tokens(token_hash, active)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS team_api_tokens")
