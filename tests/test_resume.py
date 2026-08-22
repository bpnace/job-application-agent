from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from job_application_agent.application import build_form_fill_plan
from job_application_agent.document_style import DEFAULT_ACCENT_COLOR
from job_application_agent.models import ApplicationFormField, JobListing
from job_application_agent.package import mirror_final_documents
from job_application_agent.profile import configured_cv_pdf_path, load_candidate_profile
from job_application_agent.resume import render_resume


def _candidate_path(tmp_path: Path, *, with_existing_cv: bool = False) -> Path:
    home = tmp_path / "agent-home"
    documents = home / "documents"
    documents.mkdir(parents=True)
    policy = home / "humanizer" / "private.de.md"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "---\nversion: 1\nlanguage: de\nbanned_terms: []\nbanned_patterns: []\nreplacements: {}\n---\n\nPrivate policy.\n",
        encoding="utf-8",
    )
    cv_pdf = documents / "existing.pdf"
    if with_existing_cv:
        _write_pdf(cv_pdf, "Existing CV")
    profile = {
        "profile": {
            "name": "Max Mustermann",
            "email": "max@example.test",
            "location": "Berlin, Deutschland",
            "phone": "+49 30 123456",
            "github": "https://github.com/max",
            "linkedin": "https://linkedin.com/in/max",
            "summary": "Produktdesigner für verständliche digitale Werkzeuge.",
            "core_skills": ["Research", "Prototyping", "Figma"],
            "proof_points": ["Ein Self-Service-Flow reduzierte wiederkehrende Supportanfragen."],
        },
        "documents": {
            "cv_text_path": "documents/existing.md",
            "cv_pdf_path": "documents/existing.pdf",
        },
        "humanizer": {"private_policy_path": "humanizer/private.de.md"},
        "resume": {
            "accent_color": DEFAULT_ACCENT_COLOR,
            "headline": "Product Designer",
            "experience": [
                {
                    "role": "Product Designer",
                    "employer": "Beispiel GmbH",
                    "period": "2023 – heute",
                    "location": "Berlin",
                    "highlights": ["Nutzertests in den Produktprozess integriert."],
                }
            ],
            "education": [
                {
                    "degree": "B.A. Kommunikationsdesign",
                    "institution": "Beispielhochschule",
                    "period": "2018 – 2022",
                }
            ],
            "skill_groups": [{"label": "Produkt", "items": ["Discovery", "Prototyping"]}],
            "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
            "certificates": [{"name": "UX Certificate", "issuer": "Example Institute", "issued": "2025"}],
            "attachments": [],
        },
    }
    path = home / "candidate.yaml"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_pdf(path: Path, text: str) -> None:
    document = canvas.Canvas(str(path))
    document.drawString(72, 760, text)
    document.save()


def _clear_document_overrides(monkeypatch) -> None:
    for variable in [
        "JOB_AGENT_PROFILE_PATH",
        "JOB_AGENT_CV_PDF_PATH",
        "JOB_AGENT_CV_TEXT_PATH",
        "JOB_AGENT_HUMANIZER_PATH",
    ]:
        monkeypatch.delenv(variable, raising=False)


def test_render_resume_creates_professional_pdf_and_machine_readable_sources(tmp_path, monkeypatch):
    candidate_path = _candidate_path(tmp_path)
    _clear_document_overrides(monkeypatch)
    monkeypatch.setenv("JOB_AGENT_HOME", str(candidate_path.parent))
    result = render_resume(candidate_path=candidate_path)

    assert result.pdf_path.name == "Lebenslauf_Mustermann.pdf"
    assert result.pdf_path.is_file()
    assert result.markdown_path.is_file()
    assert result.json_path.is_file()
    assert PdfReader(str(result.pdf_path)).pages
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Product Designer" in markdown
    assert "Zertifikate" in markdown
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["resume"]["accent_color"] == DEFAULT_ACCENT_COLOR

    candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    assert candidate["documents"]["cv_pdf_path"] == "documents/Lebenslauf_Mustermann.pdf"
    assert candidate["documents"]["cv_text_path"] == "documents/Lebenslauf_Mustermann.md"


def test_cv_pdf_environment_override_works_before_local_setup(tmp_path, monkeypatch):
    cv_pdf = tmp_path / "cv.pdf"
    _write_pdf(cv_pdf, "CV")
    monkeypatch.setenv("JOB_AGENT_PROFILE_PATH", str(tmp_path / "missing-candidate.yaml"))
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))

    assert configured_cv_pdf_path() == cv_pdf


def test_generated_cv_becomes_default_for_upload_plans_and_company_folder(tmp_path, monkeypatch):
    candidate_path = _candidate_path(tmp_path)
    _clear_document_overrides(monkeypatch)
    monkeypatch.setenv("JOB_AGENT_HOME", str(candidate_path.parent))
    result = render_resume(candidate_path=candidate_path)
    profile = load_candidate_profile(candidate_path)
    listing = JobListing(
        source="fixture",
        source_url="https://jobs.example.test/product-designer",
        apply_url="https://jobs.example.test/product-designer/apply",
        title="Product Designer",
        company="Beispiel GmbH",
    )
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    plan = build_form_fill_plan(
        profile,
        listing,
        package_dir=package_dir,
        fields=[
            ApplicationFormField(
                label="Lebenslauf",
                selector="#resume",
                field_type="file",
                classification="resume_upload",
                required=True,
            ),
            ApplicationFormField(
                label="Anschreiben",
                selector="#letter",
                field_type="file",
                classification="cover_letter_upload",
                required=True,
            ),
        ],
    )
    resume_instruction = next(
        item for item in plan.instructions if item.classification == "resume_upload"
    )
    assert resume_instruction.action == "upload"
    assert resume_instruction.file_path == str(result.pdf_path)

    job_path = package_dir / "job.json"
    scorecard_path = package_dir / "scorecard.md"
    cover_md_path = package_dir / "cover_letter.md"
    job_path.write_text("{}", encoding="utf-8")
    scorecard_path.write_text("# Score\n", encoding="utf-8")
    cover_md_path.write_text("# Anschreiben\n", encoding="utf-8")
    target_dir = mirror_final_documents(
        profile=profile,
        listing=listing,
        package_dir=package_dir,
        job_path=job_path,
        scorecard_path=scorecard_path,
        cover_md_path=cover_md_path,
        cover_pdf_path=package_dir / "cover_letter.pdf",
    )
    assert (target_dir / result.pdf_path.name).is_file()


def test_render_resume_refuses_to_replace_existing_cv_without_explicit_flag(tmp_path, monkeypatch):
    candidate_path = _candidate_path(tmp_path, with_existing_cv=True)
    _clear_document_overrides(monkeypatch)
    monkeypatch.setenv("JOB_AGENT_HOME", str(candidate_path.parent))

    with pytest.raises(FileExistsError, match="replace-configured-cv"):
        render_resume(candidate_path=candidate_path)

    result = render_resume(candidate_path=candidate_path, replace_configured_cv=True)
    assert result.pdf_path.is_file()
    assert (candidate_path.parent / "documents" / "existing.pdf").is_file()


def test_render_resume_can_create_optional_pdf_attachment_bundle(tmp_path, monkeypatch):
    candidate_path = _candidate_path(tmp_path)
    _clear_document_overrides(monkeypatch)
    attachment = candidate_path.parent / "documents" / "Arbeitszeugnis.pdf"
    _write_pdf(attachment, "Arbeitszeugnis")
    candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    candidate["resume"]["attachments"] = [
        {
            "label": "Arbeitszeugnis Beispiel GmbH",
            "kind": "employment_reference",
            "path": "documents/Arbeitszeugnis.pdf",
        }
    ]
    candidate_path.write_text(yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("JOB_AGENT_HOME", str(candidate_path.parent))

    result = render_resume(candidate_path=candidate_path, include_attachments=True)

    assert result.bundle_path is not None
    assert len(PdfReader(str(result.bundle_path)).pages) == 2
    manifest = json.loads(result.attachments_manifest_path.read_text(encoding="utf-8"))
    assert manifest["attachments"][0]["kind"] == "employment_reference"
    assert len(manifest["attachments"][0]["sha256"]) == 64
