from __future__ import annotations

import re
from datetime import datetime


FORBIDDEN_PRIVATE_FIELDS = {
    "privateTitle",
    "privateDescription",
    "privateLocation",
    "privateAttendees",
    "calendarName",
}


def safe_note_filename(company: str, role: str, interview_date: str) -> str:
    prefix = (
        interview_date[:10] if interview_date else datetime.now().date().isoformat()
    )
    raw = (
        f"{prefix} {company or 'Unknown Company'} {role or 'Interview'} Interview Prep"
    )
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", raw).strip()
    value = re.sub(r"\s+", " ", value)
    return f"{value}.md"


def make_claim(
    claim: str,
    *,
    source_type: str,
    source_ref: str,
    confidence: str = "high",
    verified_at: str = "",
) -> dict[str, str]:
    if not claim.strip():
        raise ValueError("claim must not be empty")
    if not source_type.strip() or not source_ref.strip():
        raise ValueError("claim source_type and source_ref are required")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("confidence must be high, medium, or low")
    return {
        "claim": claim.strip(),
        "sourceType": source_type.strip(),
        "sourceRef": source_ref.strip(),
        "confidence": confidence,
        "verifiedAt": verified_at or datetime.now().astimezone().isoformat(),
    }


def validate_claims(claims: list[dict[str, str]]) -> None:
    required = {"claim", "sourceType", "sourceRef", "confidence", "verifiedAt"}
    for claim in claims:
        missing = [
            field for field in required if not str(claim.get(field) or "").strip()
        ]
        if missing:
            raise ValueError(f"claim is missing required fields: {missing}")


def render_prep_note(
    *,
    company: str,
    role: str,
    interview_start: str,
    source_message_id: str,
    conversation_id: str = "",
    calendar_event_id: str = "",
    claims: list[dict[str, str]] | None = None,
    meeting_link: str = "",
    uncertain: list[str] | None = None,
    sources: list[str] | None = None,
) -> str:
    claims = claims or []
    validate_claims(claims)
    high_confidence = [claim for claim in claims if claim.get("confidence") != "low"]
    low_confidence = [claim for claim in claims if claim.get("confidence") == "low"]
    uncertain_items = list(uncertain or []) + [
        claim["claim"] for claim in low_confidence
    ]

    lines = [
        "---",
        f"company: {company}",
        f"role: {role}",
        f"interview_start: {interview_start}",
        f"source_message_id: {source_message_id}",
        f"conversation_id: {conversation_id}",
        f"calendar_event_id: {calendar_event_id}",
        "status: planned",
        "---",
        "",
        f"# {company} - {role} Interview Prep",
        "",
        "## Termin",
        "",
        f"- Zeit: {interview_start or 'nicht gesetzt'}",
        f"- Meetinglink: {meeting_link or 'nicht gefunden'}",
        f"- Outlook Message: {source_message_id}",
        "",
        "## Gesicherte Punkte",
        "",
    ]
    if high_confidence:
        for claim in high_confidence:
            lines.append(
                f"- {claim['claim']} [{claim['sourceType']}: {claim['sourceRef']}, {claim['confidence']}, {claim['verifiedAt']}]"
            )
    else:
        lines.append("- Noch keine gesicherten Zusatzpunkte.")
    lines.extend(["", "## Unsicher / nicht bestaetigt", ""])
    if uncertain_items:
        lines.extend(f"- {item}" for item in uncertain_items)
    else:
        lines.append("- Keine unsicheren Punkte erfasst.")
    lines.extend(["", "## Vorbereitung", ""])
    lines.extend(
        [
            "- Kurz die Rolle und die eigenen relevanten Projekte verbinden.",
            "- Nach Interviewformat, naechstem Schritt und Erwartung an Vorbereitung fragen.",
            "- Keine privaten Kalenderdetails erwaehnen.",
        ]
    )
    lines.extend(["", "## Quellen", ""])
    source_lines = list(sources or [])
    for claim in claims:
        ref = f"{claim['sourceType']}: {claim['sourceRef']}"
        if ref not in source_lines:
            source_lines.append(ref)
    if source_lines:
        lines.extend(f"- {source}" for source in source_lines)
    else:
        lines.append("- Keine externen Quellen erfasst.")
    return "\n".join(lines).rstrip() + "\n"


def assert_no_private_busy_leaks(value: str, canaries: list[str]) -> None:
    lowered = value.lower()
    for canary in canaries:
        if canary and canary.lower() in lowered:
            raise ValueError(f"private busy canary leaked: {canary}")
