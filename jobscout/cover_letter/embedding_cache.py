"""SQLite-based embedding cache to avoid recomputing vectors.

Schema:
  CREATE TABLE IF NOT EXISTS embeddings (
      content_hash TEXT NOT NULL,
      model_name TEXT NOT NULL,
      dimension INTEGER NOT NULL,
      vector BLOB NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (content_hash, model_name)
  );
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence
import numpy as np


class EmbeddingCache:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30.0)

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    content_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (content_hash, model_name)
                );
                """
            )
            conn.commit()

    def get(self, content_hash: str, model_name: str) -> np.ndarray | None:
        """Retrieve cached embedding vector as a float32 numpy array."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT vector, dimension FROM embeddings WHERE content_hash = ? AND model_name = ?",
                (content_hash, model_name),
            )
            row = cursor.fetchone()
            if not row:
                return None
            blob, dim = row
            vec = np.frombuffer(blob, dtype=np.float32)
            if len(vec) != dim:
                return None
            return vec

    def get_many(
        self, hashes: Sequence[str], model_name: str
    ) -> dict[str, np.ndarray]:
        """Batch retrieve embeddings for multiple hashes."""
        if not hashes:
            return {}
        result: dict[str, np.ndarray] = {}
        placeholders = ",".join("?" for _ in hashes)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT content_hash, vector, dimension FROM embeddings "
                f"WHERE model_name = ? AND content_hash IN ({placeholders})",
                [model_name, *hashes],
            )
            for chash, blob, dim in cursor.fetchall():
                vec = np.frombuffer(blob, dtype=np.float32)
                if len(vec) == dim:
                    result[chash] = vec
        return result

    def set(self, content_hash: str, model_name: str, vector: Sequence[float] | np.ndarray) -> None:
        """Store an embedding vector into the cache."""
        arr = np.ascontiguousarray(vector, dtype=np.float32)
        dim = int(len(arr))
        blob = arr.tobytes()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO embeddings (content_hash, model_name, dimension, vector)
                VALUES (?, ?, ?, ?)
                """,
                (content_hash, model_name, dim, blob),
            )
            conn.commit()

    def set_many(
        self, items: Sequence[tuple[str, Sequence[float] | np.ndarray]], model_name: str
    ) -> None:
        """Store multiple embeddings in a single transaction."""
        if not items:
            return
        records = []
        for chash, vector in items:
            arr = np.ascontiguousarray(vector, dtype=np.float32)
            records.append((chash, model_name, int(len(arr)), arr.tobytes()))
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO embeddings (content_hash, model_name, dimension, vector)
                VALUES (?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()
