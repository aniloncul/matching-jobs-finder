"""Hybrid Matcher for CV Bullets and Job Requirements.

Combines:
1. Dense Semantic Matching (Cosine similarity via BGE-M3 / Ollama)
2. Sparse Lexical Matching (BM25 Okapi)
3. Maximal Marginal Relevance (MMR) for diversity across career achievements
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence
import numpy as np

from jobscout.cover_letter.bullets import Bullet
from jobscout.cover_letter.embedder import Embedder
from jobscout.cover_letter.requirements import Requirement


@dataclass
class MatchedBullet:
    bullet: Bullet
    requirement: Requirement
    semantic_score: float
    keyword_score: float
    total_score: float


def _tokenize(text: str) -> list[str]:
    """Simple alphanumeric tokenizer for BM25."""
    return re.findall(r"\b\w{2,}\b", text.lower())


class HybridMatcher:
    def __init__(
        self,
        embedder: Embedder | None = None,
        semantic_weight: float = 0.7,
        keyword_weight: float = 0.3,
        threshold: float = 0.25,
        max_matches: int = 8,
    ) -> None:
        self.embedder = embedder
        self.semantic_weight = semantic_weight
        self.keyword_weight = keyword_weight
        self.threshold = threshold
        self.max_matches = max_matches

    def match(
        self,
        bullets: Sequence[Bullet],
        requirements: Sequence[Requirement],
    ) -> list[MatchedBullet]:
        """Match candidate bullets against target job requirements."""
        if not bullets or not requirements:
            return []

        # 1. Compute BM25 lexical scores
        try:
            from rank_bm25 import BM25Okapi
            corpus_tokens = [_tokenize(b.text) for b in bullets]
            bm25 = BM25Okapi(corpus_tokens)
            use_bm25 = True
        except Exception:
            use_bm25 = False

        # 2. Compute Semantic Embeddings if embedder is available
        bullet_vecs: list[np.ndarray] = []
        req_vecs: list[np.ndarray] = []
        has_vectors = False
        if self.embedder is not None:
            try:
                bullet_texts = [b.text for b in bullets]
                req_texts = [r.text for r in requirements]
                bullet_vecs = self.embedder.embed_texts(bullet_texts)
                req_vecs = self.embedder.embed_texts(req_texts)
                has_vectors = True
            except Exception:
                has_vectors = False

        # 3. Score every (bullet, requirement) pair
        candidates: list[MatchedBullet] = []

        for r_idx, req in enumerate(requirements):
            # BM25 scores for this requirement against all bullets
            if use_bm25:
                q_tokens = _tokenize(req.text)
                raw_bm25 = bm25.get_scores(q_tokens)
                max_bm25 = max(raw_bm25) if len(raw_bm25) > 0 and max(raw_bm25) > 0 else 1.0
                bm25_norm = [s / max_bm25 for s in raw_bm25]
            else:
                bm25_norm = [0.0] * len(bullets)

            for b_idx, bullet in enumerate(bullets):
                # Cosine similarity
                if has_vectors:
                    # Vectors are pre-normalized
                    cos_sim = float(np.dot(bullet_vecs[b_idx], req_vecs[r_idx]))
                    cos_sim = max(0.0, min(1.0, cos_sim))
                else:
                    cos_sim = 0.5  # Neutral fallback

                kw_sim = float(bm25_norm[b_idx])

                # Combine weights
                if has_vectors:
                    total = (self.semantic_weight * cos_sim) + (self.keyword_weight * kw_sim)
                else:
                    total = kw_sim

                if total >= self.threshold:
                    candidates.append(
                        MatchedBullet(
                            bullet=bullet,
                            requirement=req,
                            semantic_score=cos_sim,
                            keyword_score=kw_sim,
                            total_score=total,
                        )
                    )

        # 4. Filter and diversify with MMR / deduplication
        # Sort by total_score descending
        candidates.sort(key=lambda x: x.total_score, reverse=True)

        selected: list[MatchedBullet] = []
        seen_bullet_hashes: set[str] = set()
        seen_req_texts: set[str] = set()

        for cand in candidates:
            if cand.bullet.content_hash in seen_bullet_hashes:
                continue

            # Diversity: try to avoid having multiple bullets for the exact same requirement
            # until other requirements have at least one match
            if cand.requirement.text in seen_req_texts and len(selected) < len(requirements):
                continue

            selected.append(cand)
            seen_bullet_hashes.add(cand.bullet.content_hash)
            seen_req_texts.add(cand.requirement.text)

            if len(selected) >= self.max_matches:
                break

        # If not enough matches due to diversity rule, backfill from remaining candidates
        if len(selected) < self.max_matches:
            for cand in candidates:
                if cand.bullet.content_hash not in seen_bullet_hashes:
                    selected.append(cand)
                    seen_bullet_hashes.add(cand.bullet.content_hash)
                    if len(selected) >= self.max_matches:
                        break

        # If still no matches (e.g. threshold was too high), take top 3 bullets by default
        if not selected and bullets:
            for b in bullets[:3]:
                selected.append(
                    MatchedBullet(
                        bullet=b,
                        requirement=requirements[0],
                        semantic_score=0.5,
                        keyword_score=0.5,
                        total_score=0.5,
                    )
                )

        return selected
