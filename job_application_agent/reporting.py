from __future__ import annotations

from .models import SourceHealth


def format_source_health(health: list[SourceHealth]) -> str:
    return "\n".join(format_source_health_line(item) for item in health)


def format_source_health_line(item: SourceHealth) -> str:
    if item.name == "application_tracker":
        return f"- {item.name}: {item.status}, {item.message}".rstrip(" ,.")
    return (
        f"- {item.name}: {item.status}, returned {item.candidates_returned}, "
        f"direct applyable {item.direct_applyable_returned}. {item.message}"
    ).rstrip()


def format_skip_summary(skipped_count: int, tracked_skipped_count: int) -> str:
    return (
        f"Scoring excluded count: {skipped_count}\n"
        f"Tracker-suppressed existing count: {tracked_skipped_count}\n"
        "Count note: scoring exclusions are remaining candidates rejected by policy "
        "after tracker suppression; tracker suppression includes prior terminal "
        "statuses and open manual-completion cases."
    )
