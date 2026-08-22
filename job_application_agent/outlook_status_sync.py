from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .config import default_agent_home
from .mail_response import import_mail_response
from .outlook_graph_draft import (
    DEFAULT_N8N_ENV_PATH,
    _load_env_file,
    _public_url_from_api_url,
)
from .outlook_status_workflow import (
    DEFAULT_OUTLOOK_STATUS_FOLDERS,
    DEFAULT_WEBHOOK_PATH,
)
from .tracker import ApplicationTracker, TrackerEntry, record_review_event


DEFAULT_CHECKPOINT_PATH = default_agent_home() / "data" / "outlook_status_checkpoint.json"
MAX_BACKFILL_PAGES = 20
OUTLOOK_STATUS_TOKEN_ENV_KEYS = (
    "JOB_AGENT_OUTLOOK_STATUS_TOKEN",
    "N8N_OUTLOOK_STATUS_TOKEN",
    "OUTLOOK_STATUS_WEBHOOK_TOKEN",
)


def sync_outlook_statuses(
    webhook_url: str,
    *,
    tracker_path: Path | None = None,
    checkpoint_path: Path | None = None,
    backfill: bool = False,
    folders: list[str] | tuple[str, ...] = DEFAULT_OUTLOOK_STATUS_FOLDERS,
    top: int = 50,
    auth_token: str = "",
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    checkpoint = _load_checkpoint(checkpoint_path)
    cursor = {} if backfill else dict(checkpoint.get("cursor") or {})
    token = resolve_outlook_status_token(
        explicit_token=auth_token,
        n8n_env_path=n8n_env_path,
    )
    owns_client = client is None
    http = client or httpx.Client(timeout=120)
    imported = 0
    skipped = 0
    review_required = 0
    seen = 0
    errors: list[str] = []
    persisted_payloads: list[dict[str, Any]] = []
    failed_folders: set[str] = set()
    scanned: dict[str, str] = {}
    scanned_counts: dict[str, int] = {}
    pages = 0
    truncated = False
    try:
        for folder in folders:
            before: dict[str, str] = {}
            requested_folders = [folder]
            folder_pages = 0
            while requested_folders:
                pages += 1
                folder_pages += 1
                response = http.post(
                    webhook_url,
                    headers={"X-Job-Agent-Token": token},
                    json={
                        "source": "job_application_agent",
                        "mode": "backfill" if backfill else "incremental",
                        "cursor": cursor,
                        "before": before,
                        "folders": requested_folders,
                        "top": top,
                    },
                )
                response.raise_for_status()
                body = response.json()
                payloads = _payloads_from_response(body)
                page_scanned = _scanned_from_response(body)
                page_oldest = _string_dict_from_response(body, "oldest")
                page_has_more = _bool_dict_from_response(body, "hasMore")
                page_counts = _int_dict_from_response(body, "scannedCounts")
                seen += len(payloads)
                _merge_max_timestamps(scanned, page_scanned)
                for counted_folder, count in page_counts.items():
                    scanned_counts[counted_folder] = (
                        scanned_counts.get(counted_folder, 0) + count
                    )
                for index, payload in enumerate(payloads):
                    try:
                        result = import_mail_response(payload, tracker_path=tracker_path)
                    except Exception as exc:
                        try:
                            duplicate_review = _duplicate_review_event(
                                payload,
                                tracker_path,
                            )
                            if duplicate_review is not None:
                                skipped += 1
                                persisted_payloads.append(payload)
                                continue
                            record_review_event(
                                reason=f"outlook_payload_unmatched: {exc}",
                                method="mail_response_review",
                                provenance="n8n_email_monitor",
                                evidence=_review_evidence(payload, str(exc)),
                                notes=_review_notes(payload),
                                path=tracker_path,
                            )
                        except Exception as review_exc:
                            errors.append(
                                f"payload[{index}] failed: {exc}; review event failed: {review_exc}"
                            )
                            failed_folders.add(_text(payload.get("source_folder")))
                            continue
                        review_required += 1
                        persisted_payloads.append(payload)
                        continue
                    if result.duplicate:
                        skipped += 1
                        persisted_payloads.append(payload)
                        continue
                    imported += 1
                    persisted_payloads.append(payload)
                if not backfill:
                    break
                if folder_pages >= MAX_BACKFILL_PAGES:
                    truncated = True
                    break
                requested_folders = [
                    active_folder
                    for active_folder in requested_folders
                    if page_has_more.get(active_folder)
                    and active_folder not in failed_folders
                    and page_oldest.get(active_folder)
                ]
                before.update(
                    {
                        active_folder: page_oldest[active_folder]
                        for active_folder in requested_folders
                    }
                )
    finally:
        if owns_client:
            http.close()
    next_cursor = _next_cursor(cursor, persisted_payloads, scanned, failed_folders)
    if checkpoint_path is not None:
        _write_checkpoint(
            checkpoint_path,
            {
                "mode": "backfill" if backfill else "incremental",
                "cursor": next_cursor,
                "folders": list(folders),
                "top": top,
                "pages": pages,
            },
        )
    return {
        "imported": imported,
        "skipped": skipped,
        "review_required": review_required,
        "seen": seen,
        "scanned": scanned,
        "scanned_counts": scanned_counts,
        "pages": pages,
        "truncated": truncated,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else "",
        "cursor": next_cursor,
        "errors": errors,
    }


def default_webhook_url(
    *,
    webhook_path: str = "",
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
) -> str:
    env = _load_env_file(n8n_env_path)
    api_url = os.getenv("N8N_API_URL") or env.get("N8N_API_URL", "")
    if not api_url:
        raise ValueError("Missing N8N_API_URL")
    resolved_path = (
        webhook_path.strip()
        or os.getenv("JOB_AGENT_OUTLOOK_STATUS_WEBHOOK_PATH", "").strip()
        or env.get("JOB_AGENT_OUTLOOK_STATUS_WEBHOOK_PATH", "").strip()
        or DEFAULT_WEBHOOK_PATH
    )
    return f"{_public_url_from_api_url(api_url).rstrip('/')}/webhook/{resolved_path}"


def resolve_outlook_status_token(
    *,
    explicit_token: str = "",
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
) -> str:
    if explicit_token.strip():
        return explicit_token.strip()
    env = _load_env_file(n8n_env_path)
    for key in OUTLOOK_STATUS_TOKEN_ENV_KEYS:
        value = os.getenv(key) or env.get(key, "")
        if value.strip():
            return value.strip()
    raise ValueError(
        "Missing Outlook status webhook token. Set JOB_AGENT_OUTLOOK_STATUS_TOKEN "
        "in the environment or n8n env file."
    )


def _payloads_from_response(body: Any) -> list[dict[str, Any]]:
    raw_payloads = body
    if isinstance(body, dict):
        raw_payloads = body.get("payloads", [])
    if not isinstance(raw_payloads, list):
        raise ValueError("n8n Outlook status response must contain a payloads list")
    return [payload for payload in raw_payloads if isinstance(payload, dict)]


def _scanned_from_response(body: Any) -> dict[str, str]:
    return _string_dict_from_response(body, "scanned")


def _string_dict_from_response(body: Any, key: str) -> dict[str, str]:
    if not isinstance(body, dict):
        return {}
    raw_scanned = body.get(key)
    if not isinstance(raw_scanned, dict):
        return {}
    return {
        str(folder): str(value)
        for folder, value in raw_scanned.items()
        if str(folder) and str(value)
    }


def _bool_dict_from_response(body: Any, key: str) -> dict[str, bool]:
    if not isinstance(body, dict):
        return {}
    raw = body.get(key)
    if not isinstance(raw, dict):
        return {}
    return {str(folder): bool(value) for folder, value in raw.items() if str(folder)}


def _int_dict_from_response(body: Any, key: str) -> dict[str, int]:
    if not isinstance(body, dict):
        return {}
    raw = body.get(key)
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, int] = {}
    for folder, value in raw.items():
        try:
            parsed[str(folder)] = int(value)
        except (TypeError, ValueError):
            continue
    return parsed


def _merge_max_timestamps(target: dict[str, str], source: dict[str, str]) -> None:
    for folder, timestamp in source.items():
        if timestamp > target.get(folder, ""):
            target[folder] = timestamp


def _load_checkpoint(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _next_cursor(
    existing: dict[str, Any],
    payloads: list[dict[str, Any]],
    scanned: dict[str, str] | None = None,
    failed_folders: set[str] | None = None,
) -> dict[str, str]:
    cursor: dict[str, str] = {
        str(folder): str(value)
        for folder, value in existing.items()
        if str(folder) and str(value)
    }
    for payload in payloads:
        folder = _text(payload.get("source_folder"))
        received_at = _text(payload.get("received_at"))
        if not folder or not received_at:
            continue
        if received_at > cursor.get(folder, ""):
            cursor[folder] = received_at
    failed = {folder for folder in failed_folders or set() if folder}
    for folder, timestamp in (scanned or {}).items():
        if folder in failed:
            continue
        if timestamp > cursor.get(folder, ""):
            cursor[folder] = timestamp
    return cursor


def _review_evidence(payload: dict[str, Any], error: str) -> dict[str, Any]:
    evidence = {
        "mapped_status": "review_required",
        "import_error": error,
    }
    for key in (
        "company",
        "title",
        "apply_url",
        "source_url",
        "message_id",
        "internet_message_id",
        "conversation_id",
        "received_at",
        "classification",
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
    ):
        value = _text(payload.get(key))
        if not value:
            continue
        evidence[key] = value[:1000] if key == "body_excerpt" else value
    return evidence


def _review_notes(payload: dict[str, Any]) -> list[str]:
    notes = ["Outlook status payload needs manual tracker review."]
    folder = _text(payload.get("source_folder"))
    if folder:
        notes.append(f"Folder: {folder}")
    return notes


def _duplicate_review_event(
    payload: dict[str, Any],
    tracker_path: Path | None,
) -> TrackerEntry | None:
    message_ids = {
        _text(payload.get("message_id")),
        _text(payload.get("internet_message_id")),
    }
    message_ids.discard("")
    if not message_ids:
        return None
    paths = [tracker_path] if tracker_path is not None else None
    tracker = ApplicationTracker.load(paths)
    for entry in reversed(tracker.entries):
        if entry.status != "review_required":
            continue
        if entry.method != "mail_response_review":
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook-url", default="")
    parser.add_argument("--webhook-path", default="")
    parser.add_argument("--n8n-env", type=Path, default=DEFAULT_N8N_ENV_PATH)
    parser.add_argument("--tracker-path", type=Path, default=None)
    parser.add_argument("--checkpoint-path", type=Path, default=DEFAULT_CHECKPOINT_PATH)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--folder", action="append", default=[])
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--auth-token", default="")
    args = parser.parse_args(argv)
    webhook_url = args.webhook_url or default_webhook_url(
        webhook_path=args.webhook_path,
        n8n_env_path=args.n8n_env,
    )
    result = sync_outlook_statuses(
        webhook_url,
        tracker_path=args.tracker_path,
        checkpoint_path=args.checkpoint_path,
        backfill=args.backfill,
        folders=args.folder or DEFAULT_OUTLOOK_STATUS_FOLDERS,
        top=args.top,
        auth_token=args.auth_token,
        n8n_env_path=args.n8n_env,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
