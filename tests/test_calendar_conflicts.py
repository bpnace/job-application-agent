from job_application_agent.calendar_conflicts import (
    conflicts_for_slot,
    redact_busy_events,
    split_free_and_conflicting_slots,
)


def test_busy_overlap_with_buffer_blocks_slot():
    slot = {"start": "2026-06-30T14:00:00+02:00", "end": "2026-06-30T14:45:00+02:00"}
    busy = [
        {
            "start": "2026-06-30T13:50:00+02:00",
            "end": "2026-06-30T14:05:00+02:00",
            "summary": "Private Arzt Sache",
            "location": "Private Location",
            "sourceAlias": "privatvergnuegen",
        }
    ]

    conflicts = conflicts_for_slot(slot, busy, buffer_minutes=15)

    assert conflicts == [
        {
            "start": "2026-06-30T13:50:00+02:00",
            "end": "2026-06-30T14:05:00+02:00",
            "busy": True,
            "sourceAliasRedacted": "blocking-calendar",
        }
    ]
    assert "Private Arzt Sache" not in str(conflicts)
    assert "Private Location" not in str(conflicts)


def test_all_day_and_tentative_block_and_target_calendar_blocks():
    slots = [
        {"start": "2026-06-30T10:00:00+02:00", "end": "2026-06-30T10:45:00+02:00"},
        {"start": "2026-07-01T10:00:00+02:00", "end": "2026-07-01T10:45:00+02:00"},
    ]
    busy = [
        {
            "start": "2026-06-30",
            "end": "2026-07-01",
            "allDay": True,
            "sourceAlias": "privat",
        },
        {
            "start": "2026-07-01T09:30:00+02:00",
            "end": "2026-07-01T11:00:00+02:00",
            "status": "tentative",
            "sourceAlias": "geschaeftlich",
        },
    ]

    free, blocked = split_free_and_conflicting_slots(slots, busy, buffer_minutes=0)

    assert free == []
    assert len(blocked) == 2
    assert all(item["conflicts"] for item in blocked)


def test_transparent_events_are_not_blocking_and_redaction_removes_details():
    busy = [
        {
            "start": "2026-06-30T10:00:00+02:00",
            "end": "2026-06-30T11:00:00+02:00",
            "transparency": "transparent",
            "summary": "Private Secret",
        }
    ]

    redacted = redact_busy_events(busy)

    assert redacted == []
