from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .addressee import extract_company_from_listing, is_portal_name
from .cover_letter import display_role_title
from .models import CandidateProfile, JobListing


MAX_ROLE_FILENAME_PART_LENGTH = 48
MAX_JOB_FOLDER_NAME_LENGTH = 120


def cover_letter_filename(profile: CandidateProfile, listing: JobListing) -> str:
    role = compact_role_title(listing.title)
    last_name = filename_part(_last_name(profile.name))
    return f"{filename_part(role, MAX_ROLE_FILENAME_PART_LENGTH)}_{last_name}.pdf"


def final_job_folder_name(listing: JobListing) -> str:
    company = final_company_name(listing)
    role = compact_role_title(listing.title)
    return filename_part(f"{company}_{role}", MAX_JOB_FOLDER_NAME_LENGTH)


def final_company_name(listing: JobListing) -> str:
    company = listing.company.strip()
    if company and "." not in company and not is_portal_name(company):
        return company

    extracted = extract_company_from_listing(listing).strip()
    if extracted:
        return extracted

    title_company = _company_from_title_suffix(listing.title)
    if title_company:
        return title_company

    if company:
        return _company_from_domain(company) or company

    return _company_from_domain(listing.apply_url or listing.source_url) or "Unbekannt"


def compact_role_title(title: str) -> str:
    role = display_role_title(title)
    role = re.sub(
        r"\s*\((?:m|w|d|f|x)(?:[/_-](?:m|w|d|f|x))*\)",
        " ",
        role,
        flags=re.IGNORECASE,
    )
    role = re.sub(r"\s*\((?:all genders|gn)\)", " ", role, flags=re.IGNORECASE)
    role = re.sub(
        r"\b(?:mwd|m\s*/\s*w\s*/\s*d|f\s*/\s*m\s*/\s*d)\b",
        " ",
        role,
        flags=re.IGNORECASE,
    )
    role = re.sub(r"\s+bei\s+.+$", "", role, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", role).strip() or display_role_title(title)


def filename_part(value: str, max_length: int | None = None) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_")
    cleaned = _trim_underscore_words(cleaned, max_length) if max_length else cleaned
    return cleaned or "Dokument"


def _last_name(name: str) -> str:
    parts = [part for part in name.split() if part.strip()]
    return parts[-1] if parts else name


def _company_from_title_suffix(title: str) -> str:
    if " - " not in title:
        return ""
    candidate = title.rsplit(" - ", 1)[-1].strip(" .,-")
    if not candidate:
        return ""
    if len(candidate.split()) > 4:
        return ""
    return candidate


def _company_from_domain(value: str) -> str:
    host = re.sub(r"^https?://", "", value.strip(), flags=re.IGNORECASE)
    host = host.split("/", 1)[0].split("?", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    stem = host.split(".")[0]
    if not stem:
        return ""
    return "".join(part.capitalize() for part in re.split(r"[-_]+", stem) if part)


def cover_letter_filename_is_compact(
    filename: str | Path,
    profile: CandidateProfile,
    listing: JobListing,
) -> bool:
    actual = Path(filename).name
    expected = cover_letter_filename(profile, listing)
    if actual != expected:
        return False
    return len(Path(actual).stem) <= 80


def _trim_underscore_words(value: str, max_length: int | None) -> str:
    if max_length is None or len(value) <= max_length:
        return value
    parts = [part for part in value.split("_") if part]
    kept: list[str] = []
    for part in parts:
        candidate = "_".join([*kept, part])
        if len(candidate) > max_length:
            break
        kept.append(part)
    return "_".join(kept) or value[:max_length].rstrip("_")
