from __future__ import annotations

import json
import ipaddress
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal
from urllib.parse import quote, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from .application import infer_application_route, route_listing_fields
from .models import JobListing, SourceHealth, utc_now_iso
from .utils import absolute_url, normalize_space


USER_AGENT = "Mozilla/5.0 job-application-agent/0.1 public-job-discovery"
FREELANCERMAP_HOSTS = {"www.freelancermap.com", "freelancermap.com"}
ARBEITNOW_HOSTS = {"www.arbeitnow.com", "arbeitnow.com"}
ARBEITSAGENTUR_API_HOSTS = {"rest.arbeitsagentur.de"}
ARBEITSAGENTUR_PUBLIC_HOSTS = {"arbeitsagentur.de", "www.arbeitsagentur.de"}
REMOTEOK_HOSTS = {"remoteok.com", "www.remoteok.com"}
REMOTIVE_HOSTS = {"remotive.com", "www.remotive.com"}
STEPSTONE_HOSTS = {"www.stepstone.de", "stepstone.de"}
LINKEDIN_HOSTS = {"www.linkedin.com", "linkedin.com", "de.linkedin.com"}
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SERPAPI_HOSTS = {"serpapi.com", "www.serpapi.com"}
PUBLIC_JOB_BOARD_HINTS = [
    "/job",
    "/jobs",
    "/stellen",
    "/stellenangebot",
    "/stellenangebote",
    "/karriere",
    "/career",
    "/project",
    "developer",
    "engineer",
    "entwickler",
    "frontend",
    "backend",
    "fullstack",
    "automation",
    "remote",
]
PUBLIC_JOB_BOARD_CATEGORY_LABELS = {
    "all jobs",
    "companies hiring in germany",
    "find jobs",
    "job search",
    "jobsuche",
    "remote jobs",
    "stellenangebote",
}
PUBLIC_JOB_ROLE_SIGNALS = [
    "analyst",
    "automation",
    "backend",
    "berater",
    "consultant",
    "data",
    "daten",
    "developer",
    "devops",
    "engineer",
    "entwickler",
    "experte",
    "expert",
    "frontend",
    "fullstack",
    "ingenieur",
    "lead",
    "manager",
    "product",
    "software",
    "specialist",
    "spezialist",
]


@dataclass
class SourceResult:
    listings: list[JobListing]
    health: SourceHealth


def detect_language(text: str) -> Literal["de", "en", "unknown"]:
    lower = text.lower()
    german_hits = [
        " und ",
        " der ",
        " die ",
        " das ",
        " für ",
        " mit ",
        "entwickler",
        "expert:in",
        "berater",
        "bewerbung",
        "deutsch",
        "kenntnisse",
    ]
    english_hits = [
        " and ",
        " developer",
        " engineer",
        " remote",
        "skills",
        "experience",
    ]
    if sum(hit in lower for hit in german_hits) > sum(
        hit in lower for hit in english_hits
    ):
        return "de"
    if sum(hit in lower for hit in english_hits) > 0:
        return "en"
    return "unknown"


def classify_remote_type(text: str) -> str:
    lower = text.lower()
    if re.search(
        r"\bhybrid\b|teilremote|teilweise\s+remote|teilweise\s+homeoffice|"
        r"homeoffice\s+möglich|homeoffice\s+moeglich|remote\s+möglich|remote\s+moeglich|"
        r"remote\s+option|remote\s+tage|tage\s+remote",
        lower,
    ):
        return "hybrid"
    if re.search(
        r"\b(100%\s*)?remote\b|fully remote|remote[- ]first|remote[- ]only|home\s*office|homeoffice|work from home|von zu hause|von zuhause",
        lower,
    ):
        return "remote"
    return ""


def fetch_text(
    url: str,
    timeout: float = 20.0,
    allowed_hosts: set[str] | None = None,
    required_host_suffix: str | None = None,
    max_redirects: int = 5,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str | None]:
    try:
        current_url = url
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        ) as client:
            for _redirect_count in range(max_redirects + 1):
                policy_error = validate_public_source_url(
                    current_url,
                    allowed_hosts=allowed_hosts,
                    required_host_suffix=required_host_suffix,
                )
                if policy_error:
                    return "", policy_error

                response = client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return (
                            "",
                            f"Redirect response did not include a Location header: {current_url}",
                        )
                    next_url = urljoin(current_url, location)
                    policy_error = validate_public_source_url(
                        next_url,
                        allowed_hosts=allowed_hosts,
                        required_host_suffix=required_host_suffix,
                    )
                    if policy_error:
                        return "", f"Redirect blocked: {policy_error}"
                    current_url = next_url
                    continue

                response.raise_for_status()
                return response.text, None
            return "", f"Redirect limit exceeded after {max_redirects} redirects: {url}"
    except Exception as exc:  # pragma: no cover - exact network failures vary.
        return "", f"{type(exc).__name__}: {exc}"


def is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return False
    except OSError:
        pass
    return True


def validate_public_source_url(
    url: str,
    allowed_hosts: set[str] | None = None,
    required_host_suffix: str | None = None,
) -> str | None:
    if not is_public_http_url(url):
        return f"URL is not a public http(s) URL: {url}"
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if allowed_hosts and host not in allowed_hosts:
        return f"URL host is not allowed for this source: {host}"
    if required_host_suffix and not host.endswith(required_host_suffix):
        return f"URL host must end with {required_host_suffix}: {host}"
    return None


class PersonioXmlSource:
    name = "personio_xml"

    def __init__(self, feed_urls: Iterable[str]):
        self.feed_urls = list(feed_urls)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.feed_urls:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No Personio feeds configured.",
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        for url in self.feed_urls:
            if len(listings) >= max_candidates:
                break
            policy_error = validate_public_source_url(
                url, required_host_suffix=".jobs.personio.de"
            )
            if policy_error:
                messages.append(policy_error)
                continue
            body, error = fetch_text(url, required_host_suffix=".jobs.personio.de")
            if error:
                messages.append(f"{url}: {error}")
                continue
            listings.extend(
                parse_personio_xml(body, url)[: max_candidates - len(listings)]
            )
            time.sleep(host_delay_seconds)

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def _xml_text(node: ET.Element, names: list[str]) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return normalize_space(found.text)
    return ""


def parse_personio_xml(xml_text: str, source_url: str) -> list[JobListing]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    feed_company = _xml_text(root, ["company", "subcompany", "companyName"])
    postings = (
        root.findall(".//position")
        or root.findall(".//job")
        or root.findall(".//posting")
    )
    listings: list[JobListing] = []
    for node in postings:
        title = _xml_text(node, ["name", "title", "jobTitle"])
        # `office` is a location in Personio feeds, never an employer.  Use
        # an explicitly supplied company/subcompany from the posting or feed,
        # and leave an unresolved feed as the portal rather than inventing a
        # company from its office name.
        company = (
            _xml_text(node, ["company", "subcompany", "companyName"])
            or feed_company
            or "Personio"
        )
        location = _xml_text(node, ["office", "location", "city"])
        employment = _xml_text(node, ["employmentType", "employment_type", "schedule"])
        description_parts = []
        for desc in node.findall(".//jobDescription"):
            value = _xml_text(desc, ["value", "content", "description"])
            if value:
                description_parts.append(value)
        description = normalize_space(
            " ".join(description_parts) or _xml_text(node, ["description"])
        )
        url = _xml_text(node, ["recruitingCategory", "url", "applyUrl"]) or source_url
        text = " ".join([title, company, location, employment, description])
        if title:
            route = infer_application_route(
                url, source="personio_xml", source_type="personio"
            )
            listings.append(
                JobListing(
                    source="personio_xml",
                    source_url=source_url,
                    title=title,
                    company=company,
                    location=location,
                    remote_type=classify_remote_type(text),
                    work_type=employment,
                    language=detect_language(text),
                    description=description[:4000],
                    tags=[],
                    apply_url=url,
                    **route_listing_fields(route),
                    raw_excerpt=text[:1000],
                    fetched_at=utc_now_iso(),
                )
            )
    return listings


class FreelancermapPublicSource:
    name = "freelancermap_public"

    def __init__(self, urls: Iterable[str]):
        self.urls = list(urls) or ["https://www.freelancermap.com/projects/remote"]

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        listings: list[JobListing] = []
        messages: list[str] = []
        for url in self.urls:
            if len(listings) >= max_candidates:
                break
            policy_error = validate_public_source_url(
                url, allowed_hosts=FREELANCERMAP_HOSTS
            )
            if policy_error:
                messages.append(policy_error)
                continue
            body, error = fetch_text(url, allowed_hosts=FREELANCERMAP_HOSTS)
            if error:
                messages.append(f"{url}: {error}")
                continue
            listings.extend(
                parse_freelancermap_html(body, url)[: max_candidates - len(listings)]
            )
            time.sleep(host_delay_seconds)
        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_freelancermap_html(html: str, source_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".project-card")
    listings: list[JobListing] = []
    for card in cards:
        title_link = card.select_one('a[data-testid="title"]')
        if not title_link:
            continue
        title = normalize_space(title_link.get_text(" "))
        href_value = title_link.get("href", "")
        href = href_value if isinstance(href_value, str) else ""
        company_node = card.select_one(".project-info > div")
        company = (
            normalize_space(company_node.get_text(" ")) if company_node else "Unknown"
        )
        badges = [
            normalize_space(node.get_text(" "))
            for node in card.select('[data-id="project-card-keyword-link"], .badge')
        ]
        location = normalize_space(
            " ".join(node.get_text(" ") for node in card.select('[data-testid="city"]'))
        )
        remote_node = card.select_one('[data-testid="remoteInPercent"]')
        type_node = card.select_one('[data-testid="type"]')
        created_node = card.select_one('[data-testid="created"]')
        remote_type = normalize_space(remote_node.get_text(" ")) if remote_node else ""
        work_type = normalize_space(type_node.get_text(" ")) if type_node else ""
        date_posted = (
            normalize_space(created_node.get_text(" ")) if created_node else ""
        )
        description = normalize_space(
            " ".join(
                [title, company, location, remote_type, work_type, " ".join(badges)]
            )
        )
        apply_url = absolute_url(source_url, href)
        route = infer_application_route(apply_url, source="freelancermap_public")
        listings.append(
            JobListing(
                source="freelancermap_public",
                source_url=source_url,
                title=title,
                company=company,
                location=location,
                remote_type=remote_type,
                work_type=work_type,
                language=detect_language(description),
                description=description,
                tags=[tag for tag in badges if tag],
                date_posted=date_posted,
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


def host_variants(url: str) -> set[str]:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return set()
    variants = {host}
    if host.startswith("www."):
        variants.add(host.removeprefix("www."))
    else:
        variants.add(f"www.{host}")
    return variants


def strip_url_tracking(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def first_matching_text(root: Tag | BeautifulSoup | None, selectors: list[str]) -> str:
    if not root:
        return ""
    for selector in selectors:
        node = root.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" "))
            if value:
                return value
    return ""


def nearby_listing_card(anchor: Tag) -> Tag:
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        raw_classes = parent.get("class")
        if isinstance(raw_classes, list):
            classes = " ".join(str(item) for item in raw_classes)
        elif isinstance(raw_classes, str):
            classes = raw_classes
        else:
            classes = ""
        marker = " ".join(
            str(parent.get(attr) or "")
            for attr in ["data-at", "data-testid", "data-test"]
        )
        if parent.name in {"article", "li"} or re.search(
            r"job|search-card", f"{classes} {marker}", re.I
        ):
            return parent
    parent = anchor.parent
    return parent if isinstance(parent, Tag) else anchor


class StepstonePublicSource:
    name = "stepstone_public"

    def __init__(self, urls: Iterable[str]):
        self.urls = list(urls)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.urls:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No StepStone URLs configured.",
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        for url in self.urls:
            if len(listings) >= max_candidates:
                break
            policy_error = validate_public_source_url(
                url, allowed_hosts=STEPSTONE_HOSTS
            )
            if policy_error:
                messages.append(policy_error)
                continue
            body, error = fetch_text(url, allowed_hosts=STEPSTONE_HOSTS)
            if error:
                messages.append(f"{url}: {error}")
                continue
            parsed = parse_stepstone_html(body, url)
            listings.extend(parsed[: max_candidates - len(listings)])
            time.sleep(host_delay_seconds)

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_stepstone_html(html: str, source_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[JobListing] = []
    seen_urls: set[str] = set()
    for anchor in soup.select("a[href]"):
        href_value = anchor.get("href", "")
        href = href_value if isinstance(href_value, str) else ""
        apply_url = strip_url_tracking(absolute_url(source_url, href))
        parsed = urlparse(apply_url)
        if (
            parsed.hostname not in STEPSTONE_HOSTS
            or "/stellenangebote--" not in parsed.path.lower()
        ):
            continue
        if apply_url in seen_urls or validate_public_source_url(
            apply_url, allowed_hosts=STEPSTONE_HOSTS
        ):
            continue
        card = nearby_listing_card(anchor)
        text = normalize_space(anchor.get_text(" "))
        title = (
            first_matching_text(
                card, ['[data-at*="title"]', '[data-testid*="title"]', "h2", "h3"]
            )
            or text
        )
        title = normalize_space(re.sub(r"\s+-\s+StepStone.*$", "", title))[:180]
        if len(title) < 4:
            continue
        company = (
            first_matching_text(
                card,
                [
                    '[data-at*="company"]',
                    '[data-testid*="company"]',
                    '[class*="company"]',
                    'a[href*="/unternehmen--"]',
                ],
            )
            or "StepStone"
        )
        location = first_matching_text(
            card,
            [
                '[data-at*="location"]',
                '[data-testid*="location"]',
                '[class*="location"]',
            ],
        )
        card_text = normalize_space(card.get_text(" "))
        description = normalize_space(" ".join([title, company, location, card_text]))[
            :4000
        ]
        route = infer_application_route(
            apply_url, source="stepstone_public", source_type="public_job_board"
        )
        seen_urls.add(apply_url)
        listings.append(
            JobListing(
                source="stepstone_public",
                source_url=source_url,
                title=title,
                company=company[:180],
                location=location,
                remote_type=classify_remote_type(description),
                language=detect_language(description),
                description=description,
                tags=["StepStone"],
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class LinkedinPublicSource:
    name = "linkedin_public"

    def __init__(self, urls: Iterable[str]):
        self.urls = list(urls)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.urls:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No LinkedIn URLs configured.",
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        for url in self.urls:
            if len(listings) >= max_candidates:
                break
            policy_error = validate_public_source_url(url, allowed_hosts=LINKEDIN_HOSTS)
            if policy_error:
                messages.append(policy_error)
                continue
            body, error = fetch_text(url, allowed_hosts=LINKEDIN_HOSTS)
            if error:
                messages.append(f"{url}: {error}")
                continue
            parsed = parse_linkedin_jobs_html(body, url)
            listings.extend(parsed[: max_candidates - len(listings)])
            time.sleep(host_delay_seconds)

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_linkedin_jobs_html(html: str, source_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[JobListing] = []
    seen_urls: set[str] = set()
    for anchor in soup.select('a[href*="/jobs/view/"]'):
        href_value = anchor.get("href", "")
        href = href_value if isinstance(href_value, str) else ""
        apply_url = strip_url_tracking(absolute_url(source_url, href))
        parsed = urlparse(apply_url)
        if (
            parsed.hostname not in LINKEDIN_HOSTS
            or "/jobs/view/" not in parsed.path.lower()
        ):
            continue
        if apply_url in seen_urls or validate_public_source_url(
            apply_url, allowed_hosts=LINKEDIN_HOSTS
        ):
            continue
        card = nearby_listing_card(anchor)
        text = normalize_space(anchor.get_text(" "))
        title = (
            first_matching_text(
                card, [".base-search-card__title", '[class*="title"]', "h3"]
            )
            or text
        )
        title = normalize_space(title)[:180]
        if len(title) < 4 or title.lower() in {"sign in", "join now", "jobs"}:
            continue
        company = (
            first_matching_text(
                card,
                [
                    ".base-search-card__subtitle",
                    ".hidden-nested-link",
                    '[class*="company"]',
                    "h4",
                ],
            )
            or "LinkedIn"
        )
        location = first_matching_text(
            card, [".job-search-card__location", '[class*="location"]']
        )
        card_text = normalize_space(card.get_text(" "))
        description = normalize_space(" ".join([title, company, location, card_text]))[
            :4000
        ]
        route = infer_application_route(
            apply_url, source="linkedin_public", source_type="linkedin"
        )
        seen_urls.add(apply_url)
        listings.append(
            JobListing(
                source="linkedin_public",
                source_url=source_url,
                title=title,
                company=company[:180],
                location=location,
                remote_type=classify_remote_type(description),
                language=detect_language(description),
                description=description,
                tags=["LinkedIn"],
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class PublicJobBoardSource:
    name = "public_job_boards"

    def __init__(self, boards: Iterable[dict[str, Any]], per_board_limit: int = 8):
        self.boards = list(boards)
        self.per_board_limit = per_board_limit

    @staticmethod
    def board_urls(board: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        search_urls = board.get("search_urls")
        if isinstance(search_urls, list):
            urls.extend(str(url).strip() for url in search_urls if str(url).strip())
        url = str(board.get("url", "")).strip()
        if url:
            urls.append(url)
        return list(dict.fromkeys(urls))

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.boards:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No public job boards configured.",
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        disabled_boards: list[str] = []
        for board in self.boards:
            if len(listings) >= max_candidates:
                break
            name = str(board.get("name") or "public job board")
            if board.get("enabled") is False:
                disabled_boards.append(name)
                continue
            urls = self.board_urls(board)
            if not urls:
                messages.append(f"{name}: no URL configured")
                continue
            primary_url = str(board.get("url") or urls[0])
            name = str(
                board.get("name")
                or urlparse(primary_url).hostname
                or "public job board"
            )
            configured_hosts = board.get("allowed_hosts")
            allowed_hosts = (
                set(configured_hosts)
                if isinstance(configured_hosts, list)
                else host_variants(primary_url)
            )
            board_returned = 0
            for url in urls:
                if (
                    len(listings) >= max_candidates
                    or board_returned >= self.per_board_limit
                ):
                    break
                policy_error = validate_public_source_url(
                    url, allowed_hosts=allowed_hosts
                )
                if policy_error:
                    messages.append(f"{name}: {policy_error}")
                    continue
                body, error = fetch_text(url, allowed_hosts=allowed_hosts)
                if error:
                    messages.append(f"{name}: {error}")
                    continue
                parsed = parse_public_job_board_html(body, url, name)
                take = min(
                    self.per_board_limit - board_returned,
                    max_candidates - len(listings),
                )
                selected = parsed[:take]
                listings.extend(selected)
                board_returned += len(selected)
                time.sleep(host_delay_seconds)
        if disabled_boards:
            messages.append("Skipped disabled boards: " + ", ".join(disabled_boards))

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def scrapling_available() -> bool:
    try:
        from scrapling import Selector  # noqa: F401  # pyright: ignore[reportMissingImports]
    except Exception:
        return False
    return True


class ScraplingPublicJobBoardSource:
    name = "scrapling_public_job_boards"

    def __init__(self, boards: Iterable[dict[str, Any]], per_board_limit: int = 8):
        self.boards = list(boards)
        self.per_board_limit = per_board_limit

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.boards:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No public job boards configured.",
                ),
            )
        if not scrapling_available():
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="Install optional dependencies with `uv sync --extra scraping` to enable Scrapling parsing.",
                    fetched_at=utc_now_iso(),
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        disabled_boards: list[str] = []
        for board in self.boards:
            if len(listings) >= max_candidates:
                break
            name = str(board.get("name") or "public job board")
            if board.get("enabled") is False:
                disabled_boards.append(name)
                continue
            urls = PublicJobBoardSource.board_urls(board)
            if not urls:
                messages.append(f"{name}: no URL configured")
                continue
            primary_url = str(board.get("url") or urls[0])
            name = str(
                board.get("name")
                or urlparse(primary_url).hostname
                or "public job board"
            )
            configured_hosts = board.get("allowed_hosts")
            allowed_hosts = (
                set(configured_hosts)
                if isinstance(configured_hosts, list)
                else host_variants(primary_url)
            )
            board_returned = 0
            for url in urls:
                if (
                    len(listings) >= max_candidates
                    or board_returned >= self.per_board_limit
                ):
                    break
                policy_error = validate_public_source_url(
                    url, allowed_hosts=allowed_hosts
                )
                if policy_error:
                    messages.append(f"{name}: {policy_error}")
                    continue
                body, error = fetch_text(url, allowed_hosts=allowed_hosts)
                if error:
                    messages.append(f"{name}: {error}")
                    continue
                parsed = parse_scrapling_job_board_html(body, url, name)
                take = min(
                    self.per_board_limit - board_returned,
                    max_candidates - len(listings),
                )
                selected = parsed[:take]
                listings.extend(selected)
                board_returned += len(selected)
                time.sleep(host_delay_seconds)
        if disabled_boards:
            messages.append("Skipped disabled boards: " + ", ".join(disabled_boards))

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_public_job_board_html(
    html: str, source_url: str, board_name: str
) -> list[JobListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[JobListing] = []
    seen_urls: set[str] = set()
    allowed_hosts = host_variants(source_url)
    for anchor in soup.select("a[href]"):
        text = normalize_space(anchor.get_text(" "))
        href_value = anchor.get("href", "")
        href = href_value if isinstance(href_value, str) else ""
        if len(text) < 4 or not href:
            continue
        lower_text = text.lower()
        lower_href = href.lower()
        if not any(
            hint in lower_text or hint in lower_href for hint in PUBLIC_JOB_BOARD_HINTS
        ):
            continue
        apply_url = absolute_url(source_url, href)
        if looks_like_public_job_board_category_link(text, apply_url):
            continue
        if apply_url in seen_urls or validate_public_source_url(
            apply_url, allowed_hosts=allowed_hosts
        ):
            continue
        seen_urls.add(apply_url)
        description = normalize_space(" ".join([text, board_name]))
        route = infer_application_route(
            apply_url, source="public_job_board", source_type="public_job_board_listing"
        )
        listings.append(
            JobListing(
                source="public_job_board",
                source_url=source_url,
                title=text[:180],
                company=board_name,
                language=detect_language(description),
                description=description[:1000],
                tags=[board_name],
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


def looks_like_public_job_board_category_link(title: str, link: str) -> bool:
    lower_title = title.lower().strip()
    path = urlparse(link).path.lower().rstrip("/")
    slug = path.rsplit("/", 1)[-1].replace("-", " ")
    role_text = f"{lower_title} {slug}"
    if lower_title in PUBLIC_JOB_BOARD_CATEGORY_LABELS:
        return True
    if re.match(r"^jobs?\s+(with|by|in|near|for)\b", lower_title):
        return True
    if path in {"", "/", "/jobs", "/job", "/jobsuche", "/stellenangebote"}:
        return True
    if path in {"/tag/jobs", "/tags/jobs"}:
        return True
    if re.search(
        r"/(skill-areas|job-category|job-categories|career-areas|categories|category|tags|tag)($|/)",
        path,
    ):
        return True
    if re.search(
        r"/jobs/(companies|locations|countries|tags|categories|departments|salary)($|/)",
        path,
    ):
        return True
    if (
        re.match(r"^/(jobs|stellenangebote)/[^/]+$", path)
        and len(lower_title.split()) < 2
    ):
        return True
    if re.match(r"^/(jobs|stellenangebote)/[^/]+$", path) and not any(
        signal in role_text for signal in PUBLIC_JOB_ROLE_SIGNALS
    ):
        return True
    return bool(re.search(r"/jobs-(with|by|in|near|for)-|/remote-jobs($|/)", path))


def parse_scrapling_job_board_html(
    html: str, source_url: str, board_name: str
) -> list[JobListing]:
    from scrapling import Selector  # pyright: ignore[reportMissingImports]

    page = Selector(html, url=source_url)
    listings: list[JobListing] = []
    seen_urls: set[str] = set()
    allowed_hosts = host_variants(source_url)
    for anchor in page.css("a[href]"):
        text = normalize_space(str(anchor.get_all_text(separator=" ", strip=True)))
        href = normalize_space(str(anchor.css("::attr(href)").get() or ""))
        if len(text) < 4 or not href:
            continue
        lower_text = text.lower()
        lower_href = href.lower()
        if not any(
            hint in lower_text or hint in lower_href for hint in PUBLIC_JOB_BOARD_HINTS
        ):
            continue
        apply_url = absolute_url(source_url, href)
        if looks_like_public_job_board_category_link(text, apply_url):
            continue
        if apply_url in seen_urls or validate_public_source_url(
            apply_url, allowed_hosts=allowed_hosts
        ):
            continue
        seen_urls.add(apply_url)
        description = normalize_space(" ".join([text, board_name]))
        route = infer_application_route(
            apply_url,
            source="scrapling_public_job_board",
            source_type="public_job_board_listing",
        )
        listings.append(
            JobListing(
                source="scrapling_public_job_board",
                source_url=source_url,
                title=text[:180],
                company=board_name,
                language=detect_language(description),
                description=description[:1000],
                tags=[board_name, "scrapling"],
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class ArbeitnowApiSource:
    name = "arbeitnow_api"

    def __init__(self, endpoint: str = "https://www.arbeitnow.com/api/job-board-api"):
        self.endpoint = endpoint

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        policy_error = validate_public_source_url(
            self.endpoint, allowed_hosts=ARBEITNOW_HOSTS
        )
        if policy_error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=policy_error,
                    fetched_at=utc_now_iso(),
                ),
            )
        body, error = fetch_text(self.endpoint, allowed_hosts=ARBEITNOW_HOSTS)
        if error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=error,
                    fetched_at=utc_now_iso(),
                ),
            )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="degraded",
                    message=f"Invalid JSON: {exc}",
                    fetched_at=utc_now_iso(),
                ),
            )
        listings = parse_arbeitnow_json(payload, self.endpoint)[:max_candidates]
        time.sleep(host_delay_seconds)
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status="available" if listings else "unavailable",
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                fetched_at=utc_now_iso(),
            ),
        )


def parse_arbeitnow_json(payload: dict[str, Any], source_url: str) -> list[JobListing]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    listings: list[JobListing] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = normalize_space(str(item.get("title") or ""))
        if not title:
            continue
        company = normalize_space(
            str(item.get("company_name") or item.get("company") or "Unknown")
        )
        location = normalize_space(str(item.get("location") or ""))
        description_html = str(item.get("description") or "")
        description = normalize_space(
            BeautifulSoup(description_html, "lxml").get_text(" ")
        )
        raw_tags = item.get("tags", [])
        tags = (
            [normalize_space(str(tag)) for tag in raw_tags if tag]
            if isinstance(raw_tags, list)
            else []
        )
        raw_job_types = item.get("job_types", [])
        job_types = (
            [normalize_space(str(job_type)) for job_type in raw_job_types if job_type]
            if isinstance(raw_job_types, list)
            else []
        )
        apply_url = str(item.get("url") or source_url)
        if not is_public_http_url(apply_url):
            continue
        text = " ".join(
            [title, company, location, description, " ".join(tags), " ".join(job_types)]
        )
        route = infer_application_route(apply_url, source="arbeitnow_api")
        listings.append(
            JobListing(
                source="arbeitnow_api",
                source_url=source_url,
                title=title,
                company=company,
                location=location,
                remote_type="remote" if item.get("remote") else "",
                work_type=", ".join(job_types),
                language=detect_language(text),
                description=description[:4000],
                tags=tags,
                date_posted=str(item.get("created_at") or ""),
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=text[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


def _plain_html_text(value: Any) -> str:
    return normalize_space(BeautifulSoup(str(value or ""), "lxml").get_text(" "))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [
            normalize_space(str(item)) for item in value if normalize_space(str(item))
        ]
    if isinstance(value, str) and value.strip():
        return [normalize_space(value)]
    return []


def _contains_any_term(text: str, terms: Iterable[str]) -> bool:
    lower = text.lower()
    return any(term.strip().lower() in lower for term in terms if term.strip())


class ArbeitsagenturApiSource:
    """Legacy adapter for the former pc/v4 endpoint.

    The normal pipeline deliberately uses :class:`ArbeitsagenturPublicSearchSource`
    because this private endpoint has returned 403 responses in live runs.
    """
    name = "arbeitsagentur_api"

    def __init__(
        self,
        endpoint: str = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
        queries: Iterable[str] | None = None,
        locations: Iterable[str] | None = None,
        radius_km: int = 25,
        page_size: int = 25,
        max_pages: int = 1,
        api_key: str = "jobboerse-jobsuche",
        transport: httpx.BaseTransport | None = None,
    ):
        self.endpoint = endpoint
        self.queries = list(queries or [])
        self.locations = list(locations or ["Deutschland"])
        self.radius_km = radius_km
        self.page_size = page_size
        self.max_pages = max(1, max_pages)
        self.api_key = api_key
        self.transport = transport

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.queries:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No Arbeitsagentur API queries configured.",
                    fetched_at=utc_now_iso(),
                ),
            )
        policy_error = validate_public_source_url(
            self.endpoint, allowed_hosts=ARBEITSAGENTUR_API_HOSTS
        )
        if policy_error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=policy_error,
                    fetched_at=utc_now_iso(),
                ),
            )

        listings: list[JobListing] = []
        messages: list[str] = []
        with httpx.Client(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT, "X-API-Key": self.api_key},
            transport=self.transport,
        ) as client:
            for query in self.queries:
                if len(listings) >= max_candidates:
                    break
                for location in self.locations:
                    if len(listings) >= max_candidates:
                        break
                    for page in range(1, self.max_pages + 1):
                        if len(listings) >= max_candidates:
                            break
                        try:
                            response = client.get(
                                self.endpoint,
                                params={
                                    "was": query,
                                    "wo": location,
                                    "umkreis": self.radius_km,
                                    "page": page,
                                    "size": min(
                                        self.page_size,
                                        max_candidates - len(listings),
                                    ),
                                },
                            )
                            response.raise_for_status()
                            payload = response.json()
                        except (
                            Exception
                        ) as exc:  # pragma: no cover - network failures vary.
                            messages.append(
                                f"{query}/{location}/page {page}: {type(exc).__name__}: {exc}"
                            )
                            break
                        if isinstance(payload, dict):
                            parsed = parse_arbeitsagentur_json(payload, self.endpoint)
                            if not parsed:
                                break
                            listings.extend(parsed[: max_candidates - len(listings)])
                        time.sleep(host_delay_seconds)

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


class ArbeitsagenturPublicSearchSource:
    """Read the server-rendered, public BA jobsuche result page.

    This avoids depending on the non-public ``pc/v4`` API.  It only performs
    public GET requests and records official job-detail URLs for later human
    review.
    """

    name = "arbeitsagentur_public"
    endpoint = "https://www.arbeitsagentur.de/jobsuche/suche"

    def __init__(
        self,
        queries: Iterable[str] | None = None,
        locations: Iterable[str] | None = None,
        radius_km: int = 25,
        page_size: int = 25,
        max_pages: int = 1,
    ):
        self.queries = [item.strip() for item in queries or [] if item.strip()]
        self.locations = [item.strip() for item in locations or [] if item.strip()]
        self.radius_km = radius_km
        self.page_size = max(1, min(page_size, 25))
        self.max_pages = max(1, max_pages)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if not self.queries or not self.locations:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No Arbeitsagentur public-search queries or locations configured.",
                    fetched_at=utc_now_iso(),
                ),
            )
        listings: list[JobListing] = []
        messages: list[str] = []
        for query in self.queries:
            if len(listings) >= max_candidates:
                break
            for location in self.locations:
                if len(listings) >= max_candidates:
                    break
                for page in range(1, self.max_pages + 1):
                    if len(listings) >= max_candidates:
                        break
                    query_params = {
                        "was": query,
                        "wo": location,
                        "umkreis": self.radius_km,
                        "page": page,
                    }
                    source_url = f"{self.endpoint}?{urlencode(query_params)}"
                    body, error = fetch_text(
                        source_url, allowed_hosts=ARBEITSAGENTUR_PUBLIC_HOSTS
                    )
                    if error:
                        messages.append(f"{query}/{location}/page {page}: {error}")
                        break
                    parsed = parse_arbeitsagentur_search_html(body, source_url)
                    listings.extend(parsed[: max_candidates - len(listings)])
                    if len(parsed) < self.page_size:
                        break
                    time.sleep(host_delay_seconds)
        deduped: list[JobListing] = []
        seen_urls: set[str] = set()
        for listing in listings:
            key = listing.apply_url or listing.source_url
            if key in seen_urls:
                continue
            seen_urls.add(key)
            deduped.append(listing)
        return SourceResult(
            deduped[:max_candidates],
            SourceHealth(
                name=self.name,
                status="available" if deduped else "unavailable",
                candidates_seen=len(deduped),
                candidates_returned=len(deduped),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_arbeitsagentur_search_html(html: str, source_url: str) -> list[JobListing]:
    """Extract public listings from the official SSR ``ng-state`` payload."""
    soup = BeautifulSoup(html, "lxml")
    state = soup.find("script", attrs={"id": "ng-state"})
    if state is None:
        return []
    try:
        payload = json.loads(state.string or state.get_text() or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    result = payload.get("suchergebnis", {})
    items = result.get("ergebnisliste", []) if isinstance(result, dict) else []
    if not isinstance(items, list):
        return []
    return _parse_arbeitsagentur_items(
        items, source_url, source="arbeitsagentur_public"
    )


def parse_arbeitsagentur_json(
    payload: dict[str, Any], source_url: str
) -> list[JobListing]:
    items = payload.get("stellenangebote", [])
    if not isinstance(items, list):
        return []
    return _parse_arbeitsagentur_items(items, source_url, source="arbeitsagentur_api")


def _parse_arbeitsagentur_items(
    items: list[object], source_url: str, *, source: str
) -> list[JobListing]:
    listings: list[JobListing] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = normalize_space(
            str(item.get("stellenangebotsTitel") or item.get("titel") or "")
        )
        if not title:
            continue
        company = normalize_space(
            str(item.get("firma") or item.get("arbeitgeber") or "Arbeitsagentur")
        )
        raw_location = item.get("arbeitsort")
        work_location = raw_location if isinstance(raw_location, dict) else {}
        locations = item.get("stellenlokationen")
        if isinstance(locations, list) and locations and isinstance(locations[0], dict):
            candidate = locations[0].get("adresse")
            work_location = candidate if isinstance(candidate, dict) else work_location
        location = normalize_space(
            " ".join(
                str(work_location.get(key) or "")
                for key in ["ort", "region", "land"]
            )
        )
        tags = _string_list(item.get("beruf")) + _string_list(item.get("alleBerufe"))
        if item.get("hauptberuf"):
            tags.append(normalize_space(str(item["hauptberuf"])))
        employment = normalize_space(
            " ".join(
                _string_list(item.get("arbeitszeitmodell"))
                + _string_list(item.get("arbeitszeitmodelle"))
                + _string_list(item.get("befristung"))
                + (["Vollzeit"] if item.get("arbeitszeitVollzeit") else [])
            )
        )
        refnr = normalize_space(
            str(item.get("referenznummer") or item.get("refnr") or "")
        )
        external_url = normalize_space(
            str(item.get("externeURL") or item.get("externeUrl") or "")
        )
        if external_url and is_public_http_url(external_url):
            apply_url = external_url
        elif refnr:
            apply_url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{quote(refnr, safe='')}"
        else:
            apply_url = "https://www.arbeitsagentur.de/jobsuche/"
        if not is_public_http_url(apply_url):
            continue
        description = normalize_space(
            " ".join(
                [
                    title,
                    company,
                    location,
                    employment,
                    str(item.get("beruf") or ""),
                    str(item.get("branche") or ""),
                ]
            )
        )
        if item.get("homeofficemoeglich"):
            description = normalize_space(f"{description} Homeoffice möglich")
        route = infer_application_route(apply_url, source=source, source_type="public_job_board")
        listings.append(
            JobListing(
                source=source,
                source_url=source_url,
                title=title[:180],
                company=company[:180],
                location=location,
                remote_type=classify_remote_type(description),
                work_type=employment,
                language=detect_language(description),
                description=description[:4000],
                tags=tags + ["Arbeitsagentur"],
                date_posted=str(
                    item.get("datumErsteVeroeffentlichung")
                    or item.get("aktuelleVeroeffentlichungsdatum")
                    or ""
                ),
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=description[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class RemoteOkApiSource:
    name = "remoteok_api"

    def __init__(
        self,
        endpoint: str = "https://remoteok.com/api",
        include_terms: Iterable[str] | None = None,
    ):
        self.endpoint = endpoint
        self.include_terms = list(include_terms or [])

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        policy_error = validate_public_source_url(
            self.endpoint, allowed_hosts=REMOTEOK_HOSTS
        )
        if policy_error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=policy_error,
                    fetched_at=utc_now_iso(),
                ),
            )
        body, error = fetch_text(self.endpoint, allowed_hosts=REMOTEOK_HOSTS)
        if error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=error,
                    fetched_at=utc_now_iso(),
                ),
            )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="degraded",
                    message=f"Invalid JSON: {exc}",
                    fetched_at=utc_now_iso(),
                ),
            )
        listings = parse_remoteok_json(payload, self.endpoint, self.include_terms)[
            :max_candidates
        ]
        time.sleep(host_delay_seconds)
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status="available" if listings else "unavailable",
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="Remote OK API terms require source attribution and linking to the listing URL.",
                fetched_at=utc_now_iso(),
            ),
        )


def parse_remoteok_json(
    payload: Any, source_url: str, include_terms: Iterable[str] | None = None
) -> list[JobListing]:
    if not isinstance(payload, list):
        return []
    terms = list(include_terms or [])
    listings: list[JobListing] = []
    for item in payload:
        if not isinstance(item, dict) or "legal" in item:
            continue
        title = normalize_space(str(item.get("position") or ""))
        company = normalize_space(str(item.get("company") or "Remote OK"))
        apply_url = normalize_space(str(item.get("apply_url") or item.get("url") or ""))
        if not title or not is_public_http_url(apply_url):
            continue
        tags = _string_list(item.get("tags"))
        location = normalize_space(str(item.get("location") or "Remote"))
        description = _plain_html_text(item.get("description"))
        compensation = ""
        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        if salary_min or salary_max:
            compensation = normalize_space(f"{salary_min or ''} - {salary_max or ''}")
        text = normalize_space(
            " ".join([title, company, location, description, " ".join(tags)])
        )
        if terms and not _contains_any_term(text, terms):
            continue
        route = infer_application_route(apply_url, source="remoteok_api")
        listings.append(
            JobListing(
                source="remoteok_api",
                source_url=source_url,
                title=title[:180],
                company=company[:180],
                location=location,
                remote_type="remote",
                work_type="remote",
                language=detect_language(text),
                description=description[:4000],
                compensation=compensation,
                tags=tags + ["Remote OK"],
                date_posted=str(item.get("date") or ""),
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=text[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class RemotiveApiSource:
    name = "remotive_api"

    def __init__(
        self,
        endpoint: str = "https://remotive.com/api/remote-jobs",
        queries: Iterable[str] | None = None,
    ):
        self.endpoint = endpoint
        self.queries = list(queries or [])

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        policy_error = validate_public_source_url(
            self.endpoint, allowed_hosts=REMOTIVE_HOSTS
        )
        if policy_error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=policy_error,
                    fetched_at=utc_now_iso(),
                ),
            )
        queries = self.queries or [""]
        listings: list[JobListing] = []
        messages: list[str] = []
        with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
            for query in queries:
                if len(listings) >= max_candidates:
                    break
                try:
                    response = client.get(
                        self.endpoint,
                        params={"search": query} if query else None,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:  # pragma: no cover - network failures vary.
                    messages.append(f"{query or 'all'}: {type(exc).__name__}: {exc}")
                    continue
                if isinstance(payload, dict):
                    parsed = parse_remotive_json(
                        payload, self.endpoint, include_terms=self.queries
                    )
                    listings.extend(parsed[: max_candidates - len(listings)])
                time.sleep(host_delay_seconds)
        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message=(
                    "; ".join(messages)
                    or "Remotive API terms require source attribution and linking to the listing URL."
                )[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_remotive_json(
    payload: dict[str, Any],
    source_url: str,
    include_terms: Iterable[str] | None = None,
) -> list[JobListing]:
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    listings: list[JobListing] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        title = normalize_space(str(item.get("title") or ""))
        company = normalize_space(str(item.get("company_name") or "Remotive"))
        apply_url = normalize_space(str(item.get("url") or ""))
        if not title or not is_public_http_url(apply_url):
            continue
        tags = _string_list(item.get("tags"))
        location = normalize_space(
            str(item.get("candidate_required_location") or "Remote")
        )
        description = _plain_html_text(item.get("description"))
        text = normalize_space(
            " ".join(
                [
                    title,
                    company,
                    str(item.get("category") or ""),
                    location,
                    description,
                    " ".join(tags),
                ]
            )
        )
        if include_terms and not _contains_any_term(text, include_terms):
            continue
        route = infer_application_route(apply_url, source="remotive_api")
        listings.append(
            JobListing(
                source="remotive_api",
                source_url=source_url,
                title=title[:180],
                company=company[:180],
                location=location,
                remote_type="remote",
                work_type=str(item.get("job_type") or ""),
                language=detect_language(text),
                description=description[:4000],
                compensation=str(item.get("salary") or "")[:500],
                tags=tags
                + [normalize_space(str(item.get("category") or "")), "Remotive"],
                date_posted=str(item.get("publication_date") or ""),
                apply_url=apply_url,
                **route_listing_fields(route),
                raw_excerpt=text[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


class StructuredJobPageSource:
    name = "structured_job_page"

    def __init__(self, urls: Iterable[str]):
        self.urls = list(urls)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        listings: list[JobListing] = []
        for url in self.urls[:max_candidates]:
            if not is_public_http_url(url):
                continue
            body, error = fetch_text(url)
            if error:
                continue
            listings.extend(parse_jobposting_jsonld(body, url))
            time.sleep(host_delay_seconds)
        status = "available" if listings else "disabled"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(self.urls),
                candidates_returned=len(listings),
                fetched_at=utc_now_iso(),
            ),
        )


def parse_jobposting_jsonld(html: str, source_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[JobListing] = []
    scripts = soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)})
    for script in scripts:
        try:
            payload = json.loads(script.string or script.get_text() or "{}")
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if isinstance(node, dict) and node.get("@graph"):
                nodes.extend(node["@graph"])
                continue
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if isinstance(node_type, list):
                is_job = "JobPosting" in node_type
            else:
                is_job = node_type == "JobPosting"
            if not is_job:
                continue
            org = node.get("hiringOrganization") or {}
            location = node.get("jobLocation") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address", {}) if isinstance(location, dict) else {}
            description = BeautifulSoup(node.get("description", ""), "lxml").get_text(
                " "
            )
            title = normalize_space(node.get("title", ""))
            company = (
                normalize_space(org.get("name", "Unknown"))
                if isinstance(org, dict)
                else "Unknown"
            )
            location_text = normalize_space(
                " ".join(
                    str(address.get(key, ""))
                    for key in ["addressLocality", "addressRegion", "addressCountry"]
                )
            )
            if title:
                apply_url = str(node.get("url", source_url))
                route = infer_application_route(apply_url, source="structured_job_page")
                listings.append(
                    JobListing(
                        source="structured_job_page",
                        source_url=source_url,
                        title=title,
                        company=company,
                        location=location_text,
                        remote_type=str(node.get("jobLocationType", "")),
                        work_type=str(node.get("employmentType", "")),
                        language=detect_language(description or title),
                        description=normalize_space(description)[:4000],
                        compensation=str(node.get("baseSalary", ""))[:500],
                        tags=[],
                        date_posted=str(node.get("datePosted", "")),
                        apply_url=apply_url,
                        **route_listing_fields(route),
                        raw_excerpt=normalize_space(description or title)[:1000],
                        fetched_at=utc_now_iso(),
                    )
                )
    return listings


class PublicSearchSource:
    name = "public_search"

    def __init__(
        self,
        queries: Iterable[str] | None = None,
        location: str = "Germany",
        gl: str = "de",
        hl: str = "de",
        google_domain: str = "google.de",
        max_queries: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.queries = list(queries or [])
        self.location = location
        self.gl = gl
        self.hl = hl
        self.google_domain = google_domain
        self.max_queries = max_queries
        self.transport = transport

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if os.getenv("JOB_AGENT_DISABLE_SERPAPI", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="SerpAPI disabled by JOB_AGENT_DISABLE_SERPAPI.",
                    fetched_at=utc_now_iso(),
                ),
            )
        api_key = os.getenv("SERPAPI_API_KEY") or os.getenv("SERP_API_KEY")
        if not api_key:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="Set SERPAPI_API_KEY in .env to enable Google searches through SerpApi.",
                    fetched_at=utc_now_iso(),
                ),
            )
        if not self.queries:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="No public search queries configured.",
                ),
            )

        policy_error = validate_public_source_url(
            SERPAPI_ENDPOINT, allowed_hosts=SERPAPI_HOSTS
        )
        if policy_error:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="unavailable",
                    message=policy_error,
                    fetched_at=utc_now_iso(),
                ),
            )

        default_query_cap = str(self.max_queries if self.max_queries is not None else 3)
        env_max_queries = int(
            os.getenv("SERPAPI_MAX_QUERIES_PER_RUN", default_query_cap)
        )
        configured_max_queries = (
            self.max_queries if self.max_queries is not None else env_max_queries
        )
        max_queries = min(configured_max_queries, env_max_queries)
        listings: list[JobListing] = []
        messages: list[str] = []
        with httpx.Client(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
            transport=self.transport,
        ) as client:
            for query in self.queries[:max_queries]:
                if len(listings) >= max_candidates:
                    break
                try:
                    response = client.get(
                        SERPAPI_ENDPOINT,
                        params={
                            "engine": "google",
                            "q": query,
                            "api_key": api_key,
                            "google_domain": self.google_domain,
                            "gl": self.gl,
                            "hl": self.hl,
                            "location": self.location,
                            "num": min(10, max_candidates - len(listings)),
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (
                    Exception
                ) as exc:  # pragma: no cover - exact provider/network failures vary.
                    messages.append(f"{query}: {type(exc).__name__}: {exc}")
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    messages.append(f"{query}: {str(payload['error'])[:180]}")
                    continue
                if isinstance(payload, dict):
                    parsed = parse_serpapi_google_results(
                        payload, query, f"serpapi_google:{query}"
                    )
                    listings.extend(parsed[: max_candidates - len(listings)])
                time.sleep(host_delay_seconds)

        status = "available" if listings else "unavailable"
        return SourceResult(
            listings,
            SourceHealth(
                name=self.name,
                status=status,
                candidates_seen=len(listings),
                candidates_returned=len(listings),
                message="; ".join(messages)[:500],
                fetched_at=utc_now_iso(),
            ),
        )


def parse_serpapi_google_results(
    payload: dict[str, Any], query: str, source_url: str
) -> list[JobListing]:
    organic_results = payload.get("organic_results", [])
    if not isinstance(organic_results, list):
        return []
    listings: list[JobListing] = []
    seen_urls: set[str] = set()
    for item in organic_results:
        if not isinstance(item, dict):
            continue
        title = normalize_space(str(item.get("title") or ""))
        link = str(item.get("link") or "")
        snippet = normalize_space(str(item.get("snippet") or ""))
        if (
            not title
            or not link
            or link in seen_urls
            or not is_public_http_url(link)
            or looks_like_job_search_result_page(title, link, snippet)
        ):
            continue
        seen_urls.add(link)
        displayed_link = normalize_space(str(item.get("displayed_link") or ""))
        company = (
            urlparse(link).hostname or displayed_link or "Google result"
        ).removeprefix("www.")
        text = normalize_space(" ".join([title, company, snippet]))
        route = infer_application_route(
            link, source="serpapi_google", source_type="google_jobs"
        )
        listings.append(
            JobListing(
                source="serpapi_google",
                source_url=source_url,
                title=title[:180],
                company=company,
                language=detect_language(text),
                description=snippet[:4000],
                tags=["google_search", query],
                apply_url=link,
                **route_listing_fields(route),
                raw_excerpt=text[:1000],
                fetched_at=utc_now_iso(),
            )
        )
    return listings


def looks_like_job_search_result_page(title: str, link: str, snippet: str = "") -> bool:
    lower_title = title.lower()
    lower_snippet = snippet.lower()
    parsed = urlparse(link)
    host = (parsed.hostname or "").removeprefix("www.").lower()
    path = parsed.path.lower()
    combined = f"{lower_title} {lower_snippet} {path}"
    non_job_hosts = {
        "github.com",
        "de.wikipedia.org",
        "wikipedia.org",
        "reddit.com",
        "youtube.com",
        "medium.com",
    }
    if host in non_job_hosts or any(
        host.endswith(f".{item}") for item in non_job_hosts
    ):
        return True
    if re.search(
        r"/(blog|wiki|docs|documentation|ratgeber|news|magazin|learn|company)($|/|-)",
        path,
    ):
        return True
    if host.endswith("linkedin.com") and "/jobs/view/" not in path:
        return True
    if re.search(
        r"\b(jobs?\s*&\s*vacancies|job offers|jobangebote|stellenmarkt|karriereportal)\b",
        lower_title,
    ):
        return True
    if re.search(
        r"\bjobs?\s+in\b|\bstellenangebote\b|\bjob search\b|\bjobsuche\b", lower_title
    ):
        return True
    if re.search(r"^\d+\+?\s+.*\b(stellen|jobs)\b", lower_title):
        return True
    if re.search(r"\b(ai\s+)?automation jobs\b", lower_title):
        return True
    if re.search(
        r"\b(stellen|jobs)\s+(in|für|for|remote|home office|deutschland|germany|world)\b",
        lower_title,
    ):
        return True
    if re.search(r"/jobs-[^/]*($|/)|/jobs\+[^/]*($|/)", path):
        return True
    if host.endswith("indeed.com") or host.endswith("indeed.de"):
        return (
            path.startswith("/q-") or path in {"/jobs", "/jobs/"} or bool(parsed.query)
        )
    if host.endswith("linkedin.com"):
        return "/jobs/search" in path
    if host.endswith("stepstone.de"):
        return "/jobs/" in path and not re.search(r"/stellenangebote--", path)
    job_role_signal = re.search(
        r"\b(ai|ki|automation|automatisierung|developer|entwickler|engineer|specialist|spezialist|fullstack|full-stack|"
        r"software|devops|consultant|berater|llm|n8n|low[- ]code|no[- ]code)\b",
        combined,
    )
    job_context_signal = re.search(
        r"\b(job|jobs|stelle|stellenanzeige|hiring|career|karriere|remote|vollzeit|full[- ]time)\b",
        combined,
    )
    if not job_role_signal or not job_context_signal:
        return True
    return False


class GoogleJobsBrowserSource:
    name = "google_jobs_browser"

    def __init__(self, queries: Iterable[str]):
        self.queries = list(queries)

    def collect(
        self, max_candidates: int = 80, host_delay_seconds: float = 0.5
    ) -> SourceResult:
        if os.getenv("ENABLE_GOOGLE_JOBS_BROWSER", "false").lower() not in {
            "1",
            "true",
            "yes",
        }:
            return SourceResult(
                [],
                SourceHealth(
                    name=self.name,
                    status="disabled",
                    message="Google Jobs browser adapter disabled; v1 does not rely on it.",
                    fetched_at=utc_now_iso(),
                ),
            )
        return SourceResult(
            [],
            SourceHealth(
                name=self.name,
                status="unavailable",
                message="Browser adapter scaffolded but intentionally fail-closed until a manual browser QA pass approves selectors.",
                fetched_at=utc_now_iso(),
            ),
        )


def save_sanitized_cache(
    path: Path, listings: list[JobListing], health: list[SourceHealth]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": utc_now_iso(),
        "listings": [listing.model_dump(mode="json") for listing in listings],
        "source_health": [item.model_dump(mode="json") for item in health],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
