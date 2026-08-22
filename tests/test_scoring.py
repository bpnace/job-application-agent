from datetime import date

from job_application_agent.config import load_config
from job_application_agent.models import JobListing, ScoringPolicy
from job_application_agent.scoring import rank_listings, score_listing


def configured_policy() -> ScoringPolicy:
    return ScoringPolicy(
        profile_configured=True,
        target_roles=["Product Manager"],
        keywords=["B2B", "SaaS"],
        hard_exclusions=["Praktikum", "Werkstudent"],
        employer_blacklist=["ExampleCorp"],
        preferred_locations=["Deutschland", "remote"],
        required_location_terms=["Deutschland", "remote"],
    )


def matching_listing() -> JobListing:
    return JobListing(
        source="fixture",
        source_url="fixture://product-manager",
        title="Product Manager",
        company="Example Studio GmbH",
        location="Deutschland",
        remote_type="Hybrid",
        work_type="Festanstellung",
        description="B2B SaaS product role.",
        apply_url="https://example.com/job",
    )


def test_unconfigured_profile_never_selects_a_listing():
    score = score_listing(matching_listing())

    assert score.recommendation == "weak"
    assert score.selected is False
    assert "not configured" in score.concerns[0]


def test_configured_profile_scores_only_its_role_keywords_and_location():
    score = score_listing(matching_listing(), policy=configured_policy())

    assert score.recommendation == "strong"
    assert score.selected is True
    assert score.score_breakdown["configured_title"] > 0
    assert score.score_breakdown["configured_keywords"] > 0
    assert score.score_breakdown["preferred_location"] > 0


def test_configured_profile_excludes_user_defined_blockers_and_employers():
    policy = configured_policy()
    blocked_term = matching_listing().model_copy(
        update={"source_url": "fixture://blocked-term", "title": "Product Manager Praktikum"}
    )
    blocked_employer = matching_listing().model_copy(
        update={"source_url": "fixture://blocked-employer", "company": "ExampleCorp SE"}
    )

    assert score_listing(blocked_term, policy=policy).recommendation == "exclude"
    assert score_listing(blocked_employer, policy=policy).recommendation == "exclude"


def test_configured_profile_excludes_nonmatching_location():
    listing = matching_listing().model_copy(
        update={"source_url": "fixture://other-location", "location": "Other country"}
    )

    score = score_listing(listing, policy=configured_policy())

    assert score.recommendation == "exclude"
    assert "Location does not match" in (score.exclusion_reason or "")


def test_configured_profile_excludes_stale_postings(monkeypatch):
    monkeypatch.setattr(
        "job_application_agent.scoring._today_utc", lambda: date(2026, 6, 10)
    )
    listing = matching_listing().model_copy(update={"date_posted": "2026-05-19"})

    score = score_listing(listing, policy=configured_policy())

    assert score.recommendation == "exclude"
    assert "22 days old" in (score.exclusion_reason or "")


def test_rank_listings_prefers_newer_and_direct_applyable_matches(monkeypatch):
    monkeypatch.setattr(
        "job_application_agent.scoring._today_utc", lambda: date(2026, 6, 10)
    )
    policy = configured_policy()
    board = matching_listing().model_copy(
        update={
            "source_url": "fixture://board",
            "date_posted": "2026-06-02",
            "application_method": "job_board_listing",
        }
    )
    direct = matching_listing().model_copy(
        update={
            "source_url": "fixture://direct",
            "date_posted": "2026-06-09",
            "application_method": "ats_form",
        }
    )

    selected = rank_listings([board, direct], top_n=2, policy=policy)

    assert [listing.source_url for listing, _ in selected] == [
        "fixture://direct",
        "fixture://board",
    ]


def test_bundled_search_profile_requires_local_setup():
    config = load_config()
    policy = config.search.to_scoring_policy()

    assert policy.profile_configured is False
    assert policy.target_roles == []
    assert policy.keywords == []
    assert config.sources["public_search"].queries == []
