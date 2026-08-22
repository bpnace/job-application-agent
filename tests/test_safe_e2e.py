from __future__ import annotations

from pathlib import Path

from job_application_agent.approval import apply_approved, approve_packages
from job_application_agent.company_research import write_company_research
from job_application_agent.models import (
    ApplicationFormField,
    ApplicationRoute,
    CompanyFact,
    CompanyResearch,
    FormFillInstruction,
    FormFillPlan,
    JobListing,
    JobScorecard,
)
from job_application_agent.package import (
    refresh_application_package_from_research,
    write_application_package,
)
from job_application_agent.profile import load_candidate_profile
from job_application_agent.utils import write_json


def test_local_e2e_uses_synthetic_profile_research_approval_and_final_fixture_evidence(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "browser_application_form.html"
    profile = load_candidate_profile()
    listing = JobListing(
        source="local_fixture",
        source_url="https://example.test/jobs/frontend-engineer",
        apply_url=fixture.as_uri(),
        title="Frontend Engineer",
        company="Example GmbH",
        language="de",
        application_method="company_form",
        apply_platform="fixture",
    )
    package = write_application_package(
        tmp_path,
        profile,
        listing,
        JobScorecard(listing_key="local-e2e", score=99, recommendation="strong", selected=True),
    )
    package_dir = Path(package.package_dir)
    research = CompanyResearch(
        company="Example",
        contact_name="Alex Beispiel",
        facts=[CompanyFact(
            claim="Example GmbH builds a public workflow platform for teams.",
            excerpt="Example GmbH builds a public workflow platform for teams.",
            source_url="https://example.test/about",
            source_sha256="b" * 64,
        )],
        source_urls=["https://example.test/about"],
        retrieved_at="2026-08-12T00:00:00Z",
    )
    write_company_research(package_dir, research)
    refreshed = refresh_application_package_from_research(package_dir, profile, research)
    assert refreshed["cover_letter_refreshed"] is True
    letter = (package_dir / "cover_letter.md").read_text(encoding="utf-8")
    assert "Hallo Alex Beispiel," in letter
    assert "Example GmbH builds a public workflow platform" in letter
    assert (package_dir / "cover_letter.pdf").is_file()
    quality = (package_dir / "cover_letter_quality.json").read_text(encoding="utf-8")
    assert '"humanizer_loaded": true' in quality

    plan = FormFillPlan(
        job_title=listing.title,
        company=listing.company,
        apply_url=listing.apply_url,
        route=ApplicationRoute(method="company_form", apply_url=listing.apply_url, platform="fixture", can_agent_fill=True),
        fields=[
            ApplicationFormField(label="Full name", selector="#name", classification="full_name", required=True),
            ApplicationFormField(label="Email", selector="#email", classification="email", field_type="email", required=True),
        ],
        instructions=[
            FormFillInstruction(field_label="Full name", selector="#name", classification="full_name", action="fill", value="Test Candidate", required=True, confidence="high"),
            FormFillInstruction(field_label="Email", selector="#email", classification="email", action="fill", value="candidate@example.test", required=True, confidence="high"),
        ],
    )
    write_json(package_dir / "form_fill_plan.json", plan)
    manifest = approve_packages([package_dir / "job.json"], output_path=tmp_path / "approval.json")
    result = apply_approved(manifest, execute=True, tracker_path=tmp_path / "tracker.jsonl")

    assert result["results"][0]["status"] == "applied"
    assert result["results"][0]["result"]["submit_evidence_level"] == "final_confirmation"
