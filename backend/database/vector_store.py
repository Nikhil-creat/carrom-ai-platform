"""PostgreSQL + pgvector implementation of ai.rag_pipeline.VectorStore.

Synchronous on purpose: retrieval is called from the AI agent, which already
runs in a worker thread (asyncio.to_thread) so it never blocks the event loop.

Usage:
    store = PgVectorStore(os.environ["DATABASE_URL"])
    rag = RagPipeline(store=store)     # seeds the table on first run
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

from ai.rag_pipeline import Document, Hit


class PgVectorStore:
    def __init__(self, dsn: str, table: str = "knowledge_docs") -> None:
        import psycopg  # imported lazily so the rest of the app runs without it
        from pgvector.psycopg import register_vector

        self._psycopg = psycopg
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        self.table = table
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)

    def _connect(self):
        from pgvector.psycopg import register_vector

        conn = self._psycopg.connect(self.dsn, autocommit=True)
        register_vector(conn)
        return conn

    def upsert(self, doc: Document, vec: np.ndarray) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""INSERT INTO {self.table} (id, kind, title, body, meta, embedding)
                    VALUES (%s, %s, %s, %s, %s::json, %s)
                    ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, title = EXCLUDED.title,
                      body = EXCLUDED.body, meta = EXCLUDED.meta, embedding = EXCLUDED.embedding""",
                (doc.id, doc.kind, doc.title, doc.text, json.dumps(doc.meta), vec),
            )

    def search(self, vec: np.ndarray, k: int, kind: str | None) -> list[Hit]:
        where = "WHERE kind = %s" if kind else ""
        params: list[Any] = ([kind] if kind else [])
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT id, kind, title, body, meta, 1 - (embedding <=> %s) AS score
                    FROM {self.table} {where}
                    ORDER BY embedding <=> %s LIMIT %s""",
                [vec, *params, vec, k],
            ).fetchall()
        return [Hit(Document(r[0], r[1], r[2], r[3], r[4] or {}), float(r[5])) for r in rows]
