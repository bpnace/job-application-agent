import pytest

from job_application_agent.interview_scheduler import (
    classify_message,
    decide_interview_action,
    extract_candidate_slots,
    normalize_message,
    render_reschedule_reply,
)


def _message(body: str, subject: str = "Invitation - First Interview @Example"):
    return {
        "id": "msg-1",
        "conversationId": "conv-1",
        "internetMessageId": "<mail-1@example.com>",
        "subject": subject,
        "from": {"emailAddress": {"address": "jobs@example.com"}},
        "receivedDateTime": "2026-06-26T10:00:00+02:00",
        "body": body,
        "attachments": [],
    }


def test_normalize_and_classify_interview_html():
    normalized = normalize_message(
        _message("<p>Wir moechten dich zum Erstgespraech einladen.</p>")
    )

    normalized_text = normalized["normalizedText"]
    assert isinstance(normalized_text, str)
    assert "<p>" not in normalized_text
    assert classify_message(normalized) == "interview_offer"


def test_classifies_docs_request_without_interview_as_docs_request():
    normalized = normalize_message(
        _message("Bitte sende deinen Lebenslauf erneut.", "Nachfrage Lebenslauf")
    )

    assert classify_message(normalized) == "docs_request"


def test_extracts_german_and_english_slots_in_berlin_timezone():
    german = normalize_message(_message("Passt dir Dienstag, 30. Juni um 14:00 Uhr?"))
    english = normalize_message(_message("Can we meet Tuesday, June 30 at 2 pm CET?"))

    german_slots = extract_candidate_slots(german)
    english_slots = extract_candidate_slots(english)

    assert german_slots[0]["start"] == "2026-06-30T14:00:00+02:00"
    assert english_slots[0]["start"] == "2026-06-30T14:00:00+02:00"


def test_ics_slot_wins_as_candidate_source():
    normalized = normalize_message(
        {
            **_message("Please see attached invite."),
            "attachments": [
                {
                    "name": "invite.ics",
                    "content": "BEGIN:VEVENT\nDTSTART:20260630T120000Z\nDTEND:20260630T124500Z\nEND:VEVENT",
                }
            ],
        }
    )

    slots = extract_candidate_slots(normalized)

    assert slots[0]["source"] == "ics"
    assert slots[0]["start"] == "2026-06-30T14:00:00+02:00"


def test_autosend_true_is_hard_error():
    with pytest.raises(ValueError, match="autoSend=true"):
        decide_interview_action(
            {
                "autoSend": True,
                "message": _message("Interview am 30. Juni um 14:00 Uhr"),
            }
        )


def test_missing_geschaeftlich_mapping_blocks_calendar_write():
    result = decide_interview_action(
        {
            "mode": "calendar_write",
            "message": _message("Passt dir Dienstag, 30. Juni um 14:00 Uhr?"),
            "calendarRegistry": [],
            "busy": [],
            "company": "Example",
            "role": "Automation Engineer",
        }
    )

    assert result["decision"] == "manual_review"
    assert result["reason"] == "missing_geschaeftlich_mapping"
    assert result["sendStatus"] == "NOT_SENT"


def test_free_slot_creates_event_plan_without_attendees_when_target_mapped():
    result = decide_interview_action(
        {
            "mode": "calendar_write",
            "message": _message("Passt dir Dienstag, 30. Juni um 14:00 Uhr?"),
            "calendarRegistry": [
                {
                    "alias": "geschaeftlich",
                    "calendarId": "business-calendar-id",
                    "isTarget": True,
                    "isBlocking": True,
                }
            ],
            "busy": [],
            "company": "Example",
            "role": "Automation Engineer",
        }
    )

    assert result["decision"] == "created_event_plan"
    event_plan = result["calendarEventPlan"]
    assert isinstance(event_plan, dict)
    assert event_plan["calendarAlias"] == "geschaeftlich"
    assert event_plan["attendees"] == []
    assert result["allowedSideEffects"] == ["calendar_event"]


def test_conflict_creates_draft_plan_and_redacts_private_busy_data():
    result = decide_interview_action(
        {
            "mode": "draft_only",
            "message": _message("Passt dir Dienstag, 30. Juni um 14:00 Uhr?"),
            "busy": [
                {
                    "start": "2026-06-30T13:45:00+02:00",
                    "end": "2026-06-30T15:00:00+02:00",
                    "summary": "Private Secret Dentist",
                    "location": "Secret Location",
                    "sourceAlias": "Privatvergnuegen",
                }
            ],
            "company": "Example",
            "role": "Automation Engineer",
        }
    )

    serialized = str(result)
    assert result["decision"] == "create_reschedule_draft_plan"
    reply_plan = result["replyDraftPlan"]
    assert isinstance(reply_plan, dict)
    assert reply_plan["sendStatus"] == "NOT_SENT"
    assert "Private Secret Dentist" not in serialized
    assert "Secret Location" not in serialized
    assert "Privatvergnuegen" not in serialized


def test_multiple_free_slots_go_to_manual_review_without_selection_rule():
    result = decide_interview_action(
        {
            "mode": "calendar_write",
            "message": _message(
                "Passt dir Dienstag, 30. Juni um 14:00 Uhr oder Mittwoch, 1. Juli um 10:00 Uhr?"
            ),
            "calendarRegistry": [
                {"alias": "geschaeftlich", "calendarId": "business", "isTarget": True}
            ],
            "busy": [],
        }
    )

    assert result["decision"] == "manual_review"
    assert result["reason"] == "multiple_free_slots_without_rule"


def test_vague_time_goes_to_manual_review():
    result = decide_interview_action(
        {
            "mode": "dry_run",
            "message": _message("Koennen wir naechste Woche vormittags sprechen?"),
        }
    )

    assert result["decision"] == "manual_review"
    assert result["reason"] == "vague_time_request"


def test_reply_body_contains_no_private_details():
    body = render_reschedule_reply(
        company="Example",
        role="Automation Engineer",
        original_slots=[],
        alternatives=[
            {"start": "2026-07-01T10:00:00+02:00", "end": "2026-07-01T10:45:00+02:00"}
        ],
    )

    assert "Privatvergnuegen" not in body
    assert "Private" not in body
    assert "2026-07-01T10:00:00+02:00" in body
