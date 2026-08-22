import json

import pytest

from job_application_agent.mail_response import import_mail_response
from job_application_agent.models import JobListing
from job_application_agent.tracker import ApplicationTracker


def _listing() -> JobListing:
    return JobListing(
        source="personio",
        source_url="https://example.jobs.personio.de/job/123",
        apply_url="https://example.jobs.personio.de/job/123?language=de",
        title="Frontend Engineer",
        company="Example GmbH",
        description="Build product surfaces for B2B users.",
    )


def _write_package(tmp_path, listing: JobListing | None = None):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    listing = listing or _listing()
    (package_dir / "job.json").write_text(listing.model_dump_json(), encoding="utf-8")
    return package_dir, listing


def _single_event(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_import_mail_response_records_rejection_from_n8n_payload(tmp_path):
    package_dir, listing = _write_package(tmp_path)
    tracker_path = tmp_path / "applications.jsonl"

    result = import_mail_response(
        {
            "classification": "rejection",
            "message_id": "AAMkAGI2",
            "internet_message_id": "<reply-123@example.com>",
            "from": "jobs@example.com",
            "subject": "Ihre Bewerbung bei Example GmbH",
            "received_at": "2026-06-26T10:00:00+02:00",
            "body_excerpt": "Leider koennen wir Ihre Bewerbung nicht weiter beruecksichtigen.",
            "package_dir": str(package_dir),
        },
        tracker_path=tracker_path,
    )

    assert result.status == "rejected"
    assert result.matched_by == "package_dir"
    event = _single_event(tracker_path)
    assert event["status"] == "rejected"
    assert event["method"] == "mail_response"
    assert event["provenance"] == "n8n_email_monitor"
    assert event["evidence"]["message_id"] == "AAMkAGI2"
    assert event["evidence"]["classification"] == "rejection"
    assert event["package_dir"] == str(package_dir)
    tracker = ApplicationTracker.load([tracker_path])
    suppression = tracker.suppression_for_listing(listing)
    assert suppression is not None
    assert suppression.status == "rejected"


def test_response_received_is_stateful_without_suppressing_search(tmp_path):
    listing = _listing()
    tracker_path = tmp_path / "applications.jsonl"

    result = import_mail_response(
        {
            "classification": "response_received",
            "message_id": "AAMk-response",
            "from": "talent@example.com",
            "subject": "Rueckmeldung zu Ihrer Bewerbung",
            "received_at": "2026-06-26T11:00:00+02:00",
            "company": listing.company,
            "title": listing.title,
            "apply_url": listing.apply_url,
            "reply_category": "interview_or_scheduling",
            "interview_detected": "yes",
            "proposed_times": "Mo 10:00 | Di 14:00",
            "scheduler_decision": "manual_review",
        },
        tracker_path=tracker_path,
    )

    assert result.status == "response_received"
    tracker = ApplicationTracker.load([tracker_path])
    entry = tracker.match_for_listing(listing)
    assert entry is not None
    assert entry.status == "response_received"
    assert isinstance(entry.evidence, dict)
    assert entry.evidence["interview_detected"] == "yes"
    assert entry.evidence["proposed_times"] == "Mo 10:00 | Di 14:00"
    assert entry.evidence["scheduler_decision"] == "manual_review"
    event = _single_event(tracker_path)
    assert event["reply_events"][0]["status"] == "response_received"
    assert event["interview_rounds"][0]["stage"] == "interview"
    assert event["scheduling"]["status"] == "manual_review"
    assert event["scheduling"]["proposed_times"] == ["Mo 10:00", "Di 14:00"]
    assert tracker.suppression_for_listing(listing) is None


def test_mail_response_minimizes_sensitive_outlook_fields(tmp_path):
    listing = _listing()
    tracker_path = tmp_path / "applications.jsonl"

    import_mail_response(
        {
            "classification": "response_received",
            "message_id": "privacy-canary",
            "from": "secret.sender@example.com",
            "to": "candidate@example.test",
            "subject": "SECRET_SUBJECT Interview",
            "body_excerpt": "SECRET_BODY please use https://teams.microsoft.com/l/SECRET_MEETING",
            "meeting_url": "https://teams.microsoft.com/l/SECRET_MEETING",
            "mail_url": "https://outlook.live.com/mail/SECRET_MAIL",
            "company": listing.company,
            "title": listing.title,
            "apply_url": listing.apply_url,
            "interview_detected": "yes",
            "reply_type": "interview",
            "scheduler_decision": "manual_review",
        },
        tracker_path=tracker_path,
    )

    raw = tracker_path.read_text(encoding="utf-8")

    assert "SECRET_SUBJECT" not in raw
    assert "SECRET_BODY" not in raw
    assert "SECRET_MEETING" not in raw
    assert "SECRET_MAIL" not in raw
    assert "secret.sender@example.com" not in raw


def test_mail_response_fallback_source_url_uses_opaque_mail_token(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    import_mail_response(
        {
            "classification": "response_received",
            "message_id": "AAMk-sensitive-graph-id",
            "internet_message_id": "<sensitive-thread@example.invalid>",
            "company": "Opaque GmbH",
            "title": "AI Engineer",
        },
        tracker_path=tracker_path,
    )

    event = _single_event(tracker_path)

    assert event["source_url"].startswith("mail-response:")
    assert "AAMk-sensitive-graph-id" not in event["source_url"]
    assert "sensitive-thread@example.invalid" not in event["source_url"]
    assert event["reply_events"][0]["id"].startswith("mail-event:")
    assert "AAMk-sensitive-graph-id" not in event["reply_events"][0]["id"]
    assert "sensitive-thread@example.invalid" not in event["reply_events"][0]["id"]


def test_sent_application_from_outlook_marks_applied(tmp_path):
    listing = _listing()
    tracker_path = tmp_path / "applications.jsonl"

    result = import_mail_response(
        {
            "classification": "sent_application",
            "message_id": "sent-123",
            "source_folder": "sentitems",
            "from": "candidate@example.test",
            "subject": "Bewerbung als Frontend Engineer",
            "company": listing.company,
            "title": listing.title,
            "apply_url": listing.apply_url,
        },
        tracker_path=tracker_path,
    )

    assert result.status == "applied"
    event = _single_event(tracker_path)
    assert event["status"] == "applied"
    assert event["evidence"]["source_folder"] == "sentitems"
    tracker = ApplicationTracker.load([tracker_path])
    suppression = tracker.suppression_for_listing(listing)
    assert suppression is not None
    assert suppression.status == "applied"


def test_company_only_payload_matches_unique_tracker_entry(tmp_path):
    listing = _listing()
    tracker_path = tmp_path / "applications.jsonl"
    tracker_path.write_text(
        json.dumps(
            {
                "status": "applied",
                "status_at": "2026-06-25T10:00:00Z",
                "company": listing.company,
                "title": listing.title,
                "apply_url": listing.apply_url,
                "source_url": listing.source_url,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = import_mail_response(
        {
            "classification": "rejection",
            "message_id": "reply-company-only",
            "subject": "Ihre Bewerbung bei Example GmbH",
            "company": listing.company,
        },
        tracker_path=tracker_path,
    )

    assert result.status == "rejected"
    assert result.matched_by == "tracker_company"
    tracker = ApplicationTracker.load([tracker_path])
    suppression = tracker.suppression_for_listing(listing)
    assert suppression is not None
    assert suppression.status == "rejected"


def test_company_only_payload_ignores_review_only_tracker_entries(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    tracker_path.write_text(
        json.dumps(
            {
                "status": "review_required",
                "method": "mail_response_review",
                "status_at": "2026-06-25T10:00:00Z",
                "company": "Capmo",
                "title": "Unmatched Outlook response",
                "source_url": "mail-response:<raw-thread@example.invalid>",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="company/title, apply_url, or package_dir"):
        import_mail_response(
            {
                "classification": "response_received",
                "message_id": "later-company-only",
                "company": "Capmo",
            },
            tracker_path=tracker_path,
        )


def test_import_mail_response_skips_duplicate_mail_message(tmp_path):
    package_dir, _listing = _write_package(tmp_path)
    tracker_path = tmp_path / "applications.jsonl"
    payload = {
        "classification": "rejection",
        "message_id": "reply-duplicate",
        "internet_message_id": "<reply-duplicate@example.com>",
        "subject": "Ihre Bewerbung bei Example GmbH",
        "package_dir": str(package_dir),
    }

    first = import_mail_response(payload, tracker_path=tracker_path)
    second = import_mail_response(payload, tracker_path=tracker_path)

    assert first.duplicate is False
    assert second.duplicate is True
    lines = tracker_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_import_mail_response_refuses_ambiguous_payload_without_identity(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    with pytest.raises(ValueError, match="company/title, apply_url, or package_dir"):
        import_mail_response(
            {
                "classification": "rejection",
                "message_id": "AAMk-no-match",
                "subject": "Ihre Bewerbung",
            },
            tracker_path=tracker_path,
        )

    assert not tracker_path.exists()
