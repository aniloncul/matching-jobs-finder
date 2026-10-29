from __future__ import annotations

import numpy as np
import pytest

from jobscout.cover_letter.bullets import Bullet, extract_bullets
from jobscout.cover_letter.embedding_cache import EmbeddingCache
from jobscout.cover_letter.matcher import HybridMatcher
from jobscout.cover_letter.requirements import extract_requirements
from jobscout.cover_letter.verifier import verify_letter


def test_extract_bullets_from_profile() -> None:
    profile_dict = {
        "experience": [
            {
                "company": "Tech Corp",
                "role": "Senior Developer",
                "start": "2021",
                "end": "2024",
                "achievements": [
                    "Designed scalable microservices architecture in Python and FastAPI.",
                    "Reduced database query latency by 45% using Redis caching.",
                ],
            }
        ],
        "summary": "Experienced backend engineer focused on high-throughput distributed systems.",
        "skills": ["Python", "Docker", "PostgreSQL"],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "M.Sc. Computer Science",
            }
        ],
    }

    bullets = extract_bullets(profile_dict)
    assert len(bullets) >= 4

    sources = {b.source for b in bullets}
    assert "experience" in sources
    assert "skills" in sources
    assert "education" in sources

    # Check hash uniqueness
    hashes = [b.content_hash for b in bullets]
    assert len(hashes) == len(set(hashes))


def test_extract_requirements_from_german_ad() -> None:
    ad = """
    Wir suchen einen Python Entwickler (m/w/d) in Berlin.

    Deine Aufgaben:
    - Weiterentwicklung unserer Web-Plattform
    
    Das bringst du mit:
    - Mehrjährige Erfahrung mit Python und modernen Frameworks (FastAPI oder Django)
    - Fundierte Kenntnisse im Bereich Docker und CI/CD Pipelines
    - Fließende Deutsch- und gute Englischkenntnisse

    Wir bieten:
    - 30 Tage Urlaub
    - Flexible Arbeitszeiten
    """
    reqs = extract_requirements(ad)
    assert len(reqs) >= 3
    texts = " ".join(r.text for r in reqs)
    assert "Python" in texts
    assert "Docker" in texts
    must_haves = [r for r in reqs if r.category == "must_have"]
    assert len(must_haves) >= 2
    # Ensure perks like "30 Tage Urlaub" are excluded
    assert not any("Urlaub" in r.text for r in reqs)


def test_embedding_cache_sqlite(tmp_path) -> None:
    db_file = tmp_path / "test_embeddings.sqlite3"
    cache = EmbeddingCache(db_file)

    vec1 = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    cache.set("hash_1", "test_model", vec1)

    loaded = cache.get("hash_1", "test_model")
    assert loaded is not None
    assert np.allclose(loaded, vec1)

    # Different model should return None
    assert cache.get("hash_1", "other_model") is None

    # Batch get
    vec2 = np.array([0.5, 0.6, 0.7, 0.8], dtype=np.float32)
    cache.set("hash_2", "test_model", vec2)

    batch = cache.get_many(["hash_1", "hash_2", "hash_3"], "test_model")
    assert len(batch) == 2
    assert "hash_1" in batch
    assert "hash_2" in batch


def test_hybrid_matcher_bm25_fallback() -> None:
    bullets = [
        Bullet.from_text("experience", "Built distributed workflow automations using n8n and Python scripts."),
        Bullet.from_text("experience", "Managed social media marketing campaigns on Instagram and TikTok."),
        Bullet.from_text("education", "Bachelor of Science in Information Systems at University of Cologne."),
    ]

    from jobscout.cover_letter.requirements import Requirement
    requirements = [
        Requirement("Strong experience with n8n workflow automation and Python integration."),
    ]

    matcher = HybridMatcher(embedder=None, keyword_weight=1.0, threshold=0.1)
    matched = matcher.match(bullets, requirements)

    assert len(matched) >= 1
    # Top match should be the n8n bullet
    assert "n8n" in matched[0].bullet.text


def test_verifier_grounding() -> None:
    bullets = [
        Bullet.from_text("experience", "Increased organic traffic by 25% and saved 10 hours weekly."),
    ]

    # Letter with matching metric
    letter_good = (
        "In meiner vorherigen Position steigerte ich den Traffic um 25% durch gezielte Optimierungen. "
        "Zudem konnte ich 10 hours pro Woche durch Automatisierung einsparen. Ich freue mich auf das Gespräch."
    )
    result_good = verify_letter(letter_good, available_bullets=bullets)
    assert result_good.passed is True
    assert len(result_good.unverified_metrics) == 0

    # Letter with hallucinated metric
    letter_bad = (
        "In meiner vorherigen Position steigerte ich den Traffic um 85% und verwaltete ein Budget von 500k. "
        "Ich freue mich auf ein persönliches Gespräch."
    )
    result_bad = verify_letter(letter_bad, available_bullets=bullets)
    assert result_bad.passed is False
    assert len(result_bad.unverified_metrics) > 0
    assert any("85%" in m or "500k" in m for m in result_bad.unverified_metrics)
