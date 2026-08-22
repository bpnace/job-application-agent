from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen import canvas

from job_application_agent.document_theme import extract_pdf_accent_color, resolve_document_theme


def _accent_pdf(path: Path, accent: str) -> None:
    document = canvas.Canvas(str(path))
    document.setFillColor(colors.HexColor(accent))
    document.rect(48, 700, 500, 12, fill=1, stroke=0)
    document.setFillColor(colors.black)
    document.drawString(48, 680, "CV")
    document.save()


def test_extract_pdf_accent_color_uses_saturated_vector_accent(tmp_path):
    cv_pdf = tmp_path / "cv.pdf"
    _accent_pdf(cv_pdf, "#247BA0")

    assert extract_pdf_accent_color(cv_pdf) == "#247BA0"


def test_theme_prefers_provided_cv_accent_over_local_configuration(tmp_path):
    cv_pdf = tmp_path / "cv.pdf"
    _accent_pdf(cv_pdf, "#247BA0")

    theme = resolve_document_theme(cv_pdf_path=cv_pdf)

    assert theme.accent_color == "#247BA0"
    assert theme.source == "cv_pdf"


def test_theme_uses_default_when_supplied_cv_has_no_vector_accent(tmp_path):
    cv_pdf = tmp_path / "black-and-white.pdf"
    document = canvas.Canvas(str(cv_pdf))
    document.drawString(48, 700, "CV")
    document.save()

    theme = resolve_document_theme(
        cv_pdf_path=cv_pdf, candidate_path=tmp_path / "missing.yaml"
    )

    assert theme.accent_color == "#7A3E38"
    assert theme.source == "default"


def test_theme_discovers_one_clearly_named_cv_in_local_documents(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_CV_PDF_PATH", raising=False)
    candidate_path = tmp_path / "agent-home" / "candidate.yaml"
    documents = candidate_path.parent / "documents"
    documents.mkdir(parents=True)
    _accent_pdf(documents / "Lebenslauf_Muster.pdf", "#247BA0")

    theme = resolve_document_theme(candidate_path=candidate_path)

    assert theme.accent_color == "#247BA0"
    assert theme.source == "cv_pdf"
