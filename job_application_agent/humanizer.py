from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .addressee import (
    expected_addressee_names,
    portal_hits,
    salutation_line,
    salutation_matches_expected,
)
from .document_names import cover_letter_filename, cover_letter_filename_is_compact
from .humanizer_policy import HumanizerPolicy, load_private_policy
from .models import CandidateProfile, CoverLetterQuality

if TYPE_CHECKING:
    from .models import CompanyResearch, JobListing


def strip_markdown(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown, flags=re.MULTILINE)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


def _policy_for_profile(profile: CandidateProfile) -> HumanizerPolicy:
    path = Path(profile.humanizer_policy_path) if profile.humanizer_policy_path else None
    return load_private_policy(path)


def rewrite_cover_letter_for_humanizer(
    markdown: str, profile: CandidateProfile | None = None
) -> str:
    """Apply safe deterministic Humanizer repairs after an initial gate failure.

    This does not add claims or change application facts. It only removes style
    patterns the quality gate can identify mechanically.
    """
    policy = _policy_for_profile(profile) if profile is not None else HumanizerPolicy()
    repaired = markdown
    for term in policy.banned_terms:
        replacement = policy.replacements.get(term, "")
        repaired = re.sub(re.escape(term), replacement, repaired, flags=re.IGNORECASE)
    for source, replacement in policy.replacements.items():
        repaired = re.sub(re.escape(source), replacement, repaired, flags=re.IGNORECASE)
    for pattern in policy.banned_patterns:
        repaired = re.sub(pattern, "", repaired, flags=re.IGNORECASE)
    if policy.forbid_colons:
        repaired = _replace_colons_outside_urls(repaired)
    repaired = re.sub(r"[ \t]+\n", "\n", repaired)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    repaired = re.sub(r" {2,}", " ", repaired)
    return repaired.strip() + "\n"


def _replace_colons_outside_urls(markdown: str) -> str:
    parts = re.split(r"(https?://\S+)", markdown)
    repaired: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            repaired.append(part)
            continue
        segment = re.sub(r"\b(GitHub|LinkedIn):\s*", r"\1 ", part)
        segment = re.sub(r":\s+", ". ", segment)
        segment = segment.replace(":", ".")
        repaired.append(segment)
    return "".join(repaired)


def count_pdf_pages(pdf_path: Path) -> int | None:
    if not pdf_path.exists():
        return None
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            match = re.search(r"^Pages:\s+(\d+)", result.stdout, flags=re.MULTILINE)
            if match:
                return int(match.group(1))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    raw = pdf_path.read_bytes()
    if b"/Type /Page" in raw:
        return max(1, raw.count(b"/Type /Page") - raw.count(b"/Type /Pages"))
    return None


def check_cover_letter_quality(
    markdown: str,
    profile: CandidateProfile,
    pdf_path: Path | None = None,
    listing: "JobListing | None" = None,
    research: "CompanyResearch | None" = None,
    artifact_filename: str | Path | None = None,
) -> CoverLetterQuality:
    plain = strip_markdown(markdown)
    url_stripped = re.sub(r"https?://\S+", "", plain)
    lower = plain.lower()
    words = re.findall(r"\b[\wäöüÄÖÜß'-]+\b", plain)
    paragraphs = [item.strip() for item in markdown.split("\n\n") if item.strip()]
    issues: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}
    artifact_name = Path(artifact_filename).name if artifact_filename else ""
    recommended_filename = ""

    policy = _policy_for_profile(profile)
    # Manually constructed profiles remain usable in unit-level integrations;
    # real profiles only receive this excerpt after a private policy parsed.
    checks["humanizer_loaded"] = policy.loaded or bool(profile.humanizer_excerpt.strip())
    if not checks["humanizer_loaded"]:
        issues.append("Humanizer source is missing.")

    banned_hits = [term for term in policy.banned_terms if term.lower() in lower]
    pattern_hits = [pattern for pattern in policy.banned_patterns if re.search(pattern, plain, re.I)]
    checks["no_banned_humanizer_terms"] = not banned_hits and not pattern_hits
    if banned_hits or pattern_hits:
        issues.append(
            "Private Humanizer policy matched: " + ", ".join(banned_hits + pattern_hits)
        )

    checks["no_colon_prose"] = not policy.forbid_colons or ":" not in url_stripped
    if not checks["no_colon_prose"]:
        issues.append(
            "Private Humanizer policy disallows colon transitions in final professional copy."
        )

    checks["word_count_one_page_range"] = 120 <= len(words) <= 330
    if not checks["word_count_one_page_range"]:
        issues.append(
            f"Word count {len(words)} is outside the one-page target range 120-330."
        )

    checks["paragraph_count_compact"] = 5 <= len(paragraphs) <= 9
    if not checks["paragraph_count_compact"]:
        issues.append(
            f"Paragraph count {len(paragraphs)} is outside compact letter range 5-9."
        )

    external_urls = re.findall(r"https?://\S+", markdown)
    allowed_urls = {profile.github.rstrip("/"), profile.linkedin.rstrip("/")}
    disallowed_urls = [
        url
        for url in external_urls
        if url.rstrip(").,").rstrip("/") not in allowed_urls
    ]
    checks["only_allowed_links"] = not disallowed_urls
    if disallowed_urls:
        issues.append(
            "Only GitHub and LinkedIn links are allowed: " + ", ".join(disallowed_urls)
        )

    checks["required_links_present"] = (
        profile.github in markdown and profile.linkedin in markdown
    )
    if not checks["required_links_present"]:
        issues.append("GitHub and LinkedIn links must be present.")

    cv_terms = [
        term.strip().rstrip(".,;:!?")
        for term in [*profile.core_skills, *profile.proof_points]
        if len(term.strip().rstrip(".,;:!?")) >= 3
    ]
    checks["has_specific_cv_facts"] = bool(re.search(r"\d", plain)) or any(
        re.search(rf"\b{re.escape(term)}\b", plain, re.I) for term in cv_terms
    )
    if not checks["has_specific_cv_facts"]:
        issues.append("Letter lacks concrete CV facts.")

    salutation = salutation_line(markdown)
    checks["has_salutation"] = bool(salutation)
    if not checks["has_salutation"]:
        issues.append("Cover letter has no clear salutation line.")

    portal_addressee_hits = portal_hits(salutation)
    checks["no_portal_addressee"] = not portal_addressee_hits
    if portal_addressee_hits:
        issues.append(
            "Cover letter addresses a portal/job board instead of the company or contact person: "
            + ", ".join(portal_addressee_hits)
        )

    portal_company_hits = portal_hits(url_stripped)
    checks["no_portal_named_as_company"] = not portal_company_hits
    if portal_company_hits:
        issues.append(
            "Cover letter names a portal/job board as the company: "
            + ", ".join(portal_company_hits)
        )

    if listing is not None:
        expected_names = expected_addressee_names(listing)
        if research is not None and research.contact_name:
            expected_names = [research.contact_name, *expected_names]
        checks["correct_addressee_or_company"] = bool(
            expected_names
        ) and salutation_matches_expected(
            salutation,
            expected_names,
        )
        if not checks["correct_addressee_or_company"]:
            expected = (
                ", ".join(expected_names)
                if expected_names
                else "a real company/contact from the job posting"
            )
            issues.append(
                "Cover letter salutation must name the actual company or contact person from the job posting. "
                f"Expected: {expected}. Salutation: {salutation or 'missing'}."
            )
        if research is not None:
            fact_snippets = [fact.claim[:40].casefold() for fact in research.facts]
            checks["research_fact_used"] = bool(fact_snippets) and any(
                snippet and snippet in lower for snippet in fact_snippets
            )
            if not checks["research_fact_used"]:
                issues.append("Cover letter must use a source-backed company research fact.")
        recommended_filename = cover_letter_filename(profile, listing)
        filename_to_check = artifact_name or recommended_filename
        checks["compact_cover_letter_filename"] = cover_letter_filename_is_compact(
            filename_to_check,
            profile,
            listing,
        )
        if not checks["compact_cover_letter_filename"]:
            issues.append(
                "Cover-letter filename must use only candidate name and compact role description. "
                f"Expected: {recommended_filename}. Actual: {filename_to_check}."
            )
    else:
        checks["correct_addressee_or_company"] = not portal_addressee_hits

    page_count = count_pdf_pages(pdf_path) if pdf_path else None
    if page_count is not None:
        checks["one_page_pdf"] = page_count == 1
        if page_count != 1:
            issues.append(
                f"Cover-letter PDF has {page_count} pages, expected exactly 1."
            )
    else:
        checks["one_page_pdf"] = False
        warnings.append("PDF page count could not be verified.")

    return CoverLetterQuality(
        passed=not issues,
        humanizer_loaded=checks["humanizer_loaded"],
        word_count=len(words),
        paragraph_count=len(paragraphs),
        page_count=page_count,
        artifact_filename=artifact_name,
        recommended_filename=recommended_filename,
        checks=checks,
        issues=issues,
        warnings=warnings,
    )


def format_cover_letter_quality(quality: CoverLetterQuality) -> str:
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] {name}"
        for name, passed in quality.checks.items()
    )
    issues = "\n".join(f"- {item}" for item in quality.issues) or "- None"
    warnings = "\n".join(f"- {item}" for item in quality.warnings) or "- None"
    page_count = quality.page_count if quality.page_count is not None else "unknown"
    return f"""# Cover Letter Quality Gate

Passed: {quality.passed}
Humanizer loaded: {quality.humanizer_loaded}
Word count: {quality.word_count}
Paragraph count: {quality.paragraph_count}
PDF pages: {page_count}
Artifact filename: {quality.artifact_filename or "not provided"}
Recommended filename: {quality.recommended_filename or "not checked"}

## Checks
{checks}

## Issues
{issues}

## Warnings
{warnings}
"""
