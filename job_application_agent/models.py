from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DIRECT_APPLYABLE_METHODS = {"ats_form", "company_form", "email"}


class JobListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_url: str
    title: str
    company: str
    location: str = ""
    remote_type: str = ""
    work_type: str = ""
    seniority: str = ""
    language: Literal["de", "en", "unknown"] = "unknown"
    description: str = ""
    compensation: str = ""
    tags: list[str] = Field(default_factory=list)
    date_posted: str = ""
    apply_url: str = ""
    application_method: str = "unknown"
    resume_upload: str = "unknown"
    apply_platform: str = ""
    application_method_note: str = ""
    raw_excerpt: str = ""
    fetched_at: str = ""


class JobScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_key: str
    score: int = Field(ge=0, le=100)
    recommendation: Literal["strong", "review", "adjacent", "weak", "exclude"]
    selected: bool
    matched_strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    exclusion_reason: str | None = None
    score_breakdown: dict[str, int] = Field(default_factory=dict)


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)
    hard_exclusions: list[str] = Field(default_factory=list)
    employer_blacklist: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    required_location_terms: list[str] = Field(default_factory=list)
    allow_unknown_location: bool = True
    max_listing_age_days: int = 21
    fresh_listing_boost_days: int = 7
    allow_unknown_date: bool = True
    target_roles: list[str] = Field(default_factory=list)
    # True for a user-created profile.  It intentionally avoids the old
    # repository-specific career heuristics and ranks only the configured role
    # and keyword criteria.
    profile_configured: bool = False


class ApplicationPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_key: str
    package_dir: str
    job_json_path: str
    scorecard_path: str
    cover_letter_md_path: str
    cover_letter_html_path: str
    cover_letter_pdf_path: str
    pdf_renderer: Literal["reportlab", "playwright", "fallback"]
    pdf_renderer_message: str = ""
    cover_letter_quality_json_path: str
    cover_letter_quality_md_path: str
    checklist_path: str
    application_route_path: str
    form_fill_plan_json_path: str
    form_fill_plan_md_path: str
    stagehand_plan_json_path: str = ""
    stagehand_preview_ts_path: str = ""
    application_method: str = ""
    resume_upload: str = ""
    apply_platform: str = ""
    apply_url: str = ""


class CoverLetterQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    humanizer_loaded: bool
    word_count: int
    paragraph_count: int
    page_count: int | None = None
    artifact_filename: str = ""
    recommended_filename: str = ""
    checks: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApplicationRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal[
        "ats_form",
        "company_form",
        "email",
        "external_form",
        "job_board_listing",
        "linkedin_job",
        "unknown",
    ]
    apply_url: str = ""
    resume_upload: Literal["likely", "possible", "unknown", "not_applicable"] = (
        "unknown"
    )
    platform: str = ""
    can_agent_fill: bool = False
    requires_human_approval: bool = True
    note: str = ""


class ApplicationFormField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = ""
    name: str = ""
    selector: str = ""
    field_type: str = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    classification: str = "unknown"
    placeholder: str = ""
    autocomplete: str = ""
    aria_label: str = ""
    visible: bool = True
    disabled: bool = False
    frame_url: str = ""
    group_name: str = ""
    requires_manual_review: bool = False


class FormFillInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_label: str
    selector: str = ""
    classification: str
    action: Literal["fill", "select", "check", "upload", "manual", "skip"]
    value: str = ""
    file_path: str = ""
    field_type: str = ""
    frame_url: str = ""
    required: bool = False
    confidence: Literal["high", "medium", "low"] = "low"
    safety_note: str = ""


class PortalFormStep(BaseModel):
    """A reviewed subsequent form step with an explicit navigation selector."""

    model_config = ConfigDict(extra="forbid")

    name: str
    continue_selector: str
    instructions: list[FormFillInstruction] = Field(default_factory=list)


class FormFillPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    company: str
    apply_url: str
    route: ApplicationRoute
    fields: list[ApplicationFormField] = Field(default_factory=list)
    instructions: list[FormFillInstruction] = Field(default_factory=list)
    portal_steps: list[PortalFormStep] = Field(default_factory=list)
    submit_allowed: bool = False
    generated_at: str = ""


class SourceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["available", "degraded", "disabled", "unavailable"]
    candidates_seen: int = 0
    candidates_returned: int = 0
    direct_applyable_returned: int = 0
    message: str = ""
    fetched_at: str = ""


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    mode: Literal["fixtures", "live"]
    top_n: int
    max_candidates: int
    source_health: list[SourceHealth] = Field(default_factory=list)
    selected_jobs: list[JobScorecard] = Field(default_factory=list)
    packages: list[ApplicationPackage] = Field(default_factory=list)
    skipped_count: int = 0
    tracked_skipped_count: int = 0
    open_manual_completion_count: int = 0
    direct_applyable_count: int = 0
    direct_applyable_by_method: dict[str, int] = Field(default_factory=dict)
    output_dir: str

    @model_validator(mode="after")
    def sync_direct_applyability_counts(self):
        counts: dict[str, int] = {}
        for package in self.packages:
            method = package.application_method
            if method in DIRECT_APPLYABLE_METHODS:
                counts[method] = counts.get(method, 0) + 1
        self.direct_applyable_by_method = counts
        self.direct_applyable_count = sum(counts.values())
        return self


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    listing: JobListing
    scorecard: JobScorecard


class SearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    mode: Literal["fixtures", "live"]
    top_n: int
    max_candidates: int
    source_health: list[SourceHealth] = Field(default_factory=list)
    results: list[SearchResult] = Field(default_factory=list)
    skipped_count: int = 0
    tracked_skipped_count: int = 0
    open_manual_completion_count: int = 0
    direct_applyable_count: int = 0
    direct_applyable_by_method: dict[str, int] = Field(default_factory=dict)
    output_dir: str
    results_json_path: str
    results_md_path: str

    @model_validator(mode="after")
    def sync_direct_applyability_counts(self):
        counts: dict[str, int] = {}
        for result in self.results:
            method = result.listing.application_method
            if method in DIRECT_APPLYABLE_METHODS:
                counts[method] = counts.get(method, 0) + 1
        self.direct_applyable_by_method = counts
        self.direct_applyable_count = sum(counts.values())
        return self


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    location: str
    phone: str = ""
    address: str = ""
    street_address: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    github: str
    linkedin: str
    summary: str
    core_skills: list[str]
    proof_points: list[str]
    cv_excerpt: str
    humanizer_excerpt: str
    humanizer_policy_path: str = ""
    humanizer_policy_sha256: str = ""
    humanizer_baseline_id: str = ""
    humanizer_baseline_sha256: str = ""
    standard_application_answers: dict[str, str] = Field(default_factory=dict)


class CompanyFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_url: str
    excerpt: str
    source_sha256: str


class CompanyResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    company: str
    contact_name: str = ""
    facts: list[CompanyFact] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    retrieved_at: str


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()
