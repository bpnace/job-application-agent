from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .browser_apply import fill_form_with_playwright
from .company_research import load_company_research, research_is_approvable
from .config import default_approvals_dir, default_tracker_path
from .models import FormFillPlan, JobListing
from .portal import submission_blockers
from .preflight import run_pre_application_check
from .profile import candidate_document_paths
from .tracker import record_status_event
from .utils import write_json


class ApprovalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_key: str
    package_dir: str
    job_json_path: str
    form_plan_path: str
    research_path: str
    document_paths: dict[str, str] = Field(default_factory=dict)
    fingerprints: dict[str, str]


class ApprovalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    approval_id: str
    created_at: str
    expires_at: str
    items: list[ApprovalItem]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_document_paths(plan: FormFillPlan, package_dir: Path) -> dict[str, Path]:
    documents: dict[str, Path] = {}
    cover_letter = package_dir / "cover_letter.pdf"
    if cover_letter.is_file():
        documents["cover_letter_pdf"] = cover_letter.resolve()
    for index, instruction in enumerate(plan.instructions):
        if instruction.action == "upload" and instruction.file_path:
            documents[f"upload_{index}_{instruction.classification}"] = Path(
                instruction.file_path
            ).expanduser().resolve()
    try:
        cv_pdf = candidate_document_paths()["cv_pdf"]
    except (FileNotFoundError, ValueError):
        cv_pdf = None
    if cv_pdf is not None and cv_pdf.is_file():
        documents.setdefault("candidate_cv_pdf", cv_pdf)
    return documents


def _read_package(job_json_path: Path) -> tuple[JobListing, FormFillPlan, Path]:
    job_path = job_json_path.expanduser().resolve()
    package_dir = job_path.parent
    plan_path = package_dir / "form_fill_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(
            f"Missing reviewed form plan: {plan_path}. Run `job-agent inspect-apply --browser --write` first."
        )
    listing = JobListing.model_validate_json(job_path.read_text(encoding="utf-8"))
    plan = FormFillPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    blockers = submission_blockers(plan, reviewed_artifact=True)
    if blockers:
        raise ValueError("Application cannot be approved for autonomous submit: " + "; ".join(blockers))
    return listing, plan, plan_path


def approve_packages(
    job_json_paths: list[Path],
    *,
    output_path: Path | None = None,
    expires_hours: int = 24,
) -> Path:
    if not job_json_paths:
        raise ValueError("At least one package job.json is required for approval.")
    if expires_hours <= 0 or expires_hours > 168:
        raise ValueError("expires_hours must be between 1 and 168.")
    now = datetime.now(UTC).replace(microsecond=0)
    items: list[ApprovalItem] = []
    for job_json_path in job_json_paths:
        listing, plan, plan_path = _read_package(job_json_path)
        job_path = job_json_path.expanduser().resolve()
        run_pre_application_check(job_path.parent, "approve")
        documents = _required_document_paths(plan, job_path.parent)
        research_path = job_path.parent / "company_research.json"
        if not research_path.is_file():
            raise FileNotFoundError(
                f"Missing public company research: {research_path}. Run `job-agent research-company {job_path} --write` and review its sources before approval."
            )
        research = load_company_research(research_path)
        if not research_is_approvable(research):
            raise ValueError(
                "Autonomous approval requires a verified company plus at least one source-backed public company fact."
            )
        missing = [str(path) for path in documents.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("Required approved document is missing: " + ", ".join(missing))
        fingerprints = {
            "job_json": sha256_file(job_path),
            "form_plan": sha256_file(plan_path),
            "company_research": sha256_file(research_path),
            **{name: sha256_file(path) for name, path in documents.items()},
        }
        items.append(
            ApprovalItem(
                listing_key=listing_key_for_manifest(listing),
                package_dir=str(job_path.parent),
                job_json_path=str(job_path),
                form_plan_path=str(plan_path),
                research_path=str(research_path),
                document_paths={name: str(path) for name, path in documents.items()},
                fingerprints=fingerprints,
            )
        )
    approval_id = uuid4().hex
    manifest = ApprovalManifest(
        approval_id=approval_id,
        created_at=now.isoformat().replace("+00:00", "Z"),
        expires_at=(now + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
        items=items,
    )
    target = (output_path or default_approvals_dir() / f"{approval_id}.json").expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"Approval manifest already exists: {target}")
    write_json(target, manifest)
    return target


def listing_key_for_manifest(listing: JobListing) -> str:
    return f"{listing.company}|{listing.title}|{listing.apply_url or listing.source_url}"


def _validate_item(item: ApprovalItem) -> tuple[JobListing | None, FormFillPlan | None, list[str]]:
    paths = {
        "job_json": Path(item.job_json_path).expanduser(),
        "form_plan": Path(item.form_plan_path).expanduser(),
        "company_research": Path(item.research_path).expanduser(),
        **{name: Path(path).expanduser() for name, path in item.document_paths.items()},
    }
    mismatches: list[str] = []
    for name, path in paths.items():
        expected = item.fingerprints.get(name, "")
        if not expected:
            mismatches.append(f"Missing approval fingerprint for {name}.")
        elif not path.is_file():
            mismatches.append(f"Approved file is missing: {path}")
        elif sha256_file(path) != expected:
            mismatches.append(f"Approved file changed: {path}")
    if mismatches:
        return None, None, mismatches
    try:
        listing = JobListing.model_validate_json(paths["job_json"].read_text(encoding="utf-8"))
        plan = FormFillPlan.model_validate_json(paths["form_plan"].read_text(encoding="utf-8"))
        research = load_company_research(paths["company_research"])
    except (ValueError, OSError) as exc:
        return None, None, [f"Approved artifact is no longer valid: {exc}"]
    if item.listing_key != listing_key_for_manifest(listing):
        return None, None, ["Listing identity changed after approval."]
    if not research_is_approvable(research):
        return None, None, ["Approved public company research no longer meets the approval requirement."]
    return listing, plan, submission_blockers(plan, reviewed_artifact=True)


def _manifest_is_expired(manifest: ApprovalManifest) -> bool:
    try:
        expiry = datetime.fromisoformat(manifest.expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(UTC) >= expiry.astimezone(UTC)


def apply_approved(
    manifest_path: Path,
    *,
    execute: bool,
    headed: bool = False,
    tracker_path: Path | None = None,
    browser_runner: Callable[..., dict[str, Any]] = fill_form_with_playwright,
) -> dict[str, Any]:
    if not execute:
        raise ValueError("Refusing to submit. Re-run with `--execute` after reviewing the approval manifest.")
    path = manifest_path.expanduser().resolve()
    manifest = ApprovalManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if _manifest_is_expired(manifest):
        raise ValueError("Approval manifest expired. Re-inspect the live form and approve a new shortlist.")
    results: list[dict[str, Any]] = []
    for item in manifest.items:
        listing, plan, blockers = _validate_item(item)
        if blockers or listing is None or plan is None:
            results.append(
                {
                    "listing_key": item.listing_key,
                    "status": "approval_mismatch",
                    "submit_attempted": False,
                    "reasons": blockers,
                }
            )
            continue
        # The reviewed artifact deliberately remains non-submitting on disk. A
        # valid approval manifest is the only authority that activates submit
        # in memory after its listing, plan, and documents have matched.
        approved_plan = plan.model_copy(update={"submit_allowed": True})
        browser_result = browser_runner(approved_plan, headless=not headed, submit=True)
        tracker_status = str(browser_result.get("tracker_status") or "")
        if tracker_status:
            event = record_status_event(
                listing,
                tracker_status,
                method="approved_playwright_submit",
                provenance="approved_shortlist_browser_evidence",
                evidence=browser_result,
                notes=[str(browser_result.get("status_reason") or "")],
                package_dir=Path(item.package_dir),
                path=tracker_path or default_tracker_path(),
                extra_fields={"approval_id": manifest.approval_id},
            )
            browser_result["tracker_event"] = {
                "status": event["status"],
                "status_at": event["status_at"],
            }
        results.append(
            {
                "listing_key": item.listing_key,
                "status": str(browser_result.get("application_status") or "unknown"),
                "submit_attempted": bool(browser_result.get("submit_requested")),
                "result": browser_result,
            }
        )
    return {"approval_id": manifest.approval_id, "results": results}


def approval_summary(path: Path) -> str:
    manifest = ApprovalManifest.model_validate_json(path.expanduser().read_text(encoding="utf-8"))
    return json.dumps(
        {
            "approval_id": manifest.approval_id,
            "expires_at": manifest.expires_at,
            "items": len(manifest.items),
        },
        ensure_ascii=False,
    )
