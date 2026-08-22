from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
import re
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from .calendar_conflicts import (
    DEFAULT_TIMEZONE,
    redact_busy_events,
    split_free_and_conflicting_slots,
)
from .interview_note import make_claim, render_prep_note, safe_note_filename


MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "maerz": 3,
    "märz": 3,
    "mrz": 3,
    "march": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "june": 6,
    "juli": 7,
    "jul": 7,
    "july": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oktober": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dezember": 12,
    "dec": 12,
    "dez": 12,
}

INTERVIEW_TERMS = (
    "interview",
    "gespraech",
    "gespräch",
    "erstgespraech",
    "erstgespräch",
    "kennenlernen",
    "termin",
    "call",
    "meeting",
)
DOC_REQUEST_TERMS = ("lebenslauf", "cv", "resume", "unterlagen", "dokument")
REJECTION_TERMS = ("leider", "absage", "nicht weiter", "rejection", "unfortunately")
VAGUE_TIME_TERMS = (
    "naechste woche",
    "nächste woche",
    "vormittags",
    "nachmittags",
    "irgendwann",
)


@dataclass(frozen=True)
class SchedulerConfig:
    timezone: str = DEFAULT_TIMEZONE
    buffer_minutes: int = 15
    default_duration_minutes: int = 45


def normalize_message(message: dict[str, object]) -> dict[str, object]:
    body = str(message.get("body") or message.get("bodyPreview") or "")
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"</p\s*>", "\n\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    attachments = message.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    return {
        "id": str(message.get("id") or ""),
        "internetMessageId": str(message.get("internetMessageId") or ""),
        "conversationId": str(message.get("conversationId") or ""),
        "subject": str(message.get("subject") or ""),
        "from": _sender_text(message.get("from")),
        "receivedDateTime": str(message.get("receivedDateTime") or ""),
        "normalizedText": body,
        "hasIcs": any(
            str(item.get("name") or "").lower().endswith(".ics")
            for item in attachments
            if isinstance(item, dict)
        ),
        "attachments": attachments,
    }


def classify_message(normalized: dict[str, object]) -> str:
    text = _combined_text(normalized)
    if any(term in text for term in REJECTION_TERMS):
        return "rejection"
    if any(term in text for term in DOC_REQUEST_TERMS) and not any(
        term in text for term in INTERVIEW_TERMS
    ):
        return "docs_request"
    if any(term in text for term in INTERVIEW_TERMS):
        return "interview_offer"
    return "generic_reply"


def extract_candidate_slots(
    normalized: dict[str, object],
    *,
    config: SchedulerConfig = SchedulerConfig(),
) -> list[dict[str, object]]:
    base = _base_datetime(normalized, config.timezone)
    text = _combined_text(normalized)
    slots: list[dict[str, object]] = []
    slots.extend(_extract_ics_slots(normalized, config))
    slots.extend(_extract_german_slots(text, base, config))
    slots.extend(_extract_english_slots(text, base, config))
    unique: dict[tuple[str, str], dict[str, object]] = {}
    for slot in slots:
        unique[(str(slot["start"]), str(slot["end"]))] = slot
    return sorted(unique.values(), key=lambda item: str(item["start"]))


def decide_interview_action(
    payload: dict[str, object],
    *,
    config: SchedulerConfig = SchedulerConfig(),
) -> dict[str, object]:
    if bool(payload.get("autoSend")):
        raise ValueError("autoSend=true is blocked for interview scheduler v1")
    mode = str(payload.get("mode") or "dry_run")
    if mode not in {"dry_run", "draft_only", "calendar_write"}:
        raise ValueError(f"unsupported scheduler mode: {mode}")

    message = normalize_message(_dict(payload.get("message")))
    classification = classify_message(message)
    slots = extract_candidate_slots(message, config=config)
    busy_events = _list_of_dicts(payload.get("busy"))
    registry = _list_of_dicts(payload.get("calendarRegistry"))
    redacted_busy = redact_busy_events(busy_events, timezone=config.timezone)
    free_slots, blocked_slots = split_free_and_conflicting_slots(
        slots,
        busy_events,
        timezone=config.timezone,
        buffer_minutes=config.buffer_minutes,
    )
    company = str(
        payload.get("company") or _guess_company(message) or "Unknown Company"
    )
    role = str(payload.get("role") or "Interview")
    claims = _build_base_claims(message, company, role, payload)

    result: dict[str, object] = {
        "classification": classification,
        "mode": mode,
        "sendStatus": "NOT_SENT",
        "allowedSideEffects": [],
        "sourceMessageId": message["id"],
        "conversationId": message["conversationId"],
        "candidateSlots": slots,
        "freeSlots": free_slots,
        "blockedSlots": blocked_slots,
        "redactedBusy": redacted_busy,
    }

    if classification == "docs_request":
        result.update(
            {"decision": "ignored", "reason": "docs_request_routed_elsewhere"}
        )
        return result
    if classification == "rejection":
        result.update({"decision": "ignored", "reason": "rejection"})
        return result
    if classification != "interview_offer":
        result.update({"decision": "manual_review", "reason": "not_an_interview_offer"})
        return result
    if _has_vague_time(message) and not slots:
        result.update({"decision": "manual_review", "reason": "vague_time_request"})
        return result
    if not slots:
        result.update({"decision": "manual_review", "reason": "no_concrete_slot"})
        return result
    if len(free_slots) > 1:
        result.update(
            {"decision": "manual_review", "reason": "multiple_free_slots_without_rule"}
        )
        return _add_note_plan(result, company, role, free_slots[0], claims)
    if free_slots:
        selected = free_slots[0]
        if mode == "calendar_write" and not _has_target_calendar(registry):
            result.update(
                {"decision": "manual_review", "reason": "missing_geschaeftlich_mapping"}
            )
            return _add_note_plan(result, company, role, selected, claims)
        result.update(
            {
                "decision": "created_event_plan",
                "reason": "slot_free",
                "calendarEventPlan": _calendar_event_plan(
                    company, role, selected, message
                ),
                "allowedSideEffects": ["calendar_event"]
                if mode == "calendar_write"
                else [],
            }
        )
        return _add_note_plan(result, company, role, selected, claims)

    alternatives = _alternative_slots(slots, config)
    reply_body = render_reschedule_reply(
        company=company,
        role=role,
        original_slots=blocked_slots,
        alternatives=alternatives,
    )
    result.update(
        {
            "decision": "create_reschedule_draft_plan",
            "reason": "slot_conflict",
            "replyDraftPlan": {
                "sourceMessageId": message["id"],
                "conversationId": message["conversationId"],
                "sendStatus": "NOT_SENT",
                "body": reply_body,
            },
            "allowedSideEffects": ["reply_draft"]
            if mode in {"draft_only", "calendar_write"}
            else [],
        }
    )
    note_slot: dict[str, object] = slots[0] if slots else {"start": "", "end": ""}
    return _add_note_plan(result, company, role, note_slot, claims)


def render_reschedule_reply(
    *,
    company: str,
    role: str,
    original_slots: Sequence[Mapping[str, object]],
    alternatives: Sequence[Mapping[str, object]],
) -> str:
    alt_lines = "\n".join(f"- {_format_slot(slot)}" for slot in alternatives[:3])
    return (
        "Hallo,\n\n"
        "vielen Dank fuer die Einladung. Der vorgeschlagene Termin passt bei mir leider nicht.\n\n"
        "Diese Alternativen waeren bei mir frei:\n"
        f"{alt_lines}\n\n"
        "Passt einer der Termine fuer das Gespraech?\n\n"
        "Viele Gruesse\n"
        "Candidate"
    )


def _add_note_plan(
    result: dict[str, object],
    company: str,
    role: str,
    slot: Mapping[str, object],
    claims: list[dict[str, str]],
) -> dict[str, object]:
    start = str(slot.get("start") or "")
    message_id = str(result.get("sourceMessageId") or "")
    conversation_id = str(result.get("conversationId") or "")
    note = render_prep_note(
        company=company,
        role=role,
        interview_start=start,
        source_message_id=message_id,
        conversation_id=conversation_id,
        claims=claims,
    )
    result["obsidianNotePlan"] = {
        "path": safe_note_filename(company, role, start),
        "markdown": note,
    }
    result["telegramPlan"] = {
        "chatId": "5920909215",
        "decision": result.get("decision"),
        "company": company,
        "role": role,
        "time": start,
        "sendStatus": "NOT_SENT",
    }
    return result


def _calendar_event_plan(
    company: str,
    role: str,
    slot: Mapping[str, object],
    message: Mapping[str, object],
) -> dict[str, object]:
    description = "\n".join(
        [
            f"Firma: {company}",
            f"Rolle: {role}",
            f"Outlook Message: {message.get('id') or ''}",
            f"Conversation: {message.get('conversationId') or ''}",
            "Externe Attendees werden in v1 nicht automatisch eingeladen.",
        ]
    )
    return {
        "calendarAlias": "geschaeftlich",
        "summary": f"[Interview] {company} - {role}",
        "start": slot.get("start"),
        "end": slot.get("end"),
        "description": description,
        "attendees": [],
    }


def _extract_german_slots(
    text: str, base: datetime, config: SchedulerConfig
) -> list[dict[str, object]]:
    pattern = re.compile(
        r"(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)?"
        r"[\s,]*(\d{1,2})\.\s*"
        r"(januar|jan|februar|feb|maerz|märz|mrz|april|apr|mai|juni|jun|juli|jul|august|aug|september|sep|oktober|okt|november|nov|dezember|dez)"
        r"(?:\s+(\d{4}))?.{0,30}?\b(?:um\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?",
        flags=re.I,
    )
    slots = []
    for match in pattern.finditer(text):
        day, month_name, year, hour, minute = match.groups()
        slots.append(
            _slot(
                base,
                int(day),
                MONTHS[month_name.lower()],
                int(year) if year else None,
                int(hour),
                int(minute or 0),
                config,
                source="text_de",
            )
        )
    return slots


def _extract_english_slots(
    text: str, base: datetime, config: SchedulerConfig
) -> list[dict[str, object]]:
    pattern = re.compile(
        r"(january|jan|february|feb|march|april|apr|may|june|jun|july|jul|august|aug|september|sep|october|oct|november|nov|december|dec)"
        r"\s+(\d{1,2})(?:,\s*(\d{4}))?.{0,30}?\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        flags=re.I,
    )
    slots = []
    for match in pattern.finditer(text):
        month_name, day, year, hour, minute, ampm = match.groups()
        hour_int = int(hour)
        if ampm and ampm.lower() == "pm" and hour_int < 12:
            hour_int += 12
        if ampm and ampm.lower() == "am" and hour_int == 12:
            hour_int = 0
        slots.append(
            _slot(
                base,
                int(day),
                MONTHS[month_name.lower()],
                int(year) if year else None,
                hour_int,
                int(minute or 0),
                config,
                source="text_en",
            )
        )
    return slots


def _extract_ics_slots(
    normalized: dict[str, object], config: SchedulerConfig
) -> list[dict[str, object]]:
    slots = []
    raw_attachments = normalized.get("attachments")
    attachments = raw_attachments if isinstance(raw_attachments, list) else []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        content = str(
            attachment.get("content") or attachment.get("contentBytesText") or ""
        )
        if "DTSTART" not in content:
            continue
        start = _ics_value(content, "DTSTART")
        end = _ics_value(content, "DTEND")
        if start and end:
            slots.append(
                {
                    "start": _parse_ics_dt(start, config),
                    "end": _parse_ics_dt(end, config),
                    "source": "ics",
                }
            )
    return slots


def _slot(
    base: datetime,
    day: int,
    month: int,
    year: int | None,
    hour: int,
    minute: int,
    config: SchedulerConfig,
    *,
    source: str,
) -> dict[str, object]:
    tz = ZoneInfo(config.timezone)
    year_value = year or base.year
    start = datetime(year_value, month, day, hour, minute, tzinfo=tz)
    if year is None and start < base:
        start = datetime(year_value + 1, month, day, hour, minute, tzinfo=tz)
    end = start + timedelta(minutes=config.default_duration_minutes)
    return {"start": start.isoformat(), "end": end.isoformat(), "source": source}


def _base_datetime(normalized: dict[str, object], timezone: str) -> datetime:
    raw = str(normalized.get("receivedDateTime") or "")
    if raw:
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
            return parsed.astimezone(ZoneInfo(timezone))
        except ValueError:
            pass
    return datetime.now(ZoneInfo(timezone))


def _has_target_calendar(registry: list[dict[str, object]]) -> bool:
    for item in registry:
        alias = str(item.get("alias") or "").lower()
        if alias == "geschaeftlich" and item.get("calendarId") and item.get("isTarget"):
            return True
    return False


def _has_vague_time(message: dict[str, object]) -> bool:
    text = _combined_text(message)
    return any(term in text for term in VAGUE_TIME_TERMS)


def _build_base_claims(
    message: dict[str, object],
    company: str,
    role: str,
    payload: dict[str, object],
) -> list[dict[str, str]]:
    now = datetime.now().astimezone().isoformat()
    source = str(message.get("id") or message.get("internetMessageId") or "message")
    claims = [
        make_claim(
            f"Interviewkontext fuer {company} / {role} stammt aus der Outlook-Mail.",
            source_type="email",
            source_ref=source,
            confidence="high",
            verified_at=now,
        )
    ]
    for raw in _list_of_dicts(payload.get("claims")):
        claims.append(
            make_claim(
                str(raw.get("claim") or ""),
                source_type=str(raw.get("sourceType") or ""),
                source_ref=str(raw.get("sourceRef") or ""),
                confidence=str(raw.get("confidence") or "medium"),
                verified_at=str(raw.get("verifiedAt") or now),
            )
        )
    return claims


def _alternative_slots(
    original_slots: list[dict[str, object]], config: SchedulerConfig
) -> list[dict[str, object]]:
    if original_slots:
        base_start = datetime.fromisoformat(str(original_slots[0]["start"]))
    else:
        base_start = datetime.now(ZoneInfo(config.timezone))
    candidates = []
    for days, hour in ((1, 10), (1, 14), (2, 10)):
        start = (base_start + timedelta(days=days)).replace(hour=hour, minute=0)
        end = start + timedelta(minutes=config.default_duration_minutes)
        candidates.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "source": "alternative",
            }
        )
    return candidates


def _format_slot(slot: Mapping[str, object]) -> str:
    start = str(slot.get("start") or "")
    end = str(slot.get("end") or "")
    return f"{start} bis {end}"


def _combined_text(message: dict[str, object]) -> str:
    return (
        f"{message.get('subject') or ''}\n{message.get('normalizedText') or ''}".lower()
    )


def _guess_company(message: dict[str, object]) -> str:
    sender = str(message.get("from") or "")
    domain_match = re.search(r"@([A-Za-z0-9.-]+)", sender)
    if not domain_match:
        return ""
    domain = domain_match.group(1).split(".")[0]
    return domain.capitalize()


def _sender_text(value: object) -> str:
    if isinstance(value, dict):
        email = value.get("emailAddress")
        if isinstance(email, dict):
            return str(email.get("address") or email.get("name") or "")
    return str(value or "")


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _ics_value(content: str, key: str) -> str:
    for line in content.splitlines():
        if line.startswith(key):
            return line.split(":", 1)[-1].strip()
    return ""


def _parse_ics_dt(value: str, config: SchedulerConfig) -> str:
    tz = ZoneInfo(config.timezone)
    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=ZoneInfo("UTC")
        )
    else:
        parsed = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
    return parsed.astimezone(tz).isoformat()
