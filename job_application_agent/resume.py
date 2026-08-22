from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .config import default_profile_path
from .document_style import (
    CLASSIC_INK,
    CLASSIC_MUTED,
    CLASSIC_PAPER,
    CLASSIC_RULE,
    DEFAULT_ACCENT_COLOR,
)
from .document_names import filename_part
from .models import CandidateProfile
from .profile import _resolve_path, candidate_document_paths, load_candidate_profile
from .utils import write_json


class ResumeExperience(BaseModel):
    role: str
    employer: str
    period: str
    location: str = ""
    highlights: list[str] = Field(default_factory=list)


class ResumeEducation(BaseModel):
    degree: str
    institution: str
    period: str
    location: str = ""
    details: list[str] = Field(default_factory=list)


class ResumeSkillGroup(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class ResumeLanguage(BaseModel):
    language: str
    level: str


class ResumeCertificate(BaseModel):
    name: str
    issuer: str = ""
    issued: str = ""
    credential_url: str = ""


class ResumeAttachment(BaseModel):
    label: str
    kind: str = "supporting_document"
    path: str
    include_in_bundle: bool = True


class ResumeData(BaseModel):
    """Private structured facts used to create a factual, minimal CV."""

    accent_color: str = DEFAULT_ACCENT_COLOR
    headline: str = ""
    summary: str = ""
    experience: list[ResumeExperience] = Field(default_factory=list)
    education: list[ResumeEducation] = Field(default_factory=list)
    skill_groups: list[ResumeSkillGroup] = Field(default_factory=list)
    languages: list[ResumeLanguage] = Field(default_factory=list)
    certificates: list[ResumeCertificate] = Field(default_factory=list)
    attachments: list[ResumeAttachment] = Field(default_factory=list)

    @field_validator("accent_color")
    @classmethod
    def accent_color_must_be_hex(cls, value: str) -> str:
        value = value.strip()
        if len(value) != 7 or not value.startswith("#") or any(
            character not in "0123456789abcdefABCDEF" for character in value[1:]
        ):
            raise ValueError(f"accent_color must be a CSS hex colour such as {DEFAULT_ACCENT_COLOR}.")
        return value.upper()


@dataclass(frozen=True)
class ResumeRenderResult:
    pdf_path: Path
    markdown_path: Path
    json_path: Path
    attachments_manifest_path: Path
    bundle_path: Path | None
    attachment_count: int


def load_resume_data(candidate_path: Path | None = None) -> ResumeData:
    """Load optional, local-only CV facts from the candidate configuration."""
    path = (candidate_path or default_profile_path()).expanduser().resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"candidate.yaml must be a YAML object: {path}")
    resume = raw.get("resume", {})
    if not isinstance(resume, dict):
        raise ValueError("candidate.yaml field `resume` must be an object.")
    return ResumeData.model_validate(resume)


def render_resume(
    *,
    candidate_path: Path | None = None,
    output_dir: Path | None = None,
    include_attachments: bool = False,
    replace_configured_cv: bool = False,
) -> ResumeRenderResult:
    """Create factual CV source files and a minimal professional PDF locally.

    The generated PDF becomes the configured CV only when no configured CV PDF
    currently exists. Replacing an existing configured CV needs an explicit
    flag; the existing source file is never deleted.
    """
    profile_path = (candidate_path or default_profile_path()).expanduser().resolve()
    profile = load_candidate_profile(profile_path)
    configured_paths = candidate_document_paths(profile_path)
    existing_cv = configured_paths["cv_pdf"]
    if existing_cv.is_file() and not replace_configured_cv:
        raise FileExistsError(
            "A CV PDF is already configured. Refusing to replace it; pass "
            "--replace-configured-cv to set the generated CV as the default."
        )

    resume = load_resume_data(profile_path)
    target_dir = (output_dir or profile_path.parent / "documents").expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    base_name = _resume_base_name(profile)
    markdown_path = target_dir / f"{base_name}.md"
    json_path = target_dir / f"{base_name}.json"
    pdf_path = target_dir / f"{base_name}.pdf"
    attachments_manifest_path = target_dir / f"{base_name}_anlagen.json"

    markdown_path.write_text(_format_resume_markdown(profile, resume), encoding="utf-8")
    write_json(json_path, {"profile": _resume_public_profile(profile), "resume": resume.model_dump(mode="json")})
    _render_resume_pdf(profile, resume, pdf_path)

    attachments = _attachment_manifest(resume, profile_path)
    write_json(attachments_manifest_path, {"attachments": attachments})
    bundle_path = None
    if include_attachments:
        bundle_path = target_dir / f"Bewerbungsunterlagen_{_last_name(profile.name)}.pdf"
        _write_attachment_bundle(pdf_path, attachments, bundle_path)

    _set_generated_cv_as_default(profile_path, markdown_path, pdf_path)
    return ResumeRenderResult(
        pdf_path=pdf_path,
        markdown_path=markdown_path,
        json_path=json_path,
        attachments_manifest_path=attachments_manifest_path,
        bundle_path=bundle_path,
        attachment_count=len(attachments),
    )


def _resume_base_name(profile: CandidateProfile) -> str:
    return f"Lebenslauf_{filename_part(_last_name(profile.name))}"


def _last_name(name: str) -> str:
    parts = [part for part in name.split() if part.strip()]
    return filename_part(parts[-1] if parts else name)


def _resume_public_profile(profile: CandidateProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "github": profile.github,
        "linkedin": profile.linkedin,
        "summary": profile.summary,
        "core_skills": profile.core_skills,
        "proof_points": profile.proof_points,
    }


def _format_resume_markdown(profile: CandidateProfile, resume: ResumeData) -> str:
    lines = [f"# {profile.name}", ""]
    if resume.headline or profile.summary:
        lines.extend([resume.headline or profile.summary, ""])
    contact = [profile.location, profile.phone, profile.email, profile.github, profile.linkedin]
    lines.extend([" · ".join(item for item in contact if item), ""])

    summary = resume.summary or profile.summary
    if summary:
        lines.extend(["## Profil", summary, ""])
    if resume.experience:
        lines.append("## Berufserfahrung")
        for entry in resume.experience:
            location = f", {entry.location}" if entry.location else ""
            lines.extend([f"### {entry.role} · {entry.employer}", f"{entry.period}{location}"])
            lines.extend(f"- {highlight}" for highlight in entry.highlights)
            lines.append("")
    if resume.education:
        lines.append("## Ausbildung")
        for entry in resume.education:
            location = f", {entry.location}" if entry.location else ""
            lines.extend([f"### {entry.degree} · {entry.institution}", f"{entry.period}{location}"])
            lines.extend(f"- {detail}" for detail in entry.details)
            lines.append("")
    skill_groups = resume.skill_groups or ([ResumeSkillGroup(label="Kompetenzen", items=profile.core_skills)] if profile.core_skills else [])
    if skill_groups:
        lines.append("## Kompetenzen")
        lines.extend(f"- **{group.label}:** {', '.join(group.items)}" for group in skill_groups if group.items)
        lines.append("")
    if profile.proof_points:
        lines.extend(["## Ausgewählte Nachweise", *[f"- {item}" for item in profile.proof_points], ""])
    if resume.languages:
        lines.extend(["## Sprachen", *[f"- {item.language}: {item.level}" for item in resume.languages], ""])
    if resume.certificates:
        lines.append("## Zertifikate")
        for certificate in resume.certificates:
            parts = [certificate.name, certificate.issuer, certificate.issued, certificate.credential_url]
            lines.append("- " + " · ".join(item for item in parts if item))
        lines.append("")
    if resume.attachments:
        lines.extend(["## Anlagen", *[f"- {item.label} ({item.kind})" for item in resume.attachments], ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_resume_pdf(profile: CandidateProfile, resume: ResumeData, output_path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:  # pragma: no cover - package dependency contract
        raise RuntimeError("ReportLab is required to render CV PDFs.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    accent = colors.HexColor(resume.accent_color)
    ink = colors.HexColor(CLASSIC_INK)
    muted = colors.HexColor(CLASSIC_MUTED)
    rule = colors.HexColor(CLASSIC_RULE)
    paper = colors.HexColor(CLASSIC_PAPER)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=23 * mm,
        rightMargin=23 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title=f"Lebenslauf {profile.name}",
        author=profile.name,
    )
    width = A4[0] - doc.leftMargin - doc.rightMargin

    class Header(Flowable):
        def __init__(self) -> None:
            super().__init__()
            self.height = 44 * mm

        def wrap(self, aW: float, aH: float):
            _ = aH
            return aW, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setFillColor(accent)
            canvas.setFont("Helvetica-Bold", 7.2)
            canvas.drawString(0, 34 * mm, "CURRICULUM VITAE")
            canvas.setFillColor(ink)
            canvas.setFont("Times-Bold", 23)
            canvas.drawString(0, 24 * mm, profile.name)
            canvas.setFont("Times-Italic", 10.6)
            canvas.setFillColor(muted)
            canvas.drawString(0, 17.5 * mm, resume.headline or profile.summary[:92])
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(0.8)
            canvas.line(0, 8.5 * mm, width, 8.5 * mm)
            canvas.setFillColor(muted)
            canvas.setFont("Helvetica", 8.0)
            y = 31 * mm
            for item in [profile.email, profile.phone, profile.location, _display_url(profile.linkedin), _display_url(profile.github)]:
                if item:
                    canvas.drawRightString(width, y, item)
                    y -= 4.15 * mm
            canvas.restoreState()

    styles = getSampleStyleSheet()
    section_style = ParagraphStyle(
        "ResumeSection",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.6,
        leading=10.4,
        textColor=accent,
        spaceBefore=10.5,
        spaceAfter=4.6,
        uppercase=True,
    )
    body_style = ParagraphStyle(
        "ResumeBody",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=10.0,
        leading=15.0,
        textColor=ink,
        spaceAfter=5.2,
    )
    detail_style = ParagraphStyle(
        "ResumeDetail",
        parent=body_style,
        fontName="Helvetica",
        fontSize=8.3,
        leading=12.0,
        textColor=muted,
        spaceAfter=3.6,
    )
    title_style = ParagraphStyle(
        "ResumeEntryTitle",
        parent=body_style,
        fontName="Times-Bold",
        fontSize=10.5,
        leading=13.4,
        spaceAfter=2.2,
    )
    def paragraph(value: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_markup(value), style)

    story: list[Any] = [Header(), Spacer(1, 5 * mm)]
    summary = resume.summary or profile.summary
    if summary:
        story.extend([paragraph("Profil", section_style), paragraph(summary, body_style)])
    if resume.experience:
        story.append(paragraph("Berufserfahrung", section_style))
        for entry in resume.experience:
            location = f" · {entry.location}" if entry.location else ""
            parts: list[Any] = [
                paragraph(f"{entry.role} · {entry.employer}", title_style),
                paragraph(f"{entry.period}{location}", detail_style),
            ]
            parts.extend(paragraph(f"• {highlight}", body_style) for highlight in entry.highlights)
            story.append(KeepTogether(parts))
    if resume.education:
        story.append(paragraph("Ausbildung", section_style))
        for entry in resume.education:
            location = f" · {entry.location}" if entry.location else ""
            parts = [
                paragraph(f"{entry.degree} · {entry.institution}", title_style),
                paragraph(f"{entry.period}{location}", detail_style),
            ]
            parts.extend(paragraph(f"• {detail}", body_style) for detail in entry.details)
            story.append(KeepTogether(parts))
    skill_groups = resume.skill_groups or ([ResumeSkillGroup(label="Kompetenzen", items=profile.core_skills)] if profile.core_skills else [])
    if skill_groups:
        story.append(paragraph("Kompetenzen", section_style))
        for group in skill_groups:
            if group.items:
                story.append(paragraph(f"{group.label}: {', '.join(group.items)}", body_style))
    if profile.proof_points:
        story.append(paragraph("Ausgewählte Nachweise", section_style))
        story.extend(paragraph(f"• {item}", body_style) for item in profile.proof_points)
    if resume.languages:
        story.append(paragraph("Sprachen", section_style))
        story.append(paragraph(" · ".join(f"{item.language}: {item.level}" for item in resume.languages), body_style))
    if resume.certificates:
        story.append(paragraph("Zertifikate", section_style))
        for certificate in resume.certificates:
            meta = " · ".join(item for item in [certificate.issuer, certificate.issued] if item)
            value = certificate.name + (f" — {meta}" if meta else "")
            story.append(paragraph(value, body_style))
            if certificate.credential_url:
                story.append(paragraph(certificate.credential_url, detail_style))
    if resume.attachments:
        story.append(paragraph("Anlagen", section_style))
        story.append(paragraph(" · ".join(item.label for item in resume.attachments), body_style))

    def draw_frame(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(paper)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.45)
        canvas.line(doc.leftMargin, 12 * mm, A4[0] - doc.rightMargin, 12 * mm)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7.1)
        canvas.drawString(doc.leftMargin, 8 * mm, "LEBENSLAUF")
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_frame, onLaterPages=draw_frame)


def _attachment_manifest(resume: ResumeData, candidate_path: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for attachment in resume.attachments:
        path = _resolve_path(attachment.path, candidate_path)
        if not path.is_file():
            raise FileNotFoundError(f"Supporting document is missing: {attachment.label} ({path})")
        manifest.append(
            {
                "label": attachment.label,
                "kind": attachment.kind,
                "path": str(path),
                "sha256": _sha256(path),
                "include_in_bundle": attachment.include_in_bundle,
            }
        )
    return manifest


def _write_attachment_bundle(cv_pdf: Path, attachments: list[dict[str, Any]], output_path: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("pypdf is required to create an attachment bundle.") from exc

    writer = PdfWriter()
    writer.append(PdfReader(str(cv_pdf)))
    for attachment in attachments:
        if not attachment["include_in_bundle"]:
            continue
        path = Path(str(attachment["path"]))
        if path.suffix.casefold() != ".pdf":
            raise ValueError(
                f"Only PDF supporting documents can be bundled: {attachment['label']} ({path.name})"
            )
        writer.append(PdfReader(str(path)))
    writer.add_metadata({"/Title": "Bewerbungsunterlagen", "/Author": "Job Application Agent"})
    with output_path.open("wb") as stream:
        writer.write(stream)


def _set_generated_cv_as_default(candidate_path: Path, markdown_path: Path, pdf_path: Path) -> None:
    raw = yaml.safe_load(candidate_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"candidate.yaml must be a YAML object: {candidate_path}")
    documents = raw.setdefault("documents", {})
    if not isinstance(documents, dict):
        raise ValueError("candidate.yaml field `documents` must be an object.")
    documents["cv_text_path"] = _relative_path(markdown_path, candidate_path.parent)
    documents["cv_pdf_path"] = _relative_path(pdf_path, candidate_path.parent)
    candidate_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_url(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").rstrip("/")


def _markup(value: str) -> str:
    return html.escape(value).replace("\n", "<br/>")
