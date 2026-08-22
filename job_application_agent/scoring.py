from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
import re

from .models import DIRECT_APPLYABLE_METHODS, JobListing, JobScorecard, ScoringPolicy
from .utils import listing_key


DEFAULT_POLICY = ScoringPolicy()

# These terms only help distinguish a missing location from an explicitly
# configured location restriction. They do not imply a preferred country or city.
REMOTE_LOCATION_TERMS = [
    "100% remote",
    "fully remote",
    "home office",
    "homeoffice",
    "hybrid",
    "partially remote",
    "remote",
    "remote first",
    "remote only",
    "teilremote",
    "teilweise remote",
    "teilweise homeoffice",
    "von zu hause",
    "von zuhause",
    "wfh",
    "work from home",
]
COMMON_LOCATION_TERMS = REMOTE_LOCATION_TERMS + [
    "onsite",
    "on site",
    "worldwide",
    "europa",
    "europe",
]

MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "januar": 1,
    "feb": 2,
    "february": 2,
    "februar": 2,
    "mar": 3,
    "march": 3,
    "märz": 3,
    "maerz": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "mai": 5,
    "jun": 6,
    "june": 6,
    "juni": 6,
    "jul": 7,
    "july": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "october": 10,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    "dez": 12,
    "dezember": 12,
}
def _contains_any(text: str, terms: list[str]) -> list[str]:
    lower = text.lower().replace("-", " ")
    matches: list[str] = []
    for term in terms:
        candidate = term.strip().lower().replace("-", " ")
        if not candidate:
            continue
        if len(candidate) <= 3:
            pattern = rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])"
            if re.search(pattern, lower):
                matches.append(term)
            continue
        if candidate in lower:
            matches.append(term)
    return matches


def _matches_policy_exclusion(text: str, policy: ScoringPolicy) -> str | None:
    normalized = text.lower().replace("-", " ")
    for term in policy.hard_exclusions:
        candidate = term.strip().lower().replace("-", " ")
        if not candidate:
            continue
        if candidate in {"sap"} and re.search(r"\bsap\b", normalized):
            return term
        if candidate in {
            "internship",
            "intern",
            "praktikum",
            "praktikant",
            "working student",
            "werkstudent",
        } and re.search(rf"\b{re.escape(candidate)}\b", normalized):
            return term
        if (
            candidate
            not in {
                "sap",
                "internship",
                "intern",
                "praktikum",
                "praktikant",
                "working student",
                "werkstudent",
            }
            and candidate in normalized
        ):
            return term
    return None


def _normalize_employer_name(value: str) -> str:
    normalized = value.lower().replace("&", " and ")
    normalized = re.sub(
        r"\b(gmbh|ug|ag|se|inc|ltd|llc|corp|corporation|co\s*kg|kg)\b\.?",
        " ",
        normalized,
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _matches_employer_blacklist(company: str, policy: ScoringPolicy) -> str | None:
    normalized_company = _normalize_employer_name(company)
    if not normalized_company:
        return None
    company_tokens = set(normalized_company.split())
    for employer in policy.employer_blacklist:
        normalized_employer = _normalize_employer_name(employer)
        if not normalized_employer:
            continue
        employer_tokens = normalized_employer.split()
        if len(employer_tokens) == 1 and employer_tokens[0] in company_tokens:
            return employer
        if normalized_employer == normalized_company:
            return employer
    return None


def _location_text(listing: JobListing) -> str:
    parts = [listing.title, listing.location, listing.remote_type, listing.work_type]
    if not listing.location.strip() and not listing.remote_type.strip():
        parts.extend(
            [listing.description[:1200], listing.source_url, listing.apply_url]
        )
    parts.append(" ".join(listing.tags))
    return " ".join(parts)


def _has_location_signal(listing: JobListing) -> bool:
    if listing.location.strip() or listing.remote_type.strip():
        return True
    return bool(_contains_any(_location_text(listing), COMMON_LOCATION_TERMS))


def _has_hybrid_or_partial_remote_signal(location_text: str) -> bool:
    lower = location_text.lower().replace("-", " ")
    return bool(
        re.search(
            r"\\bhybrid\\b|teilremote|teilweise\\s+remote|teilweise\\s+homeoffice|"
            r"homeoffice\\s+möglich|homeoffice\\s+moeglich|remote\\s+möglich|remote\\s+moeglich|"
            r"remote\\s+option|remote\\s+tage|tage\\s+remote",
            lower,
        )
    )


def _location_exclusion_reason(
    listing: JobListing, policy: ScoringPolicy
) -> str | None:
    location_text = _location_text(listing)
    if not policy.required_location_terms:
        return None
    hits = _contains_any(location_text, policy.required_location_terms)
    if hits:
        return None
    if policy.allow_unknown_location and not _has_location_signal(listing):
        return None
    allowed = ", ".join(policy.required_location_terms[:6])
    if len(policy.required_location_terms) > 6:
        allowed += ", ..."
    return f"Location does not match required signals. Required one of: {allowed}."


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _parse_posted_date(value: str) -> date | None:
    raw = value.strip()
    if not raw:
        return None
    lower = raw.lower()
    today = _today_utc()

    if re.search(r"\b(today|heute|neu|new|just now|gerade eben)\b", lower):
        return today
    if re.search(r"\b(yesterday|gestern)\b", lower):
        return today - timedelta(days=1)

    single_relative_match = re.search(
        r"(?:vor\s+)?(?:ein(?:e|er|em|en)?|one|a|an)\s+"
        r"(minuten?|minutes?|mins?|stunden?|hours?|hrs?|"
        r"tage?n?|days?|wochen?|weeks?|monate?n?|months?)"
        r"(?:\s+ago)?",
        lower,
    )
    if single_relative_match:
        unit = single_relative_match.group(1)
        if unit.startswith(("minute", "min", "stunde", "hour", "hr")):
            return today
        if unit.startswith(("tag", "day")):
            return today - timedelta(days=1)
        if unit.startswith(("woche", "week")):
            return today - timedelta(days=7)
        if unit.startswith(("monat", "month")):
            return today - timedelta(days=30)

    relative_match = re.search(
        r"(?:vor\s+)?(?:ca\.?\s*)?(\d+)\s*\+?\s*"
        r"(minuten?|minutes?|mins?|stunden?|hours?|hrs?|"
        r"tage?n?|days?|wochen?|weeks?|monate?n?|months?)"
        r"(?:\s+ago)?",
        lower,
    )
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit.startswith(("minute", "min", "stunde", "hour", "hr")):
            return today
        if unit.startswith(("tag", "day")):
            return today - timedelta(days=amount)
        if unit.startswith(("woche", "week")):
            return today - timedelta(days=amount * 7)
        if unit.startswith(("monat", "month")):
            return today - timedelta(days=amount * 30)

    if re.fullmatch(r"\d{10}|\d{13}", raw):
        timestamp = int(raw[:10])
        return datetime.fromtimestamp(timestamp, UTC).date()

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if iso_match:
        try:
            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            )
        except ValueError:
            return None

    dotted_match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b", raw)
    if dotted_match:
        year = int(dotted_match.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, int(dotted_match.group(2)), int(dotted_match.group(1)))
        except ValueError:
            return None

    month_names = "|".join(sorted(MONTH_NAME_TO_NUMBER, key=len, reverse=True))
    month_match = re.search(
        rf"\b(\d{{1,2}})\.?\s+({month_names})\s+(\d{{4}})\b",
        lower,
    )
    if month_match:
        try:
            return date(
                int(month_match.group(3)),
                MONTH_NAME_TO_NUMBER[month_match.group(2)],
                int(month_match.group(1)),
            )
        except ValueError:
            return None

    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed else None


def _listing_age_days(date_posted: str) -> int | None:
    posted_date = _parse_posted_date(date_posted)
    if posted_date is None:
        return None
    return max(0, (_today_utc() - posted_date).days)


def _freshness_points(age_days: int, policy: ScoringPolicy) -> int:
    if policy.max_listing_age_days <= 0:
        return 0
    if age_days <= 2:
        return 10
    if age_days <= max(3, policy.fresh_listing_boost_days):
        return 7
    if age_days <= 14:
        return 4
    if age_days <= policy.max_listing_age_days:
        return 1
    return 0


def _recency_sort_value(listing: JobListing) -> int:
    posted_date = _parse_posted_date(listing.date_posted)
    return posted_date.toordinal() if posted_date else 0


def _score_configured_profile_listing(
    listing: JobListing, policy: ScoringPolicy, text: str, key: str
) -> JobScorecard:
    """Rank a locally configured search without inheriting another user's career lane."""
    policy_exclusion = _matches_policy_exclusion(text, policy)
    if policy_exclusion:
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="exclude",
            selected=False,
            exclusion_reason=f"Configured hard exclusion matched: {policy_exclusion}.",
        )
    employer_exclusion = _matches_employer_blacklist(listing.company, policy)
    if employer_exclusion:
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="exclude",
            selected=False,
            exclusion_reason=f"Configured employer blacklist matched: {employer_exclusion}.",
        )
    location_exclusion = _location_exclusion_reason(listing, policy)
    if location_exclusion:
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="exclude",
            selected=False,
            exclusion_reason=location_exclusion,
        )
    age_days = _listing_age_days(listing.date_posted)
    if (
        age_days is not None
        and policy.max_listing_age_days > 0
        and age_days > policy.max_listing_age_days
    ):
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="exclude",
            selected=False,
            exclusion_reason=(
                f"Listing is {age_days} days old; maximum is {policy.max_listing_age_days}."
            ),
        )
    if not policy.target_roles:
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="weak",
            selected=False,
            concerns=["No target roles are configured. Run `job-agent init --interactive`."],
        )

    title_hits = _contains_any(listing.title, policy.target_roles)
    keyword_hits = _contains_any(text, policy.keywords)
    strengths: list[str] = []
    concerns: list[str] = []
    breakdown: dict[str, int] = {}
    score = 0
    if title_hits:
        points = min(60, 48 + len(set(title_hits)) * 6)
        score += points
        breakdown["configured_title"] = points
        strengths.append("Configured role title: " + ", ".join(sorted(set(title_hits))[:3]))
    if keyword_hits:
        points = min(28, len(set(keyword_hits)) * 4)
        score += points
        breakdown["configured_keywords"] = points
        strengths.append("Configured keywords: " + ", ".join(sorted(set(keyword_hits))[:5]))
    if not title_hits and not keyword_hits:
        concerns.append("Neither a configured role title nor a configured keyword matched.")

    location_hits = _contains_any(_location_text(listing), policy.preferred_locations)
    if location_hits:
        score += 8
        breakdown["preferred_location"] = 8
        strengths.append("Preferred location: " + ", ".join(sorted(set(location_hits))[:3]))
    elif policy.allow_unknown_location and not _has_location_signal(listing):
        score -= 6
        breakdown["location_unknown"] = -6
        concerns.append("Location is unknown; review it before creating a package.")
    if listing.remote_type or _has_hybrid_or_partial_remote_signal(_location_text(listing)):
        score += 4
        breakdown["remote_or_hybrid"] = 4
    if age_days is not None and age_days <= policy.fresh_listing_boost_days:
        score += 5
        breakdown["fresh_listing"] = 5
    if "permanent" in text.casefold() or "festanstellung" in text.casefold():
        score += 3
        breakdown["permanent"] = 3

    score = max(0, min(100, score))
    if title_hits and score >= 70:
        recommendation = "strong"
    elif title_hits or keyword_hits:
        recommendation = "review" if score >= 35 else "adjacent"
    else:
        recommendation = "weak"
    return JobScorecard(
        listing_key=key,
        score=score,
        recommendation=recommendation,
        selected=recommendation in {"strong", "review", "adjacent"},
        matched_strengths=strengths,
        concerns=concerns,
        score_breakdown=breakdown,
    )


def score_listing(
    listing: JobListing, policy: ScoringPolicy | None = None
) -> JobScorecard:
    """Score only against an explicit, user-owned search profile.

    A repository checkout must not silently impose a former user's career path.
    Until setup has written a local profile, every listing remains unselected.
    """
    policy = policy or DEFAULT_POLICY
    text = " ".join(
        [
            listing.title,
            listing.company,
            listing.location,
            listing.remote_type,
            listing.work_type,
            listing.description,
            " ".join(listing.tags),
        ]
    )
    key = listing_key(
        listing.apply_url or listing.source_url, listing.title, listing.company
    )
    if not policy.profile_configured:
        return JobScorecard(
            listing_key=key,
            score=0,
            recommendation="weak",
            selected=False,
            concerns=[
                "Search profile is not configured. Run job-agent init --interactive."
            ],
        )
    return _score_configured_profile_listing(listing, policy, text, key)


def rank_listings(
    listings: list[JobListing],
    top_n: int = 10,
    policy: ScoringPolicy | None = None,
) -> list[tuple[JobListing, JobScorecard]]:
    scored = [(listing, score_listing(listing, policy=policy)) for listing in listings]
    scored = [
        item
        for item in scored
        if item[1].recommendation in {"strong", "review", "adjacent"}
    ]
    recommendation_order = {"strong": 0, "review": 1, "adjacent": 2}
    scored.sort(
        key=lambda item: (
            recommendation_order[item[1].recommendation],
            -item[1].score,
            -_applyability_sort_value(item[0]),
            -_recency_sort_value(item[0]),
        )
    )
    selected: list[tuple[JobListing, JobScorecard]] = []
    for listing, scorecard in scored[:top_n]:
        selected.append((listing, scorecard.model_copy(update={"selected": True})))
    return selected


def _applyability_sort_value(listing: JobListing) -> int:
    method = listing.application_method
    if method == "ats_form":
        return 3
    if method == "company_form":
        return 2
    if method == "email":
        return 1
    if method in DIRECT_APPLYABLE_METHODS:
        return 1
    return 0
