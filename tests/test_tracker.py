import json

import pytest

from job_application_agent.models import JobListing
from job_application_agent.tracker import (
    ApplicationTracker,
    default_tracker_paths,
    ensure_not_suppressed,
    filter_tracked_listings,
    load_tracker_entries,
    manual_completion_queue,
    normalize_url,
    record_package_created,
    record_status_event,
)


def _listing(
    title: str = "Frontend Engineer",
    company: str = "Example GmbH",
    apply_url: str = "https://www.example.com/jobs/123?utm_source=board",
) -> JobListing:
    return JobListing(
        source="test",
        source_url="https://board.example/jobs/123",
        title=title,
        company=company,
        apply_url=apply_url,
        description="React TypeScript product role.",
    )


def test_normalize_url_strips_tracking_and_www():
    assert (
        normalize_url("https://www.example.com/jobs/123/?utm_source=x&gclid=y&keep=1")
        == "https://example.com/jobs/123?keep=1"
    )


def test_default_tracker_paths_follow_local_runs_directory(monkeypatch, tmp_path):
    local_runs = tmp_path / "local-runs"
    monkeypatch.setenv("JOB_AGENT_RUNS_DIR", str(local_runs))

    assert default_tracker_paths()[1] == local_runs.resolve() / "application_ledger.json"


def test_tracker_loads_legacy_ledger_and_suppresses_final_and_open_manual_jobs(tmp_path):
    ledger = tmp_path / "application_ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "status": "applied",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    },
                    {
                        "status": "blocked_manual",
                        "company": "Manual GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://manual.example/jobs/456",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([ledger])

    filtered, suppressed = filter_tracked_listings(
        [
            _listing(),
            _listing(
                company="Manual GmbH",
                apply_url="https://manual.example/jobs/456?utm_campaign=x",
            ),
        ],
        tracker,
    )

    assert [entry.status for entry in suppressed] == ["applied", "blocked_manual"]
    assert filtered == []


def test_suppressive_entry_wins_over_earlier_package_created(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "package_created",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
                json.dumps(
                    {
                        "status": "applied",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(_listing())

    assert entry is not None
    assert entry.status == "applied"


def test_later_needs_completion_remains_suppressed_and_open_for_review(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "applied",
                        "status_at": "2026-06-22T10:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
                json.dumps(
                    {
                        "status": "needs_completion",
                        "status_at": "2026-06-22T11:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    suppression = tracker.suppression_for_listing(_listing())
    assert suppression is not None
    assert suppression.status == "needs_completion"
    entry = tracker.match_for_listing(_listing())
    assert entry is not None
    assert entry.status == "needs_completion"
    assert len(tracker.manual_completion_entries()) == 1


def test_manual_completion_requires_explicit_requeue_before_returning_to_search(tmp_path):
    events = tmp_path / "applications.jsonl"
    listing = _listing()
    record_status_event(
        listing,
        "needs_completion",
        method="agent_playwright_fill",
        notes=["CAPTCHA requires manual completion."],
        path=events,
    )

    blocked_tracker = ApplicationTracker.load([events])
    filtered, suppressed = filter_tracked_listings([listing], blocked_tracker)
    assert filtered == []
    assert [entry.status for entry in suppressed] == ["needs_completion"]
    assert len(blocked_tracker.manual_completion_entries()) == 1

    record_status_event(
        listing,
        "requeued",
        method="manual_completion_review",
        provenance="manual_user_reported",
        notes=["User chose to reconsider this existing application in a later run."],
        path=events,
    )

    reopened_tracker = ApplicationTracker.load([events])
    filtered, suppressed = filter_tracked_listings([listing], reopened_tracker)
    assert filtered == [listing]
    assert suppressed == []
    assert reopened_tracker.manual_completion_entries() == []


def test_manual_completion_queue_keeps_only_latest_open_handoffs(tmp_path):
    events = tmp_path / "applications.jsonl"
    first = _listing(title="Product Manager", company="First GmbH")
    second = _listing(title="Designer", company="Second GmbH")
    record_status_event(
        first,
        "needs_completion",
        method="agent_playwright_fill",
        notes=["Salary field requires manual input."],
        path=events,
    )
    record_status_event(
        second,
        "blocked_captcha",
        method="agent_playwright_fill",
        notes=["CAPTCHA detected before any fill."],
        path=events,
    )
    record_status_event(
        first,
        "applied",
        method="manual_user_reported",
        path=events,
    )

    queue = manual_completion_queue([events])

    assert len(queue) == 1
    assert queue[0]["company"] == "Second GmbH"
    assert queue[0]["status"] == "blocked_captcha"
    assert queue[0]["notes"] == ["CAPTCHA detected before any fill."]


def test_later_response_received_does_not_clear_prior_applied_suppression(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "applied",
                        "status_at": "2026-06-22T10:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
                json.dumps(
                    {
                        "status": "response_received",
                        "status_at": "2026-06-22T11:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    latest = tracker.match_for_listing(_listing())
    suppression = tracker.suppression_for_listing(_listing())

    assert latest is not None
    assert latest.status == "response_received"
    assert suppression is not None
    assert suppression.status == "applied"


def test_response_received_does_not_clear_open_manual_completion_reminder(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "needs_completion",
                        "status_at": "2026-06-22T10:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
                json.dumps(
                    {
                        "status": "response_received",
                        "status_at": "2026-06-22T11:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    queue = manual_completion_queue([events])

    assert len(queue) == 1
    assert queue[0]["status"] == "needs_completion"


def test_later_package_created_does_not_override_confirmed_applied_status(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "status": "applied",
                        "status_at": "2026-06-22T10:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
                json.dumps(
                    {
                        "status": "package_created",
                        "created_at": "2026-06-22T11:00:00Z",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(_listing())

    assert entry is not None
    assert entry.status == "applied"


def test_title_company_match_suppresses_when_url_changes(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        json.dumps(
            {
                "status": "closed_unavailable",
                "company": "Example GmbH",
                "title": "Frontend Engineer",
                "apply_url": "https://old.example/jobs/123",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(
        _listing(apply_url="https://new.example/jobs/abc")
    )

    assert entry is not None
    assert entry.status == "closed_unavailable"


def test_manual_user_finished_event_preserves_provenance_and_suppresses(tmp_path):
    events = tmp_path / "applications.jsonl"
    record_status_event(
        _listing(company="Caya GmbH"),
        "applied",
        method="manual_join_by_user",
        provenance="manual_user_reported",
        evidence="final submit user-reported",
        notes=["User reported final JOIN confirmation."],
        path=events,
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(_listing(company="Caya GmbH"))

    assert entry is not None
    assert entry.status == "applied"
    assert entry.method == "manual_join_by_user"
    assert entry.provenance == "manual_user_reported"
    assert entry.evidence == "final submit user-reported"
    assert entry.applied_at


def test_tracker_uses_actual_company_when_raw_company_is_portal(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        json.dumps(
            {
                "status": "applied",
                "company": "join.com",
                "actual_company": "Caya GmbH",
                "title": "Customer Success Manager",
                "apply_url": "https://join.com/companies/caya/jobs/csm",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(
        _listing(
            title="Customer Success Manager",
            company="Caya GmbH",
            apply_url="https://join.com/companies/caya/jobs/csm?utm_source=x",
        )
    )

    assert entry is not None
    assert entry.company == "Caya GmbH"


def test_tracker_matches_language_variants_and_legal_suffixes(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        json.dumps(
            {
                "status": "applied",
                "company": "Hypatos GmbH",
                "title": "Senior Customer Success Manager",
                "apply_url": "https://hypatos-gmbh.jobs.personio.de/job/557727?language=en",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tracker = ApplicationTracker.load([events])

    entry = tracker.suppression_for_listing(
        _listing(
            title="Senior Customer Success Manager",
            company="Hypatos",
            apply_url="https://hypatos-gmbh.jobs.personio.de/job/557727?language=de",
        )
    )

    assert entry is not None
    assert entry.status == "applied"


def test_status_writer_persists_actual_company_for_portal_listing(tmp_path):
    events = tmp_path / "applications.jsonl"
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/csm",
        apply_url="https://join.com/companies/caya/jobs/csm/apply",
        title="Customer Success Manager",
        company="join.com",
        description="Caya GmbH sucht Unterstützung für Customer Success.",
    )

    event = record_status_event(
        listing,
        "applied",
        method="manual_join_by_user",
        path=events,
    )

    assert event["company"] == "join.com"
    assert event["actual_company"] == "Caya GmbH"
    loaded = load_tracker_entries(events)
    assert loaded[0].company == "Caya GmbH"


def test_package_writer_persists_actual_company_for_portal_listing(tmp_path):
    events = tmp_path / "applications.jsonl"
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/csm",
        apply_url="https://join.com/companies/caya/jobs/csm/apply",
        title="Customer Success Manager",
        company="join.com",
        description="Caya GmbH sucht Unterstützung für Customer Success.",
    )

    record_package_created(
        listing,
        "listing-key",
        tmp_path / "package",
        path=events,
    )

    event = json.loads(events.read_text(encoding="utf-8"))
    assert event["company"] == "join.com"
    assert event["actual_company"] == "Caya GmbH"


def test_ensure_not_suppressed_blocks_approved_tracked_jobs(tmp_path):
    events = tmp_path / "applications.jsonl"
    events.write_text(
        json.dumps(
            {
                "status": "applied",
                "company": "Example GmbH",
                "title": "Frontend Engineer",
                "apply_url": "https://example.com/jobs/123",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tracker = ApplicationTracker(load_tracker_entries(events))

    with pytest.raises(ValueError, match="Use --allow-tracked"):
        ensure_not_suppressed([_listing()], tracker)
