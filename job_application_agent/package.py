from __future__ import annotations

import os
import shutil
from pathlib import Path

from .application import (
    build_form_fill_plan,
    format_application_route,
    format_form_fill_plan,
    infer_application_route,
)
from .company_research import research_is_approvable
from .cover_letter import draft_cover_letter
from .document_names import cover_letter_filename, final_job_folder_name
from .document_theme import resolve_document_theme
from .humanizer import (
    check_cover_letter_quality,
    format_cover_letter_quality,
    rewrite_cover_letter_for_humanizer,
)
from .models import (
    ApplicationPackage,
    CandidateProfile,
    CompanyResearch,
    JobListing,
    JobScorecard,
    RunReport,
)
from .reporting import format_skip_summary, format_source_health
from .renderer import (
    PdfRenderResult,
    render_cv_matched_cover_letter_pdf,
    render_cover_letter_html,
)
from .stagehand_bridge import write_stagehand_artifacts
from .config import default_agent_home
from .profile import configured_cv_pdf_path
from .utils import slugify, write_json


ANSCHREIBEN_DIR_ENV = "JOB_AGENT_ANSCHREIBEN_DIR"


def _render_cover_letter_draft(
    profile: CandidateProfile,
    listing: JobListing,
    cover_letter: str,
    draft_md_path: Path,
    draft_html_path: Path,
    draft_pdf_path: Path,
) -> PdfRenderResult:
    draft_md_path.write_text(cover_letter, encoding="utf-8")
    theme = resolve_document_theme()
    render_cover_letter_html(profile, listing, cover_letter, draft_html_path, theme=theme)
    pdf_result = render_cv_matched_cover_letter_pdf(
        profile, listing, cover_letter, draft_pdf_path, theme=theme
    )
    if pdf_result.renderer != "reportlab":
        _discard_cover_letter_drafts(draft_md_path, draft_html_path, draft_pdf_path)
        raise RuntimeError(
            "Cover-letter PDF renderer did not use ReportLab. "
            f"Renderer={pdf_result.renderer}. {pdf_result.message}"
        )
    return pdf_result


def _discard_cover_letter_drafts(*draft_paths: Path) -> None:
    for draft_path in draft_paths:
        draft_path.unlink(missing_ok=True)


def _has_mechanical_humanizer_failure(quality_issues: list[str]) -> bool:
    return any(
        issue.startswith("Private Humanizer policy matched:")
        or issue.startswith("Private Humanizer policy disallows colon")
        for issue in quality_issues
    )


def write_application_package(
    run_dir: Path,
    profile: CandidateProfile,
    listing: JobListing,
    scorecard: JobScorecard,
) -> ApplicationPackage:
    package_dir = run_dir / (
        f"{scorecard.score:03d}-{slugify(listing.company, 24)}-"
        f"{slugify(listing.title, 44)}-{scorecard.listing_key[-6:]}"
    )
    package_dir.mkdir(parents=True, exist_ok=True)

    job_path = package_dir / "job.json"
    scorecard_path = package_dir / "scorecard.md"
    cover_md_path = package_dir / "cover_letter.md"
    cover_html_path = package_dir / "cover_letter.html"
    cover_pdf_path = package_dir / "cover_letter.pdf"
    cover_quality_json_path = package_dir / "cover_letter_quality.json"
    cover_quality_md_path = package_dir / "cover_letter_quality.md"
    checklist_path = package_dir / "apply_checklist.md"
    application_route_path = package_dir / "application_route.md"
    form_fill_plan_json_path = package_dir / "form_fill_plan.json"
    form_fill_plan_md_path = package_dir / "form_fill_plan.md"
    stagehand_plan_json_path = package_dir / "stagehand_apply_plan.json"
    stagehand_preview_ts_path = package_dir / "stagehand_apply_preview.ts"

    write_json(job_path, listing)
    scorecard_path.write_text(format_scorecard(listing, scorecard), encoding="utf-8")
    write_json(package_dir / "scorecard.json", scorecard)
    original_cover_letter = draft_cover_letter(profile, listing, scorecard)
    cover_letter = original_cover_letter

    draft_md_path = package_dir / "_cover_letter.draft.md"
    draft_html_path = package_dir / "_cover_letter.draft.html"
    draft_pdf_path = package_dir / "_cover_letter.draft.pdf"
    pdf_result = _render_cover_letter_draft(
        profile,
        listing,
        cover_letter,
        draft_md_path,
        draft_html_path,
        draft_pdf_path,
    )
    quality = check_cover_letter_quality(
        cover_letter,
        profile,
        draft_pdf_path,
        listing=listing,
        artifact_filename=cover_letter_filename(profile, listing),
    )
    if (
        not quality.passed
        and quality.issues
        and _has_mechanical_humanizer_failure(quality.issues)
    ):
        repaired_cover_letter = rewrite_cover_letter_for_humanizer(cover_letter, profile)
        if repaired_cover_letter != cover_letter:
            (package_dir / "_cover_letter.pre_humanizer.md").write_text(
                cover_letter, encoding="utf-8"
            )
            initial_issues = list(quality.issues)
            cover_letter = repaired_cover_letter
            pdf_result = _render_cover_letter_draft(
                profile,
                listing,
                cover_letter,
                draft_md_path,
                draft_html_path,
                draft_pdf_path,
            )
            quality = check_cover_letter_quality(
                cover_letter,
                profile,
                draft_pdf_path,
                listing=listing,
                artifact_filename=cover_letter_filename(profile, listing),
            )
            quality.warnings.append(
                "Humanizer rewrite was applied after initial Humanizer gate failed."
            )
            quality.warnings.append(
                "Initial Humanizer issues before rewrite: " + "; ".join(initial_issues)
            )
    write_json(cover_quality_json_path, quality)
    cover_quality_md_path.write_text(
        format_cover_letter_quality(quality), encoding="utf-8"
    )
    if not quality.passed:
        _discard_cover_letter_drafts(draft_md_path, draft_html_path, draft_pdf_path)
        raise RuntimeError(
            f"Cover letter quality gate failed for {listing.company} - {listing.title}: {quality.issues}"
        )
    draft_md_path.replace(cover_md_path)
    draft_html_path.replace(cover_html_path)
    draft_pdf_path.replace(cover_pdf_path)
    mirror_final_documents(
        profile=profile,
        listing=listing,
        package_dir=package_dir,
        job_path=job_path,
        scorecard_path=scorecard_path,
        cover_md_path=cover_md_path,
        cover_pdf_path=cover_pdf_path,
    )

    route = infer_application_route(
        listing.apply_url or listing.source_url, source=listing.source
    )
    form_fill_plan = build_form_fill_plan(
        profile, listing, package_dir=package_dir, cover_letter_text=cover_letter
    )
    application_route_path.write_text(format_application_route(route), encoding="utf-8")
    write_json(form_fill_plan_json_path, form_fill_plan)
    form_fill_plan_md_path.write_text(
        format_form_fill_plan(form_fill_plan), encoding="utf-8"
    )
    stagehand_plan_json_path, stagehand_preview_ts_path = write_stagehand_artifacts(
        package_dir, form_fill_plan
    )
    checklist_path.write_text(format_checklist(listing), encoding="utf-8")

    return ApplicationPackage(
        listing_key=scorecard.listing_key,
        package_dir=str(package_dir),
        job_json_path=str(job_path),
        scorecard_path=str(scorecard_path),
        cover_letter_md_path=str(cover_md_path),
        cover_letter_html_path=str(cover_html_path),
        cover_letter_pdf_path=str(cover_pdf_path),
        pdf_renderer=pdf_result.renderer,
        pdf_renderer_message=pdf_result.message,
        cover_letter_quality_json_path=str(cover_quality_json_path),
        cover_letter_quality_md_path=str(cover_quality_md_path),
        checklist_path=str(checklist_path),
        application_route_path=str(application_route_path),
        form_fill_plan_json_path=str(form_fill_plan_json_path),
        form_fill_plan_md_path=str(form_fill_plan_md_path),
        stagehand_plan_json_path=str(stagehand_plan_json_path),
        stagehand_preview_ts_path=str(stagehand_preview_ts_path),
        application_method=listing.application_method,
        resume_upload=listing.resume_upload,
        apply_platform=listing.apply_platform,
        apply_url=listing.apply_url or listing.source_url,
    )


def refresh_application_package_from_research(
    package_dir: Path,
    profile: CandidateProfile,
    research: CompanyResearch,
) -> dict[str, str | bool]:
    """Write research-bound cover artifacts without changing the reviewed form plan.

    An incomplete research result is still saved for review, but it never
    replaces a cover letter or makes an item approvable.
    """
    package_dir = package_dir.expanduser().resolve()
    if not research_is_approvable(research):
        return {"cover_letter_refreshed": False, "reason": "research_incomplete"}
    job_path = package_dir / "job.json"
    scorecard_path = package_dir / "scorecard.json"
    if not job_path.is_file() or not scorecard_path.is_file():
        raise FileNotFoundError("Package requires job.json and scorecard.json before research can refresh the cover letter.")
    listing = JobListing.model_validate_json(job_path.read_text(encoding="utf-8"))
    scorecard = JobScorecard.model_validate_json(scorecard_path.read_text(encoding="utf-8"))
    cover_md_path = package_dir / "cover_letter.md"
    cover_html_path = package_dir / "cover_letter.html"
    cover_pdf_path = package_dir / "cover_letter.pdf"
    quality_json_path = package_dir / "cover_letter_quality.json"
    quality_md_path = package_dir / "cover_letter_quality.md"
    draft_md_path = package_dir / "_cover_letter.research.draft.md"
    draft_html_path = package_dir / "_cover_letter.research.draft.html"
    draft_pdf_path = package_dir / "_cover_letter.research.draft.pdf"
    cover_letter = draft_cover_letter(profile, listing, scorecard, research=research)
    pdf_result = _render_cover_letter_draft(
        profile, listing, cover_letter, draft_md_path, draft_html_path, draft_pdf_path
    )
    quality = check_cover_letter_quality(
        cover_letter,
        profile,
        draft_pdf_path,
        listing=listing,
        research=research,
        artifact_filename=cover_letter_filename(profile, listing),
    )
    if not quality.passed and _has_mechanical_humanizer_failure(quality.issues):
        repaired = rewrite_cover_letter_for_humanizer(cover_letter, profile)
        if repaired != cover_letter:
            cover_letter = repaired
            pdf_result = _render_cover_letter_draft(
                profile, listing, cover_letter, draft_md_path, draft_html_path, draft_pdf_path
            )
            quality = check_cover_letter_quality(
                cover_letter,
                profile,
                draft_pdf_path,
                listing=listing,
                research=research,
                artifact_filename=cover_letter_filename(profile, listing),
            )
    write_json(quality_json_path, quality)
    quality_md_path.write_text(format_cover_letter_quality(quality), encoding="utf-8")
    if not quality.passed:
        _discard_cover_letter_drafts(draft_md_path, draft_html_path, draft_pdf_path)
        raise RuntimeError("Research-bound cover letter did not pass quality checks: " + "; ".join(quality.issues))
    draft_md_path.replace(cover_md_path)
    draft_html_path.replace(cover_html_path)
    draft_pdf_path.replace(cover_pdf_path)
    mirror_final_documents(
        profile=profile,
        listing=listing,
        package_dir=package_dir,
        job_path=job_path,
        scorecard_path=package_dir / "scorecard.md",
        cover_md_path=cover_md_path,
        cover_pdf_path=cover_pdf_path,
    )
    return {"cover_letter_refreshed": True, "pdf_renderer": pdf_result.renderer}


def canonical_anschreiben_root() -> Path:
    configured = os.getenv(ANSCHREIBEN_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return default_agent_home() / "output" / "applications"


def mirror_final_documents(
    *,
    profile: CandidateProfile,
    listing: JobListing,
    package_dir: Path,
    job_path: Path,
    scorecard_path: Path,
    cover_md_path: Path,
    cover_pdf_path: Path,
) -> Path:
    target_dir = canonical_anschreiben_root() / final_job_folder_name(listing)
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cover_md_path, target_dir / "anschreiben_source.md")
    shutil.copy2(scorecard_path, target_dir / "job_info.md")
    shutil.copy2(job_path, target_dir / "job.json")
    shutil.copy2(cover_pdf_path, target_dir / cover_letter_filename(profile, listing))
    cv_path = configured_cv_pdf_path()
    if cv_path is not None:
        shutil.copy2(cv_path, target_dir / cv_path.name)
    (target_dir / "package_source.txt").write_text(
        f"{package_dir.resolve()}\n", encoding="utf-8"
    )
    return target_dir


def format_scorecard(listing: JobListing, scorecard: JobScorecard) -> str:
    strengths = (
        "\n".join(f"- {item}" for item in scorecard.matched_strengths)
        or "- No strong match found."
    )
    concerns = (
        "\n".join(f"- {item}" for item in scorecard.concerns)
        or "- No major concerns found."
    )
    breakdown = "\n".join(
        f"- {key}: {value}" for key, value in scorecard.score_breakdown.items()
    )
    return f"""# {listing.company} - {listing.title}

Score: {scorecard.score}
Recommendation: {scorecard.recommendation}
Source: {listing.apply_url or listing.source_url}
Application method: {listing.application_method}
Apply platform: {listing.apply_platform or "unknown"}
Resume upload: {listing.resume_upload}

## Strengths
{strengths}

## Concerns
{concerns}

## Score Breakdown
{breakdown}
"""


def format_checklist(listing: JobListing) -> str:
    return f"""# Manual Apply Checklist

- [ ] Open source listing: {listing.apply_url or listing.source_url}
- [ ] Application method: {listing.application_method}
- [ ] Apply platform: {listing.apply_platform or "unknown"}
- [ ] Resume upload: {listing.resume_upload}
- [ ] Note: {listing.application_method_note or "Application flow not classified yet."}
- [ ] Confirm role is still active.
- [ ] Read full job page manually.
- [ ] Identify whether the next step is a company form, jobboard form, email, login, or CV upload.
- [ ] Review `application_route.md` and `form_fill_plan.md`.
- [ ] Optional: review `stagehand_apply_plan.json` before any browser automation.
- [ ] Optional: use `stagehand_apply_preview.ts` only after checking all safe actions.
- [ ] Check salary/rate, location and contract details.
- [ ] Review `cover_letter.md` for factual accuracy.
- [ ] Confirm `cover_letter_quality.md` passed and `humanizer_loaded` is true.
- [ ] Confirm the salutation names the actual company or contact person from the job posting, never the portal/job board.
- [ ] Confirm the final cover-letter PDF filename uses the real job title plus the candidate's last name, for example `Product_Engineer_Candidate.pdf`.
- [ ] Attach current CV manually.
- [ ] Submit only after explicit human approval.
"""


def write_run_report(report: RunReport, output_path: Path) -> None:
    health = format_source_health(report.source_health)
    selected = "\n".join(
        f"- {item.score} {item.recommendation}: {item.listing_key}"
        for item in report.selected_jobs
    )
    routes = "\n".join(
        f"- {Path(package.package_dir).name}: {package.application_method}"
        + (f" via {package.apply_platform}" if package.apply_platform else "")
        + f", resume={package.resume_upload}, url={package.apply_url}"
        for package in report.packages
    )
    renderers = "\n".join(
        f"- {Path(package.package_dir).name}: {package.pdf_renderer}"
        + (
            f" ({package.pdf_renderer_message[:160]})"
            if package.pdf_renderer_message
            else ""
        )
        for package in report.packages
    )
    output_path.write_text(
        f"""# Job Application Agent Run

Run: {report.run_id}
Mode: {report.mode}
Top N: {report.top_n}
Max candidates: {report.max_candidates}
Output: {report.output_dir}

## Source Health
{health}

## Selected Jobs
{selected}

## Application Routes
{routes}

## PDF Rendering
{renderers}

{format_skip_summary(report.skipped_count, report.tracked_skipped_count)}
""",
        encoding="utf-8",
    )
