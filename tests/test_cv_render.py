import shutil
from pathlib import Path

import pytest
from test_cv_master import make_profile

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import CVBullet, from_master, parse_payload
from jobscout.cv.render import estimate_pages, render_cv, tex_text
from jobscout.cover_letter.latex import compile_pdf

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
TECTONIC = shutil.which("tectonic")


def make_payload(**overrides):
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master)
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def test_tex_text_normalises_then_escapes() -> None:
    escaped = tex_text("100% & 20$ \\ #_{~^")
    assert "\\%" in escaped and "\\&" in escaped and "\\$" in escaped
    assert "\\textbackslash{}" in escaped
    assert tex_text("Müller").startswith("Müller")
    assert "\u2014" not in tex_text("a\u2014b")  # em dash folded to ASCII
    assert "\u201e" not in tex_text("\u201ezeit\u201c")


def test_escaping_protects_the_document() -> None:
    payload = make_payload()
    payload.summary = "100% & mehr: Kosten um 20$ gesenkt #1 {test} _x y^z ~w"
    tex = render_cv(TEMPLATES, payload, language="de")
    body = tex[tex.index("\\begin{document}") :]
    assert "100\\%" in body
    assert "\\&" in body
    assert "\\#1" in body
    # Nothing unescaped that would change LaTeX meaning.
    assert " {test}" not in body
    assert "\\textbackslash{}" not in body  # no backslash in this input


def test_render_contains_all_master_sections() -> None:
    payload = make_payload()
    tex = render_cv(TEMPLATES, payload, language="de")
    for fragment in (
        "Anna Beispiel",
        "AI Engineer",
        "Reportingsystem",
        "TU Berlin",
        "Berufserfahrung",
        "Deutsch",
    ):
        assert fragment in tex


def test_english_headings_and_babel() -> None:
    payload = make_payload()
    payload.meta.language = "en"
    tex = render_cv(TEMPLATES, payload)
    assert "[english]" in tex
    assert "Experience" in tex and "Berufserfahrung" not in tex


def test_gaps_never_reach_the_template() -> None:
    payload = make_payload()
    payload.gaps = parse_payload({"gaps": [{"term": "Kubernetes"}]}).gaps
    tex = render_cv(TEMPLATES, payload, language="de")
    assert "Kubernetes" not in tex


def test_estimate_pages_grows_with_content() -> None:
    small = make_payload()
    assert estimate_pages(small) == 1
    big = make_payload()
    role = big.experience[0]
    for index in range(40):
        role.bullets.append(
            CVBullet(
                text=f"Langes Achievement mit vielen Wortern Nummer {index} "
                "und einem langen Satz, damit die Seite wirklich voll wird"
            )
        )
    assert estimate_pages(big) > 1


@pytest.mark.skipif(TECTONIC is None, reason="tectonic is not installed")
def test_compiles_to_a_one_page_pdf(tmp_path: Path) -> None:
    payload = make_payload()
    tex = render_cv(TEMPLATES, payload, language="de")
    tex_path = tmp_path / "cv.tex"
    tex_path.write_text(tex, encoding="utf-8")
    pdf_path, error = compile_pdf(tex_path)
    assert error is None, error
    assert pdf_path is not None and pdf_path.exists() and pdf_path.stat().st_size > 1000
