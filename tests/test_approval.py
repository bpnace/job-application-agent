from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_application_agent.approval import apply_approved, approve_packages
from job_application_agent.cli import main
from job_application_agent.models import (
    ApplicationFormField,
    ApplicationRoute,
    CompanyFact,
    CompanyResearch,
    FormFillInstruction,
    FormFillPlan,
    JobListing,
)
from job_application_agent.utils import write_json


def _approved_package(tmp_path: Path) -> tuple[Path, FormFillPlan]:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    job_path = package_dir / "job.json"
    listing = JobListing(
        source="fixture",
        source_url="https://example.test/jobs/1",
        apply_url="https://example.test/apply/1",
        title="Frontend Engineer",
        company="Example GmbH",
    )
    plan = FormFillPlan(
        job_title=listing.title,
        company=listing.company,
        apply_url=listing.apply_url,
        route=ApplicationRoute(
            method="company_form",
            apply_url=listing.apply_url,
            platform="example.test",
            can_agent_fill=True,
        ),
        fields=[
            ApplicationFormField(
                label="Full name",
                selector="#name",
                classification="full_name",
                required=True,
            )
        ],
        instructions=[
            FormFillInstruction(
                field_label="Full name",
                selector="#name",
                classification="full_name",
                action="fill",
                value="Test Candidate",
                required=True,
                confidence="high",
            )
        ],
        submit_allowed=False,
    )
    write_json(job_path, listing)
    write_json(package_dir / "form_fill_plan.json", plan)
    write_json(
        package_dir / "company_research.json",
        CompanyResearch(
            company="Example",
            contact_name="Test Contact",
            facts=[
                CompanyFact(
                    claim="Example builds public test software for application fixtures.",
                    excerpt="Example builds public test software for application fixtures.",
                    source_url="https://example.test/about",
                    source_sha256="a" * 64,
                )
            ],
            source_urls=["https://example.test/about"],
            retrieved_at="2026-08-12T00:00:00Z",
        ),
    )
    return job_path, plan


def test_apply_approved_refuses_changed_listing_without_browser_call(tmp_path):
    job_path, _plan = _approved_package(tmp_path)
    manifest_path = approve_packages([job_path], output_path=tmp_path / "approval.json")
    job_path.write_text(job_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    calls: list[FormFillPlan] = []

    result = apply_approved(
        manifest_path,
        execute=True,
        browser_runner=lambda plan, **kwargs: calls.append(plan) or {},
    )

    assert calls == []
    assert result["results"][0]["status"] == "approval_mismatch"
    assert result["results"][0]["submit_attempted"] is False


def test_apply_approved_refuses_changed_form_plan_without_browser_call(tmp_path):
    job_path, _plan = _approved_package(tmp_path)
    manifest_path = approve_packages([job_path], output_path=tmp_path / "approval.json")
    plan_path = job_path.parent / "form_fill_plan.json"
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    calls: list[FormFillPlan] = []

    result = apply_approved(
        manifest_path,
        execute=True,
        browser_runner=lambda plan, **kwargs: calls.append(plan) or {},
    )

    assert calls == []
    assert result["results"][0]["status"] == "approval_mismatch"


def test_apply_approved_refuses_changed_upload_document_without_browser_call(tmp_path):
    job_path, plan = _approved_package(tmp_path)
    upload = job_path.parent / "cv.pdf"
    upload.write_bytes(b"initial document")
    upload_field = ApplicationFormField(
        label="CV",
        selector="#cv",
        classification="resume_upload",
        required=True,
    )
    upload_instruction = FormFillInstruction(
        field_label="CV",
        selector="#cv",
        classification="resume_upload",
        action="upload",
        file_path=str(upload),
        required=True,
        confidence="high",
    )
    write_json(
        job_path.parent / "form_fill_plan.json",
        plan.model_copy(
            update={
                "fields": [*plan.fields, upload_field],
                "instructions": [*plan.instructions, upload_instruction],
            }
        ),
    )
    manifest_path = approve_packages([job_path], output_path=tmp_path / "approval.json")
    upload.write_bytes(b"changed document")
    calls: list[FormFillPlan] = []

    result = apply_approved(
        manifest_path,
        execute=True,
        browser_runner=lambda plan, **kwargs: calls.append(plan) or {},
    )

    assert calls == []
    assert result["results"][0]["status"] == "approval_mismatch"


def test_apply_approved_records_only_browser_evidence_for_unchanged_item(tmp_path):
    job_path, _plan = _approved_package(tmp_path)
    manifest_path = approve_packages([job_path], output_path=tmp_path / "approval.json")
    tracker_path = tmp_path / "tracker.jsonl"

    result = apply_approved(
        manifest_path,
        execute=True,
        tracker_path=tracker_path,
        browser_runner=lambda plan, **kwargs: {
            "submit_requested": True,
            "application_status": "applied",
            "tracker_status": "applied",
            "status_reason": "Fixture success confirmation.",
        },
    )

    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert result["results"][0]["status"] == "applied"
    assert event["status"] == "applied"
    assert event["method"] == "approved_playwright_submit"
    assert event["approval_id"] == result["approval_id"]


def test_apply_approved_activates_submit_only_after_manifest_validation(tmp_path):
    job_path, _plan = _approved_package(tmp_path)
    manifest_path = approve_packages([job_path], output_path=tmp_path / "approval.json")
    observed: list[bool] = []

    apply_approved(
        manifest_path,
        execute=True,
        browser_runner=lambda plan, **kwargs: observed.append(plan.submit_allowed)
        or {"submit_requested": False, "application_status": "not_applied"},
    )

    assert observed == [True]


def test_approve_rejects_a_form_plan_that_carries_submit_authority(tmp_path):
    job_path, plan = _approved_package(tmp_path)
    write_json(job_path.parent / "form_fill_plan.json", plan.model_copy(update={"submit_allowed": True}))

    with pytest.raises(ValueError, match="must not carry submit authority"):
        approve_packages([job_path], output_path=tmp_path / "approval.json")


def test_approve_rejects_sensitive_fields_even_if_an_instruction_has_a_value(tmp_path):
    job_path, plan = _approved_package(tmp_path)
    sensitive = plan.instructions[0].model_copy(
        update={"field_label": "Salary", "classification": "salary", "value": "60000"}
    )
    write_json(job_path.parent / "form_fill_plan.json", plan.model_copy(update={"instructions": [sensitive]}))

    with pytest.raises(ValueError, match="Sensitive field requires manual review"):
        approve_packages([job_path], output_path=tmp_path / "approval.json")


def test_direct_submit_switch_is_refused_before_any_browser_work():
    with pytest.raises(SystemExit) as exc_info:
        main(["fill-form", "missing/job.json", "--confirm-fill", "--confirm-submit"])

    assert exc_info.value.code == 2
