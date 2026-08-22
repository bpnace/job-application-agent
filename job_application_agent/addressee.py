from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from .models import JobListing


PORTAL_COMPANY_NAMES = [
    "join.com",
    "join",
    "Berlin Startup Jobs",
    "berlinstartupjobs.com",
    "StepStone",
    "stepstone.de",
    "Indeed",
    "indeed.com",
    "LinkedIn Jobs",
    "linkedin.com",
    "Xing Jobs",
    "Arbeitnow",
    "arbeitnow.com",
    "Arbeitsagentur",
    "Jobtensor",
    "DEVjobs",
    "Remotive",
    "remotive.com",
    "Remote OK",
    "remoteok.com",
    "Remote Rocketship",
    "RemoteRocketship",
    "remoterocketship.com",
    "Wellfound",
    "Otta",
    "Workwise",
    "Personio",
    "personio.com",
]

LEGAL_SUFFIXES = [
    "GmbH & Co. KG",
    "PartGmbB",
    "GmbH",
    "mbH",
    "AG",
    "SE",
    "UG",
    "KG",
    "Inc.",
    "Inc",
    "Ltd.",
    "Ltd",
    "LLC",
]

ROLE_WORDS = {
    "ai",
    "automation",
    "business",
    "customer",
    "developer",
    "engineer",
    "frontend",
    "full",
    "fullstack",
    "implementation",
    "junior",
    "ki",
    "lead",
    "manager",
    "product",
    "senior",
    "solutions",
    "stack",
    "success",
    "technical",
    "workflow",
}

SLUG_NOISE_WORDS = {
    "apply",
    "berlin",
    "career",
    "careers",
    "companies",
    "company",
    "de",
    "en",
    "jobs",
    "job",
    "remote",
    "work",
    "www",
}
COMPANY_PATH_MARKERS = {
    "companies",
    "company",
    "employer",
    "employers",
    "organisation",
    "organisations",
    "organization",
    "organizations",
    "unternehmen",
}

LEGAL_SLUG_SUFFIXES = {
    "gmbh": "GmbH",
    "mbh": "mbH",
    "ag": "AG",
    "se": "SE",
    "ug": "UG",
    "kg": "KG",
    "inc": "Inc",
    "ltd": "Ltd",
    "llc": "LLC",
}


def normalize_name(value: str) -> str:
    text = value.lower().replace("&", " and ")
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"[^\wäöüß]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def portal_hits(value: str) -> list[str]:
    normalized = f" {normalize_name(value)} "
    hits: list[str] = []
    for portal in PORTAL_COMPANY_NAMES:
        term = normalize_name(portal)
        if term and f" {term} " in normalized:
            hits.append(portal)
    return hits


def is_portal_name(value: str) -> bool:
    if not value.strip():
        return False
    normalized = f" {normalize_name(value)} "
    return any(
        f" {normalize_name(portal)} " in normalized for portal in PORTAL_COMPANY_NAMES
    )


def company_base_name(company: str) -> str:
    cleaned = re.sub(r"\s+", " ", company.strip(" \t\r\n,.-"))
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        cleaned = re.sub(
            rf"\s+{re.escape(suffix)}$", "", cleaned, flags=re.IGNORECASE
        ).strip()
    return cleaned or company.strip()


def salutation_line(markdown: str) -> str:
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith(
            ("github ", "linkedin ", "viele grüße", "best regards")
        ):
            continue
        return line
    return ""


def expected_addressee_names(listing: JobListing) -> list[str]:
    names: list[str] = []
    for contact in _extract_contact_names(listing):
        names.append(contact)

    company = listing.company.strip()
    if company and not is_portal_name(company):
        names.append(company_base_name(company))
    else:
        extracted = extract_company_from_listing(listing)
        if extracted:
            names.append(company_base_name(extracted))

    return _dedupe_names(names)


def salutation_matches_expected(salutation: str, expected_names: list[str]) -> bool:
    salutation_norm = f" {normalize_name(salutation)} "
    for name in expected_names:
        base = company_base_name(name)
        terms = [base]
        parts = normalize_name(base).split()
        if len(parts) >= 2:
            terms.append(parts[-1])
        for term in terms:
            normalized = normalize_name(term)
            if normalized and f" {normalized} " in salutation_norm:
                return True
    return False


def extract_company_from_listing(listing: JobListing) -> str:
    text = "\n".join(
        [
            listing.title,
            listing.company,
            listing.description,
            listing.raw_excerpt,
            " ".join(listing.tags),
        ]
    )
    for candidate in _company_candidates_from_text(text):
        if not is_portal_name(candidate):
            return candidate

    return _company_from_urls(listing.apply_url, listing.source_url)


def _company_candidates_from_text(text: str) -> list[str]:
    suffix_pattern = (
        r"GmbH\s*&\s*Co\.?\s*KG|PartGmbB|GmbH|mbH|AG|SE|UG|KG|Inc\.?|Ltd\.?|LLC"
    )
    pattern = re.compile(
        rf"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&+.'’/-]*(?:\s+[A-ZÄÖÜx&+][A-Za-zÄÖÜäöüß0-9&+.'’/-]*){{0,5}}\s+(?:{suffix_pattern}))\b"
    )
    candidates: list[str] = []
    for match in pattern.finditer(text):
        candidate = _drop_role_prefixes(match.group(1).strip(" ,.-"))
        if candidate:
            candidates.append(candidate)
    return candidates


def _drop_role_prefixes(candidate: str) -> str:
    parts = candidate.split()
    while len(parts) > 2 and normalize_name(parts[0]) in ROLE_WORDS:
        parts = parts[1:]
    return " ".join(parts)


def _company_from_urls(*urls: str) -> str:
    for url in urls:
        if not url:
            continue
        parsed = urlparse(url)
        text = unquote(" ".join([parsed.netloc, parsed.path, parsed.query])).lower()
        text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
        tokens = [
            token for token in text.split() if token and token not in SLUG_NOISE_WORDS
        ]
        for idx, token in enumerate(tokens):
            suffix = LEGAL_SLUG_SUFFIXES.get(token)
            if not suffix:
                continue
            for width in range(1, min(4, idx) + 1):
                name_tokens = tokens[idx - width : idx]
                if any(term in SLUG_NOISE_WORDS for term in name_tokens):
                    continue
                candidate = f"{_titlecase_slug_name(name_tokens)} {suffix}".strip()
                if candidate and not is_portal_name(candidate):
                    return candidate
        candidate = _company_from_portal_path(parsed.path)
        if candidate:
            return candidate
    return ""


def _company_from_portal_path(path: str) -> str:
    segments = [
        re.sub(r"[^a-z0-9äöüß]+", " ", unquote(segment).lower()).strip()
        for segment in path.split("/")
        if segment.strip()
    ]
    for index, segment in enumerate(segments[:-1]):
        if segment not in COMPANY_PATH_MARKERS:
            continue
        candidate = _company_from_slug_segment(segments[index + 1])
        if candidate:
            return candidate
    return ""


def _company_from_slug_segment(segment: str) -> str:
    tokens = [
        token
        for token in segment.split()
        if token and token not in SLUG_NOISE_WORDS and token not in ROLE_WORDS
    ]
    if not tokens or len(tokens) > 5:
        return ""
    if any(token in LEGAL_SLUG_SUFFIXES for token in tokens):
        suffix_index = next(
            index for index, token in enumerate(tokens) if token in LEGAL_SLUG_SUFFIXES
        )
        name_tokens = tokens[:suffix_index]
        suffix = LEGAL_SLUG_SUFFIXES[tokens[suffix_index]]
        if not name_tokens:
            return ""
        candidate = f"{_titlecase_slug_name(name_tokens)} {suffix}".strip()
    else:
        candidate = _titlecase_slug_name(tokens)
    if len(normalize_name(candidate)) < 3 or is_portal_name(candidate):
        return ""
    return candidate


def _titlecase_slug_name(tokens: list[str]) -> str:
    words = []
    for token in tokens:
        if token in {"ai", "api", "crm", "llm"}:
            words.append(token.upper())
        else:
            words.append(token[:1].upper() + token[1:])
    return " ".join(words)


def _extract_contact_names(listing: JobListing) -> list[str]:
    text = "\n".join([listing.description, listing.raw_excerpt])
    pattern = re.compile(
        r"(?:Ansprechpartner(?:in)?|Kontakt|Contact|Recruiter|Hiring Manager)\s*[:\-]\s*"
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'’-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'’-]+){1,2})"
    )
    return [match.group(1).strip() for match in pattern.finditer(text)]


def _dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        normalized = normalize_name(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(name)
    return deduped
