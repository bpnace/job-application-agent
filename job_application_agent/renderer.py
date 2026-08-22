from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html
import re
from pathlib import Path
from typing import Literal, cast

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .cover_letter import display_role_title
from .document_style import CLASSIC_INK, CLASSIC_MUTED, CLASSIC_PAPER, CLASSIC_RULE
from .document_theme import DocumentTheme, resolve_document_theme
from .models import CandidateProfile, JobListing


@dataclass(frozen=True)
class PdfRenderResult:
    renderer: Literal["reportlab", "playwright", "fallback"]
    message: str = ""
    accent_color: str = ""
    theme_source: str = ""


def markdown_to_basic_html(markdown: str) -> str:
    lines = markdown.splitlines()
    html_lines: list[str] = []
    in_paragraph: list[str] = []

    def flush() -> None:
        if in_paragraph:
            text = " ".join(in_paragraph)
            text = re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', html.escape(text))
            html_lines.append(f"<p>{text}</p>")
            in_paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("# "):
            flush()
            html_lines.append(f"<h2>{html.escape(stripped[2:])}</h2>")
        else:
            in_paragraph.append(stripped)
    flush()
    return "\n".join(html_lines)


def render_cover_letter_html(
    profile: CandidateProfile,
    listing: JobListing,
    markdown: str,
    output_path: Path,
    *,
    theme: DocumentTheme | None = None,
) -> None:
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("cover_letter.html.j2")
    body_html = markdown_to_basic_html(markdown)
    document_theme = theme or resolve_document_theme()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        template.render(
            title=f"{profile.name} - {listing.title}",
            language=listing.language if listing.language in {"de", "en"} else "de",
            candidate_name=profile.name,
            candidate_email=profile.email,
            candidate_role=profile.summary[:96],
            candidate_contact=[
                item
                for item in [
                    profile.email,
                    profile.phone,
                    profile.location,
                    _display_url(profile.linkedin),
                    _display_url(profile.github),
                ]
                if item
            ],
            listing_company=listing.company or "Recruiting Team",
            letter_date=_german_today(profile.location),
            accent_color=document_theme.accent_color,
            body_html=body_html,
        ),
        encoding="utf-8",
    )


def render_pdf_from_html(html_path: Path, pdf_path: Path) -> PdfRenderResult:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _write_minimal_pdf(pdf_path, html_path.read_text(encoding="utf-8", errors="ignore"))
        return PdfRenderResult(renderer="fallback", message=f"Playwright import unavailable: {exc}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.pdf(path=str(pdf_path), format="A4", print_background=True)
            browser.close()
        return PdfRenderResult(renderer="playwright")
    except PlaywrightError as exc:
        _write_minimal_pdf(pdf_path, html_path.read_text(encoding="utf-8", errors="ignore"))
        return PdfRenderResult(renderer="fallback", message=f"Playwright PDF unavailable: {exc}")


def render_cv_matched_cover_letter_pdf(
    profile: CandidateProfile,
    listing: JobListing,
    markdown: str,
    pdf_path: Path,
    *,
    theme: DocumentTheme | None = None,
) -> PdfRenderResult:
    """Render an A4 cover letter using the exact visual language of the CV."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        _write_minimal_pdf(pdf_path, markdown)
        return PdfRenderResult(renderer="fallback", message=f"ReportLab import unavailable: {exc}")

    document_theme = theme or resolve_document_theme()
    accent = colors.HexColor(document_theme.accent_color)
    ink = colors.HexColor(CLASSIC_INK)
    muted = colors.HexColor(CLASSIC_MUTED)
    rule = colors.HexColor(CLASSIC_RULE)
    paper = colors.HexColor(CLASSIC_PAPER)
    regular_font = "Times-Roman"
    bold_font = "Times-Bold"

    doc_data = _cover_letter_doc_data(profile, listing, markdown)

    class Header(Flowable):
        def __init__(self, data: dict[str, object], width: float):
            super().__init__()
            self.data = data
            self.width = width
            self.height = 44 * mm

        def wrap(self, aW: float, aH: float):
            _ = aH
            self.width = aW
            return aW, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setFillColor(accent)
            canvas.setFont("Helvetica-Bold", 7.2)
            canvas.drawString(0, 34 * mm, "BEWERBUNG")
            canvas.setFillColor(ink)
            canvas.setFont(bold_font, 23)
            canvas.drawString(0, 24 * mm, str(self.data["sender_name"]))
            canvas.setFont("Times-Italic", 10.6)
            canvas.setFillColor(muted)
            canvas.drawString(0, 17.5 * mm, str(self.data["sender_role"]))
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(0.8)
            canvas.line(0, 8.5 * mm, self.width, 8.5 * mm)
            canvas.setFillColor(muted)
            canvas.setFont("Helvetica", 8.0)
            y = 31 * mm
            for line in cast(list[str], self.data["contact"]):
                canvas.drawRightString(self.width, y, str(line))
                y -= 4.15 * mm
            canvas.restoreState()

    class RecipientDateSubject(Flowable):
        def __init__(self, data: dict[str, object], width: float):
            super().__init__()
            self.data = data
            self.width = width
            self.height = 42 * mm

        def wrap(self, aW: float, aH: float):
            _ = aH
            self.width = aW
            return aW, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", 7.0)
            canvas.setFillColor(accent)
            canvas.drawString(0, self.height - 7 * mm, "AN")
            canvas.setFont(regular_font, 10.0)
            canvas.setFillColor(muted)
            y = self.height - 13 * mm
            for line in cast(list[str], self.data["recipient"]):
                canvas.drawString(0, y, str(line))
                y -= 5.0 * mm
            canvas.setFont("Helvetica", 8.2)
            canvas.drawRightString(self.width, self.height - 13 * mm, str(self.data["date"]))
            canvas.setFillColor(accent)
            subject = str(self.data["subject"])
            font_size = _fit_font_size(subject, bold_font, 12.4, self.width, pdfmetrics)
            canvas.setFont(bold_font, font_size)
            canvas.drawString(0, 5.3 * mm, subject)
            canvas.restoreState()

    def draw_frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(paper)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.45)
        canvas.line(doc.leftMargin, 12 * mm, A4[0] - doc.rightMargin, 12 * mm)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7.1)
        canvas.drawString(doc.leftMargin, 8 * mm, "ANSCHREIBEN")
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=10.1,
        leading=15.6,
        textColor=ink,
        alignment=TA_LEFT,
        spaceAfter=7.2,
    )
    sign_style = ParagraphStyle(
        "Sign",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=10.1,
        leading=15.6,
        textColor=ink,
        alignment=TA_LEFT,
        spaceAfter=3.0,
    )
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=23 * mm,
        leftMargin=23 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title=str(doc_data["title"]),
        author=profile.name,
    )
    doc_width = A4[0] - doc.leftMargin - doc.rightMargin
    story = [
        Header(doc_data, doc_width),
        Spacer(1, 7 * mm),
        RecipientDateSubject(doc_data, doc_width),
        Spacer(1, 4 * mm),
    ]
    body = cast(list[str], doc_data["body"])
    for paragraph in body:
        style = sign_style if _is_signature_paragraph(str(paragraph)) else body_style
        story.append(Paragraph(_paragraph_markup(str(paragraph)), style))
    doc.build(story, onFirstPage=draw_frame, onLaterPages=draw_frame)
    return PdfRenderResult(
        renderer="reportlab",
        accent_color=document_theme.accent_color,
        theme_source=document_theme.source,
    )


def render_brutalist_cover_letter_pdf(
    profile: CandidateProfile,
    listing: JobListing,
    markdown: str,
    pdf_path: Path,
    *,
    theme: DocumentTheme | None = None,
) -> PdfRenderResult:
    """Compatibility alias for callers of the former renderer name."""
    return render_cv_matched_cover_letter_pdf(
        profile, listing, markdown, pdf_path, theme=theme
    )


def _fit_font_size(subject: str, font_name: str, max_size: float, width: float, pdfmetrics) -> float:
    size = max_size
    while size > 8.4 and pdfmetrics.stringWidth(subject, font_name, size) > width:
        size -= 0.2
    return size


def _cover_letter_doc_data(profile: CandidateProfile, listing: JobListing, markdown: str) -> dict[str, object]:
    subject, body = _extract_subject_and_body(markdown, listing)
    contact = [
        profile.location,
        profile.phone,
        profile.email,
        _display_url(profile.github),
        _display_url(profile.linkedin),
    ]
    return {
        "title": f"{profile.name} Anschreiben {listing.company} {display_role_title(listing.title)}",
        "sender_name": profile.name,
        "sender_role": profile.summary[:96],
        "contact": [item for item in contact if item],
        "recipient": [listing.company or "Recruiting Team", "Recruiting Team"],
        "date": _german_today(profile.location),
        "subject": subject,
        "body": body,
    }


def _extract_subject_and_body(markdown: str, listing: JobListing) -> tuple[str, list[str]]:
    subject = f"Bewerbung als {listing.title}" if listing.language == "de" else f"Application for {listing.title}"
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    body: list[str] = []
    for block in blocks:
        if block.startswith("# "):
            subject = block[2:].strip()
            continue
        if block.startswith("## "):
            subject = block[3:].strip()
            continue
        body.append(block)
    return subject, body


def _paragraph_markup(paragraph: str) -> str:
    return "<br/>".join(html.escape(line.strip()) for line in paragraph.splitlines() if line.strip())


def _is_signature_paragraph(paragraph: str) -> bool:
    lines = [line.strip().casefold() for line in paragraph.splitlines() if line.strip()]
    if not lines:
        return False
    signoffs = {
        "kind regards",
        "best regards",
        "regards",
        "many thanks",
        "viele grüße",
        "viele grüsse",
        "mit freundlichen grüßen",
        "mit freundlichen grüssen",
        "beste grüße",
        "beste grüsse",
    }
    return lines[0] in signoffs


def _display_url(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").rstrip("/")


def _german_today(location: str = "") -> str:
    months = {
        1: "Januar",
        2: "Februar",
        3: "März",
        4: "April",
        5: "Mai",
        6: "Juni",
        7: "Juli",
        8: "August",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Dezember",
    }
    today = datetime.now().date()
    prefix = f"{location}, " if location else ""
    return f"{prefix}{today.day}. {months[today.month]} {today.year}"


def _write_minimal_pdf(path: Path, text: str) -> None:
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain)[:1800]
    escaped = plain.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 50 780 Td ({escaped}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('utf-8'))} >> stream\n{stream}\nendstream endobj",
    ]
    content = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(content.encode("utf-8")))
        content += obj + "\n"
    xref_start = len(content.encode("utf-8"))
    content += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        content += f"{offset:010d} 00000 n \n"
    content += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
    path.write_bytes(content.encode("latin-1", errors="replace"))
