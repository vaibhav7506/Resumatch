

"""
Embedding + pgvector operations: chunking, embedding (with a cache),
insertion, and cosine-similarity retrieval.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from pgvector.utils import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 800  # chars, not tokens — good enough for a portfolio project
CHUNK_OVERLAP = 120


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Simple sliding-window chunker. Overlap preserves context that would
    otherwise be cut at a chunk boundary (e.g. a bullet split mid-sentence)."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Voyage AI, using the embedding_cache table
    to avoid re-embedding content we've already seen.

    NOTE: requires `pip install voyageai` and a VOYAGE_API_KEY. Swap this
    function's body for OpenAI/Cohere/whatever embedding provider you
    prefer — everything downstream only depends on getting back a
    list[list[float]] of the configured dimension.
    """
    import voyageai  # local import so the rest of the module works without the dep installed yet

    client = voyageai.Client(api_key=settings.voyage_api_key)

    hashes = [_content_hash(t) for t in texts]
    embeddings: list[list[float] | None] = [None] * len(texts)

    with psycopg.connect(settings.database_url, row_factory=cast(Any, dict_row)) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash, embedding FROM embedding_cache WHERE content_hash = ANY(%s)",
                (hashes,),
            )
            cached = {
                row["content_hash"]: row["embedding"]
                for row in cast(list[dict[str, Any]], cur.fetchall())
            }

        to_embed_idx = [i for i, h in enumerate(hashes) if h not in cached]
        if to_embed_idx:
            result = client.embed(
                [texts[i] for i in to_embed_idx],
                model=settings.embedding_model,
                input_type="document",
            )
            with conn.cursor() as cur:
                for idx, vec in zip(to_embed_idx, result.embeddings):
                    embeddings[idx] = vec
                    cur.execute(
                        "INSERT INTO embedding_cache (content_hash, embedding) "
                        "VALUES (%s, %s) ON CONFLICT (content_hash) DO NOTHING",
                        (hashes[idx], vec),
                    )
            conn.commit()
            logger.info("embedded_new_chunks count=%d", len(to_embed_idx))

        for i, h in enumerate(hashes):
            if embeddings[i] is None:
                embeddings[i] = cached[h]

    logger.info(
        "embed_batch total=%d from_cache=%d", len(texts), len(texts) - len(to_embed_idx)
    )
    return embeddings  # type: ignore[return-value]


def ingest_document(document_id: str, document_type: str, text: str) -> int:
    """Chunk, embed, and store a document (resume or JD). Returns chunk count.

    `text` should already be PII-redacted by app/core/guardrails.py before
    it reaches this function — this module doesn't redact, it just stores.
    """
    chunks = chunk_text(text)
    embeddings = embed_texts(chunks)

    with psycopg.connect(settings.database_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                cur.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, document_type, chunk_index, content, content_hash, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (document_id, document_type, idx, chunk, _content_hash(chunk), emb),
                )
        conn.commit()

    logger.info("ingested document_id=%s type=%s chunks=%d", document_id, document_type, len(chunks))
    return len(chunks)


@dataclass
class RetrievedChunk:
    content: str
    document_id: str
    similarity: float


def retrieve_similar(
    query_text: str, document_type: str | None = None, top_k: int = 5
) -> list[RetrievedChunk]:
    """Embed the query and return the top_k most similar stored chunks
    by cosine similarity, optionally filtered to one document_type."""
    query_embedding = Vector(embed_texts([query_text])[0])   # <-- wrapped here

    sql = """
        SELECT content, document_id, 1 - (embedding <=> %s) AS similarity
        FROM document_chunks
    """
    params: list = [query_embedding]
    if document_type:
        sql += " WHERE document_type = %s"
        params.append(document_type)
    sql += " ORDER BY embedding <=> %s LIMIT %s"
    params.extend([query_embedding, top_k])

    with psycopg.connect(settings.database_url, row_factory=cast(Any, dict_row)) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cast(list[dict[str, Any]], cur.fetchall())

    return [
        RetrievedChunk(content=r["content"], document_id=r["document_id"], similarity=r["similarity"])
        for r in rows
    ]


def retrieve_similar_batch(
    query_texts: list[str],
    document_type: str | None = None,
    document_id: str | None = None,
    top_k: int = 5,
) -> dict[str, list[RetrievedChunk]]:
    """Embed all query texts once, then retrieve similar chunks for each one.
    Optionally scoped to a single document_id so retrieval doesn't leak
    chunks from other previously ingested documents of the same type."""
    if not query_texts:
        return {}

    query_embeddings = [Vector(embedding) for embedding in embed_texts(query_texts)]
    sql = """
        SELECT content, document_id, 1 - (embedding <=> %s) AS similarity
        FROM document_chunks
    """
    results_by_query: dict[str, list[RetrievedChunk]] = {}

    with psycopg.connect(settings.database_url, row_factory=cast(Any, dict_row)) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            for query_text, query_embedding in zip(query_texts, query_embeddings):
                params: list = [query_embedding]
                query_sql = sql
                conditions = []
                if document_type:
                    conditions.append("document_type = %s")
                    params.append(document_type)
                if document_id:
                    conditions.append("document_id = %s")
                    params.append(document_id)
                if conditions:
                    query_sql += " WHERE " + " AND ".join(conditions)
                query_sql += " ORDER BY embedding <=> %s LIMIT %s"
                params.extend([query_embedding, top_k])

                cur.execute(query_sql, params)
                rows = cast(list[dict[str, Any]], cur.fetchall())
                results_by_query[query_text] = [
                    RetrievedChunk(
                        content=row["content"],
                        document_id=row["document_id"],
                        similarity=row["similarity"],
                    )
                    for row in rows
                ]

    return results_by_query