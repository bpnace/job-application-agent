from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .addressee import extract_company_from_listing, is_portal_name
from .config import default_runs_dir, default_tracker_path
from .models import JobListing, SourceHealth, utc_now_iso
from .utils import listing_key, normalize_space, write_json


FINAL_SUPPRESSIVE_STATUSES = {
    "applied",
    "rejected",
    "ignored",
    "closed_unavailable",
}
MANUAL_COMPLETION_STATUSES = {
    "needs_completion",
    "blocked_manual",
    "blocked_captcha",
}
# Open manual work is deliberately treated as an existing application. It
# stays out of fresh search results until the user explicitly resolves it.
SUPPRESSIVE_STATUSES = FINAL_SUPPRESSIVE_STATUSES | MANUAL_COMPLETION_STATUSES | {
    "in_progress"
}
SUPPRESSION_NEUTRAL_STATUSES = {"response_received"}
STATEFUL_STATUSES = SUPPRESSIVE_STATUSES | {
    "response_received",
    "review_required",
    "requeued",
}
COMPLETION_RESOLUTION_STATUSES = {"applied", "ignored", "requeued"}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "display",
    "language",
    "mc_cid",
    "mc_eid",
    "pk_campaign",
    "pk_kwd",
}


class ListingIdentities(TypedDict):
    keys: set[str]
    urls: set[str]
    title_company: str


@dataclass(frozen=True)
class TrackerEntry:
    status: str
    listing_key: str = ""
    apply_url: str = ""
    source_url: str = ""
    title: str = ""
    company: str = ""
    source: str = ""
    package_dir: str = ""
    created_at: str = ""
    status_at: str = ""
    applied_at: str = ""
    method: str = ""
    provenance: str = ""
    evidence: dict[str, Any] | str | None = None
    notes: list[str] | None = None

    @property
    def suppresses_search(self) -> bool:
        return self.status in SUPPRESSIVE_STATUSES

    @property
    def normalized_urls(self) -> set[str]:
        return {
            normalized
            for normalized in [
                normalize_url(self.apply_url),
                normalize_url(self.source_url),
            ]
            if normalized
        }

    @property
    def title_company_key(self) -> str:
        return normalize_title_company(self.title, self.company)


class ApplicationTracker:
    def __init__(self, entries: list[TrackerEntry]):
        self.entries = entries

    @classmethod
    def load(cls, paths: list[Path] | None = None) -> "ApplicationTracker":
        entries: list[TrackerEntry] = []
        for path in paths or default_tracker_paths():
            entries.extend(load_tracker_entries(path))
        return cls(entries)

    def match_for_listing(self, listing: JobListing) -> TrackerEntry | None:
        return self._latest_matching_entry(listing)

    def suppression_for_listing(self, listing: JobListing) -> TrackerEntry | None:
        entry = self._latest_matching_entry(
            listing,
            stateful_only=True,
            ignored_statuses=SUPPRESSION_NEUTRAL_STATUSES,
        )
        if entry and entry.suppresses_search:
            return entry
        return None

    def manual_completion_entries(self) -> list[TrackerEntry]:
        """Return only the newest open manual-action state for each application."""
        latest: dict[str, tuple[tuple[str, int], TrackerEntry]] = {}
        for index, entry in enumerate(self.entries):
            if entry.status in SUPPRESSION_NEUTRAL_STATUSES:
                continue
            identity = (
                entry.listing_key
                or next(iter(entry.normalized_urls), "")
                or entry.title_company_key
            )
            if not identity:
                continue
            order = _entry_order_key(entry, index)
            current = latest.get(identity)
            if current is None or order > current[0]:
                latest[identity] = (order, entry)
        return [
            entry
            for _order, entry in sorted(
                latest.values(), key=lambda item: item[0], reverse=True
            )
            if entry.status in MANUAL_COMPLETION_STATUSES
        ]

    def _latest_matching_entry(
        self,
        listing: JobListing,
        *,
        stateful_only: bool = False,
        ignored_statuses: set[str] | None = None,
    ) -> TrackerEntry | None:
        identities = listing_identities(listing)
        candidates: list[tuple[tuple[str, int], TrackerEntry]] = []
        for index, entry in enumerate(self.entries):
            if stateful_only and entry.status not in STATEFUL_STATUSES:
                continue
            if ignored_statuses and entry.status in ignored_statuses:
                continue
            if not _entry_matches_identities(entry, identities):
                continue
            candidates.append((_entry_order_key(entry, index), entry))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]


def _entry_matches_identities(
    entry: TrackerEntry, identities: ListingIdentities
) -> bool:
    if entry.listing_key and entry.listing_key in identities["keys"]:
        return True
    if entry.normalized_urls & identities["urls"]:
        return True
    title_company = entry.title_company_key
    return bool(title_company and title_company == identities["title_company"])


def _entry_order_key(entry: TrackerEntry, index: int) -> tuple[str, int]:
    timestamp = entry.status_at or entry.applied_at or entry.created_at
    return (timestamp, index)


def default_tracker_paths() -> list[Path]:
    return [
        default_tracker_path(),
        default_runs_dir() / "application_ledger.json",
    ]


def manual_completion_queue(paths: list[Path] | None = None) -> list[dict[str, Any]]:
    """Create a compact, local-only handoff list for human form completion."""
    entries = ApplicationTracker.load(paths).manual_completion_entries()
    return [
        {
            "status": entry.status,
            "title": entry.title,
            "company": entry.company,
            "apply_url": entry.apply_url or entry.source_url,
            "source": entry.source,
            "status_at": entry.status_at,
            "method": entry.method,
            "provenance": entry.provenance,
            "notes": entry.notes or [],
            "evidence": entry.evidence or "",
            "package_dir": entry.package_dir,
        }
        for entry in entries
    ]


def normalize_url(value: str) -> str:
    value = normalize_space(value)
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.netloc:
        return value.rstrip("/")
    query_items = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS or lower_key.startswith(
            TRACKING_QUERY_PREFIXES
        ):
            continue
        query_items.append((key, item_value))
    query = urlencode(sorted(query_items))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower().removeprefix("www."),
            path,
            "",
            query,
            "",
        )
    )


def normalize_title_company(title: str, company: str) -> str:
    title_part = normalize_space(title).casefold()
    company_part = normalize_company_for_match(company)
    if not title_part or not company_part:
        return ""
    return f"{company_part}|{title_part}"


def normalize_company_for_match(company: str) -> str:
    company_part = normalize_space(company).casefold()
    company_part = re.sub(
        r"\b(gmbh|ug|ag|se|inc|ltd|llc|corp|corporation|co\\.?\\s*kg|kg)\\b\\.?",
        "",
        company_part,
    )
    company_part = re.sub(r"[^a-z0-9]+", " ", company_part)
    return normalize_space(company_part)


def listing_identities(listing: JobListing) -> ListingIdentities:
    apply_or_source = listing.apply_url or listing.source_url
    company = listing.company
    if is_portal_name(company):
        resolved = extract_company_from_listing(listing)
        if resolved and not is_portal_name(resolved):
            company = resolved
    keys = {
        listing_key(apply_or_source, listing.title, company),
        listing_key(listing.source_url, listing.title, company),
    }
    if listing.apply_url:
        keys.add(listing_key(listing.apply_url, listing.title, company))
    urls = {
        normalized
        for normalized in [
            normalize_url(listing.apply_url),
            normalize_url(listing.source_url),
        ]
        if normalized
    }
    return {
        "keys": keys,
        "urls": urls,
        "title_company": normalize_title_company(listing.title, company),
    }


def _actual_company_for_tracking(listing: JobListing) -> str:
    company = listing.company.strip()
    if not company:
        return ""
    if not is_portal_name(company):
        return company
    resolved = extract_company_from_listing(listing)
    if resolved and not is_portal_name(resolved):
        return resolved
    return ""


def load_tracker_entries(path: Path) -> list[TrackerEntry]:
    if not path.exists():
        return []
    if path.suffix == ".jsonl":
        return [
            entry
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for entry in [_entry_from_raw(json.loads(line))]
            if entry is not None
        ]
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [
            entry
            for item in raw["entries"]
            for entry in [_entry_from_raw(item)]
            if entry is not None
        ]
    if isinstance(raw, list):
        return [
            entry
            for item in raw
            for entry in [_entry_from_raw(item)]
            if entry is not None
        ]
    return []


def _entry_from_raw(raw: object) -> TrackerEntry | None:
    if not isinstance(raw, dict):
        return None
    company = str(raw.get("company") or raw.get("actual_company") or "")
    actual_company = str(raw.get("actual_company") or "")
    if actual_company and (not company.strip() or is_portal_name(company)):
        company = actual_company
    raw_notes = raw.get("notes", [])
    evidence = raw.get("evidence")
    return TrackerEntry(
        status=str(raw.get("status") or ""),
        listing_key=str(raw.get("listing_key") or ""),
        apply_url=str(raw.get("apply_url") or raw.get("employer_apply_url") or ""),
        source_url=str(raw.get("source_url") or ""),
        title=str(raw.get("title") or ""),
        company=company,
        source=str(raw.get("source") or ""),
        package_dir=str(raw.get("package_dir") or ""),
        created_at=str(raw.get("created_at") or ""),
        status_at=str(raw.get("status_at") or raw.get("created_at") or ""),
        applied_at=str(
            raw.get("applied_at")
            or (raw.get("status_at") if raw.get("status") == "applied" else "")
            or ""
        ),
        method=str(raw.get("method") or raw.get("application_method") or ""),
        provenance=str(raw.get("provenance") or ""),
        evidence=evidence if isinstance(evidence, (dict, str)) else None,
        notes=[str(note) for note in raw_notes] if isinstance(raw_notes, list) else [],
    )


def filter_tracked_listings(
    listings: list[JobListing],
    tracker: ApplicationTracker,
    include_tracked: bool = False,
) -> tuple[list[JobListing], list[TrackerEntry]]:
    suppressed: list[TrackerEntry] = []
    filtered: list[JobListing] = []
    for listing in listings:
        entry = tracker.suppression_for_listing(listing)
        if entry:
            suppressed.append(entry)
            if include_tracked:
                filtered.append(listing)
            continue
        filtered.append(listing)
    return filtered, suppressed


def tracker_health(
    tracker: ApplicationTracker,
    suppressed_count: int,
    include_tracked: bool = False,
) -> SourceHealth:
    status = "available" if tracker.entries else "disabled"
    mode = "included" if include_tracked else "suppressed"
    open_manual_count = len(tracker.manual_completion_entries())
    reminder = (
        f" {open_manual_count} open manual-completion case(s) remain in the local queue."
        if open_manual_count
        else ""
    )
    return SourceHealth(
        name="application_tracker",
        status=status,
        candidates_seen=len(tracker.entries),
        candidates_returned=suppressed_count,
        message=(
            f"{suppressed_count} existing jobs {mode} from ranked results.{reminder}"
        ),
        fetched_at=utc_now_iso(),
    )


def ensure_not_suppressed(
    listings: list[JobListing],
    tracker: ApplicationTracker,
) -> None:
    blocked: list[str] = []
    for listing in listings:
        entry = tracker.suppression_for_listing(listing)
        if entry:
            blocked.append(f"{listing.company} - {listing.title} ({entry.status})")
    if blocked:
        joined = "; ".join(blocked)
        raise ValueError(
            "Approved jobs include already applied/rejected/ignored/unavailable or open manual-completion entries: "
            f"{joined}. Use --allow-tracked only if you intentionally want to override."
        )


def record_package_created(
    listing: JobListing,
    listing_key_value: str,
    package_dir: Path,
    path: Path | None = None,
) -> None:
    target = path or default_tracker_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    actual_company = _actual_company_for_tracking(listing)
    event = {
        "status": "package_created",
        "created_at": utc_now_iso(),
        "listing_key": listing_key_value,
        "apply_url": listing.apply_url,
        "source_url": listing.source_url,
        "title": listing.title,
        "company": listing.company,
        "source": listing.source,
        "application_method": listing.application_method,
        "apply_platform": listing.apply_platform,
        "resume_upload": listing.resume_upload,
        "package_dir": str(package_dir),
    }
    if actual_company:
        event["actual_company"] = actual_company
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def record_status_event(
    listing: JobListing,
    status: str,
    *,
    method: str,
    provenance: str = "agent",
    evidence: dict[str, Any] | str | None = None,
    notes: list[str] | None = None,
    package_dir: Path | None = None,
    listing_key_value: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or default_tracker_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    status_at = utc_now_iso()
    actual_company = _actual_company_for_tracking(listing)
    identity_company = actual_company or listing.company
    key = listing_key_value or listing_key(
        listing.apply_url or listing.source_url, listing.title, identity_company
    )
    event: dict[str, Any] = {
        "status": status,
        "created_at": status_at,
        "status_at": status_at,
        "listing_key": key,
        "apply_url": listing.apply_url,
        "source_url": listing.source_url,
        "title": listing.title,
        "company": listing.company,
        "source": listing.source,
        "method": method,
        "provenance": provenance,
        "notes": notes or [],
    }
    if actual_company:
        event["actual_company"] = actual_company
    if status == "applied":
        event["applied_at"] = status_at
    if package_dir is not None:
        event["package_dir"] = str(package_dir)
    if evidence is not None:
        event["evidence"] = evidence
    for key, value in (extra_fields or {}).items():
        if key in event or value in (None, "", [], {}):
            continue
        event[key] = value
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def resolve_manual_completion(
    entry: TrackerEntry,
    status: Literal["applied", "ignored", "requeued"],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record a user's explicit resolution of an open manual application.

    ``requeued`` is intentionally non-suppressive. It is the only resolution
    that returns an otherwise open case to a later search run.
    """
    if entry.status not in MANUAL_COMPLETION_STATUSES:
        raise ValueError("Only open manual-completion entries can be resolved.")
    if status not in COMPLETION_RESOLUTION_STATUSES:
        raise ValueError(f"Unsupported manual-completion resolution: {status}")
    listing = JobListing(
        source=entry.source or "application_tracker",
        source_url=entry.source_url or entry.apply_url,
        apply_url=entry.apply_url,
        title=entry.title,
        company=entry.company,
    )
    notes = {
        "applied": "User confirmed final manual completion.",
        "ignored": "User chose not to continue this manual-completion case.",
        "requeued": "User explicitly requeued this existing case for a later search.",
    }
    return record_status_event(
        listing,
        status,
        method="manual_completion_review",
        provenance="manual_user_reported",
        evidence="manual completion review",
        notes=[notes[status]],
        package_dir=Path(entry.package_dir) if entry.package_dir else None,
        listing_key_value=entry.listing_key or None,
        path=path,
    )


def record_review_event(
    *,
    reason: str,
    method: str,
    provenance: str,
    evidence: dict[str, Any] | str | None = None,
    notes: list[str] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or default_tracker_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    status_at = utc_now_iso()
    event: dict[str, Any] = {
        "status": "review_required",
        "created_at": status_at,
        "status_at": status_at,
        "listing_key": _review_listing_key(evidence),
        "apply_url": _review_evidence_text(evidence, "apply_url"),
        "source_url": _review_source_url(evidence),
        "title": _review_title(evidence),
        "company": _review_company(evidence),
        "source": "outlook_status_sync",
        "method": method,
        "provenance": provenance,
        "review_reason": reason,
        "notes": notes or [],
    }
    if evidence is not None:
        event["evidence"] = evidence
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def _review_evidence_text(evidence: dict[str, Any] | str | None, key: str) -> str:
    if not isinstance(evidence, dict):
        return ""
    return normalize_space(str(evidence.get(key) or ""))


def _review_company(evidence: dict[str, Any] | str | None) -> str:
    company = _review_evidence_text(evidence, "company")
    if company:
        return company
    sender = _review_evidence_text(evidence, "from")
    domain = sender.split("@", 1)[1] if "@" in sender else ""
    stem = domain.split(".", 1)[0] if domain else ""
    return normalize_space(stem.replace("-", " ").replace("_", " ")) or "Outlook Review"


def _review_title(evidence: dict[str, Any] | str | None) -> str:
    return (
        _review_evidence_text(evidence, "title")
        or _review_evidence_text(evidence, "subject")
        or "Unmatched Outlook response"
    )


def _review_source_url(evidence: dict[str, Any] | str | None) -> str:
    mail_url = _review_evidence_text(evidence, "mail_url")
    if mail_url:
        return mail_url
    message_id = _review_evidence_text(evidence, "message_id")
    if message_id:
        return f"mail-response:{_opaque_mail_token(message_id)}"
    return "mail-response:review-required"


def _opaque_mail_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _review_listing_key(evidence: dict[str, Any] | str | None) -> str:
    return listing_key(
        _review_source_url(evidence),
        _review_title(evidence),
        _review_company(evidence),
    )


def write_tracker_snapshot(path: Path, tracker: ApplicationTracker) -> None:
    write_json(
        path,
        {
            "generated_at": utc_now_iso(),
            "entries": [entry.__dict__ for entry in tracker.entries],
        },
    )
