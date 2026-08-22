from __future__ import annotations

from pathlib import Path

import pytest

from job_application_agent.approval import apply_approved, approve_packages
from job_application_agent.application import build_form_fill_plan
from job_application_agent.company_research import (
    research_company,
    research_is_approvable,
    write_company_research,
)
from job_application_agent.models import JobListing, JobScorecard
from job_application_agent.package import (
    refresh_application_package_from_research,
    write_application_package,
)
from job_application_agent.profile import load_candidate_profile
from job_application_agent.utils import write_json


def _listing() -> JobListing:
    return JobListing(
        source="fixture",
        source_url="https://jobs.example.test/role",
        apply_url="https://jobs.example.test/role",
        title="Frontend Engineer",
        company="Example GmbH",
        language="de",
        description="Public product engineering role.",
        application_method="company_form",
        apply_platform="example.test",
    )


def _scorecard() -> JobScorecard:
    return JobScorecard(
        listing_key="example-frontend-1", score=91, recommendation="strong", selected=True
    )


def _fetcher(url: str) -> str:
    pages = {
        "https://jobs.example.test/role": """
            <html><body><p>Example GmbH is hiring a Frontend Engineer for its public workflow product.</p>
            <p>Ansprechpartnerin: Alex Beispiel</p><a href="https://example.test/about">Über Example</a></body></html>
        """,
        "https://example.test/about": """
            <html><head><meta name="description" content="Example GmbH builds a public workflow platform for teams."></head>
            <body><p>Example GmbH builds a public workflow platform for teams.</p></body></html>
        """,
    }
    return pages[url]


def _research_bound_package(tmp_path: Path) -> tuple[Path, JobListing]:
    listing = _listing()
    profile = load_candidate_profile()
    package = write_application_package(tmp_path, profile, listing, _scorecard())
    package_dir = Path(package.package_dir)
    research = research_company(listing, fetcher=_fetcher)
    write_company_research(package_dir, research)
    refresh = refresh_application_package_from_research(package_dir, profile, research)
    assert refresh["cover_letter_refreshed"] is True
    plan = build_form_fill_plan(
        profile,
        listing,
        package_dir=package_dir,
        fields=[],
        cover_letter_text=(package_dir / "cover_letter.md").read_text(encoding="utf-8"),
    )
    # A small observed fixture plan avoids unrelated live inspection in this
    # approval test; the approval still binds the research artifact.
    plan = plan.model_copy(update={"fields": [], "instructions": []})
    # The autonomous portal guard requires observed fields, so use the package's
    # existing synthetic direct form fixture plan on an explicit route.
    from job_application_agent.models import ApplicationFormField, ApplicationRoute, FormFillInstruction

    field = ApplicationFormField(label="Name", selector="#name", classification="full_name", required=True)
    instruction = FormFillInstruction(field_label="Name", selector="#name", classification="full_name", action="fill", value="Test Candidate", required=True, confidence="high")
    plan = plan.model_copy(update={
        "route": ApplicationRoute(method="company_form", apply_url=listing.apply_url, platform="example.test", can_agent_fill=True),
        "fields": [field],
        "instructions": [instruction],
    })
    write_json(package_dir / "form_fill_plan.json", plan)
    return package_dir / "job.json", listing


def test_research_uses_only_public_source_text_and_refreshes_contacted_cover(tmp_path):
    listing = _listing()
    research = research_company(listing, fetcher=_fetcher)

    assert research_is_approvable(research)
    assert research.contact_name == "Alex Beispiel"
    assert research.source_urls == ["https://jobs.example.test/role", "https://example.test/about"]
    assert all(fact.claim == fact.excerpt for fact in research.facts)

    job_path, _ = _research_bound_package(tmp_path)
    letter = (job_path.parent / "cover_letter.md").read_text(encoding="utf-8")
    assert "Hallo Alex Beispiel," in letter
    assert "Example GmbH is hiring a Frontend Engineer" in letter
    assert (job_path.parent / "company_research.md").is_file()


def test_approval_requires_research_and_blocks_research_fingerprint_change(tmp_path):
    job_path, _listing_value = _research_bound_package(tmp_path)
    manifest = approve_packages([job_path], output_path=tmp_path / "approval.json")
    research_path = job_path.parent / "company_research.json"
    research_path.write_text(research_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    calls: list[object] = []

    result = apply_approved(
        manifest,
        execute=True,
        browser_runner=lambda plan, **kwargs: calls.append(plan) or {},
    )

    assert calls == []
    assert result["results"][0]["status"] == "approval_mismatch"


def test_approval_refuses_missing_company_research(tmp_path):
    listing = _listing()
    profile = load_candidate_profile()
    package = write_application_package(tmp_path, profile, listing, _scorecard())
    package_dir = Path(package.package_dir)
    from job_application_agent.models import ApplicationFormField, ApplicationRoute, FormFillInstruction, FormFillPlan

    write_json(
        package_dir / "form_fill_plan.json",
        FormFillPlan(
            job_title=listing.title,
            company=listing.company,
            apply_url=listing.apply_url,
            route=ApplicationRoute(method="company_form", apply_url=listing.apply_url, can_agent_fill=True),
            fields=[ApplicationFormField(label="Name", selector="#name", classification="full_name", required=True)],
            instructions=[FormFillInstruction(field_label="Name", selector="#name", classification="full_name", action="fill", value="Test Candidate", required=True, confidence="high")],
        ),
    )
    with pytest.raises(FileNotFoundError, match="Missing public company research"):
        approve_packages([package_dir / "job.json"], output_path=tmp_path / "blocked.json")
