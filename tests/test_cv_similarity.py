"""JD-similarity CV reuse: tokenisation, seniority gate, recommendation, copy."""

from __future__ import annotations

import json
from pathlib import Path

from test_cv_pipeline import make_job, make_settings

from jobscout.cover_letter.description_store import save_job_description
from jobscout.cv.pipeline import CVBuildError, cv_paths, reuse_cv
from jobscout.cv.similarity import (
    hard_mismatch,
    jaccard_similarity,
    level_of,
    recommend_cv_reuse,
    recommend_jd_reuse,
    tokenize,
)

SENIOR_JD = """Senior Data Engineer (m/w/d)
Python, PySpark und SQL für unsere Data Pipelines in AWS.
Erfahrung mit Airflow und Data Warehouse erforderlich."""

SENIOR_JD_REWRITTEN = """Data Engineer (m/w/d), Senior Level
Wir bauen Data Pipelines in AWS mit Python, PySpark, Airflow,
SQL und einem Data Warehouse."""

MARKETING_JD = """Social Media Manager (m/w/d)
Instagram und TikTok Inhalte planen, Kampagnen auswerten,
Videos schneiden und Community betreuen."""


# ── tokens and similarity ─────────────────────────────────────────────


def test_tokenize_folds_and_drops_stopwords_and_noise() -> None:
    terms = tokenize("Senior Data Engineer (m/w/d) — mit Müller & Co.")
    assert "mueller" in terms  # umlaut folded like the ATS matcher
    assert "senior" in terms and "data" in terms
    assert "mit" not in terms  # German stopword
    assert "m/w/d" not in terms and not {"m", "w", "d"} & terms  # boilerplate dropped
    assert "co" in terms  # two-letter terms are kept, like career-ops


def test_jaccard_similarity_edges() -> None:
    assert jaccard_similarity("python sql", "python sql") == 1.0
    assert jaccard_similarity("", "") == 1.0
    assert jaccard_similarity("python", "") == 0.0
    assert jaccard_similarity(MARKETING_JD, SENIOR_JD) < 0.05
    # token sets short-circuit the re-tokenisation
    assert jaccard_similarity(tokenize(SENIOR_JD), tokenize(SENIOR_JD_REWRITTEN)) > 0.5


# ── seniority gate ────────────────────────────────────────────────────


def test_level_of_english_and_german() -> None:
    assert level_of("Senior Data Engineer") == 3
    assert level_of("Junior Developer (m/w/d)") == 1
    assert level_of("Werkstudent Marketing") == 0
    assert level_of("Praktikant/in gesucht") == 0
    assert level_of("Teamleitung Data Platform") == 4
    assert level_of("Data Engineer ohne Levelangabe") == -1


def test_level_of_ambiguity_and_boilerplate() -> None:
    # naming two levels is ambiguous: the gate must stand down
    assert level_of("Egal ob Junior oder Senior bei uns") == -1
    # boilerplate uses of level words are not job levels
    assert level_of("These lead to better pipelines") == -1
    assert level_of("Principal responsibilities include Python") == -1
    assert level_of("head quarters worldwide") == -1
    assert level_of("mid-market companies") == -1
    # real titles still count
    assert level_of("Staff Data Engineer gesucht") == 4
    assert level_of("Head of Engineering") == 4


def test_hard_mismatch_only_for_two_clear_levels() -> None:
    assert hard_mismatch("Junior Data Engineer", SENIOR_JD) is True
    assert hard_mismatch(SENIOR_JD, SENIOR_JD_REWRITTEN) is False
    assert hard_mismatch("Data Engineer (m/w/d)", SENIOR_JD) is False  # previous-only level


# ── recommendation ────────────────────────────────────────────────────


def test_recommend_jd_reuse_thresholds() -> None:
    high = recommend_jd_reuse(SENIOR_JD, SENIOR_JD)
    assert high["decision"] == "reuse" and high["reason"] == "high-similarity"

    low = recommend_jd_reuse(SENIOR_JD, MARKETING_JD)
    assert low["decision"] == "regenerate" and low["reason"] == "low-similarity"

    mid = recommend_jd_reuse(SENIOR_JD, SENIOR_JD_REWRITTEN)
    assert mid["decision"] in {"reuse", "reuse-with-edits"}

    gated = recommend_jd_reuse(
        "Junior Data Engineer Python PySpark SQL AWS Airflow Data Warehouse",
        SENIOR_JD,
    )
    assert gated == {"decision": "regenerate", "score": gated["score"],
                     "reason": "level-mismatch"}


# ── cross-CV discovery + copy ─────────────────────────────────────────


def _seed_cv(settings, key: str, *, source: str, job_id: str, jd: str) -> None:
    paths = cv_paths(settings, key)
    paths["tex"].write_text("% cv", encoding="utf-8")
    paths["pdf"].write_bytes(b"%PDF-1.4")
    paths["json"].write_text(
        json.dumps(
            {
                "key": key,
                "job": {"source": source, "job_id": job_id,
                        "company": f"C-{job_id}", "title": "Data Engineer"},
                "verification": {"verdict": "pass", "blockers": [], "warnings": []},
                "tailoring": {"coverage": {"coverage_pct": 42.0}},
                "pages": 2,
                "ats": {"score": 71, "grade": "B"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from jobscout.models import Job

    save_job_description(
        settings,
        Job(company=f"C-{job_id}", title="Data Engineer", link="https://x.invalid",
            source=source, job_id=job_id),
        jd,
    )


def test_recommend_cv_reuse_finds_best_and_skips_self(tmp_path: Path) -> None:
    from jobscout.models import Job

    settings = make_settings(tmp_path)
    _seed_cv(settings, "linkedin-src", source="linkedin", job_id="src", jd=SENIOR_JD)
    _seed_cv(settings, "linkedin-other", source="linkedin", job_id="other", jd=MARKETING_JD)

    target = Job(company="Nova AI", title="Data Engineer", link="https://x.invalid",
                 source="linkedin", job_id="src")  # same job -> self-skip
    check = recommend_cv_reuse(settings, target)
    assert check.jd_available is True
    assert [c.key for c in check.candidates] == ["linkedin-other"]  # self excluded

    target2 = Job(company="Nova AI", title="Data Engineer", link="https://x.invalid",
                  source="linkedin", job_id="new")
    save_job_description(settings, target2, SENIOR_JD_REWRITTEN)
    check2 = recommend_cv_reuse(settings, target2)
    best = check2.best
    assert best is not None and best.key in {"linkedin-src", "linkedin-other"}
    scores = {c.key: c.score for c in check2.candidates}
    assert scores["linkedin-src"] > scores["linkedin-other"]
    assert best.key == "linkedin-src"

    # No JD anywhere and no snippet: nothing to compare.
    bare = Job(company="X", title="Y", link="https://x.invalid",
               source="linkedin", job_id="bare")
    check3 = recommend_cv_reuse(settings, bare)
    assert check3.jd_available is False and check3.candidates == []


def test_reuse_cv_copies_and_restamps_provenance(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _seed_cv(settings, "linkedin-src", source="linkedin", job_id="src", jd=SENIOR_JD)

    job = make_job("99")
    result = reuse_cv(settings, job, "linkedin-src")

    assert result.key == "linkedin-99" and result.reused is True
    assert result.verdict == "pass" and result.coverage_pct == 42.0
    assert result.pages == 2 and result.ats_score == 71 and result.ats_grade == "B"
    target = cv_paths(settings, "linkedin-99")
    assert target["tex"].read_text(encoding="utf-8") == "% cv"
    assert target["pdf"].exists()

    meta = json.loads(target["json"].read_text(encoding="utf-8"))
    assert meta["job"] == {"source": "linkedin", "job_id": "99",
                           "company": job.company, "title": job.title, "link": job.link}
    assert meta["reused"] is True
    assert meta["reused_from"]["key"] == "linkedin-src"
    assert meta["reused_from"]["job_id"] == "src"

    # The source CV keeps living under its own key.
    source_meta = json.loads(cv_paths(settings, "linkedin-src")["json"].read_text("utf-8"))
    assert source_meta["job"]["job_id"] == "src" and "reused_from" not in source_meta

    # Reusing a key that was never built is refused.
    try:
        reuse_cv(settings, job, "linkedin-nope")
        raise AssertionError("expected CVBuildError")
    except CVBuildError as exc:
        assert "linkedin-nope" in str(exc)
