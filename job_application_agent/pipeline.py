from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .addressee import extract_company_from_listing, is_portal_name
from .config import ROOT, AppConfig, default_runs_dir, load_config
from .models import (
    DIRECT_APPLYABLE_METHODS,
    JobListing,
    JobScorecard,
    RunReport,
    SourceHealth,
    utc_now_iso,
)
from .models import SearchReport, SearchResult
from .package import write_application_package, write_run_report
from .preflight import run_pre_application_check
from .profile import load_candidate_profile
from .reporting import format_skip_summary, format_source_health
from .scoring import rank_listings, score_listing
from .sources import (
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
    parse_freelancermap_html,
    parse_jobposting_jsonld,
    parse_personio_xml,
    save_sanitized_cache,
)
from .tracker import (
    ApplicationTracker,
    ensure_not_suppressed,
    filter_tracked_listings,
    record_package_created,
    tracker_health,
)
from .utils import listing_key, write_json


def run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")


def fresh_output_dir(base_dir: Path, base_id: str) -> tuple[str, Path]:
    candidate_id = base_id
    candidate = base_dir / candidate_id
    counter = 2
    while candidate.exists():
        candidate_id = f"{base_id}-{counter}"
        candidate = base_dir / candidate_id
        counter += 1
    return candidate_id, candidate


def load_fixture_jobs() -> tuple[list[JobListing], list[SourceHealth]]:
    fixture_dir = ROOT / "fixtures"
    jobs: list[JobListing] = []
    jobs.extend(
        parse_personio_xml(
            (fixture_dir / "personio_sample.xml").read_text(encoding="utf-8"),
            "fixture://personio",
        )
    )
    jobs.extend(
        parse_freelancermap_html(
            (fixture_dir / "freelancermap_sample.html").read_text(encoding="utf-8"),
            "fixture://freelancermap",
        )
    )
    jobs.extend(
        parse_jobposting_jsonld(
            (fixture_dir / "jobposting_sample.html").read_text(encoding="utf-8"),
            "fixture://jobposting",
        )
    )
    return jobs, [
        SourceHealth(
            name="fixtures",
            status="available",
            candidates_seen=len(jobs),
            candidates_returned=len(jobs),
            fetched_at=utc_now_iso(),
        )
    ]


def collect_live_jobs(config: AppConfig) -> tuple[list[JobListing], list[SourceHealth]]:
    max_candidates = config.search.max_candidates
    delay = config.search.host_delay_seconds
    listings: list[JobListing] = []
    health: list[SourceHealth] = []

    def add_result(result: SourceResult) -> None:
        remaining = max_candidates - len(listings)
        if remaining <= 0:
            return
        accepted = result.listings[:remaining]
        listings.extend(accepted)
        direct_applyable_returned = sum(
            1
            for listing in accepted
            if listing.application_method in DIRECT_APPLYABLE_METHODS
        )
        health.append(
            result.health.model_copy(
                update={"direct_applyable_returned": direct_applyable_returned}
            )
        )

    def source_budget(source: object, default: int) -> int:
        remaining = max_candidates - len(listings)
        configured = int(getattr(source, "max_candidates", default))
        return max(0, min(remaining, configured))

    def rankable_count() -> int:
        policy = config.search.to_scoring_policy()
        return sum(
            1
            for listing in dedupe_listings(listings)
            if score_listing(listing, policy=policy).recommendation
            in {"strong", "review", "adjacent"}
        )

    arbeitnow = config.sources.get("arbeitnow")
    if arbeitnow and arbeitnow.enabled and len(listings) < max_candidates:
        remaining = source_budget(arbeitnow, 20)
        if remaining:
            endpoint = str(
                getattr(
                    arbeitnow, "endpoint", "https://www.arbeitnow.com/api/job-board-api"
                )
            )
            add_result(
                ArbeitnowApiSource(endpoint).collect(
                    max_candidates=remaining, host_delay_seconds=delay
                )
            )

    arbeitsagentur = config.sources.get("arbeitsagentur")
    if arbeitsagentur and arbeitsagentur.enabled and len(listings) < max_candidates:
        remaining = source_budget(arbeitsagentur, 30)
        if remaining:
            add_result(
                ArbeitsagenturPublicSearchSource(
                    queries=getattr(arbeitsagentur, "queries", []),
                    locations=getattr(arbeitsagentur, "locations", []),
                    radius_km=int(getattr(arbeitsagentur, "radius_km", 25)),
                    page_size=int(getattr(arbeitsagentur, "page_size", 25)),
                    max_pages=int(getattr(arbeitsagentur, "max_pages", 1)),
                ).collect(max_candidates=remaining, host_delay_seconds=delay)
            )

    stepstone = config.sources.get("stepstone")
    if stepstone and stepstone.enabled and len(listings) < max_candidates:
        remaining = source_budget(stepstone, 40)
        if remaining:
            add_result(
                StepstonePublicSource(stepstone.urls).collect(
                    max_candidates=remaining, host_delay_seconds=delay
                )
            )

    linkedin = config.sources.get("linkedin")
    if linkedin and linkedin.enabled and len(listings) < max_candidates:
        remaining = source_budget(linkedin, 30)
        if remaining:
            add_result(
                LinkedinPublicSource(linkedin.urls).collect(
                    max_candidates=remaining, host_delay_seconds=delay
                )
            )

    freelancermap = config.sources.get("freelancermap")
    if freelancermap and freelancermap.enabled and len(listings) < max_candidates:
        remaining = source_budget(freelancermap, 20)
        if remaining:
            add_result(
                FreelancermapPublicSource(freelancermap.urls).collect(
                    max_candidates=remaining, host_delay_seconds=delay
                )
            )

    public_job_boards = config.sources.get("public_job_boards")
    if (
        public_job_boards
        and public_job_boards.enabled
        and len(listings) < max_candidates
    ):
        remaining = source_budget(public_job_boards, 40)
        if remaining:
            boards = getattr(public_job_boards, "boards", [])
            per_board_limit = int(getattr(public_job_boards, "per_board_limit", 8))
            add_result(
                PublicJobBoardSource(boards, per_board_limit=per_board_limit).collect(
                    max_candidates=remaining, host_delay_seconds=delay
                )
            )

    scrapling_boards = config.sources.get("scrapling_public_job_boards")
    if scrapling_boards and scrapling_boards.enabled and len(listings) < max_candidates:
        remaining = source_budget(scrapling_boards, 40)
        if remaining:
            boards = getattr(scrapling_boards, "boards", [])
            per_board_limit = int(getattr(scrapling_boards, "per_board_limit", 8))
            add_result(
                ScraplingPublicJobBoardSource(
                    boards, per_board_limit=per_board_limit
                ).collect(
                    max_candidates=remaining,
                    host_delay_seconds=delay,
                )
            )

    remoteok = config.sources.get("remoteok")
    if remoteok and remoteok.enabled and len(listings) < max_candidates:
        remaining = source_budget(remoteok, 30)
        if remaining:
            add_result(
                RemoteOkApiSource(
                    endpoint=str(
                        getattr(remoteok, "endpoint", "https://remoteok.com/api")
                    ),
                    include_terms=getattr(remoteok, "include_terms", []),
                ).collect(max_candidates=remaining, host_delay_seconds=delay)
            )

    remotive = config.sources.get("remotive")
    if remotive and remotive.enabled and len(listings) < max_candidates:
        remaining = source_budget(remotive, 30)
        if remaining:
            add_result(
                RemotiveApiSource(
                    endpoint=str(
                        getattr(
                            remotive, "endpoint", "https://remotive.com/api/remote-jobs"
                        )
                    ),
                    queries=getattr(remotive, "queries", []),
                ).collect(max_candidates=remaining, host_delay_seconds=delay)
            )

    public_search = config.sources.get("public_search")
    if public_search and public_search.enabled and len(listings) < max_candidates:
        threshold = int(getattr(public_search, "booster_threshold", 40))
        current_rankable = rankable_count()
        mode = str(getattr(public_search, "mode", "booster"))
        if mode == "always" or current_rankable < threshold:
            remaining = source_budget(public_search, 10)
            if remaining:
                add_result(
                    PublicSearchSource(
                        queries=public_search.queries,
                        location=str(getattr(public_search, "location", "Germany")),
                        gl=str(getattr(public_search, "gl", "de")),
                        hl=str(getattr(public_search, "hl", "de")),
                        google_domain=str(
                            getattr(public_search, "google_domain", "google.de")
                        ),
                        max_queries=int(getattr(public_search, "max_queries", 3)),
                    ).collect(max_candidates=remaining, host_delay_seconds=delay)
                )
        else:
            health.append(
                SourceHealth(
                    name="public_search",
                    status="disabled",
                    candidates_seen=current_rankable,
                    candidates_returned=0,
                    message=(
                        "Skipped SerpAPI booster because "
                        f"{current_rankable} rankable candidates met threshold {threshold}."
                    ),
                    fetched_at=utc_now_iso(),
                )
            )

    personio = config.sources.get("personio")
    if personio and personio.enabled and len(listings) < max_candidates:
        remaining = max_candidates - len(listings)
        add_result(
            PersonioXmlSource(personio.feed_urls).collect(
                max_candidates=remaining, host_delay_seconds=delay
            )
        )

    google_jobs = config.sources.get("google_jobs_browser")
    if google_jobs and len(listings) < max_candidates:
        remaining = max_candidates - len(listings)
        add_result(
            GoogleJobsBrowserSource(google_jobs.queries).collect(
                max_candidates=remaining, host_delay_seconds=delay
            )
        )
    return listings, health


def dedupe_listings(listings: list[JobListing]) -> list[JobListing]:
    seen: set[str] = set()
    unique: list[JobListing] = []
    for listing in listings:
        key = listing_key(
            listing.apply_url or listing.source_url, listing.title, listing.company
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(listing)
    return unique


def normalize_listing_company(listing: JobListing) -> JobListing:
    if not is_portal_name(listing.company):
        return listing
    resolved_company = extract_company_from_listing(listing)
    if not resolved_company or is_portal_name(resolved_company):
        return listing
    tags = list(dict.fromkeys([*listing.tags, "company_resolved_from_portal"]))
    return listing.model_copy(update={"company": resolved_company, "tags": tags})


def normalize_listing_companies(listings: list[JobListing]) -> list[JobListing]:
    return [normalize_listing_company(listing) for listing in listings]


def ranked_search_results(
    mode: Literal["fixtures", "live"],
    top_n: int | None = None,
    config_path: Path | None = None,
    include_tracked: bool = False,
    tracker_paths: list[Path] | None = None,
) -> tuple[
    AppConfig,
    int,
    list[JobListing],
    list[SourceHealth],
    list[tuple[JobListing, JobScorecard]],
    int,
    int,
    int,
]:
    config = load_config(config_path)
    top_n = top_n or config.search.top_n
    scoring_policy = config.search.to_scoring_policy()
    if mode == "fixtures":
        listings, health = load_fixture_jobs()
    elif mode == "live":
        listings, health = collect_live_jobs(config)
    listings = normalize_listing_companies(listings)
    listings = dedupe_listings(listings)
    tracker = ApplicationTracker.load(tracker_paths)
    listings, suppressed = filter_tracked_listings(
        listings, tracker, include_tracked=include_tracked
    )
    health.append(
        tracker_health(
            tracker,
            suppressed_count=len(suppressed),
            include_tracked=include_tracked,
        )
    )
    selected = rank_listings(listings, top_n=top_n, policy=scoring_policy)
    skipped_count = sum(
        1
        for listing in listings
        if score_listing(listing, policy=scoring_policy).recommendation == "exclude"
    )
    tracked_skipped_count = 0 if include_tracked else len(suppressed)
    open_manual_completion_count = len(tracker.manual_completion_entries())
    return (
        config,
        top_n,
        listings,
        health,
        selected,
        skipped_count,
        tracked_skipped_count,
        open_manual_completion_count,
    )


def run_search(
    mode: Literal["fixtures", "live"],
    top_n: int | None = None,
    config_path: Path | None = None,
    output_base: Path | None = None,
    include_tracked: bool = False,
    tracker_paths: list[Path] | None = None,
) -> SearchReport:
    current_run_id, output_dir = fresh_output_dir(
        output_base or default_runs_dir(), f"{run_id()}-search"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    run_pre_application_check(output_dir, "search")
    (
        config,
        top_n,
        listings,
        health,
        selected,
        skipped_count,
        tracked_skipped_count,
        open_manual_completion_count,
    ) = ranked_search_results(
        mode,
        top_n=top_n,
        config_path=config_path,
        include_tracked=include_tracked,
        tracker_paths=tracker_paths,
    )
    save_sanitized_cache(output_dir / "cache" / "sanitized_jobs.json", listings, health)
    results_json_path = output_dir / "search_results.json"
    results_md_path = output_dir / "search_results.md"
    search_results = [
        SearchResult(rank=index, listing=listing, scorecard=scorecard)
        for index, (listing, scorecard) in enumerate(selected, start=1)
    ]
    report = SearchReport(
        run_id=current_run_id,
        created_at=utc_now_iso(),
        mode=mode,
        top_n=top_n,
        max_candidates=config.search.max_candidates,
        source_health=health,
        results=search_results,
        skipped_count=skipped_count,
        tracked_skipped_count=tracked_skipped_count,
        open_manual_completion_count=open_manual_completion_count,
        output_dir=str(output_dir),
        results_json_path=str(results_json_path),
        results_md_path=str(results_md_path),
    )
    write_json(results_json_path, report)
    write_search_report(report, results_md_path)
    write_portal_report(report, output_dir / "portal_results.md")
    return report


def approved_search_results(
    report: SearchReport, approval_refs: list[str]
) -> list[SearchResult]:
    refs = {item.strip() for item in approval_refs if item.strip()}
    approved: list[SearchResult] = []
    for result in report.results:
        aliases = {
            str(result.rank),
            result.scorecard.listing_key,
            result.scorecard.listing_key[-6:],
            f"{result.rank:02d}",
        }
        if aliases & refs:
            approved.append(result)
    return approved


def ensure_approved_results_match_current_policy(
    approved: list[SearchResult], config_path: Path | None = None
) -> None:
    config = load_config(config_path)
    policy = config.search.to_scoring_policy()
    blocked: list[str] = []
    for result in approved:
        current_scorecard = score_listing(result.listing, policy=policy)
        if current_scorecard.recommendation != "exclude":
            continue
        reason = current_scorecard.exclusion_reason or "current search policy"
        blocked.append(f"{result.listing.company} - {result.listing.title}: {reason}")
    if blocked:
        details = "; ".join(blocked)
        raise ValueError(f"Approved jobs include current hard exclusions: {details}")


def create_packages_from_search_report(
    search_results_path: Path,
    approval_refs: list[str],
    output_base: Path | None = None,
    allow_tracked: bool = False,
    tracker_paths: list[Path] | None = None,
    tracker_write_path: Path | None = None,
    config_path: Path | None = None,
) -> RunReport:
    report = SearchReport.model_validate_json(
        search_results_path.read_text(encoding="utf-8")
    )
    approved = approved_search_results(report, approval_refs)
    if not approved:
        raise ValueError(
            "No approved jobs matched. Use result rank numbers or listing keys from search_results.md."
        )
    ensure_approved_results_match_current_policy(approved, config_path=config_path)
    tracker = ApplicationTracker.load(tracker_paths)
    if not allow_tracked:
        ensure_not_suppressed([result.listing for result in approved], tracker)
    profile = load_candidate_profile()
    current_run_id, output_dir = fresh_output_dir(
        output_base or default_runs_dir(), f"{report.run_id}-approved"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    packages = [
        write_application_package(
            output_dir,
            profile,
            result.listing,
            result.scorecard.model_copy(update={"selected": True}),
        )
        for result in approved
    ]
    for result, package in zip(approved, packages, strict=True):
        record_package_created(
            result.listing,
            result.scorecard.listing_key,
            Path(package.package_dir),
            path=tracker_write_path,
        )
    selected_scorecards = [
        result.scorecard.model_copy(update={"selected": True}) for result in approved
    ]
    run_report = RunReport(
        run_id=current_run_id,
        created_at=utc_now_iso(),
        mode=report.mode,
        top_n=len(approved),
        max_candidates=report.max_candidates,
        source_health=report.source_health,
        selected_jobs=selected_scorecards,
        packages=packages,
        skipped_count=report.skipped_count,
        tracked_skipped_count=report.tracked_skipped_count,
        open_manual_completion_count=report.open_manual_completion_count,
        output_dir=str(output_dir),
    )
    write_json(output_dir / "run_report.json", run_report)
    write_run_report(run_report, output_dir / "run_report.md")
    return run_report


def run_pipeline(
    mode: Literal["fixtures", "live"],
    top_n: int | None = None,
    config_path: Path | None = None,
    output_base: Path | None = None,
) -> RunReport:
    raise RuntimeError(
        "Direct package generation is disabled. Run `search` first, review search_results.md, "
        "then call `create-packages` with explicit --approve ranks or keys."
    )


def write_search_report(report: SearchReport, output_path: Path) -> None:
    health = format_source_health(report.source_health)
    sections: list[str] = []
    for recommendation, title in [
        ("strong", "Strong Matches"),
        ("review", "Review Matches"),
        ("adjacent", "Adjacent Matches"),
    ]:
        items = [
            item
            for item in report.results
            if item.scorecard.recommendation == recommendation
        ]
        if not items:
            continue
        sections.append(
            f"## {title}\n\n" + "\n".join(format_search_result(item) for item in items)
        )
    results = "\n\n".join(sections) or "_No matching jobs selected._"
    output_path.write_text(
        f"""# Job Search Results

Run: {report.run_id}
Mode: {report.mode}
Top N: {report.top_n}
Max candidates: {report.max_candidates}
Output: {report.output_dir}
Approved-package command:

```bash
uv run python -m job_application_agent create-packages {report.results_json_path} --approve 1,2
```

## Source Health
{health}

{results}

{format_skip_summary(report.skipped_count, report.tracked_skipped_count)}
Open manual-completion reminders: {report.open_manual_completion_count}
Use `uv run job-agent needs-completion --review` to mark a case applied, ignore it, or explicitly requeue it.
Direct applyable: {report.direct_applyable_count}
Direct applyable by method: {format_applyability_counts(report.direct_applyable_by_method)}
""",
        encoding="utf-8",
    )


def write_portal_report(report: SearchReport, output_path: Path) -> None:
    grouped: dict[str, list[SearchResult]] = {}
    for result in report.results:
        portal = result.listing.apply_platform or result.listing.source or "unknown"
        grouped.setdefault(portal, []).append(result)

    health = format_source_health(report.source_health)
    sections: list[str] = []
    for portal in sorted(grouped):
        rows = "\n".join(format_portal_result(item) for item in grouped[portal])
        sections.append(f"## {portal} ({len(grouped[portal])})\n\n{rows}")
    portal_results = "\n\n".join(sections) or "_No matching jobs selected._"
    output_path.write_text(
        f"""# Job Results By Portal

Run: {report.run_id}
Mode: {report.mode}
Total selected: {len(report.results)}
{format_skip_summary(report.skipped_count, report.tracked_skipped_count)}
Open manual-completion reminders: {report.open_manual_completion_count}
Direct applyable: {report.direct_applyable_count}
Direct applyable by method: {format_applyability_counts(report.direct_applyable_by_method)}

## Source Health
{health}

{portal_results}
""",
        encoding="utf-8",
    )


def format_applyability_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    ordered = [
        method
        for method in ("ats_form", "company_form", "email")
        if counts.get(method, 0)
    ]
    ordered.extend(sorted(method for method in counts if method not in set(ordered)))
    return " | ".join(f"{method} {counts[method]}" for method in ordered)


def format_search_result(result: SearchResult) -> str:
    listing = result.listing
    scorecard = result.scorecard
    strengths = "; ".join(scorecard.matched_strengths[:2]) or "-"
    concerns = "; ".join(scorecard.concerns[:2]) or "-"
    return f"""### {result.rank}. {listing.company} - {listing.title}

- Score: {scorecard.score} {scorecard.recommendation}
- Key: {scorecard.listing_key}
- Source: {listing.source}
- Location: {listing.location or "-"}
- Remote: {listing.remote_type or "-"}
- Work type: {listing.work_type or "-"}
- Application: {listing.application_method or "unknown"} via {listing.apply_platform or "unknown"}, resume {listing.resume_upload}
- Strengths: {strengths}
- Concerns: {concerns}
- URL: {listing.apply_url or listing.source_url}
"""


def format_portal_result(result: SearchResult) -> str:
    listing = result.listing
    scorecard = result.scorecard
    return (
        f"- #{result.rank} [{scorecard.recommendation} {scorecard.score}] "
        f"{listing.title} | {listing.company} | "
        f"{listing.application_method or 'unknown'} | "
        f"{listing.apply_url or listing.source_url}"
    )
