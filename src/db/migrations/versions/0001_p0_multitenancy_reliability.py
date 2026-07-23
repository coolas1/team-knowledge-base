"""P0 multitenancy, durable operations, facts, and outbox.

Revision ID: 0001_p0
Revises: None
"""

from alembic import op

revision = "0001_p0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This migration is deliberately idempotent so it can adopt the existing
    # create_all-managed TKB database as well as bootstrap a fresh database.
    op.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id varchar(128) PRIMARY KEY,
            name text NOT NULL,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        INSERT INTO teams (id, name) VALUES ('default', 'Default Team')
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL DEFAULT 'default' REFERENCES teams(id),
            title text NOT NULL,
            file_type text NOT NULL,
            raw_text text NOT NULL DEFAULT '',
            overview text NOT NULL DEFAULT '',
            file_path text,
            content_hash text,
            status text NOT NULL DEFAULT 'pending',
            error_msg text,
            scope text NOT NULL DEFAULT 'team',
            tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            version integer NOT NULL DEFAULT 1,
            graph_status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    for statement in (
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS team_id varchar(128) NOT NULL DEFAULT 'default'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'team'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags jsonb NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS graph_status text NOT NULL DEFAULT 'pending'",
    ):
        op.execute(statement)
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_documents_team') THEN
            ALTER TABLE documents ADD CONSTRAINT fk_documents_team FOREIGN KEY (team_id) REFERENCES teams(id);
          END IF;
        END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL DEFAULT 'default' REFERENCES teams(id),
            doc_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index integer NOT NULL,
            chunk_text text NOT NULL,
            embedding vector(768),
            overview text NOT NULL DEFAULT '',
            doc_uri text NOT NULL,
            token_count integer,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_chunks_doc_chunk_index UNIQUE (doc_id, chunk_index)
        )
    """)
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS team_id varchar(128) NOT NULL DEFAULT 'default'")
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_chunks_team') THEN
            ALTER TABLE chunks ADD CONSTRAINT fk_chunks_team FOREIGN KEY (team_id) REFERENCES teams(id);
          END IF;
        END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS extracted_entities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            doc_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            document_version integer NOT NULL,
            chunk_index integer NOT NULL,
            name text NOT NULL,
            normalized_name text NOT NULL,
            entity_type text NOT NULL,
            description text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_extracted_entity_source UNIQUE
              (team_id, doc_id, document_version, chunk_index, normalized_name, entity_type)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS extracted_relations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            doc_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            document_version integer NOT NULL,
            chunk_index integer NOT NULL,
            from_name text NOT NULL,
            to_name text NOT NULL,
            relation_type text NOT NULL,
            description text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_extracted_relation_source UNIQUE
              (team_id, doc_id, document_version, chunk_index, from_name, to_name, relation_type)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS document_relations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            source_doc_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            target_doc_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            relation_type text NOT NULL,
            reason text NOT NULL DEFAULT '',
            CONSTRAINT uq_document_relation UNIQUE
              (team_id, source_doc_id, target_doc_id, relation_type)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
            operation_type text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            idempotency_key varchar(255) NOT NULL,
            request_hash varchar(64) NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            result jsonb NOT NULL DEFAULT '{}'::jsonb,
            progress integer NOT NULL DEFAULT 0,
            attempt_count integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 3,
            next_retry_at timestamptz,
            worker_id varchar(255),
            lease_expires_at timestamptz,
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_operation_idempotency UNIQUE (team_id, idempotency_key)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS outbox_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id varchar(128) NOT NULL REFERENCES teams(id),
            aggregate_type text NOT NULL,
            aggregate_id varchar(255) NOT NULL,
            aggregate_version integer NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'pending',
            attempt_count integer NOT NULL DEFAULT 0,
            next_retry_at timestamptz,
            worker_id varchar(255),
            lease_expires_at timestamptz,
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            processed_at timestamptz,
            CONSTRAINT uq_outbox_aggregate_version UNIQUE
              (team_id, aggregate_type, aggregate_id, aggregate_version, event_type)
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_documents_team_status ON documents(team_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_documents_team_scope ON documents(team_id, scope)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_team_doc ON chunks(team_id, doc_id)",
        "CREATE INDEX IF NOT EXISTS idx_extracted_entities_projection ON extracted_entities(team_id, doc_id, document_version)",
        "CREATE INDEX IF NOT EXISTS idx_extracted_relations_projection ON extracted_relations(team_id, doc_id, document_version)",
        "CREATE INDEX IF NOT EXISTS idx_operations_claim ON operations(status, next_retry_at, lease_expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_operations_team_created ON operations(team_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_outbox_claim ON outbox_events(status, next_retry_at, lease_expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_documents_title_trgm ON documents USING gin(title gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "outbox_events", "operations", "document_relations",
        "extracted_relations", "extracted_entities",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for column in ("graph_status", "version", "tags", "scope"):
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS team_id")
    op.execute("DROP TABLE IF EXISTS teams CASCADE")
