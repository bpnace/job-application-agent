"""Resolve the private visual theme shared by CV and cover-letter documents."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import colorsys
import re
from typing import Literal

from .config import default_profile_path
from .document_style import DEFAULT_ACCENT_COLOR
from .profile import configured_cv_pdf_path
from .resume import load_resume_data


@dataclass(frozen=True)
class DocumentTheme:
    """A local visual theme without any candidate content."""

    accent_color: str
    source: Literal["cv_pdf", "resume_config", "default"]


def resolve_document_theme(
    *,
    cv_pdf_path: Path | None = None,
    candidate_path: Path | None = None,
) -> DocumentTheme:
    """Use a supplied CV's visible vector accent before local configuration.

    PDFs that are scans or contain no saturated vector colour intentionally fall
    back to ``resume.accent_color``. The value is private configuration and is
    never written into the repository.
    """
    cv_pdf = cv_pdf_path or configured_cv_pdf_path(candidate_path)
    if cv_pdf is None:
        cv_pdf = _discover_supplied_cv_pdf(candidate_path)
    if cv_pdf is not None:
        accent_color = extract_pdf_accent_color(cv_pdf)
        if accent_color:
            return DocumentTheme(accent_color=accent_color, source="cv_pdf")
    try:
        accent_color = load_resume_data(candidate_path).accent_color
    except (FileNotFoundError, ValueError):
        accent_color = DEFAULT_ACCENT_COLOR
        source: Literal["resume_config", "default"] = "default"
    else:
        source = "resume_config"
    return DocumentTheme(
        accent_color=_normalise_hex_color(accent_color) or DEFAULT_ACCENT_COLOR,
        source=source,
    )


def _discover_supplied_cv_pdf(candidate_path: Path | None) -> Path | None:
    """Find one unambiguously supplied CV in the local agent documents folder.

    This covers the common first-run case where a person copies a PDF into
    ``.job-agent/documents`` before they have selected it in ``candidate.yaml``.
    It intentionally does not guess when several arbitrary PDFs are present:
    certificates and portfolios must never determine the document design.
    """
    profile_path = (candidate_path or default_profile_path()).expanduser().resolve()
    documents_dir = profile_path.parent / "documents"
    if not documents_dir.is_dir():
        return None
    pdfs = sorted(
        (path for path in documents_dir.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf"),
        key=lambda path: path.name.casefold(),
    )
    named_cvs = [
        path
        for path in pdfs
        if re.search(r"(?:^|[-_ ])(?:cv|lebenslauf|resume)(?:[-_ .]|$)", path.stem, re.IGNORECASE)
    ]
    if len(named_cvs) == 1:
        return named_cvs[0]
    return pdfs[0] if len(pdfs) == 1 else None


def extract_pdf_accent_color(pdf_path: Path) -> str:
    """Return the most used saturated RGB fill/stroke colour on page one.

    This deliberately examines only PDF drawing operations. It avoids OCR,
    does not export CV contents and never uploads the supplied document.
    """
    try:
        from pypdf import PdfReader
        from pypdf.generic import ContentStream

        reader = PdfReader(str(pdf_path), strict=False)
        if not reader.pages:
            return ""
        content = ContentStream(reader.pages[0].get_contents(), reader)
    except Exception:
        return ""

    colours: Counter[tuple[int, int, int]] = Counter()
    for operands, operator in content.operations:
        if operator not in {b"rg", b"RG"} or len(operands) != 3:
            continue
        try:
            red, green, blue = (float(value) for value in operands)
        except (TypeError, ValueError):
            continue
        if not _is_candidate_accent(red, green, blue):
            continue
        colours[(_channel(red), _channel(green), _channel(blue))] += 1
    if not colours:
        return ""
    red, green, blue = colours.most_common(1)[0][0]
    return f"#{red:02X}{green:02X}{blue:02X}"


def _is_candidate_accent(red: float, green: float, blue: float) -> bool:
    if any(value < 0 or value > 1 for value in (red, green, blue)):
        return False
    _hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    return saturation >= 0.22 and 0.18 <= value <= 0.92


def _channel(value: float) -> int:
    return max(0, min(255, round(value * 255)))


def _normalise_hex_color(value: str) -> str:
    cleaned = value.strip().upper()
    return cleaned if re.fullmatch(r"#[0-9A-F]{6}", cleaned) else ""
