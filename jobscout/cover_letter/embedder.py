"""Local and API embedding client with SQLite caching.

Supports:
- Ollama native `/api/embed` endpoint (standard for Ollama >= 0.1.44)
- OpenAI-compatible `/v1/embeddings` fallback
- Automatic batching and SQLite caching
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence
import httpx
import numpy as np

from jobscout.cover_letter.embedding_cache import EmbeddingCache


class Embedder:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "bge-m3",
        cache_db: str | Path | None = None,
        timeout_seconds: float = 60.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self.cache: EmbeddingCache | None = None
        if cache_db:
            self.cache = EmbeddingCache(cache_db)

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        if norm > 1e-12:
            return vec / norm
        return vec

    def embed_texts(self, texts: Sequence[str]) -> list[np.ndarray]:
        """Embed a list of texts into normalized float32 vectors.

        Leverages SQLite cache where possible to prevent redundant API calls.
        """
        if not texts:
            return []

        # Generate hashes
        hashes = [hashlib.sha256(t.strip().encode("utf-8")).hexdigest() for t in texts]

        cached_vectors: dict[str, np.ndarray] = {}
        if self.cache:
            cached_vectors = self.cache.get_many(hashes, self.model)

        # Identify missing texts that need calculation
        missing_indices: list[int] = []
        missing_texts: list[str] = []
        for i, (text, h) in enumerate(zip(texts, hashes)):
            if h not in cached_vectors:
                missing_indices.append(i)
                missing_texts.append(text)

        if missing_texts:
            fresh_vectors = self._fetch_embeddings(missing_texts)
            to_cache = []
            for idx, vec in zip(missing_indices, fresh_vectors):
                norm_vec = self._normalize(np.array(vec, dtype=np.float32))
                h = hashes[idx]
                cached_vectors[h] = norm_vec
                to_cache.append((h, norm_vec))
            if self.cache and to_cache:
                self.cache.set_many(to_cache, self.model)

        results: list[np.ndarray] = []
        for h in hashes:
            results.append(self._normalize(cached_vectors[h]))
        return results

    def embed_text(self, text: str) -> np.ndarray:
        """Embed a single text string."""
        return self.embed_texts([text])[0]

    def _fetch_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Call either Ollama /api/embed or OpenAI /v1/embeddings."""
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "ollama":
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 1. Try Ollama native /api/embed first if url doesn't have /v1
        is_ollama_native = "/v1" not in self.base_url
        if is_ollama_native:
            endpoint = f"{self.base_url}/api/embed"
            payload = {"model": self.model, "input": texts}
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if "embeddings" in data and isinstance(data["embeddings"], list):
                            return data["embeddings"]
            except Exception:
                pass  # Fall back to OpenAI style

        # 2. Try OpenAI-compatible /v1/embeddings (or /embeddings)
        endpoint = (
            f"{self.base_url}/embeddings"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/embeddings"
        )
        payload = {"model": self.model, "input": texts}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if "data" in data and isinstance(data["data"], list):
                    # Sort by index if present
                    sorted_items = sorted(
                        data["data"], key=lambda x: x.get("index", 0)
                    )
                    return [item["embedding"] for item in sorted_items]
        except Exception as exc:
            raise RuntimeError(
                f"Failed to generate embeddings from {endpoint} using model '{self.model}': {exc}"
            ) from exc

        raise RuntimeError(f"Unexpected embedding response from {self.base_url}")
