from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import JobListing
from .config import default_tracker_path
from .tracker import (
    ApplicationTracker,
    TrackerEntry,
    normalize_company_for_match,
    record_status_event,
)
from .utils import normalize_space


MAIL_RESPONSE_STATUSES = {
    "applied",
    "rejected",
    "response_received",
    "needs_completion",
}
CLASSIFICATION_TO_STATUS = {
    "sent_application": "applied",
    "application_sent": "applied",
    "outgoing_application": "applied",
    "rejection": "rejected",
    "rejected": "rejected",
    "declined": "rejected",
    "absage": "rejected",
    "response": "response_received",
    "response_received": "response_received",
    "reply": "response_received",
    "reply_received": "response_received",
    "interview": "response_received",
    "positive": "response_received",
    "next_step": "response_received",
    "needs_completion": "needs_completion",
    "completion_required": "needs_completion",
    "action_required": "needs_completion",
}
EVIDENCE_FIELDS = (
    "message_id",
    "internet_message_id",
    "thread_id",
    "conversation_id",
    "received_at",
    "classification",
    "confidence",
    "source_folder",
    "reply_category",
    "reply_type",
    "action_required",
    "deadline",
    "interview_detected",
    "interview_stage",
    "proposed_times",
    "timezone",
    "scheduler_decision",
    "scheduling_summary",
)


@dataclass(frozen=True)
class MailResponseImportResult:
    status: str
    matched_by: str
    tracker_path: Path
    event: dict[str, Any]
    duplicate: bool = False


def load_mail_response_payload(
    path: Path,
    *,
    stdin_text: str | None = None,
) -> dict[str, Any]:
    if str(path) == "-":
        raw = json.loads(stdin_text or "")
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Mail response payload must be a JSON object.")
    return raw


def import_mail_response(
    payload: Mapping[str, Any],
    *,
    tracker_path: Path | None = None,
) -> MailResponseImportResult:
    if not isinstance(payload, Mapping):
        raise ValueError("Mail response payload must be a JSON object.")
    status = _status_from_payload(payload)
    listing, matched_by, package_dir = _listing_from_payload(
        payload, tracker_path=tracker_path
    )
    evidence = _evidence_from_payload(payload, status)
    duplicate = _duplicate_mail_event(evidence, status, tracker_path)
    if duplicate is not None:
        return MailResponseImportResult(
            status=status,
            matched_by=matched_by,
            tracker_path=tracker_path or default_tracker_path(),
            event=duplicate.__dict__,
            duplicate=True,
        )
    event = record_status_event(
        listing,
        status,
        method="mail_response",
        provenance="n8n_email_monitor",
        evidence=evidence,
        notes=_notes_from_payload(payload, status),
        package_dir=package_dir,
        listing_key_value=_text(payload.get("listing_key")) or None,
        extra_fields=_structured_event_fields_from_payload(payload, status),
        path=tracker_path,
    )
    return MailResponseImportResult(
        status=status,
        matched_by=matched_by,
        tracker_path=tracker_path or default_tracker_path(),
        event=event,
    )


def _status_from_payload(payload: Mapping[str, Any]) -> str:
    explicit_status = _canonical_token(payload.get("status"))
    if explicit_status:
        if explicit_status not in MAIL_RESPONSE_STATUSES:
            raise ValueError(f"Unsupported mail response status: {explicit_status}")
        return explicit_status
    classification = _canonical_token(payload.get("classification"))
    if not classification:
        raise ValueError("Mail response payload needs classification or status.")
    status = CLASSIFICATION_TO_STATUS.get(classification)
    if not status:
        raise ValueError(f"Unsupported mail response classification: {classification}")
    return status


def _listing_from_payload(
    payload: Mapping[str, Any],
    *,
    tracker_path: Path | None = None,
) -> tuple[JobListing, str, Path | None]:
    package_dir = _optional_path(payload.get("package_dir"))
    job_json = _optional_path(payload.get("job_json") or payload.get("job_json_path"))
    if package_dir is not None:
        job_json = package_dir / "job.json"
        matched_by = "package_dir"
    elif job_json is not None:
        matched_by = "job_json"
        package_dir = job_json.parent
    else:
        matched_by = ""

    if job_json is not None:
        if not job_json.exists():
            raise FileNotFoundError(f"Job JSON not found: {job_json}")
        listing = JobListing.model_validate_json(job_json.read_text(encoding="utf-8"))
        return listing, matched_by, package_dir

    apply_url = _text(payload.get("apply_url"))
    source_url = _text(payload.get("source_url")) or apply_url
    company = _text(payload.get("company") or payload.get("actual_company"))
    title = _text(payload.get("title") or payload.get("job_title"))
    if company and not title and not apply_url:
        resolved = _listing_from_unique_tracker_company(company, tracker_path)
        if resolved is not None:
            return resolved
    if not apply_url and not (company and title):
        raise ValueError(
            "Mail response payload needs company/title, apply_url, or package_dir."
        )
    if not source_url:
        source_url = _fallback_source_url(payload)
    matched_by = "apply_url" if apply_url else "title_company"
    listing = JobListing(
        source="mail_response",
        source_url=source_url,
        apply_url=apply_url,
        title=title,
        company=company,
        description="Mail response import.",
    )
    return listing, matched_by, None


def _listing_from_unique_tracker_company(
    company: str,
    tracker_path: Path | None,
) -> tuple[JobListing, str, Path | None] | None:
    paths = [tracker_path] if tracker_path is not None else None
    tracker = ApplicationTracker.load(paths)
    target = normalize_company_for_match(company)
    matches = [
        entry
        for entry in tracker.entries
        if target and normalize_company_for_match(entry.company) == target
    ]
    unique_by_identity: dict[tuple[str, str, str], TrackerEntry] = {}
    for entry in matches:
        if not entry.title or _is_review_only_tracker_entry(entry):
            continue
        if not _has_real_application_identity(entry):
            continue
        unique_by_identity[(entry.company, entry.title, entry.apply_url)] = entry
    if len(unique_by_identity) != 1:
        return None
    entry = next(iter(unique_by_identity.values()))
    listing = JobListing(
        source=entry.source or "tracker",
        source_url=entry.source_url or entry.apply_url or _fallback_source_url({}),
        apply_url=entry.apply_url,
        title=entry.title,
        company=entry.company,
        description="Matched from application tracker company.",
    )
    package_dir = Path(entry.package_dir) if entry.package_dir else None
    return listing, "tracker_company", package_dir


def _evidence_from_payload(
    payload: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"mapped_status": status}
    for field in EVIDENCE_FIELDS:
        value = _text(payload.get(field))
        if not value:
            continue
        evidence[field] = value
    return evidence


def _structured_event_fields_from_payload(
    payload: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"reply_events": [_reply_event_from_payload(payload, status)]}
    if _is_interview_payload(payload):
        fields["interview_rounds"] = [_interview_round_from_payload(payload)]
    scheduling = _scheduling_from_payload(payload)
    if scheduling:
        fields["scheduling"] = scheduling
    return fields


def _reply_event_from_payload(
    payload: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    classification = _text(payload.get("classification"))
    reply_type = _text(payload.get("reply_type")) or classification or status
    return {
        "id": _mail_opaque_id(payload),
        "received_at": _text(payload.get("received_at")),
        "channel": _text(payload.get("source_folder")) or "outlook",
        "status": status,
        "summary": _text(payload.get("scheduling_summary"))
        or f"Outlook mail classified as {reply_type}.",
        "sentiment": _sentiment_for_payload(status, reply_type),
    }


def _interview_round_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stage = _text(payload.get("interview_stage")) or "interview"
    return {
        "id": _mail_opaque_id(payload),
        "round": stage.replace("_", " ").title(),
        "stage": stage,
        "interviewers": [],
        "format": "",
        "location": "",
        "outcome": "offered",
        "notes": _text(payload.get("scheduling_summary")),
    }


def _scheduling_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    proposed_times = _split_proposed_times(payload.get("proposed_times"))
    scheduling_summary = _text(payload.get("scheduling_summary"))
    scheduler_decision = _text(payload.get("scheduler_decision"))
    timezone = _text(payload.get("timezone"))
    if not (proposed_times or scheduling_summary or scheduler_decision or timezone):
        return {}
    conflict = scheduler_decision in {"conflict", "blocked", "manual_review"}
    return {
        "status": scheduler_decision or "detected",
        "conflict": conflict,
        "conflict_reason": scheduling_summary if conflict else "",
        "proposed_times": proposed_times,
        "timezone": timezone,
        "notes": [scheduling_summary] if scheduling_summary else [],
    }


def _split_proposed_times(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def _is_interview_payload(payload: Mapping[str, Any]) -> bool:
    return (
        _canonical_token(payload.get("interview_detected")) == "yes"
        or "interview" in _canonical_token(payload.get("reply_type"))
        or "interview" in _canonical_token(payload.get("reply_category"))
    )


def _sentiment_for_payload(status: str, reply_type: str) -> str:
    if status == "rejected":
        return "negative"
    if status == "response_received" and "interview" in reply_type.casefold():
        return "positive"
    return "neutral"


def _duplicate_mail_event(
    evidence: Mapping[str, Any],
    status: str,
    tracker_path: Path | None,
) -> TrackerEntry | None:
    message_ids = {
        _text(evidence.get("message_id")),
        _text(evidence.get("internet_message_id")),
    }
    message_ids.discard("")
    if not message_ids:
        return None
    paths = [tracker_path] if tracker_path is not None else None
    tracker = ApplicationTracker.load(paths)
    for entry in reversed(tracker.entries):
        if entry.status != status:
            continue
        if entry.method != "mail_response":
            continue
        if entry.provenance != "n8n_email_monitor":
            continue
        if not isinstance(entry.evidence, dict):
            continue
        existing_ids = {
            _text(entry.evidence.get("message_id")),
            _text(entry.evidence.get("internet_message_id")),
        }
        existing_ids.discard("")
        if message_ids & existing_ids:
            return entry
    return None


def _notes_from_payload(payload: Mapping[str, Any], status: str) -> list[str]:
    return [f"n8n mail response import mapped to {status}."]


def _fallback_source_url(payload: Mapping[str, Any]) -> str:
    for key in ("internet_message_id", "message_id"):
        value = _text(payload.get(key))
        if value:
            return f"mail-response:{_opaque_mail_token(value)}"
    return "mail-response:unknown"


def _mail_opaque_id(payload: Mapping[str, Any]) -> str:
    for key in ("internet_message_id", "message_id"):
        value = _text(payload.get(key))
        if value:
            return f"mail-event:{_opaque_mail_token(value)}"
    return ""


def _opaque_mail_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _is_review_only_tracker_entry(entry: TrackerEntry) -> bool:
    return (
        entry.status == "review_required"
        or entry.method == "mail_response_review"
        or entry.title.casefold() == "unmatched outlook response"
    )


def _has_real_application_identity(entry: TrackerEntry) -> bool:
    if entry.package_dir or entry.apply_url:
        return True
    return bool(entry.source_url and not _is_mail_response_source_url(entry.source_url))


def _is_mail_response_source_url(value: str) -> bool:
    return value.startswith("mail-response:")


def _optional_path(value: Any) -> Path | None:
    text = _text(value)
    if not text:
        return None
    return Path(text).expanduser()


def _canonical_token(value: Any) -> str:
    return _text(value).casefold().replace("-", "_").replace(" ", "_")


def _text(value: Any) -> str:
    return normalize_space(str(value or ""))
