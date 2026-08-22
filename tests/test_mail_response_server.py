import json

from job_application_agent.mail_response_server import handle_mail_response_request


def test_mail_response_webhook_handler_records_tracker_event(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    status_code, response = handle_mail_response_request(
        {
            "classification": "rejection",
            "message_id": "reply-webhook",
            "company": "Example GmbH",
            "title": "Frontend Engineer",
            "apply_url": "https://example.com/jobs/123",
        },
        tracker_path=tracker_path,
    )

    assert status_code == 200
    assert response["status"] == "rejected"
    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert event["status"] == "rejected"
    assert event["evidence"]["message_id"] == "reply-webhook"


def test_mail_response_webhook_handler_reports_bad_payload_without_write(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    status_code, response = handle_mail_response_request(
        {"classification": "rejection"},
        tracker_path=tracker_path,
    )

    assert status_code == 400
    assert "error" in response
    assert not tracker_path.exists()
