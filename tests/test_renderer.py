from pypdf import PdfReader

from job_application_agent.document_style import DEFAULT_ACCENT_COLOR
from job_application_agent.document_theme import DocumentTheme, extract_pdf_accent_color
from job_application_agent.models import CandidateProfile, JobListing
from job_application_agent.renderer import (
    _is_signature_paragraph,
    render_cover_letter_html,
    render_cv_matched_cover_letter_pdf,
)


def test_signature_style_applies_only_to_actual_signoff():
    assert not _is_signature_paragraph(
        "I can tell you more in person about how I would contribute to Pandata."
    )
    assert _is_signature_paragraph("Kind regards\nTest Candidate")
    assert _is_signature_paragraph("Viele Grüße\nTest Candidate")


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.test",
        location="Berlin, Deutschland",
        phone="+49 30 000000",
        github="https://github.com/example",
        linkedin="https://www.linkedin.com/in/example",
        summary="Product designer for understandable workflows.",
        core_skills=["Research"],
        proof_points=["Test evidence"],
        cv_excerpt="CV",
        humanizer_excerpt="Policy",
    )


def _listing() -> JobListing:
    return JobListing(
        source="fixture",
        source_url="https://example.test/jobs/1",
        title="Product Designer",
        company="Example GmbH",
        language="de",
    )


def test_cv_matched_cover_letter_uses_shared_accent_in_pdf_and_html(tmp_path):
    theme = DocumentTheme(accent_color=DEFAULT_ACCENT_COLOR, source="default")
    markdown = "# Bewerbung als Product Designer\n\nHallo Example-Team,\n\nViele Grüße\nTest Candidate"
    pdf_path = tmp_path / "cover.pdf"
    html_path = tmp_path / "cover.html"

    result = render_cv_matched_cover_letter_pdf(
        _profile(), _listing(), markdown, pdf_path, theme=theme
    )
    render_cover_letter_html(_profile(), _listing(), markdown, html_path, theme=theme)

    assert result.renderer == "reportlab"
    assert result.accent_color == DEFAULT_ACCENT_COLOR
    assert result.theme_source == "default"
    assert PdfReader(str(pdf_path)).pages
    assert extract_pdf_accent_color(pdf_path) == DEFAULT_ACCENT_COLOR
    assert DEFAULT_ACCENT_COLOR in html_path.read_text(encoding="utf-8")
