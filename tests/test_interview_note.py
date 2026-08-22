import pytest

from job_application_agent.interview_note import (
    assert_no_private_busy_leaks,
    make_claim,
    render_prep_note,
    safe_note_filename,
)


def test_render_note_requires_claim_sources_and_includes_provenance():
    claim = make_claim(
        "Marie Stein ist Kontaktperson fuer das Interview.",
        source_type="email",
        source_ref="msg-1",
        confidence="high",
        verified_at="2026-06-26T12:00:00+02:00",
    )

    note = render_prep_note(
        company="Example",
        role="Automation Engineer",
        interview_start="2026-06-30T14:00:00+02:00",
        source_message_id="msg-1",
        conversation_id="conv-1",
        claims=[claim],
    )

    assert "Marie Stein ist Kontaktperson" in note
    assert "email: msg-1" in note
    assert "2026-06-26T12:00:00+02:00" in note


def test_low_confidence_claim_goes_to_uncertain_section():
    claim = make_claim(
        "Personenmatch aus Websuche ist unsicher.",
        source_type="public_web",
        source_ref="https://example.com/person",
        confidence="low",
        verified_at="2026-06-26T12:00:00+02:00",
    )

    note = render_prep_note(
        company="Example",
        role="Automation Engineer",
        interview_start="2026-06-30T14:00:00+02:00",
        source_message_id="msg-1",
        claims=[claim],
    )

    uncertain = note.split("## Unsicher / nicht bestaetigt", 1)[1]
    assert "Personenmatch aus Websuche ist unsicher." in uncertain


def test_missing_claim_source_is_rejected():
    with pytest.raises(ValueError):
        make_claim("Unbelegte Aussage", source_type="", source_ref="")


def test_safe_filename_removes_problematic_characters():
    assert safe_note_filename(
        "ACME GmbH!", "AI/Automation: Engineer", "2026-06-30"
    ) == ("2026-06-30 ACME GmbH AIAutomation Engineer Interview Prep.md")


def test_private_busy_canary_detection():
    with pytest.raises(ValueError, match="private busy canary leaked"):
        assert_no_private_busy_leaks("Text mit Secret Dentist", ["Secret Dentist"])
