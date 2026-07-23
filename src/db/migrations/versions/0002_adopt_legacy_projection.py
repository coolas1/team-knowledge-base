"""Adopt the pre-outbox Neo4j projection state.

Revision ID: 0002_legacy
Revises: 0001_p0
"""

from alembic import op

revision = "0002_legacy"
down_revision = "0001_p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy indexed documents already have a Neo4j graph. Neo4jClient.initialize
    # assigns those nodes to the default team and projection version 1.
    op.execute(
        "UPDATE documents SET graph_status = 'ready' "
        "WHERE status = 'indexed' AND graph_status = 'pending' AND version = 1"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE documents SET graph_status = 'pending' "
        "WHERE status = 'indexed' AND version = 1"
    )
