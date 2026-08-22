from email import policy
from email.parser import BytesParser

import pytest

from job_application_agent.cli import _mail_attachments
from job_application_agent.mail_draft import (
    default_email_body,
    default_email_subject,
    write_mail_draft,
)
from job_application_agent.models import CandidateProfile, JobListing


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Berlin",
        github="https://github.com/example",
        linkedin="https://www.linkedin.com/in/example",
        summary="Developer",
        core_skills=[],
        proof_points=[],
        cv_excerpt="",
        humanizer_excerpt="Humanizer",
    )


def test_write_mail_draft_creates_not_sent_eml_and_apple_mail_script(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/job",
        title="AI Automation Developer",
        company="Example GmbH",
        language="de",
    )
    attachment = tmp_path / "cover_letter.pdf"
    attachment.write_bytes(b"%PDF-1.4\n%%EOF\n")

    result = write_mail_draft(
        tmp_path,
        _profile(),
        listing,
        to="jobs@example.com",
        subject="Bewerbung",
        body="Hallo Team,\n\nanbei meine Unterlagen.\n\nViele Grüße\nCandidate",
        attachments=[attachment],
        write_apple_mail_script=True,
    )

    assert result.markdown_path.exists()
    assert result.eml_path.exists()
    assert result.apple_mail_script_path is not None
    assert result.apple_mail_script_path.exists()

    message = BytesParser(policy=policy.default).parsebytes(
        result.eml_path.read_bytes()
    )
    assert message["X-Job-Agent-Dry-Run"] == "true"
    assert message["To"] == "jobs@example.com"
    assert message["Subject"] == "Bewerbung"
    assert any(
        part.get_filename() == "AI_Automation_Developer_Candidate.pdf"
        for part in message.walk()
    )
    assert (
        tmp_path / "AI_Automation_Developer_Candidate.pdf"
    ).exists()
    assert "does not send" in result.apple_mail_script_path.read_text(encoding="utf-8")


def test_default_german_mail_text_uses_short_title_and_human_closing():
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/job",
        title="Fullstack Next.js / React Developer (m/w/d) – AI-powered & agentic",
        company="Heritaxa GmbH",
        language="de",
    )

    subject = default_email_subject(listing)
    body = default_email_body(_profile(), listing)

    assert subject == "Bewerbung als Fullstack Next.js / React Developer (m/w/d)"
    assert "Fullstack Next.js / React Developer (m/w/d)." in body
    assert "AI-powered & agentic" not in body
    assert "Ich freue mich, wenn wir sprechen." not in body
    assert "Ich erzähle Ihnen gern persönlich mehr dazu." in body


def test_write_mail_draft_sanitizes_noncanonical_attachment_names(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/job",
        title="AI Automation Developer",
        company="Example GmbH",
        language="de",
    )
    attachment = tmp_path / "Portfolio Screenshot #1!.pdf"
    attachment.write_bytes(b"%PDF-1.4\n%%EOF\n")

    result = write_mail_draft(
        tmp_path,
        _profile(),
        listing,
        to="jobs@example.com",
        subject="Bewerbung",
        body="Hallo Team",
        attachments=[attachment],
    )

    message = BytesParser(policy=policy.default).parsebytes(
        result.eml_path.read_bytes()
    )

    assert any(
        part.get_filename() == "Portfolio_Screenshot_1.pdf" for part in message.walk()
    )
    assert (tmp_path / "Portfolio_Screenshot_1.pdf").exists()


def test_write_mail_draft_deduplicates_sanitized_attachment_names(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/job",
        title="AI Automation Developer",
        company="Example GmbH",
        language="de",
    )
    first = tmp_path / "Portfolio Screenshot #1!.pdf"
    second = tmp_path / "Portfolio Screenshot 1.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    result = write_mail_draft(
        tmp_path,
        _profile(),
        listing,
        to="jobs@example.com",
        subject="Bewerbung",
        body="Hallo Team",
        attachments=[first, second],
    )

    message = BytesParser(policy=policy.default).parsebytes(
        result.eml_path.read_bytes()
    )
    attachments = {
        part.get_filename(): part.get_payload(decode=True)
        for part in message.walk()
        if part.get_filename()
    }

    assert attachments["Portfolio_Screenshot_1.pdf"] == b"first"
    assert attachments["Portfolio_Screenshot_1_2.pdf"] == b"second"
    assert (tmp_path / "Portfolio_Screenshot_1.pdf").read_bytes() == b"first"
    assert (tmp_path / "Portfolio_Screenshot_1_2.pdf").read_bytes() == b"second"


def test_mail_attachments_use_separate_cv_and_cover_only(tmp_path, monkeypatch):
    cv_pdf = tmp_path / "Test_Candidate_Lebenslauf.pdf"
    cover_pdf = tmp_path / "cover_letter.pdf"
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    cover_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("JOB_AGENT_CV_PDF_PATH", str(cv_pdf))

    attachments = _mail_attachments(tmp_path)

    assert attachments == [cv_pdf.resolve(), cover_pdf.resolve()]
    assert combined_pdf.resolve() not in attachments


def test_mail_attachments_reject_combined_application_pdf(tmp_path):
    combined_pdf = tmp_path / "Test_Candidate_Bewerbung_Example.pdf"
    cover_pdf = tmp_path / "cover_letter.pdf"
    combined_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    cover_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(ValueError, match="not a combined application PDF"):
        _mail_attachments(tmp_path, cv_pdf=combined_pdf, cover_pdf=cover_pdf)
