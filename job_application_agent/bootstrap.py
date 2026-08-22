from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path
from typing import Any, Callable

from urllib.parse import quote_plus

import yaml

from .config import (
    ROOT,
    default_agent_home,
    default_profile_path,
    default_search_profile_path,
    default_tracker_path,
    load_config,
)
from .profile import candidate_document_paths, load_candidate_profile
from .resume import render_resume
from .humanizer_policy import load_private_policy, public_baseline_status


def initialize_local_state(
    *,
    agent_home: Path | None = None,
    interactive: bool = False,
    overwrite: bool = False,
    input_fn: Callable[[str], str] = input,
) -> dict[str, str | bool]:
    """Create ignored state and optionally collect a private first-run profile."""
    home = (agent_home or default_agent_home()).expanduser().resolve()
    for directory in [
        home,
        home / "approvals",
        home / "data",
        home / "documents",
        home / "humanizer",
        home / "humanizer" / "public",
        home / "output",
        home / "runs",
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    profile_path = home / "candidate.yaml"
    search_profile_path = home / "search_profile.yaml"
    profile_existed = profile_path.exists()
    search_profile_existed = search_profile_path.exists()
    profile_created = False
    search_profile_created = False
    if not profile_existed:
        example = ROOT / "config" / "candidate.example.yaml"
        profile_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        profile_created = True
    if not search_profile_existed:
        example = ROOT / "config" / "search_profile.yaml"
        search_profile_path.write_text(
            example.read_text(encoding="utf-8"), encoding="utf-8"
        )
        search_profile_created = True
    policy_path = home / "humanizer" / "private.de.md"
    if not policy_path.exists():
        policy_path.write_text(
            "---\nversion: 1\nlanguage: de\nbanned_terms: []\nbanned_patterns: []\nreplacements: {}\n---\n\n# Private deutsche Humanizer-Policy\n\nLokale Regeln und Stilhinweise hier ergänzen. Diese Datei wird absichtlich nicht von Git erfasst.\n",
            encoding="utf-8",
        )
    interactive_status = "not_requested"
    generated_cv_path = ""
    if interactive:
        if (profile_existed or search_profile_existed) and not overwrite:
            interactive_status = "skipped_existing_local_profile"
        else:
            generated_cv_path = _write_interactive_setup(home, input_fn=input_fn)
            interactive_status = "completed"
    return {
        "agent_home": str(home),
        "profile_path": str(profile_path),
        "profile_created": profile_created,
        "search_profile_path": str(search_profile_path),
        "search_profile_created": search_profile_created,
        "interactive_setup": interactive_status,
        "generated_cv_path": generated_cv_path,
        "tracker_path": str(home / "data" / "applications.jsonl"),
    }


def _write_interactive_setup(home: Path, *, input_fn: Callable[[str], str]) -> str:
    """Collect candidate data locally and derive all public portal queries from it."""
    name = _ask_required(input_fn, "Vollständiger Name")
    email = _ask_required(input_fn, "E-Mail-Adresse")
    phone = _ask(input_fn, "Telefonnummer (optional)")
    country = _ask(input_fn, "Zielland", default="Deutschland")
    city = _ask(input_fn, "Wohnort oder bevorzugte Stadt (optional)")
    location = ", ".join(part for part in [city, country] if part)
    target_roles = _ask_list(input_fn, "Gewünschte Positionen/Titel (kommagetrennt)", required=True)
    keywords = _ask_list(
        input_fn,
        "Zusätzliche Suchbegriffe (kommagetrennt, Enter übernimmt Titel)",
        default=target_roles,
    )
    exclusions = _ask_list(
        input_fn,
        "Sperrwörter für Ergebnisse (kommagetrennt)",
        default=["Praktikum", "Werkstudent", "Internship"],
    )
    employer_blacklist = _ask_list(
        input_fn, "Ausgeschlossene Arbeitgeber (kommagetrennt, optional)"
    )
    remote_allowed = _ask(input_fn, "Remote oder hybrid zulassen? (ja/nein)", default="ja")
    summary = _ask_required(
        input_fn, "Kurze sachliche Profilzusammenfassung für Anschreiben"
    )
    core_skills = _ask_list(input_fn, "Kernkompetenzen (kommagetrennt)")
    proof_points = _ask_list(
        input_fn, "Belegte Erfolge/Arbeitsproben (durch Semikolon getrennt)", separator=";"
    )
    github = _ask(input_fn, "GitHub-URL (optional)")
    linkedin = _ask(input_fn, "LinkedIn-URL (optional)")
    existing_cv = _is_yes(
        _ask(input_fn, "Bestehenden Lebenslauf als PDF verwenden? (ja/nein)", default="nein")
    )
    if existing_cv:
        cv_text_path = _ask(
            input_fn,
            "Pfad zum CV-Text (für Anschreiben, relativ zu .job-agent oder absolut)",
            default="documents/cv.txt",
        )
        cv_pdf_path = _ask(
            input_fn,
            "Pfad zum CV-PDF (für Uploads, relativ zu .job-agent oder absolut)",
            default="documents/cv.pdf",
        )
        resume = _empty_resume()
    else:
        cv_text_path = "documents/cv.txt"
        cv_pdf_path = "documents/cv.pdf"
        resume = _ask_basic_resume(
            input_fn,
            summary=summary,
            core_skills=core_skills,
        )

    profile = {
        "profile": {
            "name": name,
            "email": email,
            "location": location,
            "phone": phone,
            "address": "",
            "street_address": "",
            "postal_code": "",
            "city": city,
            "country": country,
            "github": github,
            "linkedin": linkedin,
            "summary": summary,
            "core_skills": core_skills,
            "proof_points": proof_points,
            # Sensitive answers such as salary, work authorisation and EEO are
            # intentionally not requested here.  They remain manual by default.
            "standard_application_answers": {},
        },
        "documents": {"cv_text_path": cv_text_path, "cv_pdf_path": cv_pdf_path},
        "humanizer": {"private_policy_path": "humanizer/private.de.md"},
        "resume": resume,
    }
    (home / "candidate.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    template = yaml.safe_load(
        (ROOT / "config" / "search_profile.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(template, dict)
    template["search"] = _search_settings(
        country=country,
        city=city,
        target_roles=target_roles,
        keywords=keywords,
        exclusions=exclusions,
        employer_blacklist=employer_blacklist,
        remote_allowed=_is_yes(remote_allowed),
    )
    template["sources"] = _sources_for_search(
        country=country,
        city=city,
        target_roles=target_roles,
        keywords=keywords,
    )
    (home / "search_profile.yaml").write_text(
        yaml.safe_dump(template, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if existing_cv:
        return ""
    generated = render_resume(candidate_path=home / "candidate.yaml")
    return str(generated.pdf_path)


def _empty_resume() -> dict[str, object]:
    return {
        "accent_color": "#7A3E38",
        "headline": "",
        "summary": "",
        "experience": [],
        "education": [],
        "skill_groups": [],
        "languages": [],
        "certificates": [],
        "attachments": [],
    }


def _ask_basic_resume(
    input_fn: Callable[[str], str],
    *,
    summary: str,
    core_skills: list[str],
) -> dict[str, object]:
    """Collect factual CV data only when no prior CV is available locally."""
    resume = _empty_resume()
    resume["headline"] = _ask(input_fn, "Berufsbezeichnung im Lebenslauf", default=summary)
    resume["summary"] = _ask(
        input_fn,
        "Kurzprofil im Lebenslauf (Enter übernimmt die Profilzusammenfassung)",
        default=summary,
    )
    resume["experience"] = _ask_experience(input_fn)
    resume["education"] = _ask_education(input_fn)
    if core_skills:
        resume["skill_groups"] = [{"label": "Kompetenzen", "items": core_skills}]
    resume["languages"] = _ask_languages(input_fn)
    resume["certificates"] = _ask_certificates(input_fn)
    return resume


def _ask_experience(input_fn: Callable[[str], str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    while _is_yes(_ask(input_fn, "Berufserfahrung hinzufügen? (ja/nein)", default="nein")):
        entries.append(
            {
                "role": _ask_required(input_fn, "  Rolle"),
                "employer": _ask_required(input_fn, "  Arbeitgeber"),
                "period": _ask_required(input_fn, "  Zeitraum, z. B. 2023 - heute"),
                "location": _ask(input_fn, "  Ort (optional)"),
                "highlights": _ask_list(
                    input_fn,
                    "  Belegte Beiträge oder Erfolge (durch Semikolon getrennt, optional)",
                    separator=";",
                ),
            }
        )
    return entries


def _ask_education(input_fn: Callable[[str], str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    while _is_yes(_ask(input_fn, "Ausbildung oder Studium hinzufügen? (ja/nein)", default="nein")):
        entries.append(
            {
                "degree": _ask_required(input_fn, "  Abschluss oder Ausbildung"),
                "institution": _ask_required(input_fn, "  Institution"),
                "period": _ask_required(input_fn, "  Zeitraum"),
                "location": _ask(input_fn, "  Ort (optional)"),
                "details": _ask_list(
                    input_fn,
                    "  Relevante Details (durch Semikolon getrennt, optional)",
                    separator=";",
                ),
            }
        )
    return entries


def _ask_languages(input_fn: Callable[[str], str]) -> list[dict[str, str]]:
    values = _ask_list(
        input_fn,
        "Sprachen mit Niveau, z. B. Deutsch: Muttersprache; Englisch: C1",
        separator=";",
    )
    languages: list[dict[str, str]] = []
    for value in values:
        language, separator, level = value.partition(":")
        if language.strip() and separator and level.strip():
            languages.append({"language": language.strip(), "level": level.strip()})
    return languages


def _ask_certificates(input_fn: Callable[[str], str]) -> list[dict[str, str]]:
    values = _ask_list(
        input_fn,
        "Zertifikate, z. B. Name | Anbieter | Jahr (durch Semikolon getrennt, optional)",
        separator=";",
    )
    certificates: list[dict[str, str]] = []
    for value in values:
        parts = [part.strip() for part in value.split("|", maxsplit=2)]
        parts.extend([""] * (3 - len(parts)))
        name, issuer, issued = parts[:3]
        if name:
            certificates.append({"name": name, "issuer": issuer, "issued": issued})
    return certificates


def _ask(input_fn: Callable[[str], str], label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input_fn(f"{label}{suffix}: ").strip()
    return value or default


def _ask_required(input_fn: Callable[[str], str], label: str) -> str:
    while True:
        value = _ask(input_fn, label)
        if value:
            return value
        print("Dieser Wert ist für ein personalisiertes Anschreiben erforderlich.")


def _ask_list(
    input_fn: Callable[[str], str],
    label: str,
    *,
    default: list[str] | None = None,
    required: bool = False,
    separator: str = ",",
) -> list[str]:
    default_text = separator.join(default or [])
    while True:
        value = _ask(input_fn, label, default=default_text)
        values = [item.strip() for item in value.split(separator) if item.strip()]
        if values or not required:
            return values
        print("Mindestens ein Eintrag ist erforderlich.")


def _is_yes(value: str) -> bool:
    return value.casefold() not in {"nein", "n", "no", "false", "0"}


def _slug(value: str) -> str:
    text = value.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-") or "deutschland"


def _search_settings(
    *,
    country: str,
    city: str,
    target_roles: list[str],
    keywords: list[str],
    exclusions: list[str],
    employer_blacklist: list[str],
    remote_allowed: bool,
) -> dict[str, Any]:
    preferred_locations = [item for item in [city, country] if item]
    required_location_terms = [item for item in [city, country] if item]
    if remote_allowed:
        preferred_locations.extend(["remote", "hybrid"])
        required_location_terms.extend(["remote", "hybrid", "homeoffice"])
    return {
        "market": country,
        "profile_configured": True,
        "target_roles": target_roles,
        "keywords": keywords,
        "hard_exclusions": exclusions,
        "employer_blacklist": employer_blacklist,
        "preferred_locations": preferred_locations,
        "required_location_terms": required_location_terms,
        "allow_unknown_location": True,
        "max_listing_age_days": 21,
        "fresh_listing_boost_days": 7,
        "allow_unknown_date": True,
        "top_n": 20,
        "max_candidates": 250,
        "host_delay_seconds": 0.5,
    }


def _sources_for_search(
    *, country: str, city: str, target_roles: list[str], keywords: list[str]
) -> dict[str, Any]:
    location = ", ".join(part for part in [city, country] if part) or country
    country_slug = "deutschland" if country.casefold() in {"deutschland", "germany"} else _slug(country)
    portal_roles = target_roles[:8]
    public_queries = [f"{role} {location}" for role in portal_roles]
    public_queries.extend(
        f"site:{portal} {role} {location}"
        for role in portal_roles[:3]
        for portal in ["jobs.personio.de", "boards.greenhouse.io", "jobs.ashbyhq.com"]
    )
    encoded_location = quote_plus(location)
    return {
        "public_search": {
            "enabled": True,
            "provider": "serpapi_google",
            "mode": "always",
            "booster_threshold": 20,
            "max_candidates": 80,
            "max_queries": min(16, len(public_queries)),
            "location": country,
            "gl": "de" if country.casefold() in {"deutschland", "germany"} else "",
            "hl": "de",
            "google_domain": "google.de",
            "queries": public_queries,
        },
        "arbeitnow": {
            "enabled": True,
            "endpoint": "https://www.arbeitnow.com/api/job-board-api",
            "max_candidates": 50,
        },
        "arbeitsagentur": {
            "enabled": True,
            "max_candidates": 80,
            "radius_km": 50,
            "page_size": 25,
            "max_pages": 2,
            "locations": [city or country],
            "queries": portal_roles,
        },
        "stepstone": {
            "enabled": True,
            "max_candidates": 70,
            "urls": [
                f"https://www.stepstone.de/jobs/{_slug(role)}/in-{country_slug}"
                for role in portal_roles
            ],
        },
        "linkedin": {
            "enabled": True,
            "max_candidates": 70,
            "urls": [
                "https://www.linkedin.com/jobs/search/?keywords="
                f"{quote_plus(role)}&location={encoded_location}"
                for role in portal_roles
            ],
        },
        "freelancermap": {
            "enabled": True,
            "max_candidates": 25,
            "urls": ["https://www.freelancermap.com/projects/remote"],
        },
        "remoteok": {
            "enabled": True,
            "endpoint": "https://remoteok.com/api",
            "max_candidates": 30,
            "include_terms": list(dict.fromkeys([*target_roles, *keywords]))[:20],
        },
        "remotive": {
            "enabled": True,
            "endpoint": "https://remotive.com/api/remote-jobs",
            "max_candidates": 30,
            "queries": portal_roles,
        },
        "public_job_boards": {
            "enabled": True,
            "max_candidates": 80,
            "per_board_limit": 10,
            "boards": [
                {"name": "get-in-it", "url": "https://www.get-in-it.de/jobsuche"},
                {"name": "DEVjobs.de", "url": "https://devjobs.de/jobs"},
                {"name": "Heise Jobs", "url": "https://jobs.heise.de/"},
                {"name": "Golem Jobs", "url": "https://jobs.golem.de/"},
                {"name": "Workwise", "url": "https://www.workwise.io/jobsuche"},
                {"name": "GermanTechJobs", "url": "https://germantechjobs.de/"},
            ],
        },
        "scrapling_public_job_boards": {"enabled": False, "boards": []},
    }


def _check_browser() -> tuple[bool, str]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return False, "Playwright Python package is not installed. Run `uv sync --frozen --all-groups`."

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("about:blank")
            browser.close()
    except Error as exc:
        return False, (
            "Chromium is unavailable or incompatible. Run `uv run playwright install chromium`. "
            f"Details: {str(exc).splitlines()[0][:180]}"
        )
    except Exception as exc:
        return False, f"Playwright browser check failed: {str(exc).splitlines()[0][:180]}"
    return True, "Chromium launch succeeded."


def doctor_report() -> dict[str, Any]:
    """Return readiness checks without echoing profile values or environment secrets."""
    checks: dict[str, dict[str, Any]] = {}
    profile_path = default_profile_path()
    checks["profile_config"] = {
        "ok": profile_path.is_file(),
        "path": str(profile_path),
        "message": "Candidate configuration exists." if profile_path.is_file() else "Run `job-agent init` to create candidate.yaml.",
    }
    if profile_path.is_file():
        try:
            profile = load_candidate_profile(profile_path)
            paths = candidate_document_paths(profile_path)
            checks["profile_schema"] = {
                "ok": True,
                "message": "Candidate configuration parsed without revealing its values.",
            }
            checks["documents"] = {
                "ok": bool(profile.cv_excerpt) and bool(profile.humanizer_excerpt) and paths["cv_pdf"].is_file(),
                "cv_text": paths["cv_text"].is_file(),
                "cv_pdf": paths["cv_pdf"].is_file(),
                "humanizer": paths["humanizer"].is_file() and bool(profile.humanizer_excerpt),
                "message": "Document paths checked; document contents are not printed.",
            }
            private_policy = load_private_policy(paths["humanizer"])
            checks["humanizer"] = {
                "ok": private_policy.loaded,
                "private_status": "ready" if private_policy.loaded else "missing",
                "private_source_id": private_policy.source_id,
                "private_sha256": private_policy.sha256,
                "public_baseline": public_baseline_status(),
                "message": "Only Humanizer load metadata is reported; policy contents are never printed.",
            }
        except (OSError, ValueError) as exc:
            checks["profile_schema"] = {
                "ok": False,
                "message": f"Candidate configuration is invalid: {str(exc).splitlines()[0][:180]}",
            }
    else:
        checks["profile_schema"] = {"ok": False, "message": "Profile configuration is missing."}
        checks["documents"] = {"ok": False, "message": "Profile configuration is missing."}
        checks["humanizer"] = {
            "ok": False,
            "private_status": "not_configured",
            "public_baseline": public_baseline_status(),
            "message": "Profile configuration is missing.",
        }

    search_profile_path = default_search_profile_path()
    if not search_profile_path.is_file():
        checks["search_profile"] = {
            "ok": False,
            "path": str(search_profile_path),
            "message": "Search profile is missing. Run `job-agent init --interactive`.",
        }
    else:
        try:
            search_config = load_config(search_profile_path)
            configured = bool(
                search_config.search.profile_configured
                and search_config.search.target_roles
            )
            checks["search_profile"] = {
                "ok": configured,
                "path": str(search_profile_path),
                "message": (
                    "Local search profile is configured without printing role or exclusion values."
                    if configured
                    else "Complete `job-agent init --interactive` before a personalised live search."
                ),
            }
        except (OSError, ValueError, yaml.YAMLError) as exc:
            checks["search_profile"] = {
                "ok": False,
                "path": str(search_profile_path),
                "message": f"Search profile is invalid: {str(exc).splitlines()[0][:180]}",
            }

    browser_ok, browser_message = _check_browser()
    checks["browser"] = {
        "ok": browser_ok,
        "playwright_version": _playwright_version(),
        "message": browser_message,
    }
    checks["state"] = {
        "ok": True,
        "agent_home": str(default_agent_home()),
        "tracker_path": str(default_tracker_path()),
        "search_profile_path": str(default_search_profile_path()),
        "message": "Runtime state is local and ignored by Git.",
    }
    return {
        "ready": all(bool(check.get("ok")) for check in checks.values()),
        "checks": checks,
    }


def _playwright_version() -> str:
    try:
        return importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"
