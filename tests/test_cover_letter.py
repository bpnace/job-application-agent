import pytest

from job_application_agent.cover_letter import display_role_title, draft_cover_letter
from job_application_agent.document_names import cover_letter_filename
from job_application_agent.humanizer import check_cover_letter_quality
from job_application_agent.models import CandidateProfile, JobListing, JobScorecard


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Berlin",
        github="https://github.com/example",
        linkedin="https://linkedin.com/in/example",
        summary="Developer",
        core_skills=[],
        proof_points=[],
        cv_excerpt="",
        humanizer_excerpt="Humanizer",
    )


def test_german_cover_letter_does_not_leak_scorecard_labels():
    profile = _profile()
    listing = JobListing(
        source="fixture",
        source_url="fixture://de",
        title="Expert:in für Tracking",
        company="Junico GmbH",
        language="de",
    )
    scorecard = JobScorecard(
        listing_key="de",
        score=75,
        recommendation="review",
        selected=True,
        matched_strengths=["Remote-compatible.", "Preferred location signal: remote"],
    )

    letter = draft_cover_letter(profile, listing, scorecard)

    assert "Remote-compatible" not in letter
    assert "Preferred location signal" not in letter
    assert "Standortsignal" not in letter
    assert "Tech-Stack-Fit" not in letter
    assert "Hallo liebes Junico-Team" in letter


def test_german_cover_letter_normalizes_colon_titles_for_quality_gate():
    profile = _profile()
    listing = JobListing(
        source="fixture",
        source_url="fixture://de",
        title="Expert:in für Tracking",
        company="Junico GmbH",
        language="de",
    )
    scorecard = JobScorecard(
        listing_key="de",
        score=75,
        recommendation="review",
        selected=True,
    )

    letter = draft_cover_letter(profile, listing, scorecard)
    quality = check_cover_letter_quality(letter, profile)

    assert "Expert:in" not in letter
    assert "Expertin für Tracking" in letter
    assert quality.checks["no_colon_prose"] is True


def test_display_role_title_removes_marketing_suffixes():
    title = "Fullstack Next.js / React Developer (m/w/d) – AI-powered & agentic"

    assert display_role_title(title) == "Fullstack Next.js / React Developer (m/w/d)"


def test_display_role_title_removes_jobboard_description_noise():
    title = "Full-Stack-Entwickler / KI-Engineer Neu Welzer & Partner Mbb Steuerberater Rechtsanwälte Villingen-Schwenningen In dieser Position entwickelst du die Architektur"

    assert display_role_title(title) == "Full-Stack-Entwickler / KI-Engineer"


def test_display_role_title_keeps_short_pipe_title_readable():
    title = "Full-Stack Software Engineer | AI Accelerated | (m/w/d)"

    assert (
        display_role_title(title)
        == "Full-Stack Software Engineer AI Accelerated (m/w/d)"
    )


def test_german_cover_letter_uses_short_display_title():
    profile = _profile()
    listing = JobListing(
        source="fixture",
        source_url="fixture://de",
        title="Fullstack Next.js / React Developer (m/w/d) – AI-powered & agentic",
        company="Heritaxa GmbH",
        language="de",
    )
    scorecard = JobScorecard(
        listing_key="de",
        score=90,
        recommendation="strong",
        selected=True,
    )

    letter = draft_cover_letter(profile, listing, scorecard)

    assert "# Bewerbung als Fullstack Next.js / React Developer (m/w/d)" in letter
    assert "AI-powered & agentic" not in letter


def test_cover_letter_quality_has_no_public_project_brand_catalogue():
    profile = CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Berlin",
        github="https://github.com/example",
        linkedin="https://linkedin.com/in/example",
        summary="Developer",
        core_skills=[],
        proof_points=[],
        cv_excerpt="",
        humanizer_excerpt="Humanizer",
    )
    letter = """# Bewerbung als AI Developer

Hallo Team,

Ich arbeite mit React, Next.js und n8n.

Example Brand ist hier erwähnt.

GitHub https://github.com/example
LinkedIn https://linkedin.com/in/example

Viele Grüße
Test Candidate
"""

    quality = check_cover_letter_quality(letter, profile)

    assert "no_removed_project_brands" not in quality.checks
    assert not any("project brand" in issue.casefold() for issue in quality.issues)


def test_cover_letter_resolves_real_company_when_listing_company_is_portal():
    profile = _profile()
    listing = JobListing(
        source="berlin_startup_jobs",
        source_url="https://berlinstartupjobs.com/companies/stackgini-gmbh/jobs/product-engineer",
        apply_url="https://berlinstartupjobs.com/companies/stackgini-gmbh/jobs/product-engineer/apply",
        title="Product Engineer",
        company="Berlin Startup Jobs",
        language="de",
    )
    scorecard = JobScorecard(
        listing_key="stackgini",
        score=82,
        recommendation="strong",
        selected=True,
    )

    letter = draft_cover_letter(profile, listing, scorecard)
    quality = check_cover_letter_quality(letter, profile, listing=listing)

    assert "Hallo liebes Stackgini-Team" in letter
    assert "Berlin Startup Jobs" not in letter
    assert quality.checks["no_portal_addressee"] is True
    assert quality.checks["correct_addressee_or_company"] is True


def test_cover_letter_uses_real_company_for_join_slug_and_description():
    profile = _profile()
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/customer-success-manager",
        apply_url="https://join.com/companies/caya/jobs/customer-success-manager/apply",
        title="Customer Success Manager - German Speaking (EMEA)",
        company="join.com",
        language="de",
        description="Caya GmbH sucht Unterstützung für Customer Success, SaaS Onboarding und Automation.",
    )
    scorecard = JobScorecard(
        listing_key="caya",
        score=80,
        recommendation="review",
        selected=True,
    )

    letter = draft_cover_letter(profile, listing, scorecard)
    quality = check_cover_letter_quality(letter, profile, listing=listing)

    assert "Hallo liebes Caya-Team" in letter
    assert "join.com" not in letter.lower()
    assert quality.checks["correct_addressee_or_company"] is True


def test_cover_letter_blocks_portal_listing_without_resolved_company():
    profile = _profile()
    listing = JobListing(
        source="job_board",
        source_url="https://jobs.example/listing/123",
        title="AI Automation Engineer",
        company="join.com",
        language="de",
    )
    scorecard = JobScorecard(
        listing_key="portal-only",
        score=72,
        recommendation="review",
        selected=True,
    )

    with pytest.raises(ValueError, match="portal/job board"):
        draft_cover_letter(profile, listing, scorecard)


def test_quality_gate_rejects_portal_addressee_for_listing():
    profile = _profile()
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya-gmbh/jobs/123",
        title="Junior Business Automation AI Engineer",
        company="join.com",
        language="de",
        description="Caya GmbH sucht Unterstützung für Automation und AI Workflows.",
    )
    letter = """# Bewerbung als Junior Business Automation AI Engineer

Hallo liebes join.com-Team,

Ich arbeite mit React, Next.js, PostgreSQL und n8n und habe 15+ produktive Workflows gebaut.

GitHub https://github.com/example
LinkedIn https://linkedin.com/in/example

Viele Grüße
Test Candidate
"""

    quality = check_cover_letter_quality(letter, profile, listing=listing)

    assert quality.passed is False
    assert quality.checks["no_portal_addressee"] is False
    assert quality.checks["no_portal_named_as_company"] is False
    assert quality.checks["correct_addressee_or_company"] is False


def test_cover_letter_filename_uses_role_not_company_or_gender_marker():
    profile = _profile()
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/product-engineer",
        title="Product Engineer (m/f/d)",
        company="Stackgini GmbH",
        language="de",
    )

    assert (
        cover_letter_filename(profile, listing)
        == "Product_Engineer_Candidate.pdf"
    )


def test_quality_gate_rejects_long_company_based_cover_letter_filename():
    profile = _profile()
    listing = JobListing(
        source="fixture",
        source_url="https://example.com/jobs/product-engineer",
        title="Product Engineer (m/f/d)",
        company="Stackgini GmbH",
        language="de",
    )
    letter = """# Bewerbung als Product Engineer

Hallo liebes Stackgini-Team,

Ihre Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n. Ich baue Weboberflächen und Automatisierungen, die nah an echten Abläufen bleiben.

In eigenen Projekten habe ich praktische Automatisierungen und Webanwendungen umgesetzt. Konkrete Beispiele stehen im beigefügten Lebenslauf.

Mich interessiert die Verbindung aus sauberer Umsetzung, Prozessblick und Kundennähe. Genau dort kann ich schnell produktiv werden.

Öffentliche Referenzen

GitHub https://github.com/example
LinkedIn https://linkedin.com/in/example

Viele Grüße
Test Candidate
"""

    quality = check_cover_letter_quality(
        letter,
        profile,
        listing=listing,
        artifact_filename="Test_Candidate_Anschreiben_stackgini_gmbh_product_engineer_m_f_d.pdf",
    )

    assert quality.passed is False
    assert (
        quality.recommended_filename
        == "Product_Engineer_Candidate.pdf"
    )
    assert quality.checks["compact_cover_letter_filename"] is False
    assert any(
        "candidate name and compact role description" in issue
        for issue in quality.issues
    )
