import json
from pathlib import Path

import pytest

from job_application_agent import package as package_module
from job_application_agent.document_names import (
    cover_letter_filename,
    final_job_folder_name,
)
from job_application_agent.models import (
    CandidateProfile,
    CoverLetterQuality,
    JobListing,
    JobScorecard,
    RunReport,
    SearchReport,
    SearchResult,
    SourceHealth,
    utc_now_iso,
)
from job_application_agent.pipeline import (
    create_packages_from_search_report,
    run_pipeline,
    run_search,
)
from job_application_agent.profile import load_candidate_profile


def _package_profile(policy_path: Path | None = None) -> CandidateProfile:
    return CandidateProfile(
        name="Test Candidate",
        email="candidate@example.com",
        location="Example City, Example Country",
        github="https://github.com/example",
        linkedin="https://www.linkedin.com/in/example",
        summary="Developer",
        core_skills=["Python"],
        proof_points=["Test evidence"],
        cv_excerpt="CV",
        humanizer_excerpt="Humanizer rules loaded",
        humanizer_policy_path=str(policy_path or ""),
    )


def _package_listing() -> JobListing:
    return JobListing(
        source="fixture",
        source_url="fixture://example",
        apply_url="https://example.test/apply",
        title="Frontend Engineer",
        company="Example GmbH",
        language="de",
    )


def _package_scorecard() -> JobScorecard:
    return JobScorecard(
        listing_key="example-frontend",
        score=90,
        recommendation="strong",
        selected=True,
    )


def _private_policy(tmp_path: Path) -> Path:
    path = tmp_path / "private.de.md"
    path.write_text(
        "---\nversion: 1\nlanguage: de\nbanned_terms: [PRIVATE_TEST_BANNED]\nbanned_patterns: []\nreplacements: {PRIVATE_TEST_REPLACE: Private test replacement}\nforbid_colons: true\n---\n\nPrivate test policy.\n",
        encoding="utf-8",
    )
    return path


def _configured_search_profile(tmp_path: Path) -> Path:
    path = tmp_path / "search_profile.yaml"
    path.write_text(
        """search:
  profile_configured: true
  target_roles: [Developer, Engineer]
  keywords: [AI, automation, React]
  hard_exclusions: [Praktikum, SAP]
sources: {}
""",
        encoding="utf-8",
    )
    return path


def test_mechanical_humanizer_failure_detection_is_specific():
    assert package_module._has_mechanical_humanizer_failure(
        ["Private Humanizer policy matched: PRIVATE_TEST_BANNED"]
    )
    assert package_module._has_mechanical_humanizer_failure(
        ["Private Humanizer policy disallows colon transitions in final professional copy."]
    )
    assert not package_module._has_mechanical_humanizer_failure(
        ["Humanizer source is missing."]
    )


def test_final_job_folder_name_uses_resolved_company_not_portal():
    listing = JobListing(
        source="stepstone_public",
        source_url="https://www.stepstone.de/jobs/ai-automation-engineer/in-deutschland",
        apply_url="https://www.stepstone.de/stellenangebote--AI-Automation-Engineer-m-w-d-in-Muenchen-Berlin-Homeoffice-Muenchen-Berlin-Becker-Buettner-Held-Rechtsanwaelte-Steuerberater-Unternehmensberater-PartGmbB--14222117-inline.html",
        title="AI & Automation Engineer (m/w/d) in München, Berlin, Homeoffice",
        company="StepStone",
        description=(
            "Becker Büttner Held Rechtsanwälte Steuerberater "
            "Unternehmensberater PartGmbB München, Berlin"
        ),
    )

    folder_name = final_job_folder_name(listing)

    assert folder_name.startswith("Becker_Buttner_Held")
    assert "StepStone" not in folder_name


def test_run_report_labels_skip_counts_unambiguously(tmp_path):
    report = RunReport(
        run_id="test-run",
        created_at="2026-06-25T00:00:00Z",
        mode="live",
        top_n=1,
        max_candidates=10,
        source_health=[
            SourceHealth(
                name="application_tracker",
                status="available",
                candidates_seen=12,
                candidates_returned=2,
                message="2 previously applied/rejected/ignored/unavailable jobs suppressed.",
            )
        ],
        selected_jobs=[],
        packages=[],
        skipped_count=7,
        tracked_skipped_count=2,
        output_dir=str(tmp_path),
    )
    output_path = tmp_path / "run_report.md"

    package_module.write_run_report(report, output_path)

    text = output_path.read_text(encoding="utf-8")
    assert "Scoring excluded count: 7" in text
    assert "Tracker-suppressed existing count: 2" in text
    assert "application_tracker: available, 2 previously" in text
    assert "Skipped count:" not in text


def test_approved_fixture_search_writes_package(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    config_path = _configured_search_profile(tmp_path)
    search_report = run_search(
        mode="fixtures",
        top_n=2,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )
    report = create_packages_from_search_report(
        Path(search_report.results_json_path),
        ["1"],
        output_base=tmp_path,
        tracker_paths=[tracker_path],
        tracker_write_path=tracker_path,
        config_path=config_path,
    )
    assert report.packages
    first = report.packages[0]
    assert Path(first.job_json_path).exists()
    assert Path(first.scorecard_path).exists()
    assert Path(first.cover_letter_md_path).exists()
    assert Path(first.cover_letter_html_path).exists()
    assert Path(first.cover_letter_pdf_path).exists()
    assert Path(first.cover_letter_quality_json_path).exists()
    assert Path(first.cover_letter_quality_md_path).exists()
    assert first.pdf_renderer == "reportlab"
    assert Path(first.checklist_path).exists()
    assert Path(first.application_route_path).exists()
    assert Path(first.form_fill_plan_json_path).exists()
    assert Path(first.form_fill_plan_md_path).exists()
    assert Path(first.stagehand_plan_json_path).exists()
    assert Path(first.stagehand_preview_ts_path).exists()
    assert Path(report.output_dir, "run_report.md").exists()

    job = json.loads(Path(first.job_json_path).read_text(encoding="utf-8"))
    checklist = Path(first.checklist_path).read_text(encoding="utf-8")
    stagehand_plan = json.loads(
        Path(first.stagehand_plan_json_path).read_text(encoding="utf-8")
    )
    quality = json.loads(
        Path(first.cover_letter_quality_json_path).read_text(encoding="utf-8")
    )
    assert job["application_method"]
    assert "Application method:" in checklist
    assert "Resume upload:" in checklist
    assert stagehand_plan["submit_allowed"] is False
    assert quality["passed"] is True
    assert quality["page_count"] == 1

    listing = JobListing.model_validate(job)
    mirror_dir = package_module.canonical_anschreiben_root() / final_job_folder_name(
        listing
    )
    assert mirror_dir.exists()
    assert (mirror_dir / "anschreiben_source.md").read_text(encoding="utf-8") == Path(
        first.cover_letter_md_path
    ).read_text(encoding="utf-8")
    assert (mirror_dir / "job_info.md").exists()
    assert (mirror_dir / "job.json").exists()
    assert (mirror_dir / "package_source.txt").exists()
    assert (mirror_dir / "Test_Candidate_CV_26.pdf").exists()
    assert (mirror_dir / cover_letter_filename(load_candidate_profile(), listing)).exists()
    assert not (mirror_dir / "cover_letter.pdf").exists()


def test_direct_run_pipeline_is_disabled_without_approval(tmp_path):
    with pytest.raises(RuntimeError, match="Direct package generation is disabled"):
        run_pipeline(mode="fixtures", top_n=1, output_base=tmp_path)


def test_search_only_writes_no_application_packages(tmp_path):
    config_path = _configured_search_profile(tmp_path)
    report = run_search(
        mode="fixtures",
        top_n=2,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tmp_path / "applications.jsonl"],
    )

    assert report.results
    assert Path(report.results_json_path).exists()
    assert Path(report.results_md_path).exists()
    assert not list(Path(report.output_dir).glob("*/cover_letter.pdf"))
    assert not list(Path(report.output_dir).glob("*/cover_letter.md"))


def test_create_packages_uses_only_approved_search_results(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    config_path = _configured_search_profile(tmp_path)
    search_report = run_search(
        mode="fixtures",
        top_n=2,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )
    package_report = create_packages_from_search_report(
        Path(search_report.results_json_path),
        ["1"],
        output_base=tmp_path,
        tracker_paths=[tracker_path],
        tracker_write_path=tracker_path,
        config_path=config_path,
    )

    assert len(package_report.packages) == 1
    package_dir = Path(package_report.packages[0].package_dir)
    assert package_dir.exists()
    assert Path(package_report.packages[0].cover_letter_pdf_path).exists()
    assert Path(package_report.packages[0].cover_letter_quality_json_path).exists()
    assert len([path for path in package_dir.parent.iterdir() if path.is_dir()]) == 1


def test_create_packages_rejects_unmatched_approval_refs(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    config_path = _configured_search_profile(tmp_path)
    search_report = run_search(
        mode="fixtures",
        top_n=2,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )

    with pytest.raises(ValueError, match="No approved jobs matched"):
        create_packages_from_search_report(
            Path(search_report.results_json_path),
            ["does-not-exist"],
            output_base=tmp_path,
            tracker_paths=[tracker_path],
        )


def test_rapid_runs_get_distinct_output_dirs(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    config_path = _configured_search_profile(tmp_path)
    first = run_search(
        mode="fixtures",
        top_n=1,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )
    second = run_search(
        mode="fixtures",
        top_n=1,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )
    assert first.run_id != second.run_id
    assert first.output_dir != second.output_dir


def test_create_packages_rejects_already_applied_jobs(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    config_path = _configured_search_profile(tmp_path)
    search_report = run_search(
        mode="fixtures",
        top_n=2,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker_path],
    )
    first_listing = search_report.results[0].listing
    tracker_path.write_text(
        json.dumps(
            {
                "status": "applied",
                "company": first_listing.company,
                "title": first_listing.title,
                "apply_url": first_listing.apply_url or first_listing.source_url,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already applied"):
        create_packages_from_search_report(
            Path(search_report.results_json_path),
            ["1"],
            output_base=tmp_path,
            tracker_paths=[tracker_path],
            tracker_write_path=tracker_path,
            config_path=config_path,
        )


def test_create_packages_rejects_manual_report_with_current_hard_exclusion(tmp_path):
    search_results_path = tmp_path / "search_results.json"
    config_path = tmp_path / "blocked-search-profile.yaml"
    config_path.write_text(
        """search:
  profile_configured: true
  target_roles: [Engineer]
  hard_exclusions: [Excluded Role]
sources: {}
""",
        encoding="utf-8",
    )
    listing = JobListing(
        source="manual",
        source_url="https://example.test/jobs/excluded-role",
        apply_url="https://example.test/jobs/excluded-role/apply",
        title="Excluded Role Engineer",
        company="Example",
        location="Remote Germany",
        remote_type="remote",
        seniority="senior",
        description="React, TypeScript, Node.js and product engineering.",
    )
    report = SearchReport(
        run_id="manual-direct-candidates",
        created_at=utc_now_iso(),
        mode="live",
        top_n=1,
        max_candidates=1,
        results=[
            SearchResult(
                rank=1,
                listing=listing,
                scorecard=JobScorecard(
                listing_key="manual-excluded-role",
                    score=85,
                    recommendation="review",
                    selected=False,
                ),
            )
        ],
        output_dir=str(tmp_path),
        results_json_path=str(search_results_path),
        results_md_path=str(tmp_path / "search_results.md"),
    )
    search_results_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="Configured hard exclusion matched: Excluded Role"):
        create_packages_from_search_report(
            search_results_path,
            ["1"],
            output_base=tmp_path,
            tracker_paths=[tmp_path / "applications.jsonl"],
            config_path=config_path,
        )


def test_package_repairs_cover_letter_after_quality_gate_failure(tmp_path, monkeypatch):
    bad_letter = """# Bewerbung als Frontend Engineer

Hallo liebes Example-Team,

PRIVATE_TEST_REPLACE Einstieg mit PRIVATE_TEST_BANNED: Die Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n. Ich baue Weboberflächen und Automatisierungen, die nah an echten Abläufen bleiben.

In eigenen Projekten habe ich praktische Automatisierungen und Webanwendungen umgesetzt. Konkrete Beispiele stehen im beigefügten Lebenslauf.

Mich interessiert die Verbindung aus sauberer Umsetzung, Prozessblick und Kundennähe. Genau dort kann ich schnell produktiv werden.

Ich mag Rollen, in denen man nicht nur über Automatisierung spricht, sondern sie an realen Übergaben, Formularen, Daten und kleinen Fehlerfällen festmacht. Das ist oft weniger Show, aber deutlich näher an produktiver Arbeit.

Öffentliche Referenzen

GitHub: https://github.com/example
LinkedIn: https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""
    monkeypatch.setattr(
        package_module,
        "draft_cover_letter",
        lambda profile, listing, scorecard: bad_letter,
    )
    profile = _package_profile(_private_policy(tmp_path))
    listing = _package_listing()
    scorecard = _package_scorecard()

    package = package_module.write_application_package(
        tmp_path, profile, listing, scorecard
    )
    package_dir = Path(package.package_dir)
    final_text = Path(package.cover_letter_md_path).read_text(encoding="utf-8")
    quality = json.loads(
        Path(package.cover_letter_quality_json_path).read_text(encoding="utf-8")
    )

    assert quality["passed"] is True
    assert "PRIVATE_TEST_REPLACE" not in final_text
    assert "PRIVATE_TEST_BANNED:" not in final_text
    assert "GitHub:" not in final_text
    assert any(
        "after initial Humanizer gate failed" in item for item in quality["warnings"]
    )
    assert (package_dir / "_cover_letter.pre_humanizer.md").exists()


def test_package_repairs_cover_letter_after_failed_quality_gate(tmp_path, monkeypatch):
    bad_letter = """# Bewerbung als Frontend Engineer

Hallo liebes Example-Team,

PRIVATE_TEST_REPLACE Einstieg mit PRIVATE_TEST_BANNED: Die Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n. Ich baue Weboberflächen und Automatisierungen, die nah an echten Abläufen bleiben.

In eigenen Projekten habe ich praktische Automatisierungen und Webanwendungen umgesetzt. Konkrete Beispiele stehen im beigefügten Lebenslauf.

Mich interessiert die Verbindung aus sauberer Umsetzung, Prozessblick und Kundennähe. Genau dort kann ich schnell produktiv werden.

Ich mag Rollen, in denen man nicht nur über Automatisierung spricht, sondern sie an realen Übergaben, Formularen, Daten und kleinen Fehlerfällen festmacht. Das ist oft weniger Show, aber deutlich näher an produktiver Arbeit.

Öffentliche Referenzen

GitHub: https://github.com/example
LinkedIn: https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""
    checked_markdown: list[str] = []
    events: list[tuple[str, str]] = []

    def fake_quality(markdown, profile, pdf_path, listing=None, artifact_filename=None):
        events.append(("quality", markdown))
        checked_markdown.append(markdown)
        passed = len(checked_markdown) > 1
        return CoverLetterQuality(
            passed=passed,
            humanizer_loaded=True,
            word_count=180,
            paragraph_count=7,
            page_count=1,
            checks={"humanizer_loaded": True},
            issues=[] if passed else ["Private Humanizer policy matched: PRIVATE_TEST_BANNED"],
            warnings=[],
        )

    def fake_render_html(profile, listing, markdown, output_path, *, theme=None):
        _ = theme
        events.append(("html", markdown))
        output_path.write_text("<html></html>", encoding="utf-8")

    def fake_render_pdf(profile, listing, markdown, pdf_path, *, theme=None):
        _ = theme
        events.append(("pdf", markdown))
        pdf_path.write_bytes(b"%PDF-1.4 /Type /Pages /Type /Page")
        return package_module.PdfRenderResult(renderer="reportlab")

    monkeypatch.setattr(
        package_module,
        "draft_cover_letter",
        lambda profile, listing, scorecard: bad_letter,
    )
    monkeypatch.setattr(package_module, "check_cover_letter_quality", fake_quality)
    monkeypatch.setattr(
        package_module,
        "rewrite_cover_letter_for_humanizer",
        lambda markdown, profile: markdown.replace("PRIVATE_TEST_BANNED", "replacement").replace("PRIVATE_TEST_REPLACE", "Private").replace(":", "."),
    )
    monkeypatch.setattr(package_module, "render_cover_letter_html", fake_render_html)
    monkeypatch.setattr(
        package_module, "render_cv_matched_cover_letter_pdf", fake_render_pdf
    )
    profile = _package_profile()
    listing = _package_listing()
    scorecard = _package_scorecard()

    package = package_module.write_application_package(
        tmp_path, profile, listing, scorecard
    )
    quality = json.loads(
        Path(package.cover_letter_quality_json_path).read_text(encoding="utf-8")
    )

    assert checked_markdown[0] == bad_letter
    assert checked_markdown[1] != bad_letter
    assert "Dieser" not in checked_markdown[1]
    assert events == [
        ("html", checked_markdown[0]),
        ("pdf", checked_markdown[0]),
        ("quality", checked_markdown[0]),
        ("html", checked_markdown[1]),
        ("pdf", checked_markdown[1]),
        ("quality", checked_markdown[1]),
    ]
    assert quality["passed"] is True
    assert any(
        "after initial Humanizer gate failed" in item for item in quality["warnings"]
    )


def test_package_aborts_when_repaired_cover_letter_still_fails(tmp_path, monkeypatch):
    bad_letter = """# Bewerbung als Frontend Engineer

Hallo liebes Example-Team,

PRIVATE_TEST_REPLACE Einstieg mit PRIVATE_TEST_BANNED: Die Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n.

GitHub: https://github.com/example
LinkedIn: https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""
    checked_markdown: list[str] = []

    def failing_quality(
        markdown, profile, pdf_path, listing=None, artifact_filename=None
    ):
        checked_markdown.append(markdown)
        return CoverLetterQuality(
            passed=False,
            humanizer_loaded=True,
            word_count=95,
            paragraph_count=4,
            page_count=1,
            checks={"humanizer_loaded": True},
            issues=(
                ["Private Humanizer policy matched: PRIVATE_TEST_BANNED"]
                if len(checked_markdown) == 1
                else ["Word count 95 is outside the one-page target range 120-330."]
            ),
            warnings=[],
        )

    monkeypatch.setattr(
        package_module,
        "draft_cover_letter",
        lambda profile, listing, scorecard: bad_letter,
    )
    monkeypatch.setattr(package_module, "check_cover_letter_quality", failing_quality)
    monkeypatch.setattr(
        package_module,
        "rewrite_cover_letter_for_humanizer",
        lambda markdown, profile: markdown.replace("PRIVATE_TEST_BANNED", "replacement"),
    )

    with pytest.raises(RuntimeError, match="Cover letter quality gate failed"):
        package_module.write_application_package(
            tmp_path, _package_profile(), _package_listing(), _package_scorecard()
        )

    package_dir = next(tmp_path.glob("*/job.json")).parent
    quality = json.loads(
        (package_dir / "cover_letter_quality.json").read_text(encoding="utf-8")
    )

    assert len(checked_markdown) == 2
    assert checked_markdown[0] == bad_letter
    assert checked_markdown[1] != bad_letter
    assert quality["passed"] is False
    assert (package_dir / "_cover_letter.pre_humanizer.md").exists()
    assert not (package_dir / "cover_letter.md").exists()
    assert not (package_dir / "cover_letter.html").exists()
    assert not (package_dir / "cover_letter.pdf").exists()
    assert not (package_dir / "_cover_letter.draft.md").exists()
    assert not (package_dir / "_cover_letter.draft.html").exists()
    assert not (package_dir / "_cover_letter.draft.pdf").exists()


def test_package_does_not_rewrite_when_initial_quality_gate_passes(
    tmp_path, monkeypatch
):
    rewriteable_letter = """# Bewerbung als Frontend Engineer

Hallo liebes Example-Team,

Dieser Einstieg ist klar: Die Rolle passt gut zu meiner Arbeit mit React, Next.js, PostgreSQL und n8n.

GitHub: https://github.com/example
LinkedIn: https://www.linkedin.com/in/example

Viele Grüße
Test Candidate
"""
    checked_markdown: list[str] = []

    def passing_quality(
        markdown, profile, pdf_path, listing=None, artifact_filename=None
    ):
        checked_markdown.append(markdown)
        return CoverLetterQuality(
            passed=True,
            humanizer_loaded=True,
            word_count=180,
            paragraph_count=7,
            page_count=1,
            checks={"humanizer_loaded": True},
            issues=[],
            warnings=[],
        )

    monkeypatch.setattr(
        package_module,
        "draft_cover_letter",
        lambda profile, listing, scorecard: rewriteable_letter,
    )
    monkeypatch.setattr(package_module, "check_cover_letter_quality", passing_quality)

    package = package_module.write_application_package(
        tmp_path, _package_profile(), _package_listing(), _package_scorecard()
    )
    package_dir = Path(package.package_dir)
    final_text = Path(package.cover_letter_md_path).read_text(encoding="utf-8")

    assert checked_markdown == [rewriteable_letter]
    assert final_text == rewriteable_letter
    assert not (package_dir / "_cover_letter.pre_humanizer.md").exists()
