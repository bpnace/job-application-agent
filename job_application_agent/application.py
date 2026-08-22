from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import (
    ApplicationFormField,
    ApplicationRoute,
    CandidateProfile,
    FormFillInstruction,
    FormFillPlan,
    JobListing,
    utc_now_iso,
)
from .profile import configured_cv_pdf_path
from .utils import normalize_space


ATS_PLATFORMS = [
    ("greenhouse", ["greenhouse.io", "job-boards.eu.greenhouse.io"]),
    ("ashby", ["ashbyhq.com"]),
    ("lever", ["lever.co"]),
    ("personio", ["jobs.personio.de", "personio.com"]),
    ("workday", ["myworkdayjobs.com", "workdayjobs.com"]),
    ("workable", ["workable.com"]),
    ("smartrecruiters", ["smartrecruiters.com"]),
    ("teamtailor", ["teamtailor.com"]),
    ("recruitee", ["recruitee.com"]),
    ("join", ["join.com"]),
    ("softgarden", ["softgarden.io", "softgarden.de"]),
    ("bamboohr", ["bamboohr.com"]),
]

JOB_BOARD_PLATFORMS = [
    ("arbeitnow", ["arbeitnow.com"]),
    ("stepstone", ["stepstone.de"]),
    ("xing", ["xing.com"]),
    ("indeed", ["indeed.com", "indeed.de"]),
    ("heise", ["heise.de"]),
    ("it-jobs", ["it-jobs.de"]),
    ("jobware", ["jobware.de"]),
    ("monster", ["monster.de"]),
    ("kimeta", ["kimeta.de"]),
    ("golem", ["golem.de"]),
    ("arbeitsagentur", ["arbeitsagentur.de"]),
    ("remoteok", ["remoteok.com"]),
    ("remotive", ["remotive.com"]),
    ("freelance.de", ["freelance.de"]),
    ("freelancermap", ["freelancermap.de", "freelancermap.com"]),
    ("devjobs", ["devjobs.de"]),
    ("berlinstartupjobs", ["berlinstartupjobs.com"]),
    ("jobvector", ["jobvector.de"]),
    ("get-in-it", ["get-in-it.de"]),
    ("malt", ["malt.de"]),
    ("truffls", ["truffls.de"]),
    ("euremotejobs", ["euremotejobs.com"]),
    ("germantechjobs", ["germantechjobs.de"]),
    ("instaffo", ["instaffo.com"]),
    ("thelocal", ["thelocal.de"]),
    ("workwise", ["workwise.io"]),
]

JOIN_LIKE_PLATFORMS = {"join"}
JOIN_LIKE_DOMAINS = ("join.com",)
LISTED_SALARY_INCREMENT_EUR = 1000
NON_EUR_SALARY_MARKERS = {
    "$",
    "usd",
    "dollar",
    "gbp",
    "pound",
    "£",
    "chf",
    "aud",
    "cad",
}
NON_ANNUAL_SALARY_MARKERS = {
    "monthly",
    "per month",
    "/month",
    "month",
    "monat",
    "hour",
    "hourly",
    "/hour",
    "stunde",
    "daily",
    "per day",
    "/day",
    "tagessatz",
}
SALARY_RANGE_PATTERN = re.compile(
    r"""
    (?:€\s*)?
    (?P<lower>\d{2,6}(?:[.,]\d{3})*|\d{2,3}(?:[.,]\d)?)
    \s*(?P<lower_k>k)?
    \s*(?:-|–|—|to|bis)\s*
    (?:€\s*)?
    (?P<upper>\d{2,6}(?:[.,]\d{3})*|\d{2,3}(?:[.,]\d)?)
    \s*(?P<upper_k>k)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

OPTION_ALIASES = {
    "google for jobs": [
        "google jobs",
        "google",
        "public search",
        "google jobs / public job search",
        "job board",
        "job board e g indeed glassdoor",
    ],
    "linkedin": ["linkedin jobs", "linked in"],
    "stepstone": ["step stone"],
    "arbeitnow": ["arbeitnow jobs"],
    "freelancermap": ["freelancer map"],
    "fritz! karriereseite": [
        "fritz karriereseite",
        "avm karriereseite",
        "karriereseite",
    ],
    "deutschland": ["germany", "bundesrepublik deutschland", "de"],
    "yes": ["ja", "true", "i agree", "agree", "accepted"],
    "no": ["nein", "false"],
    "prefer not to say": [
        "keine angabe",
        "prefer not to answer",
        "i don't wish to answer",
        "decline to answer",
        "not specified",
    ],
    "native fluent": [
        "native fluent",
        "fluent native",
        "c2 fluent native",
        "c2",
        "native",
        "fluent",
    ],
    "5+ years": [
        "5 years",
        "5-6 years",
        "5 6 years",
        "5–6 years",
        "6-7 years",
        "6 7 years",
        "more than 5 years",
        "mehr als 5 jahre",
    ],
    "yes, authorized to work in germany; no visa required": [
        "yes",
        "ja",
        "authorized to work",
        "work authorization",
        "no visa required",
        "citizenship",
        "eu passport",
    ],
    "no visa required.": ["not applicable", "n/a", "none"],
    "not applicable.": ["not applicable", "n/a", "none"],
}


def host_matches(hostname: str, domains: list[str]) -> bool:
    return any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    )


def infer_application_route(
    raw_url: str, source: str = "", source_type: str = "", apply_option_title: str = ""
) -> ApplicationRoute:
    apply_url = normalize_space(raw_url)
    if not apply_url:
        return ApplicationRoute(method="unknown", note="No apply URL available.")

    if apply_url.startswith("mailto:"):
        return ApplicationRoute(
            method="email",
            apply_url=apply_url,
            resume_upload="not_applicable",
            platform="email",
            can_agent_fill=False,
            note="Email application route. Agent can draft the email. Sending requires an explicit send command.",
        )

    try:
        hostname = (urlparse(apply_url).hostname or "").removeprefix("www.").lower()
    except ValueError:
        return ApplicationRoute(
            method="unknown", apply_url=apply_url, note="Apply URL could not be parsed."
        )

    source_text = f"{source} {source_type} {apply_option_title}".lower()
    if source_type in {"job_board", "public_job_board_listing"}:
        return ApplicationRoute(
            method="job_board_listing",
            apply_url=apply_url,
            resume_upload="unknown",
            platform=hostname or "job_board",
            can_agent_fill=False,
            note="Public job board listing. Inspect downstream apply target before deciding whether the agent can fill a form.",
        )
    source_is_job_board = (
        source_type == "public_job_board" or source == "public_job_board"
    )
    if source_is_job_board:
        for platform, domains in JOB_BOARD_PLATFORMS:
            if host_matches(hostname, domains):
                return ApplicationRoute(
                    method="job_board_listing",
                    apply_url=apply_url,
                    resume_upload="unknown",
                    platform=hostname or platform,
                    can_agent_fill=False,
                    note="Public job board listing. Inspect downstream apply target before deciding whether the agent can fill a form.",
                )

    if source_type == "linkedin" or host_matches(hostname, ["linkedin.com"]):
        return ApplicationRoute(
            method="linkedin_job",
            apply_url=apply_url,
            resume_upload="possible",
            platform="linkedin",
            can_agent_fill=False,
            note="LinkedIn applications usually require login and human review.",
        )

    for platform, domains in ATS_PLATFORMS:
        if host_matches(hostname, domains):
            return ApplicationRoute(
                method="ats_form",
                apply_url=apply_url,
                resume_upload="likely",
                platform=platform,
                can_agent_fill=True,
                note="Known ATS form. Agent may fill visible fields. Final autonomous submit requires a reviewed approval manifest.",
            )

    for platform, domains in JOB_BOARD_PLATFORMS:
        if host_matches(hostname, domains):
            return ApplicationRoute(
                method="job_board_listing",
                apply_url=apply_url,
                resume_upload="unknown",
                platform=platform,
                can_agent_fill=False,
                note="Job board listing. Inspect downstream apply target before deciding whether the agent can fill a form.",
            )

    if source_type == "google_jobs" or "google jobs" in source_text:
        return ApplicationRoute(
            method="external_form",
            apply_url=apply_url,
            resume_upload="unknown",
            platform=hostname or "google_jobs",
            can_agent_fill=False,
            note="Google-discovered external page. Inspect the downstream form before deciding whether the agent can fill it.",
        )

    return ApplicationRoute(
        method="external_form",
        apply_url=apply_url,
        resume_upload="unknown",
        platform=hostname or "external",
        can_agent_fill=False,
        note="Unverified external application page. Inspect the downstream form before deciding whether the agent can fill it.",
    )


def is_join_like_route(route: ApplicationRoute) -> bool:
    platform = (route.platform or "").casefold()
    apply_url = (route.apply_url or "").casefold()
    return platform in JOIN_LIKE_PLATFORMS or any(
        domain in apply_url for domain in JOIN_LIKE_DOMAINS
    )


def route_listing_fields(route: ApplicationRoute) -> dict[str, str]:
    return {
        "application_method": route.method,
        "resume_upload": route.resume_upload,
        "apply_platform": route.platform,
        "application_method_note": route.note,
    }


def enrich_listing_route(listing: JobListing, source_type: str = "") -> JobListing:
    route = infer_application_route(
        listing.apply_url or listing.source_url,
        source=listing.source,
        source_type=source_type,
    )
    return listing.model_copy(update=route_listing_fields(route))


def classify_form_field(label: str, name: str, field_type: str) -> str:
    text = f"{label} {name} {field_type}".lower()
    if field_type == "search" and re.search(r"\bsearch\b|iti-\d+__search-input", text):
        return "unknown"
    upload_text_pattern = r"upload|attach|hochladen|datei|file|pdf"
    is_upload_control = field_type == "file" or (
        field_type in {"checkbox", "radio"}
        and bool(re.search(upload_text_pattern, text))
    )
    if is_upload_control:
        upload_patterns = [
            (
                "cover_letter_upload",
                r"cover.*(upload|file|pdf)|anschreiben.*(upload|hochladen|datei|pdf)",
            ),
            ("resume_upload", r"resume|cv|lebenslauf|curriculum"),
            (
                "document_upload",
                r"(document|dokument|unterlage|anlage).*(upload|hochladen|file|datei|pdf)|(upload|hochladen).*(document|dokument|unterlage|anlage|file|datei|pdf)",
            ),
        ]
        for classification, pattern in upload_patterns:
            if re.search(pattern, text):
                return classification
        if field_type == "file":
            return "document_upload"

    patterns = [
        (
            "cover_letter",
            r"cover letter|anschreiben|motivation|why.*role|warum.*rolle|additional information|zusätzliche",
        ),
        ("email", r"e-?mail"),
        ("phone", r"phone|telefon|mobile|handy"),
        (
            "source",
            r"how did you hear|where did you hear|how did you learn|where did you learn|\bsource\b|wie.*aufmerksam|wo.*stellenanzeige.*aufmerksam|auf.*stellenanzeige.*aufmerksam|wie.*gefunden|quelle",
        ),
        ("linkedin", r"linkedin"),
        ("github", r"github"),
        ("portfolio", r"portfolio"),
        ("website", r"website|webseite|personal site|homepage"),
        (
            "referral",
            r"\breferral\b|\bemployee\s+referrer\b|\breferred\b|\breferrer\b|empfehlung",
        ),
        ("first_name", r"first[_\s-]?name|firstname|given[_\s-]?name|vorname"),
        (
            "last_name",
            r"last[_\s-]?name|lastname|family[_\s-]?name|nachname|surname",
        ),
        ("honeypot", r"\barbeitnow_name_[a-z0-9]+\b|\barbeitnow_valid_from\b"),
        ("full_name", r"full name|name"),
        ("postal_code", r"\bplz\b|postleitzahl|postal code|zip code|\bzip\b"),
        (
            "street_address",
            r"street|straße|strasse|hausnummer|road|address line 1|adresse zeile 1|anschrift.*(straße|strasse|nr|nummer)",
        ),
        ("country", r"\bcountry\b|\bland\b|deutschland|germany"),
        ("city", r"\bcity\b|stadt|\bort\b|wohnort"),
        ("address", r"postanschrift|anschrift|address|adresse"),
        ("location", r"location|standort"),
        ("salary", r"salary|gehalt|compensation|vergütung"),
        (
            "availability",
            r"availability|available|start date|eintritt|verfügbarkeit|verfügbar|notice",
        ),
        ("work_authorization", r"work authorization|arbeitserlaubnis|visa|visum"),
        ("relocation", r"relocat|umzug|umziehen"),
        ("work_model", r"remote|hybrid|office|büro|homeoffice"),
        ("language", r"language|sprache|deutsch|english|german"),
        ("education", r"education|ausbildung|studium|university|hochschule"),
        ("degree", r"degree|abschluss|bachelor|master"),
        ("current_company", r"current company|aktuelle firma|employer|arbeitgeber"),
        ("current_title", r"current title|job title|position|rolle"),
        ("pronouns", r"pronoun|pronomen"),
        (
            "eeo",
            r"gender|geschlecht|race|ethnicity|ethnic background|sexual orientation|disability|chronic condition|veteran|diversity|equal opportunity",
        ),
        (
            "screening_question",
            r"question|frage|willing|bereit|experience with|erfahrung mit",
        ),
        ("consent", r"privacy|datenschutz|terms|bedingungen|consent|einverstanden"),
    ]
    for classification, pattern in patterns:
        if re.search(pattern, text):
            return classification
    return "unknown"


def css_attr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_for_field(node: Tag, field_type: str, name: str, node_id: str) -> str:
    if node_id and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", node_id):
        return f"#{node_id}"
    if node_id:
        return f'[id="{css_attr_value(node_id)}"]'
    if name:
        if field_type in {"checkbox", "radio"}:
            field_value = normalize_space(str(node.get("value") or ""))
            if field_value:
                return f'input[type="{field_type}"][name="{css_attr_value(name)}"][value="{css_attr_value(field_value)}"]'
            return f'input[type="{field_type}"][name="{css_attr_value(name)}"]'
        return f'[name="{css_attr_value(name)}"]'
    tag_name = getattr(node, "name", "") or "input"
    return tag_name


def _tag_text(node: Tag | None) -> str:
    if not node:
        return ""
    return normalize_space(node.get_text(" "))


def label_for_node(soup: BeautifulSoup, node: Tag) -> str:
    node_id = normalize_space(str(node.get("id") or ""))
    aria_label = normalize_space(str(node.get("aria-label") or ""))
    placeholder = normalize_space(str(node.get("placeholder") or ""))

    if node_id:
        explicit_label = soup.find("label", attrs={"for": node_id})
        if isinstance(explicit_label, Tag):
            explicit_text = _tag_text(explicit_label)
            if explicit_text:
                return explicit_text

    parent_label = node.find_parent("label")
    if isinstance(parent_label, Tag):
        parent_text = _tag_text(parent_label)
        if parent_text:
            field_type = str(node.get("type") or node.name or "text").lower()
            if field_type in {"checkbox", "radio"}:
                fieldset = node.find_parent("fieldset")
                if isinstance(fieldset, Tag):
                    legend = fieldset.find("legend")
                    legend_text = _tag_text(legend) if isinstance(legend, Tag) else ""
                    if legend_text and legend_text not in parent_text:
                        return normalize_space(f"{legend_text} {parent_text}")
            return parent_text

    fieldset = node.find_parent("fieldset")
    if isinstance(fieldset, Tag):
        legend = fieldset.find("legend")
        legend_text = _tag_text(legend) if isinstance(legend, Tag) else ""
        if legend_text:
            return legend_text

    return aria_label or placeholder


def field_context_text(soup: BeautifulSoup, node: Tag, label: str) -> str:
    parts = [label]
    node_id = normalize_space(str(node.get("id") or ""))
    if node_id:
        explicit_label = soup.find("label", attrs={"for": node_id})
        if isinstance(explicit_label, Tag):
            parts.append(_tag_text(explicit_label))
    parent_label = node.find_parent("label")
    if isinstance(parent_label, Tag):
        parts.append(_tag_text(parent_label))
    fieldset = node.find_parent("fieldset")
    if isinstance(fieldset, Tag):
        parts.append(_tag_text(fieldset.find("legend")))
    describedby = normalize_space(str(node.get("aria-describedby") or ""))
    for described_id in describedby.split():
        described = soup.find(id=described_id)
        if isinstance(described, Tag):
            parts.append(_tag_text(described))
    sibling = node.find_previous_sibling()
    if isinstance(sibling, Tag):
        sibling_text = _tag_text(sibling)
        if len(sibling_text) <= 300:
            parts.append(sibling_text)
    parent = node.parent if isinstance(node.parent, Tag) else None
    if parent and parent.name not in {"form", "body", "html"}:
        parent_text = _tag_text(parent)
        if len(parent_text) <= 500:
            parts.append(parent_text)
    parts.extend(
        normalize_space(str(node.get(attr) or ""))
        for attr in [
            "name",
            "id",
            "aria-label",
            "aria-describedby",
            "placeholder",
            "autocomplete",
        ]
    )
    return normalize_space(" ".join(part for part in parts if part))


def is_required_field(node: Tag, context: str) -> bool:
    if node.has_attr("required"):
        return True
    aria_required = str(node.get("aria-required") or "").lower()
    if aria_required in {"true", "1"}:
        return True
    return bool(re.search(r"\brequired\b|\bpflicht\b|erforderlich|\*", context.lower()))


def option_label_for_input(soup: BeautifulSoup, input_node: Tag) -> str:
    node_id = normalize_space(str(input_node.get("id") or ""))
    if node_id:
        label = soup.find("label", attrs={"for": node_id})
        if isinstance(label, Tag):
            value = _tag_text(label)
            if value:
                return value
    parent_label = input_node.find_parent("label")
    if isinstance(parent_label, Tag):
        value = _tag_text(parent_label)
        if value:
            return value
    return normalize_space(str(input_node.get("value") or ""))


def input_group_options(soup: BeautifulSoup, field_type: str, name: str) -> list[str]:
    if field_type not in {"checkbox", "radio"} or not name:
        return []
    options: list[str] = []
    for node in soup.find_all("input", attrs={"name": name}):
        if not isinstance(node, Tag):
            continue
        if str(node.get("type") or "").lower() != field_type:
            continue
        option = option_label_for_input(soup, node)
        if option and option not in options:
            options.append(option)
    return options


def extract_form_fields_from_html(html: str) -> list[ApplicationFormField]:
    soup = BeautifulSoup(html, "lxml")
    fields: list[ApplicationFormField] = []
    for node in soup.select("input, textarea, select"):
        if not isinstance(node, Tag):
            continue
        field_type = str(node.get("type") or node.name or "text").lower()
        if field_type in {"hidden", "submit", "button", "reset", "image"}:
            continue
        name = normalize_space(str(node.get("name") or ""))
        node_id = normalize_space(str(node.get("id") or ""))
        label = label_for_node(soup, node) or name or node_id
        context = field_context_text(soup, node, label)
        selector = selector_for_field(node, field_type, name, node_id)
        options = [
            normalize_space(option.get_text(" ") or str(option.get("value") or ""))
            for option in node.select("option")
            if normalize_space(option.get_text(" ") or str(option.get("value") or ""))
        ]
        options.extend(
            option
            for option in input_group_options(soup, field_type, name)
            if option not in options
        )
        classification = classify_form_field(context, name, field_type)
        fields.append(
            ApplicationFormField(
                label=label,
                name=name,
                selector=selector,
                field_type=field_type,
                required=is_required_field(node, context),
                options=options,
                classification=classification,
                placeholder=normalize_space(str(node.get("placeholder") or "")),
                autocomplete=normalize_space(str(node.get("autocomplete") or "")),
                aria_label=normalize_space(str(node.get("aria-label") or "")),
                disabled=bool(
                    node.get("disabled") or node.get("aria-disabled") == "true"
                ),
                group_name=name if field_type in {"checkbox", "radio"} else "",
                requires_manual_review=classification
                in {
                    "salary",
                    "availability",
                    "work_authorization",
                    "relocation",
                    "eeo",
                    "screening_question",
                    "consent",
                },
            )
        )
    return fields


def default_form_fields() -> list[ApplicationFormField]:
    return [
        ApplicationFormField(label="Full name", classification="full_name"),
        ApplicationFormField(label="Email", classification="email"),
        ApplicationFormField(label="Phone", classification="phone"),
        ApplicationFormField(label="Address", classification="address"),
        ApplicationFormField(label="Street address", classification="street_address"),
        ApplicationFormField(label="Postal code", classification="postal_code"),
        ApplicationFormField(label="City", classification="city"),
        ApplicationFormField(
            label="Country",
            classification="country",
            field_type="select",
            options=["Deutschland"],
        ),
        ApplicationFormField(label="LinkedIn", classification="linkedin"),
        ApplicationFormField(label="GitHub", classification="github"),
        ApplicationFormField(label="Location", classification="location"),
        ApplicationFormField(
            label="Resume/CV upload", field_type="file", classification="resume_upload"
        ),
        ApplicationFormField(
            label="Cover letter upload",
            field_type="file",
            classification="cover_letter_upload",
        ),
        ApplicationFormField(
            label="Cover letter or motivation",
            field_type="textarea",
            classification="cover_letter",
        ),
    ]


def normalized_option(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def source_label_for_listing(listing: JobListing) -> str:
    text = " ".join(
        [listing.source, listing.apply_platform, listing.source_url, listing.apply_url]
    ).lower()
    if "stepstone" in text:
        return "Stepstone"
    if "linkedin" in text:
        return "LinkedIn"
    if "serpapi" in text or "google" in text:
        return "Google for Jobs"
    if "arbeitnow" in text:
        return "Arbeitnow"
    if "freelancermap" in text:
        return "Freelancermap"
    if "avm" in text or "fritz" in text:
        return "FRITZ! Karriereseite"
    if listing.apply_platform and listing.apply_platform != "company":
        return listing.apply_platform
    return "Karriereseite"


def choose_select_value(field: ApplicationFormField, desired_value: str) -> str:
    if not desired_value:
        return ""
    options = [option for option in field.options if option]
    if not options:
        return desired_value
    desired = normalized_option(desired_value)
    normalized_options = {normalized_option(option): option for option in options}
    label = normalized_option(field.label)
    if {"yes", "no"}.issubset(normalized_options):
        if field.classification == "language" and any(
            marker in desired for marker in ["native", "fluent", "c1", "c2"]
        ):
            return normalized_options["yes"]
    if (
        ("hear about" in label or "learn about" in label)
        and {"online search research", "other"} & set(normalized_options)
        and desired in {"google for jobs", "karriereseite"}
    ):
        return (
            normalized_options.get("online search research")
            or normalized_options["other"]
        )
    if field.classification == "salary":
        salary_option = choose_salary_range_option(options, desired_value)
        if salary_option:
            return salary_option
    aliases = [normalized_option(alias) for alias in OPTION_ALIASES.get(desired, [])]
    candidates = [desired, *aliases]
    for option in options:
        normalized = normalized_option(option)
        if normalized in candidates:
            return option
    for option in options:
        normalized = normalized_option(option)
        if any(
            candidate and (candidate in normalized or normalized in candidate)
            for candidate in candidates
        ):
            return option
    return ""


def should_check_option(field: ApplicationFormField, desired_value: str) -> bool:
    if not desired_value:
        return False
    desired = normalized_option(desired_value)
    aliases = [normalized_option(alias) for alias in OPTION_ALIASES.get(desired, [])]
    candidates = [desired, *aliases]
    label = normalized_option(field.label)
    selector_value_match = re.search(r'\[value="([^"]*)"\]', field.selector)
    selector_value = (
        normalized_option(selector_value_match.group(1)) if selector_value_match else ""
    )
    if label and label in candidates:
        return True
    if selector_value and selector_value in candidates:
        return True
    negative_terms = {"no", "nein", "not", "nicht", "other", "outside", "außerhalb"}
    desired_is_negative = desired in {"no", "nein", "false"}
    if desired_is_negative:
        return selector_value in {"no", "nein", "false", "0", "other"} or bool(
            negative_terms & set(label.split())
        )
    if selector_value in {"no", "nein", "false", "0", "other"} or (
        negative_terms & set(label.split())
    ):
        return False
    desired_parts = {
        normalized_option(part)
        for part in re.split(r"[,;/]|\bor\b|\band\b|\bund\b", desired_value)
        if normalized_option(part)
    }
    desired_words = set(desired.split())
    if label and (label in desired_parts or label in desired_words):
        return True
    if selector_value and (
        selector_value in desired_parts or selector_value in desired_words
    ):
        return True
    if {"yes", "ja", "true", "agree", "accepted"} & desired_words:
        if selector_value in {"yes", "ja", "true", "1"}:
            return True
        if {"yes", "ja"} & set(label.split()):
            return True
    return any(
        candidate
        and (
            candidate in label
            or selector_value == candidate
            or (len(candidate) > 2 and candidate in selector_value)
        )
        for candidate in candidates
    )


def field_match_text(field: ApplicationFormField) -> str:
    return normalized_option(
        " ".join(
            [
                field.label,
                field.name,
                field.placeholder,
                field.aria_label,
                field.autocomplete,
                " ".join(field.options),
            ]
        )
    )


def salary_amount_to_eur(
    amount: str, *, uses_k: bool = False, implied_k: bool = False
) -> int | None:
    cleaned = normalize_space(amount).lower().replace(" ", "")
    cleaned = cleaned.replace("€", "").replace("eur", "").replace("euro", "")
    cleaned = cleaned.replace("k", "")
    if not cleaned:
        return None
    if uses_k or implied_k:
        try:
            return int(round(float(cleaned.replace(",", ".")) * 1000))
        except ValueError:
            return None
    if re.fullmatch(r"\d{2,3}([.,]\d{3})+", cleaned):
        return int(re.sub(r"[.,]", "", cleaned))
    if re.fullmatch(r"\d{5,6}", cleaned):
        return int(cleaned)
    return None


def salary_range_from_text(text: str) -> tuple[int, int] | None:
    normalized = normalize_space(text).lower()
    if not normalized:
        return None
    if any(marker in normalized for marker in NON_EUR_SALARY_MARKERS):
        return None
    if any(marker in normalized for marker in NON_ANNUAL_SALARY_MARKERS):
        return None
    match = SALARY_RANGE_PATTERN.search(text)
    if not match:
        return None
    lower_k = bool(match.group("lower_k"))
    upper_k = bool(match.group("upper_k"))
    lower = salary_amount_to_eur(
        match.group("lower"), uses_k=lower_k, implied_k=upper_k and not lower_k
    )
    upper = salary_amount_to_eur(
        match.group("upper"), uses_k=upper_k, implied_k=lower_k and not upper_k
    )
    if lower is None or upper is None or lower > upper:
        return None
    return lower, upper


def choose_salary_range_option(options: list[str], desired_value: str) -> str:
    desired_amount = salary_amount_to_eur(desired_value)
    if desired_amount is None:
        return ""
    for option in options:
        option_range = salary_range_from_text(option)
        if option_range is None:
            continue
        lower, upper = option_range
        if lower <= desired_amount <= upper:
            return option
    return ""


def salary_expectation_answer(
    answers: dict[str, str], listing: JobListing | None = None
) -> str:
    default_answer = answers.get("salary", "")
    default_amount = salary_amount_to_eur(default_answer)
    if default_amount is None:
        # Salary is personal and is never inferred from a repository default.
        return ""
    salary_range = salary_range_from_text(listing.compensation if listing else "")
    if salary_range is None:
        return default_answer
    lower, _upper = salary_range
    if lower > default_amount:
        return str(lower + LISTED_SALARY_INCREMENT_EUR)
    return default_answer


def approved_standard_answer(
    field: ApplicationFormField,
    profile: CandidateProfile,
    listing: JobListing | None = None,
) -> str:
    answers = profile.standard_application_answers
    if not answers:
        return ""
    text = field_match_text(field)
    classification = field.classification
    if (
        "previous salary" in text
        or "current salary" in text
        or "previous role" in text
        or "salary slips" in text
    ):
        if "usd" in text or "monthly" in text or "month" in text:
            return answers.get("previous_salary_usd_monthly", "")
        return answers.get("current_salary", "")
    if ("salary" in text or "gehalt" in text or classification == "salary") and (
        "usd" in text or "monthly" in text or "month" in text
    ):
        return answers.get("salary_usd_monthly", "")
    if "salary" in text or "gehalt" in text or classification == "salary":
        return salary_expectation_answer(answers, listing)
    if "currently living" in text and "relocat" in text:
        # This combines residence and relocation into one decision. A location
        # string alone cannot safely answer it, so surface it for review.
        return ""
    if "backend" in text and (
        "experience" in text or "erfahrung" in text or "technologies" in text
    ):
        return answers.get("backend_experience", "")
    if "frontend" in text and (
        "experience" in text or "erfahrung" in text or "technologies" in text
    ):
        return answers.get("frontend_experience", "")
    if (
        "years of experience" in text
        or "years_of_experience" in text
        or "jahre berufserfahrung" in text
        or "jahre erfahrung" in text
    ):
        value = answers.get("general_experience_years", "")
        if field.field_type == "select" and normalized_option(value) == "5 years":
            return "5-6 years"
        return value
    if "office" in text and (
        "days" in text or "tage" in text or "woche" in text or "week" in text
    ):
        return answers.get("office_days_per_week", "")
    if "english" in text and ("proficiency" in text or classification == "language"):
        return answers.get("english_proficiency", "")
    if ("german" in text or "deutsch" in text) and (
        "level" in text
        or "proficiency" in text
        or "speaking" in text
        or "writing" in text
        or "listening" in text
        or classification == "language"
    ):
        return answers.get("german_proficiency", "")
    if "other language" in text or "business proficient" in text:
        return answers.get("other_business_languages", "")
    if "currently reside" in text or "current country" in text:
        return answers.get("current_country", "")
    if (
        "aktuelle situation" in text
        or "deine situation" in text
        or "current situation" in text
        or "employment status" in text
        or "employment situation" in text
        or "arbeitssuchend" in text
        or "arbeitsuchend" in text
    ):
        return answers.get("current_situation", "")
    if "wohnst" in text and (
        "deutschland" in text or "germany" in text or "spanien" in text
    ):
        return answers.get("current_location", "")
    if "energiebranche" in text or "stromnetze" in text or "electrical grids" in text:
        return answers.get("energy_industry_knowledge", "")
    if "n8n" in text and (
        "how many years" in text
        or "years of experience" in text
        or "experience" in text
    ):
        return answers.get("n8n_years", "")
    if "client facing" in text:
        return answers.get("client_facing_experience", "")
    if "timezones" in text or "timezone" in text:
        return answers.get("timezones", "")
    if "eastern time" in text or "dubai time" in text:
        return answers.get("eastern_dubai_time", "")
    if "ai coding" in text or "coding environments" in text:
        return answers.get("ai_coding_environments", "")
    if "tools software" in text or "software and platforms" in text:
        return answers.get("tools_platforms", "")
    if "industries" in text:
        return answers.get("industries", "")
    if "type of company" in text or "company do you prefer" in text:
        return answers.get("company_type_preference", "")
    if "outside of your home country" in text or "international" in text:
        return answers.get("international_client_countries", "")
    if (
        "last thing" in text
        and ("typescript" in text or "javascript" in text)
        and "built" in text
    ):
        return answers.get("last_typescript_javascript_build", "")
    if "who on your team" in text and "admire" in text:
        return answers.get("admired_teammate", "")
    if (
        "how did you hear" in text
        or "where did you hear" in text
        or "hear about this opportunity" in text
        or classification == "source"
    ):
        return answers.get("opportunity_source", "")
    if "what product or technology" in text and "should already exist" in text:
        return answers.get("limetax_product_answer", "")
    if (
        "start" in text
        or "available" in text
        or "notice period" in text
        or "eintritt" in text
        or "availability" in text
        or classification == "availability"
    ):
        return answers.get("availability", "")
    if (
        "current location" in text
        or "country city" in text
    ):
        return answers.get("current_location", "")
    if "open to work from" in text or "countries cities" in text:
        return answers.get("work_locations", "")
    if "hybrid model" in text or "60 in office" in text:
        return answers.get("hybrid_model", "")
    if "talent pool" in text:
        return answers.get("talent_pool_consent", "")
    if (
        "terms and conditions" in text
        or "bedingungen" in text
        or classification == "consent"
    ):
        return answers.get("terms_consent", "")
    if "privacy policy" in text or "datenschutz" in text:
        return answers.get("privacy_consent", "")
    if "type of visa" in text or "visa type" in text:
        return answers.get("visa_type", "")
    if "visa valid" in text or "valid until" in text:
        return answers.get("visa_valid_until", "")
    if (
        "valid visa" in text
        or "work visa" in text
        or "work permit" in text
        or classification == "work_authorization"
    ):
        return answers.get("work_authorization", "")
    if "gender identity" in text or "geschlecht" in text:
        return answers.get("gender_identity", "")
    if any(
        marker in text
        for marker in [
            "ethnic background",
            "race",
            "veteran",
        ]
    ):
        return answers.get("ethnic_background", "")
    if "sexual orientation" in text:
        return answers.get("sexual_orientation", "")
    if "disability" in text or "chronic condition" in text:
        return answers.get("disability", "")
    if "relocat" in text or "umzug" in text or "umziehen" in text:
        return answers.get("relocation", "")
    return answers.get(classification, "")


def instruction_from_approved_answer(
    field: ApplicationFormField, classification: str, value: str
) -> FormFillInstruction:
    action: str = "fill"
    safety_note = "Uses a user-approved standard application answer."
    if field.field_type == "select":
        selected = choose_select_value(field, value)
        value = selected
        action = "select" if value else "manual"
        safety_note = (
            "Uses a user-approved standard application answer."
            if value
            else "No matching select option found for the approved standard answer."
        )
    elif field.field_type in {"checkbox", "radio"}:
        positive = normalized_option(value) in {
            "yes",
            "ja",
            "true",
            "i agree",
            "agree",
            "accepted",
        }
        if field.field_type == "checkbox" and positive:
            action = "check"
        else:
            action = "check" if should_check_option(field, value) else "manual"
        safety_note = (
            "Uses a user-approved standard application answer."
            if action == "check"
            else "Grouped checkbox/radio value requires human confirmation."
        )
    return FormFillInstruction(
        field_label=field.label,
        selector=field.selector,
        classification=classification,
        action=action if value else "manual",
        value=value,
        field_type=field.field_type,
        frame_url=field.frame_url,
        required=field.required,
        confidence="high" if value else "medium",
        safety_note=safety_note,
    )


def build_form_fill_plan(
    profile: CandidateProfile,
    listing: JobListing,
    package_dir: Path | None = None,
    fields: list[ApplicationFormField] | None = None,
    cover_letter_text: str = "",
    submit_allowed: bool = False,
    single_upload_verified: bool = False,
) -> FormFillPlan:
    route = infer_application_route(
        listing.apply_url or listing.source_url, source=listing.source
    )
    fields = default_form_fields() if fields is None else fields
    configured_cv = configured_cv_pdf_path()
    cv_path = str(configured_cv) if configured_cv else ""
    cover_pdf_path = str(package_dir / "cover_letter.pdf") if package_dir else ""
    combined_pdf_path = resolve_combined_application_pdf(package_dir)
    separate_cover_letter_upload_present = any(
        field.classification == "cover_letter_upload" for field in fields
    )
    separate_resume_upload_present = any(
        field.classification == "resume_upload" for field in fields
    )
    separate_document_upload_present = any(
        field.classification == "document_upload" for field in fields
    )
    join_like_route = is_join_like_route(route)
    source_label = source_label_for_listing(listing)
    instructions = [
        build_instruction(
            field,
            profile,
            listing=listing,
            cv_path=cv_path,
            cover_pdf_path=cover_pdf_path,
            combined_pdf_path=combined_pdf_path,
            separate_cover_letter_upload_present=separate_cover_letter_upload_present,
            separate_resume_upload_present=separate_resume_upload_present,
            separate_document_upload_present=separate_document_upload_present,
            single_upload_verified=single_upload_verified,
            cover_letter_text=cover_letter_text,
            source_label=source_label,
            join_like_route=join_like_route,
        )
        for field in fields
    ]
    return FormFillPlan(
        job_title=listing.title,
        company=listing.company,
        apply_url=listing.apply_url or listing.source_url,
        route=route,
        fields=fields,
        instructions=instructions,
        submit_allowed=submit_allowed,
        generated_at=utc_now_iso(),
    )


def resolve_combined_application_pdf(package_dir: Path | None = None) -> str:
    explicit = os.getenv("JOB_AGENT_COMBINED_APPLICATION_PDF_PATH", "")
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.exists():
            return str(explicit_path.resolve())
    if not package_dir:
        return ""
    candidates = [
        package_dir / "combined_application.pdf",
        package_dir / "application.pdf",
    ]
    candidates.extend(
        path
        for path in sorted(package_dir.glob("*.pdf"))
        if path.name != "cover_letter.pdf"
        and (
            "bewerbung" in path.name.lower()
            or "application" in path.name.lower()
            or "combined" in path.name.lower()
        )
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return ""


def build_instruction(
    field: ApplicationFormField,
    profile: CandidateProfile,
    listing: JobListing | None = None,
    cv_path: str = "",
    cover_pdf_path: str = "",
    combined_pdf_path: str = "",
    separate_cover_letter_upload_present: bool = False,
    separate_resume_upload_present: bool = False,
    separate_document_upload_present: bool = False,
    single_upload_verified: bool = False,
    cover_letter_text: str = "",
    source_label: str = "Google for Jobs",
    join_like_route: bool = False,
) -> FormFillInstruction:
    full_name_parts = profile.name.split()
    first_name = full_name_parts[0] if full_name_parts else profile.name
    last_name = " ".join(full_name_parts[1:]) if len(full_name_parts) > 1 else ""
    values = {
        "full_name": profile.name,
        "first_name": first_name,
        "last_name": last_name,
        "email": profile.email,
        "phone": profile.phone,
        "address": profile.address,
        "street_address": profile.street_address,
        "postal_code": profile.postal_code,
        "city": profile.city or profile.location,
        "country": profile.country,
        "linkedin": profile.linkedin,
        "github": profile.github,
        "portfolio": profile.github,
        "website": profile.github,
        "location": profile.location,
        "cover_letter": cover_letter_text,
        "source": source_label,
    }
    classification = field.classification
    standard_value = approved_standard_answer(field, profile, listing)
    if standard_value:
        return instruction_from_approved_answer(field, classification, standard_value)
    if classification in values:
        value = values[classification]
        action: str = "fill"
        safety_note = ""
        if field.field_type == "select":
            value = choose_select_value(field, value)
            action = "select" if value else "manual"
            safety_note = (
                "" if value else "No matching select option found; choose manually."
            )
        elif field.field_type in {"checkbox", "radio"}:
            action = "check" if should_check_option(field, value) else "manual"
            safety_note = (
                ""
                if action == "check"
                else "Grouped checkbox/radio value requires human confirmation."
            )
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action=action if value else "manual",
            value=value,
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence="high" if value else "medium",
            safety_note=safety_note
            if value
            else "Missing profile value; fill manually.",
        )
    if classification == "resume_upload":
        if field.field_type in {"radio", "checkbox"}:
            action = "check" if should_check_option(field, "Upload CV") else "manual"
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action=action,
                value="Upload CV" if action == "check" else "",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="medium" if action == "check" else "low",
                safety_note=(
                    "Select the upload route; the actual reviewed PDF is attached through the file input."
                    if action == "check"
                    else "Grouped upload route requires human confirmation."
                ),
            )
        if join_like_route:
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action="upload" if cv_path else "manual",
                file_path=cv_path,
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="high" if cv_path else "low",
                safety_note=(
                    "JOIN/Arbeitnow first resume step is CV-only. Upload the reviewed CV here; never upload the combined application PDF on the first JOIN upload. Continue to the next step and upload the cover letter separately if JOIN exposes that field."
                    if cv_path
                    else "JOIN/Arbeitnow first resume step is CV-only, but no CV path is configured. Configure the reviewed CV before continuing."
                ),
            )
        if (
            not separate_cover_letter_upload_present
            and not separate_document_upload_present
        ):
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action=(
                    "upload"
                    if combined_pdf_path and single_upload_verified
                    else "manual"
                ),
                file_path=combined_pdf_path if single_upload_verified else "",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence=(
                    "high" if combined_pdf_path and single_upload_verified else "low"
                ),
                safety_note=(
                    "No separate cover-letter upload field was detected in the verified flow; upload the reviewed combined application PDF."
                    if combined_pdf_path and single_upload_verified
                    else "No separate cover-letter upload field was detected in the current fields. Inspect the next form step if present, then use a reviewed combined three-page application PDF only after that check."
                ),
            )
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action="upload" if cv_path else "manual",
            file_path=cv_path,
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence="medium" if cv_path else "low",
            safety_note=(
                "Separate cover-letter/additional-document upload detected; upload only the reviewed CV PDF here."
            ),
        )
    if classification == "cover_letter_upload":
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action="upload" if cover_pdf_path else "manual",
            file_path=cover_pdf_path,
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence="high" if cover_pdf_path else "low",
            safety_note="Upload only after reviewing the generated cover letter PDF.",
        )
    if classification == "document_upload":
        document_text = field_match_text(field)
        specific_unapproved_document = any(
            marker in document_text
            for marker in [
                "arbeitszeugnis",
                "employment reference",
                "reference letter",
                "certificate",
                "zertifikat",
                "zeugnis",
                "work sample",
                "portfolio sample",
                "transcript",
            ]
        )
        if specific_unapproved_document:
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action="manual" if field.required else "skip",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="medium",
                safety_note="Specific document upload requires an approved matching file. Do not upload the CV or cover letter here.",
            )
        if separate_cover_letter_upload_present:
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action="skip",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="medium",
                safety_note="Separate CV and cover-letter upload fields were detected; do not use a generic document field unless the page asks for certificates or another specific file.",
            )
        if join_like_route:
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action="manual",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="medium",
                safety_note="JOIN/Arbeitnow generic upload requires inspection. Do not upload the combined PDF on the first JOIN upload; continue through the CV-only step and use the cover-letter step if it appears.",
            )
        if separate_resume_upload_present:
            return FormFillInstruction(
                field_label=field.label,
                selector=field.selector,
                classification=classification,
                action="upload" if cover_pdf_path else "manual",
                file_path=cover_pdf_path if cover_pdf_path else "",
                field_type=field.field_type,
                frame_url=field.frame_url,
                required=field.required,
                confidence="medium" if cover_pdf_path else "low",
                safety_note=(
                    "Generic additional-document upload detected next to a CV field; upload the reviewed cover-letter PDF here."
                    if cover_pdf_path
                    else "Generic upload field detected, but no cover-letter PDF exists. Inspect before uploading anything here."
                ),
            )
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action=(
                "upload" if combined_pdf_path and single_upload_verified else "manual"
            ),
            file_path=combined_pdf_path if single_upload_verified else "",
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence=(
                "high" if combined_pdf_path and single_upload_verified else "low"
            ),
            safety_note=(
                "Generic single-upload field with no separate CV or cover-letter upload; upload the reviewed combined application PDF."
                if combined_pdf_path and single_upload_verified
                else "Generic upload field with no separate CV or cover-letter upload in the current fields. Inspect the next form step if present, then use a reviewed combined three-page application PDF only after that check."
            ),
        )
    if classification in {
        "salary",
        "availability",
        "work_authorization",
        "relocation",
        "eeo",
        "screening_question",
        "consent",
    }:
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action="manual",
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence="medium",
            safety_note="Requires current human confirmation before filling.",
        )
    if classification == "honeypot":
        return FormFillInstruction(
            field_label=field.label,
            selector=field.selector,
            classification=classification,
            action="skip",
            field_type=field.field_type,
            frame_url=field.frame_url,
            required=field.required,
            confidence="high",
            safety_note="Likely anti-spam field; leave empty.",
        )
    return FormFillInstruction(
        field_label=field.label,
        selector=field.selector,
        classification=classification,
        action="skip",
        field_type=field.field_type,
        frame_url=field.frame_url,
        required=field.required,
        confidence="low",
        safety_note="Unknown field; inspect manually.",
    )


def format_application_route(route: ApplicationRoute) -> str:
    return f"""# Application Route

- Method: {route.method}
- Platform: {route.platform or "unknown"}
- Resume upload: {route.resume_upload}
- Agent can fill: {"yes" if route.can_agent_fill else "no"}
- Submit allowed by default: no
- Apply URL: {route.apply_url}

{route.note}
"""


def format_form_fill_plan(plan: FormFillPlan) -> str:
    lines = [
        "# Form Fill Plan",
        "",
        f"- Company: {plan.company}",
        f"- Role: {plan.job_title}",
        f"- Apply URL: {plan.apply_url}",
        f"- Method: {plan.route.method}",
        f"- Platform: {plan.route.platform or 'unknown'}",
        f"- Resume upload: {plan.route.resume_upload}",
        f"- Submit allowed: {'yes' if plan.submit_allowed else 'no'}",
        "",
        "## Instructions",
    ]
    for instruction in plan.instructions:
        target = instruction.value or instruction.file_path or instruction.safety_note
        details = []
        if instruction.required:
            details.append("required")
        if instruction.selector:
            details.append(f"selector `{instruction.selector}`")
        if instruction.frame_url:
            details.append(f"frame `{instruction.frame_url}`")
        suffix = f" [{', '.join(details)}]" if details else ""
        lines.append(
            f"- {instruction.action}: {instruction.field_label} ({instruction.classification}){suffix} -> {target}"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "- Filling a form requires `fill-form --confirm-fill`.",
            "- Upload rule: if separate CV and cover-letter upload fields exist, upload each PDF separately.",
            "- Upload rule: use a combined three-page application PDF only after confirming that no cover-letter upload exists, including a possible next form step.",
            "- JOIN/Arbeitnow rule: the first `/apply/cv` or first resume-upload step is CV-only; never use the combined PDF on that first upload.",
            "- Final autonomous submit requires a reviewed `job-agent approve` manifest and `job-agent apply-approved MANIFEST --execute`.",
            "- Login, CAPTCHA, payment, identity verification, or unexpected personal-data questions require manual handling.",
        ]
    )
    return "\n".join(lines) + "\n"
