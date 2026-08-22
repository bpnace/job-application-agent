from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Mapping, Sequence, TypedDict
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Europe/Berlin"


class BusyInterval(TypedDict):
    start: datetime
    end: datetime
    busy: bool
    sourceAlias: str


def parse_datetime(value: str, timezone: str = DEFAULT_TIMEZONE) -> datetime:
    if not value:
        raise ValueError("datetime value is required")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and len(text) == 10:
        parsed = datetime.combine(date.fromisoformat(text), time.min)
    else:
        parsed = datetime.fromisoformat(text)
    tz = ZoneInfo(timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def normalize_interval(
    item: Mapping[str, object], timezone: str = DEFAULT_TIMEZONE
) -> BusyInterval:
    start_raw = str(item.get("start") or item.get("dateTimeStart") or "")
    end_raw = str(item.get("end") or item.get("dateTimeEnd") or "")
    all_day = bool(item.get("allDay") or item.get("all_day"))
    if all_day:
        start = parse_datetime(start_raw[:10], timezone)
        if end_raw:
            end = parse_datetime(end_raw[:10], timezone)
        else:
            end = start + timedelta(days=1)
    else:
        start = parse_datetime(start_raw, timezone)
        end = parse_datetime(end_raw, timezone)
    if end <= start:
        raise ValueError(f"busy interval end must be after start: {item}")
    transparency = str(item.get("transparency") or item.get("showAs") or "").lower()
    status = str(item.get("status") or item.get("responseStatus") or "").lower()
    busy = transparency not in {"free", "transparent"} and status not in {
        "cancelled",
        "canceled",
    }
    return {
        "start": start,
        "end": end,
        "busy": busy,
        "sourceAlias": str(
            item.get("sourceAlias")
            or item.get("calendarAlias")
            or item.get("calendar")
            or "blocking-calendar"
        ),
    }


def redact_busy_events(
    events: Sequence[Mapping[str, object]],
    *,
    timezone: str = DEFAULT_TIMEZONE,
) -> list[dict[str, object]]:
    redacted: list[dict[str, object]] = []
    for event in events:
        interval = normalize_interval(event, timezone)
        if not interval["busy"]:
            continue
        redacted.append(
            {
                "start": interval["start"].isoformat(),
                "end": interval["end"].isoformat(),
                "busy": True,
                "sourceAliasRedacted": "blocking-calendar",
            }
        )
    return redacted


def intervals_overlap(
    start: datetime,
    end: datetime,
    busy_start: datetime,
    busy_end: datetime,
    *,
    buffer_minutes: int = 0,
) -> bool:
    buffered_start = start - timedelta(minutes=buffer_minutes)
    buffered_end = end + timedelta(minutes=buffer_minutes)
    return buffered_start < busy_end and busy_start < buffered_end


def conflicts_for_slot(
    slot: Mapping[str, object],
    busy_events: Sequence[Mapping[str, object]],
    *,
    timezone: str = DEFAULT_TIMEZONE,
    buffer_minutes: int = 15,
) -> list[dict[str, object]]:
    slot_start = parse_datetime(str(slot.get("start") or ""), timezone)
    slot_end = parse_datetime(str(slot.get("end") or ""), timezone)
    conflicts: list[dict[str, object]] = []
    for event in busy_events:
        interval = normalize_interval(event, timezone)
        if not interval["busy"]:
            continue
        if intervals_overlap(
            slot_start,
            slot_end,
            interval["start"],
            interval["end"],
            buffer_minutes=buffer_minutes,
        ):
            conflicts.append(
                {
                    "start": interval["start"].isoformat(),
                    "end": interval["end"].isoformat(),
                    "busy": True,
                    "sourceAliasRedacted": "blocking-calendar",
                }
            )
    return conflicts


def split_free_and_conflicting_slots(
    slots: Sequence[Mapping[str, object]],
    busy_events: Sequence[Mapping[str, object]],
    *,
    timezone: str = DEFAULT_TIMEZONE,
    buffer_minutes: int = 15,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    free: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for slot in slots:
        conflicts = conflicts_for_slot(
            slot,
            busy_events,
            timezone=timezone,
            buffer_minutes=buffer_minutes,
        )
        enriched = dict(slot)
        enriched["conflicts"] = conflicts
        if conflicts:
            blocked.append(enriched)
        else:
            free.append(enriched)
    return free, blocked
