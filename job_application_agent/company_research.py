"""Public, source-bound company research for an application package.

This module deliberately performs a small GET-only crawl: the public job page
and, at most, one explicitly linked company page.  It does not use cookies,
credentials, search accounts, or speculative facts.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .addressee import company_base_name, extract_company_from_listing, is_portal_name
from .models import CompanyFact, CompanyResearch, JobListing
from .utils import normalize_space, write_json


FetchText = Callable[[str], str]


def _public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".local") or re.fullmatch(r"(?:127|10|192\.168|172\.(?:1[6-9]|2\d|3[0-1]))(?:\.\d{1,3}){3}", host):
        return False
    return True


def _get_public_html(url: str) -> str:
    if not _public_http_url(url):
        raise ValueError(f"Research accepts public http(s) URLs only: {url}")
    with httpx.Client(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "job-application-agent/0.1 (+public-research)"},
    ) as client:
        response = client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.casefold():
        raise ValueError(f"Research page is not HTML: {url}")
    return response.text


def _resolved_company(listing: JobListing) -> str:
    if listing.company and not is_portal_name(listing.company):
        return company_base_name(listing.company)
    extracted = extract_company_from_listing(listing)
    if extracted:
        return company_base_name(extracted)
    return ""


def _source_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _visible_text(soup: BeautifulSoup) -> list[str]:
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    values = [normalize_space(node.get_text(" ", strip=True)) for node in soup.find_all(["p", "li", "h2", "h3"])]
    return [value for value in values if len(value) >= 40]


def _contact_from_text(text: str) -> str:
    patterns = [
        r"(?:Ansprechperson|Ansprechpartner(?:in)?|Kontaktperson|Contact person|Hiring manager)\s*[:\-]?\s*([A-ZÄÖÜ][\wÄÖÜäöüß.'-]+(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.'-]+){1,2}?)",
        r"(?:Bei Fragen kontaktieren Sie|For questions contact)\s+([A-ZÄÖÜ][\wÄÖÜäöüß.'-]+(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.'-]+){1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return normalize_space(match.group(1)).strip(" ,.;")
    return ""


def _company_link(job_url: str, soup: BeautifulSoup) -> str:
    job_host = (urlparse(job_url).hostname or "").casefold()
    for anchor in soup.find_all("a", href=True):
        label = normalize_space(anchor.get_text(" ", strip=True)).casefold()
        href = urljoin(job_url, str(anchor["href"]))
        host = (urlparse(href).hostname or "").casefold()
        company_hint = any(token in label for token in ["über", "about", "unternehmen", "company", "team"])
        if _public_http_url(href) and (host == job_host or company_hint):
            return href
    return ""


def _facts_from_page(company: str, url: str, html: str) -> list[CompanyFact]:
    soup = BeautifulSoup(html, "html.parser")
    source_hash = _source_hash(html)
    candidates: list[str] = []
    description = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if description and description.get("content"):
        candidates.append(normalize_space(str(description["content"])))
    candidates.extend(_visible_text(soup))
    facts: list[CompanyFact] = []
    for candidate in candidates:
        if len(candidate) < 40 or len(candidate) > 420:
            continue
        # Keep only wording present on a public source.  The claim equals its
        # traceable excerpt; no model summary or inference is introduced.
        facts.append(CompanyFact(claim=candidate, excerpt=candidate, source_url=url, source_sha256=source_hash))
        if len(facts) >= 3:
            break
    return facts


def research_company(listing: JobListing, *, fetcher: FetchText | None = None) -> CompanyResearch:
    """Collect quoted public facts and optional public contact from a listing."""
    company = _resolved_company(listing)
    job_url = listing.apply_url or listing.source_url
    if not company:
        return CompanyResearch(company="", retrieved_at=_now())
    get = fetcher or _get_public_html
    if not _public_http_url(job_url):
        raise ValueError(f"Research accepts public http(s) URLs only: {job_url}")
    job_html = get(job_url)
    job_soup = BeautifulSoup(job_html, "html.parser")
    company_url = _company_link(job_url, job_soup)
    pages = [(job_url, job_html)]
    if company_url and company_url != job_url:
        try:
            pages.append((company_url, get(company_url)))
        except (httpx.HTTPError, ValueError, OSError):
            pass
    # Only a contact displayed on the listing itself may be used in the
    # salutation. A generic company-page contact can be unrelated to hiring.
    contact = _contact_from_text(
        normalize_space(BeautifulSoup(job_html, "html.parser").get_text(" ", strip=True))
    )
    facts: list[CompanyFact] = []
    for url, html in pages:
        facts.extend(_facts_from_page(company, url, html))
    seen: set[tuple[str, str]] = set()
    unique_facts = []
    for fact in facts:
        key = (fact.source_url, fact.excerpt)
        if key not in seen:
            seen.add(key)
            unique_facts.append(fact)
    return CompanyResearch(
        company=company,
        contact_name=contact,
        facts=unique_facts[:3],
        source_urls=[url for url, _html in pages],
        retrieved_at=_now(),
    )


def research_is_approvable(research: CompanyResearch) -> bool:
    return bool(research.company.strip() and research.facts and research.source_urls)


def write_company_research(package_dir: Path, research: CompanyResearch) -> tuple[Path, Path]:
    package_dir = package_dir.expanduser().resolve()
    json_path = package_dir / "company_research.json"
    md_path = package_dir / "company_research.md"
    write_json(json_path, research)
    facts = "\n".join(f"- {fact.claim}\n  Source: {fact.source_url}" for fact in research.facts) or "- No source-backed company fact found."
    sources = "\n".join(f"- {url}" for url in research.source_urls) or "- None"
    md_path.write_text(
        f"# Public Company Research\n\nCompany: {research.company or 'unresolved'}\nContact: {research.contact_name or 'not publicly named'}\nRetrieved: {research.retrieved_at}\n\n## Source-backed facts\n{facts}\n\n## Crawled public URLs\n{sources}\n",
        encoding="utf-8",
    )
    return json_path, md_path


def load_company_research(path: Path) -> CompanyResearch:
    return CompanyResearch.model_validate_json(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
