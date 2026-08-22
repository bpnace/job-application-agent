import json
from pathlib import Path
from typing import Literal

from job_application_agent.config import AppConfig, SearchSettings, SourceSettings
from job_application_agent.models import (
    JobListing,
    JobScorecard,
    SearchReport,
    SearchResult,
    SourceHealth,
)
from job_application_agent.pipeline import (
    collect_live_jobs,
    normalize_listing_company,
    run_search,
    write_portal_report,
    write_search_report,
)
from job_application_agent.sources import (
    ArbeitsagenturPublicSearchSource,
    ArbeitnowApiSource,
    FreelancermapPublicSource,
    GoogleJobsBrowserSource,
    LinkedinPublicSource,
    PersonioXmlSource,
    PublicJobBoardSource,
    PublicSearchSource,
    RemoteOkApiSource,
    RemotiveApiSource,
    ScraplingPublicJobBoardSource,
    SourceResult,
    StepstonePublicSource,
)


def _listing(title: str) -> JobListing:
    return JobListing(
        source="test",
        source_url=f"https://example.com/{title}",
        title=title,
        company="Example",
        description="React automation role",
    )


def _health(name: str, returned: int) -> SourceHealth:
    return SourceHealth(name=name, status="available", candidates_returned=returned)


def _search_result(
    rank: int,
    listing: JobListing,
    score: int = 90,
    recommendation: Literal["strong", "review", "adjacent", "weak", "exclude"] = "strong",
) -> SearchResult:
    return SearchResult(
        rank=rank,
        listing=listing,
        scorecard=JobScorecard(
            listing_key=f"{listing.company}-{listing.title}-{rank}".lower().replace(
                " ", "-"
            ),
            score=score,
            recommendation=recommendation,
            selected=True,
        ),
    )


def test_normalize_listing_company_resolves_portal_company_before_scoring():
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/customer-success-manager",
        apply_url="https://join.com/companies/caya/jobs/customer-success-manager/apply",
        title="Customer Success Manager",
        company="join.com",
        description="Caya GmbH sucht Unterstützung für Customer Success.",
    )

    normalized = normalize_listing_company(listing)

    assert normalized.company == "Caya GmbH"
    assert "company_resolved_from_portal" in normalized.tags


def test_write_portal_report_groups_results_by_apply_platform(tmp_path):
    linkedin = JobListing(
        source="linkedin_public",
        source_url="https://linkedin.example/jobs/1",
        title="AI Automation Engineer",
        company="Example AI",
        apply_url="https://linkedin.example/jobs/1",
        application_method="linkedin_job",
        apply_platform="linkedin",
    )
    stepstone = JobListing(
        source="stepstone_public",
        source_url="https://stepstone.example/jobs/1",
        title="Frontend Engineer",
        company="Frontend GmbH",
        apply_url="https://stepstone.example/jobs/1",
        application_method="job_board_listing",
        apply_platform="stepstone.de",
    )
    report = SearchReport(
        run_id="test-run",
        created_at="2026-06-04T00:00:00Z",
        mode="live",
        top_n=2,
        max_candidates=10,
        source_health=[_health("linkedin_public", 1), _health("stepstone_public", 1)],
        results=[
            SearchResult(
                rank=1,
                listing=linkedin,
                scorecard=JobScorecard(
                    listing_key="linkedin-ai",
                    score=95,
                    recommendation="strong",
                    selected=True,
                ),
            ),
            SearchResult(
                rank=2,
                listing=stepstone,
                scorecard=JobScorecard(
                    listing_key="stepstone-frontend",
                    score=88,
                    recommendation="strong",
                    selected=True,
                ),
            ),
        ],
        skipped_count=0,
        output_dir=str(tmp_path),
        results_json_path=str(tmp_path / "search_results.json"),
        results_md_path=str(tmp_path / "search_results.md"),
    )

    output_path = tmp_path / "portal_results.md"
    write_portal_report(report, output_path)

    text = output_path.read_text(encoding="utf-8")
    assert "# Job Results By Portal" in text
    assert "## linkedin (1)" in text
    assert "## stepstone.de (1)" in text
    assert "#1 [strong 95] AI Automation Engineer" in text
    assert "linkedin_public: available" in text


def test_reports_include_direct_applyability_counts(tmp_path):
    ats = JobListing(
        source="personio_xml",
        source_url="https://jobs.personio.de/acme/123",
        title="AI Experience Designer",
        company="Acme",
        apply_url="https://jobs.personio.de/acme/job/123",
        application_method="ats_form",
        apply_platform="personio",
    )
    company = JobListing(
        source="public_search",
        source_url="https://example.com/jobs/product-ai",
        title="Product AI Specialist",
        company="Example",
        apply_url="https://example.com/careers/product-ai",
        application_method="company_form",
        apply_platform="company",
    )
    board = JobListing(
        source="linkedin_public",
        source_url="https://linkedin.com/jobs/view/1",
        title="AI Automation Engineer",
        company="LinkedIn Co",
        apply_url="https://linkedin.com/jobs/view/1",
        application_method="linkedin_job",
        apply_platform="linkedin",
    )
    report = SearchReport(
        run_id="test-run",
        created_at="2026-06-25T00:00:00Z",
        mode="live",
        top_n=3,
        max_candidates=10,
        source_health=[_health("personio_xml", 1)],
        results=[
            _search_result(1, ats),
            _search_result(2, company),
            _search_result(3, board),
        ],
        skipped_count=0,
        output_dir=str(tmp_path),
        results_json_path=str(tmp_path / "search_results.json"),
        results_md_path=str(tmp_path / "search_results.md"),
    )

    assert report.direct_applyable_count == 2
    assert report.direct_applyable_by_method == {"ats_form": 1, "company_form": 1}

    search_output = tmp_path / "search_results.md"
    portal_output = tmp_path / "portal_results.md"
    write_search_report(report, search_output)
    write_portal_report(report, portal_output)

    search_text = search_output.read_text(encoding="utf-8")
    portal_text = portal_output.read_text(encoding="utf-8")
    assert "Direct applyable: 2" in search_text
    assert "ats_form 1" in search_text
    assert "company_form 1" in search_text
    assert "Direct applyable: 2" in portal_text


def test_reports_label_skip_counts_unambiguously(tmp_path):
    listing = JobListing(
        source="fixture",
        source_url="https://example.test/jobs/frontend",
        title="Frontend Engineer",
        company="Example GmbH",
    )
    report = SearchReport(
        run_id="test-run",
        created_at="2026-06-25T00:00:00Z",
        mode="live",
        top_n=1,
        max_candidates=10,
        source_health=[
            _health("fixtures", 5),
            SourceHealth(
                name="application_tracker",
                status="available",
                candidates_seen=12,
                candidates_returned=2,
                message="2 previously applied/rejected/ignored/unavailable jobs suppressed.",
            ),
        ],
        results=[_search_result(1, listing)],
        skipped_count=7,
        tracked_skipped_count=2,
        output_dir=str(tmp_path),
        results_json_path=str(tmp_path / "search_results.json"),
        results_md_path=str(tmp_path / "search_results.md"),
    )

    search_output = tmp_path / "search_results.md"
    portal_output = tmp_path / "portal_results.md"
    write_search_report(report, search_output)
    write_portal_report(report, portal_output)

    combined = (
        search_output.read_text(encoding="utf-8")
        + "\n"
        + portal_output.read_text(encoding="utf-8")
    )
    assert "Scoring excluded count: 7" in combined
    assert "Tracker-suppressed existing count: 2" in combined
    assert "after tracker suppression" in combined
    assert "open manual-completion cases" in combined
    assert "application_tracker: available, 2 previously" in combined
    assert "application_tracker: available, returned 2" not in combined
    assert "Skipped count:" not in combined
    assert "Tracked skipped" not in combined


def test_collect_live_jobs_enforces_global_candidate_budget(monkeypatch):
    calls: list[tuple[str, int]] = []

    def freelancermap_result(
        self: FreelancermapPublicSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("freelancermap", max_candidates))
        return SourceResult(
            [_listing("A"), _listing("B")], _health("freelancermap_public", 2)
        )

    def personio_result(
        self: PersonioXmlSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("personio", max_candidates))
        return SourceResult([_listing("C")], _health("personio_xml", 1))

    def google_result(
        self: GoogleJobsBrowserSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("google", max_candidates))
        return SourceResult([_listing("D")], _health("google_jobs_browser", 1))

    monkeypatch.setattr(FreelancermapPublicSource, "collect", freelancermap_result)
    monkeypatch.setattr(PersonioXmlSource, "collect", personio_result)
    monkeypatch.setattr(GoogleJobsBrowserSource, "collect", google_result)

    config = AppConfig(
        search=SearchSettings(market="test", max_candidates=1, host_delay_seconds=0),
        sources={
            "freelancermap": SourceSettings(
                enabled=True, urls=["https://www.freelancermap.com/projects/remote"]
            ),
            "personio": SourceSettings(
                enabled=True,
                feed_urls=["https://company.jobs.personio.de/xml?language=de"],
            ),
            "google_jobs_browser": SourceSettings(
                enabled=True, queries=["AI Automation Engineer"]
            ),
        },
    )

    listings, health = collect_live_jobs(config)

    assert len(listings) == 1
    assert [listing.title for listing in listings] == ["A"]
    assert calls == [("freelancermap", 1)]
    assert [item.name for item in health] == ["freelancermap_public"]


def test_collect_live_jobs_records_direct_applyability_per_source(monkeypatch):
    def arbeitnow_result(
        self: ArbeitnowApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        direct = _listing("Direct ATS")
        board = _listing("Board Listing")
        return SourceResult(
            [
                direct.model_copy(
                    update={
                        "application_method": "ats_form",
                        "apply_platform": "personio",
                    }
                ),
                board.model_copy(
                    update={
                        "application_method": "job_board_listing",
                        "apply_platform": "arbeitnow",
                    }
                ),
            ],
            _health("arbeitnow_api", 2),
        )

    monkeypatch.setattr(ArbeitnowApiSource, "collect", arbeitnow_result)

    config = AppConfig(
        search=SearchSettings(
            market="test",
            profile_configured=True,
            target_roles=["Frontend Engineer"],
            keywords=["React"],
            max_candidates=5,
            host_delay_seconds=0,
        ),
        sources={
            "arbeitnow": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://www.arbeitnow.com/api/job-board-api",
                    "max_candidates": 2,
                }
            ),
        },
    )

    listings, health = collect_live_jobs(config)

    assert len(listings) == 2
    assert health[0].direct_applyable_returned == 1


def test_collect_live_jobs_uses_source_budgets(monkeypatch):
    calls: list[tuple[str, int]] = []

    def arbeitnow_result(
        self: ArbeitnowApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("arbeitnow", max_candidates))
        return SourceResult(
            [_listing(f"Arbeitnow {index}") for index in range(max_candidates)],
            _health("arbeitnow_api", max_candidates),
        )

    def arbeitsagentur_result(
        self: ArbeitsagenturPublicSearchSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("arbeitsagentur", max_candidates))
        return SourceResult(
            [_listing(f"Arbeitsagentur {index}") for index in range(max_candidates)],
            _health("arbeitsagentur_public", max_candidates),
        )

    def freelancermap_result(
        self: FreelancermapPublicSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("freelancermap", max_candidates))
        return SourceResult(
            [_listing(f"Freelancermap {index}") for index in range(max_candidates)],
            _health("freelancermap_public", max_candidates),
        )

    def stepstone_result(
        self: StepstonePublicSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("stepstone", max_candidates))
        return SourceResult(
            [_listing(f"Stepstone {index}") for index in range(max_candidates)],
            _health("stepstone_public", max_candidates),
        )

    def linkedin_result(
        self: LinkedinPublicSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("linkedin", max_candidates))
        return SourceResult(
            [_listing(f"LinkedIn {index}") for index in range(max_candidates)],
            _health("linkedin_public", max_candidates),
        )

    def boards_result(
        self: PublicJobBoardSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("boards", max_candidates))
        return SourceResult(
            [_listing(f"Board {index}") for index in range(max_candidates)],
            _health("public_job_boards", max_candidates),
        )

    def remoteok_result(
        self: RemoteOkApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("remoteok", max_candidates))
        return SourceResult(
            [_listing(f"RemoteOK {index}") for index in range(max_candidates)],
            _health("remoteok_api", max_candidates),
        )

    def remotive_result(
        self: RemotiveApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("remotive", max_candidates))
        return SourceResult(
            [_listing(f"Remotive {index}") for index in range(max_candidates)],
            _health("remotive_api", max_candidates),
        )

    def scrapling_result(
        self: ScraplingPublicJobBoardSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("scrapling_boards", max_candidates))
        return SourceResult(
            [_listing(f"Scrapling Board {index}") for index in range(max_candidates)],
            _health("scrapling_public_job_boards", max_candidates),
        )

    monkeypatch.setattr(ArbeitnowApiSource, "collect", arbeitnow_result)
    monkeypatch.setattr(ArbeitsagenturPublicSearchSource, "collect", arbeitsagentur_result)
    monkeypatch.setattr(StepstonePublicSource, "collect", stepstone_result)
    monkeypatch.setattr(LinkedinPublicSource, "collect", linkedin_result)
    monkeypatch.setattr(FreelancermapPublicSource, "collect", freelancermap_result)
    monkeypatch.setattr(PublicJobBoardSource, "collect", boards_result)
    monkeypatch.setattr(RemoteOkApiSource, "collect", remoteok_result)
    monkeypatch.setattr(RemotiveApiSource, "collect", remotive_result)
    monkeypatch.setattr(ScraplingPublicJobBoardSource, "collect", scrapling_result)

    config = AppConfig(
        search=SearchSettings(market="test", max_candidates=80, host_delay_seconds=0),
        sources={
            "arbeitnow": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://www.arbeitnow.com/api/job-board-api",
                    "max_candidates": 2,
                }
            ),
            "arbeitsagentur": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "queries": ["Frontend"],
                    "locations": ["Berlin"],
                    "max_candidates": 3,
                }
            ),
            "freelancermap": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "urls": ["https://www.freelancermap.com/projects/remote"],
                    "max_candidates": 3,
                }
            ),
            "stepstone": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "urls": [
                        "https://www.stepstone.de/jobs/ai-automation-engineer/in-deutschland"
                    ],
                    "max_candidates": 6,
                }
            ),
            "linkedin": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "urls": ["https://www.linkedin.com/jobs/search/?keywords=AI"],
                    "max_candidates": 7,
                }
            ),
            "public_job_boards": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "boards": [
                        {"name": "Example Jobs", "url": "https://jobs.example.com/jobs"}
                    ],
                    "max_candidates": 4,
                }
            ),
            "remoteok": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://remoteok.com/api",
                    "include_terms": ["frontend"],
                    "max_candidates": 4,
                }
            ),
            "remotive": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://remotive.com/api/remote-jobs",
                    "queries": ["frontend"],
                    "max_candidates": 5,
                }
            ),
            "scrapling_public_job_boards": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "boards": [
                        {"name": "Example Jobs", "url": "https://jobs.example.com/jobs"}
                    ],
                    "max_candidates": 5,
                }
            ),
        },
    )

    listings, health = collect_live_jobs(config)

    assert len(listings) == 39
    assert calls == [
        ("arbeitnow", 2),
        ("arbeitsagentur", 3),
        ("stepstone", 6),
        ("linkedin", 7),
        ("freelancermap", 3),
        ("boards", 4),
        ("scrapling_boards", 5),
        ("remoteok", 4),
        ("remotive", 5),
    ]
    assert [item.name for item in health] == [
        "arbeitnow_api",
        "arbeitsagentur_public",
        "stepstone_public",
        "linkedin_public",
        "freelancermap_public",
        "public_job_boards",
        "scrapling_public_job_boards",
        "remoteok_api",
        "remotive_api",
    ]


def test_collect_live_jobs_runs_public_search_as_sparse_booster(monkeypatch):
    calls: list[tuple[str, int]] = []

    def arbeitnow_result(
        self: ArbeitnowApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("arbeitnow", max_candidates))
        return SourceResult(
            [
                JobListing(
                    source="arbeitnow_api",
                    source_url="https://www.arbeitnow.com/jobs/1",
                    title="Frontend Engineer React",
                    company="Example",
                    location="Berlin",
                    description="React TypeScript product role.",
                )
            ],
            _health("arbeitnow_api", 1),
        )

    def public_result(
        self: PublicSearchSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("public_search", max_candidates))
        return SourceResult(
            [_listing("Public Search Match")], _health("public_search", 1)
        )

    monkeypatch.setattr(ArbeitnowApiSource, "collect", arbeitnow_result)
    monkeypatch.setattr(PublicSearchSource, "collect", public_result)

    config = AppConfig(
        search=SearchSettings(
            market="test",
            profile_configured=True,
            target_roles=["Frontend Engineer"],
            keywords=["React"],
            max_candidates=5,
            host_delay_seconds=0,
        ),
        sources={
            "arbeitnow": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://www.arbeitnow.com/api/job-board-api",
                    "max_candidates": 1,
                }
            ),
            "public_search": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "mode": "booster",
                    "booster_threshold": 2,
                    "queries": ["Frontend Engineer Berlin"],
                    "max_candidates": 2,
                    "max_queries": 1,
                }
            ),
        },
    )

    listings, health = collect_live_jobs(config)

    assert [listing.title for listing in listings] == [
        "Frontend Engineer React",
        "Public Search Match",
    ]
    assert calls == [("arbeitnow", 1), ("public_search", 2)]
    assert [item.name for item in health] == ["arbeitnow_api", "public_search"]


def test_collect_live_jobs_skips_public_search_when_threshold_is_met(monkeypatch):
    calls: list[tuple[str, int]] = []

    def arbeitnow_result(
        self: ArbeitnowApiSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        calls.append(("arbeitnow", max_candidates))
        return SourceResult(
            [
                JobListing(
                    source="arbeitnow_api",
                    source_url=f"https://www.arbeitnow.com/jobs/{index}",
                    title=f"Frontend Engineer React {index}",
                    company="Example",
                    location="Berlin",
                    description="React TypeScript product role.",
                )
                for index in range(2)
            ],
            _health("arbeitnow_api", 2),
        )

    def public_result(
        self: PublicSearchSource,
        max_candidates: int = 80,
        host_delay_seconds: float = 0.5,
    ) -> SourceResult:
        raise AssertionError("SerpAPI booster should be skipped when threshold is met")

    monkeypatch.setattr(ArbeitnowApiSource, "collect", arbeitnow_result)
    monkeypatch.setattr(PublicSearchSource, "collect", public_result)

    config = AppConfig(
        search=SearchSettings(
            market="test",
            profile_configured=True,
            target_roles=["Frontend Engineer"],
            keywords=["React"],
            max_candidates=5,
            host_delay_seconds=0,
        ),
        sources={
            "arbeitnow": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "endpoint": "https://www.arbeitnow.com/api/job-board-api",
                    "max_candidates": 2,
                }
            ),
            "public_search": SourceSettings.model_validate(
                {
                    "enabled": True,
                    "mode": "booster",
                    "booster_threshold": 2,
                    "queries": ["Frontend Engineer Berlin"],
                    "max_candidates": 2,
                    "max_queries": 1,
                }
            ),
        },
    )

    listings, health = collect_live_jobs(config)

    assert len(listings) == 2
    assert calls == [("arbeitnow", 2)]
    assert [item.name for item in health] == ["arbeitnow_api", "public_search"]
    assert health[-1].status == "disabled"
    assert "Skipped SerpAPI booster" in health[-1].message


def test_run_search_filters_tracked_applied_jobs(tmp_path, monkeypatch):
    def fixture_jobs():
        return (
            [
                JobListing(
                    source="fixture",
                    source_url="https://board.example/jobs/old",
                    title="Frontend Engineer",
                    company="Applied GmbH",
                    location="Berlin",
                    description="React TypeScript frontend role.",
                    apply_url="https://applied.example/jobs/123?utm_source=x",
                ),
                JobListing(
                    source="fixture",
                    source_url="https://board.example/jobs/new",
                    title="Frontend Engineer React",
                    company="Fresh GmbH",
                    location="Berlin",
                    description="React TypeScript frontend role.",
                    apply_url="https://fresh.example/jobs/456",
                ),
            ],
            [_health("fixtures", 2)],
        )

    tracker = tmp_path / "applications.jsonl"
    tracker.write_text(
        '{"status":"applied","company":"Applied GmbH","title":"Frontend Engineer","apply_url":"https://applied.example/jobs/123"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "job_application_agent.pipeline.load_fixture_jobs", fixture_jobs
    )
    config_path = tmp_path / "search_profile.yaml"
    config_path.write_text(
        """search:
  profile_configured: true
  target_roles: [Frontend Engineer]
  keywords: [React]
sources: {}
""",
        encoding="utf-8",
    )

    report = run_search(
        mode="fixtures",
        top_n=5,
        config_path=config_path,
        output_base=tmp_path,
        tracker_paths=[tracker],
    )

    assert report.tracked_skipped_count == 1
    assert [result.listing.company for result in report.results] == ["Fresh GmbH"]
    assert any(item.name == "application_tracker" for item in report.source_health)
    preflight = json.loads(
        (Path(report.output_dir) / "pre_application_check.json").read_text(
            encoding="utf-8"
        )
    )
    assert preflight["status"] == "passed"
    assert preflight["action"] == "search"
