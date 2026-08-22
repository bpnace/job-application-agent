from __future__ import annotations

import re

from .addressee import company_base_name, extract_company_from_listing, is_portal_name
from .models import CandidateProfile, CompanyResearch, JobListing, JobScorecard


def display_role_title(title: str) -> str:
    cleaned = title.replace(":", "")
    cleaned = re.split(r"\s+[–—-]\s+", cleaned, maxsplit=1)[0]
    cleaned = re.split(
        r"\s+(?:Neu|In dieser Rolle|In dieser Position|In this role|Bei diesem Job)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    if "|" in cleaned:
        parts = [part.strip() for part in cleaned.split("|") if part.strip()]
        gender_part = next(
            (part for part in parts[1:] if re.fullmatch(r"\([^)]+\)", part)), ""
        )
        cleaned = " ".join(
            parts[:2]
            + ([gender_part] if gender_part and gender_part not in parts[:2] else [])
        )
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) <= 72:
        return cleaned
    words = cleaned.split()
    shortened: list[str] = []
    for word in words:
        candidate = " ".join([*shortened, word])
        if len(candidate) > 72:
            break
        shortened.append(word)
    return " ".join(shortened).strip() or cleaned[:72].strip()


def draft_cover_letter(
    profile: CandidateProfile,
    listing: JobListing,
    scorecard: JobScorecard,
    research: CompanyResearch | None = None,
) -> str:
    if listing.language == "de":
        return _draft_german(profile, listing, scorecard, research)
    return _draft_english(profile, listing, scorecard, research)


def _draft_german(
    profile: CandidateProfile,
    listing: JobListing,
    scorecard: JobScorecard,
    research: CompanyResearch | None = None,
) -> str:
    _ = scorecard
    company_name = _resolved_company_name(listing)
    team_name = research.contact_name if research and research.contact_name else _team_name(company_name)
    role = _clean_role(listing.title)
    display_title = display_role_title(listing.title)
    focus = _research_focus(research) or _german_focus(listing)
    evidence = _profile_evidence(profile, language="de")
    profile_summary = profile.summary.strip() or "Mein Profil verbindet relevante fachliche und praktische Erfahrung."
    links = _profile_links(profile, language="de")
    return f"""# Bewerbung als {display_title}

Hallo {team_name},

die Position als {role} passt gut zu meinem Profil. {profile_summary}

Ich arbeite gern dort, wo fachliche Anforderungen in nachvollziehbare Ergebnisse überführt werden. Dabei achte ich auf saubere Kommunikation, pragmatische Umsetzung und Ergebnisse, die für das Team tatsächlich nutzbar sind.

Bei {company_name} reizt mich besonders {focus}. Ich kann mich schnell in Produktlogik und bestehende Abläufe einarbeiten, technische Dinge pragmatisch bauen und sie so erklären, dass Fachbereiche und Entwicklung in dieselbe Richtung laufen.

{evidence}

{links}

Ich erzähle Ihnen gern persönlich mehr dazu, wie ich bei {company_name} schnell sinnvoll beitragen kann.

Viele Grüße
{profile.name}
"""


def _draft_english(
    profile: CandidateProfile,
    listing: JobListing,
    scorecard: JobScorecard,
    research: CompanyResearch | None = None,
) -> str:
    _ = scorecard
    company_name = _resolved_company_name(listing)
    role = _clean_role(listing.title)
    display_title = display_role_title(listing.title)
    focus = _english_focus(listing)
    addressee = research.contact_name if research and research.contact_name else f"{company_name} team"
    focus = _research_focus(research) or _english_focus(listing)
    evidence = _profile_evidence(profile, language="en")
    profile_summary = profile.summary.strip() or "My profile combines relevant subject-matter and practical experience."
    links = _profile_links(profile, language="en")
    return f"""# Application for {display_title}

Hello {addressee},

The {role} role fits my profile well. {profile_summary}

I enjoy turning requirements into clear, useful results. That means communicating well, implementing pragmatically and making sure the result is workable for the team.

What draws me to {company_name} is {focus}. I can get into product logic and existing processes quickly, build pragmatic technical solutions and explain them in a way that keeps business and engineering moving together.

{evidence}

{links}

I would be happy to talk and see where I can contribute fastest at {company_name}.

Best regards
{profile.name}
"""


def _profile_evidence(profile: CandidateProfile, *, language: str) -> str:
    points = [point.strip().rstrip(".") for point in profile.proof_points if point.strip()]
    skills = [skill.strip() for skill in profile.core_skills if skill.strip()]
    if language == "de":
        if points:
            return f"Aus meinem Profil bringe ich unter anderem {', '.join(points[:3])} mit."
        if skills:
            return f"Mein Profil belegt praktische Erfahrung mit {', '.join(skills[:5])}."
        return "Mein Lebenslauf enthält konkrete, für die Rolle relevante Arbeitsproben."
    if points:
        return f"A few concrete anchors from my profile are {', '.join(points[:3])}."
    if skills:
        return f"My profile documents practical experience with {', '.join(skills[:5])}."
    return "My resume contains concrete work samples relevant to the role."


def _profile_links(profile: CandidateProfile, *, language: str) -> str:
    links: list[str] = []
    if profile.github.strip():
        links.append(f"GitHub {profile.github.strip()}")
    if profile.linkedin.strip():
        links.append(f"LinkedIn {profile.linkedin.strip()}")
    if links:
        return "\n".join(links)
    return "" if language == "de" else ""


def _resolved_company_name(listing: JobListing) -> str:
    company = listing.company.strip()
    if company and not is_portal_name(company):
        return company_base_name(company)
    extracted = extract_company_from_listing(listing)
    if extracted:
        return company_base_name(extracted)
    if company:
        raise ValueError(
            "Cannot draft cover letter because listing company is a portal/job board "
            f"({company}) and no actual company/contact person could be resolved."
        )
    return "Ihrem Team"


def _team_name(company: str) -> str:
    if not company or company == "Ihrem Team":
        return "liebes Team"
    return f"liebes {company}-Team"


def _research_focus(research: CompanyResearch | None) -> str:
    if research is None or not research.facts:
        return ""
    excerpt = re.split(r"(?<=[.!?])\s+", research.facts[0].claim.strip(), maxsplit=1)[0]
    return excerpt[:240].rstrip(" ,;:")


def _clean_role(title: str) -> str:
    return display_role_title(title).replace("–", "-").strip()


def _combined_listing_text(listing: JobListing) -> str:
    return " ".join(
        [
            listing.title,
            listing.description,
            listing.raw_excerpt,
            " ".join(listing.tags),
        ]
    ).lower()


def _german_focus(listing: JobListing) -> str:
    text = _combined_listing_text(listing)
    if any(
        token in text for token in ["customer success", "implementation", "onboarding"]
    ):
        return (
            "die Mischung aus Kundennähe, technischer Umsetzung und sauberem Onboarding"
        )
    if any(token in text for token in ["ai", "agent", "automation", "llm", "ki"]):
        return "die Verbindung aus KI-gestützten Prozessen und praktischer Produktarbeit"
    if any(token in text for token in ["frontend", "react", "next.js", "web"]):
        return "die Nähe zu moderner Webentwicklung, Produktoberfläche und schneller Umsetzung"
    if any(token in text for token in ["ecommerce", "shop", "marketing", "crm"]):
        return "die Verbindung aus operativem Geschäft, digitalem Produkt und messbarer Umsetzung"
    return (
        "die Mischung aus praktischer Umsetzung, Produktdenken und technischen Abläufen"
    )


def _english_focus(listing: JobListing) -> str:
    text = _combined_listing_text(listing)
    if any(
        token in text for token in ["customer success", "implementation", "onboarding"]
    ):
        return "the mix of customer-facing work, technical implementation and structured onboarding"
    if any(token in text for token in ["ai", "agent", "automation", "llm"]):
        return "the connection between AI-assisted processes and practical product work"
    if any(token in text for token in ["frontend", "react", "next.js", "web"]):
        return "the proximity to modern web development, product interfaces and fast implementation"
    if any(token in text for token in ["ecommerce", "shop", "marketing", "crm"]):
        return "the connection between operations, digital product work and measurable execution"
    return (
        "the mix of practical implementation, product thinking and technical workflows"
    )
