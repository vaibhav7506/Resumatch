"""
Creates the pgvector extension and the tables this app needs.

Run once: `python -m app.db.init_db`

Kept as plain SQL (not an ORM/migration framework) so the schema is easy to
read end-to-end in one file — appropriate for a portfolio project of this
size. If this grew into a real product you'd move to Alembic migrations.
"""

import psycopg

from app.core.config import settings

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per chunk of text (resume chunk or JD chunk), with its embedding.
CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL,       -- groups chunks belonging to one resume/JD
    document_type   TEXT NOT NULL,       -- 'resume' | 'job_description'
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,       -- PII-redacted before it ever gets here
    content_hash    TEXT NOT NULL,       -- for the embedding cache
    embedding       VECTOR(1024) NOT NULL,  -- voyage-3 dimension; adjust if you swap models
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest-neighbor index for fast similarity search at scale.
-- (For small portfolio-project data volumes this isn't strictly necessary,
-- but it's the correct real-world pattern to demonstrate.)
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx
    ON document_chunks (document_id);

-- Simple embedding cache keyed by content hash, so re-ingesting the same
-- resume/JD text twice doesn't re-call the embeddings API. This is the
-- "caching to reduce latency and token usage" bullet, implemented for real.
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash    TEXT PRIMARY KEY,
    embedding       VECTOR(1024) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per match run, so you can show "history" in a UI later and it
-- doubles as an audit log of guardrail hits.
CREATE TABLE IF NOT EXISTS match_runs (
    id                  BIGSERIAL PRIMARY KEY,
    resume_document_id  TEXT NOT NULL,
    jd_document_id      TEXT NOT NULL,
    score               NUMERIC(5,2),
    had_pii_redacted    BOOLEAN NOT NULL DEFAULT false,
    had_injection_flag  BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_db() -> None:
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Schema created (or already existed).")


if __name__ == "__main__":
    init_db()
