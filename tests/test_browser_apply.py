from job_application_agent.browser_apply import (
    _join_completion_guard,
    evaluate_submit_evidence,
    fill_form_with_playwright,
)
from job_application_agent.models import (
    ApplicationFormField,
    ApplicationRoute,
    FormFillInstruction,
    FormFillPlan,
    PortalFormStep,
)
from job_application_agent.portal import classify_portal_state
from pathlib import Path
from urllib.parse import quote

import pytest


def test_join_completion_guard_marks_arbeitnow_as_not_final():
    plan = FormFillPlan(
        job_title="Full-Stack Software Engineer | AI Accelerated | (m/w/d)",
        company="Merlin Digital Solutions GmbH",
        apply_url="https://www.arbeitnow.com/jobs/companies/merlin-digital-solutions-gmbh/full-stack-software-engineer-ai-accelerated-munich-396494",
        route=ApplicationRoute(
            method="job_board_listing",
            apply_url="https://www.arbeitnow.com/jobs/companies/merlin-digital-solutions-gmbh/full-stack-software-engineer-ai-accelerated-munich-396494",
            platform="arbeitnow",
        ),
    )

    guard = _join_completion_guard(plan)

    assert guard["join_completion_status"] == "final_confirmation_required"
    assert "first click" in guard["join_completion_note"]
    assert "needs_completion" in guard["join_completion_note"]


def test_join_completion_guard_ignores_non_join_routes():
    plan = FormFillPlan(
        job_title="Frontend Engineer",
        company="Example GmbH",
        apply_url="https://example.com/apply",
        route=ApplicationRoute(
            method="company_form",
            apply_url="https://example.com/apply",
            platform="company",
        ),
    )

    assert _join_completion_guard(plan) == {}


def test_login_and_captcha_are_hard_portal_stops():
    assert classify_portal_state(
        url="https://jobs.example/login", page_text="Sign in with password"
    )["state"] == "needs_completion"
    assert classify_portal_state(
        url="https://jobs.example/apply", page_text="Please complete the reCAPTCHA"
    )["state"] == "blocked_captcha"


@pytest.mark.parametrize(
    ("gate_text", "expected_state", "expected_status"),
    [
        ("Sign in with your password", "needs_completion", "needs_completion"),
        ("Please complete the reCAPTCHA", "blocked_captcha", "blocked_captcha"),
    ],
)
def test_browser_gate_does_not_fill_or_submit(
    gate_text, expected_state, expected_status
):
    html = f"<form><input id='name'><button type='submit'>Submit</button></form><p>{gate_text}</p>"
    apply_url = f"data:text/html,{quote(html)}"
    plan = FormFillPlan(
        job_title="Fixture role",
        company="Fixture company",
        apply_url=apply_url,
        route=ApplicationRoute(
            method="company_form",
            apply_url=apply_url,
            platform="fixture",
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
        submit_allowed=True,
    )

    result = fill_form_with_playwright(plan, headless=True, submit=True)

    assert result["portal_state"] == expected_state
    assert result["application_status"] == expected_status
    assert result["submit"] == "blocked_portal_gate"
    assert result["results"] == []


def test_local_browser_fixture_captures_final_submit_evidence():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "browser_application_form.html"
    plan = FormFillPlan(
        job_title="Fixture role",
        company="Fixture company",
        apply_url=fixture.as_uri(),
        route=ApplicationRoute(
            method="company_form",
            apply_url=fixture.as_uri(),
            platform="fixture",
            can_agent_fill=True,
        ),
        fields=[
            ApplicationFormField(label="Full name", selector="#name", classification="full_name", required=True),
            ApplicationFormField(label="Email", selector="#email", classification="email", field_type="email", required=True),
        ],
        instructions=[
            FormFillInstruction(field_label="Full name", selector="#name", classification="full_name", action="fill", value="Test Candidate", required=True, confidence="high"),
            FormFillInstruction(field_label="Email", selector="#email", classification="email", action="fill", value="candidate@example.test", required=True, confidence="high"),
        ],
        submit_allowed=True,
    )

    result = fill_form_with_playwright(plan, headless=True, submit=True)

    assert result["application_status"] == "applied"
    assert result["submit_evidence_level"] == "final_confirmation"


def test_personio_explicit_multistep_fixture_submits_only_after_final_evidence():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "personio_multistep_form.html"
    plan = FormFillPlan(
        job_title="Fixture role",
        company="Fixture company",
        apply_url=fixture.as_uri(),
        route=ApplicationRoute(
            method="ats_form",
            apply_url=fixture.as_uri(),
            platform="personio",
            can_agent_fill=True,
        ),
        fields=[ApplicationFormField(label="Full name", selector="#name", classification="full_name", required=True)],
        instructions=[FormFillInstruction(field_label="Full name", selector="#name", classification="full_name", action="fill", value="Test Candidate", required=True, confidence="high")],
        portal_steps=[
            PortalFormStep(
                name="contact",
                continue_selector="#next",
                instructions=[FormFillInstruction(field_label="Email", selector="#email", classification="email", action="fill", value="candidate@example.test", required=True, confidence="high")],
            )
        ],
        submit_allowed=True,
    )

    result = fill_form_with_playwright(plan, headless=True, submit=True)

    assert any(item["action"] == "continue" and item["status"] == "continued" for item in result["results"])
    assert result["application_status"] == "applied"


def test_submit_click_without_final_join_confirmation_is_not_applied_proof():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "join_completion_status": "final_confirmation_required",
            "final_url": "https://join.com/apply/cv",
            "page_text_excerpt": "Confirm your CV and click Weiter",
            "validation": [],
            "responses": [
                {
                    "method": "POST",
                    "status": 200,
                    "url": "https://join.com/api/apply",
                }
            ],
        }
    )

    assert evidence["application_status"] == "needs_completion"
    assert evidence["tracker_status"] == "needs_completion"
    assert evidence["submit_evidence_level"] == "intermediate"


def test_submit_success_text_is_applied_proof_for_standard_form():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://careers.example/apply/thanks",
            "page_text_excerpt": "Thank you for applying. Your application was submitted.",
            "validation": [],
            "responses": [],
        }
    )

    assert evidence["application_status"] == "applied"
    assert evidence["tracker_status"] == "applied"
    assert evidence["submit_evidence_level"] == "final_confirmation"


def test_submit_success_response_with_validation_is_not_applied_proof():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://jobs.example/apply",
            "page_text_excerpt": "Please complete required fields.",
            "validation": [
                {
                    "label": "Salary expectation",
                    "message": "Please fill out this field.",
                }
            ],
            "responses": [
                {
                    "method": "POST",
                    "status": 200,
                    "url": "https://jobs.example/application",
                }
            ],
        }
    )

    assert evidence["application_status"] == "needs_completion"
    assert evidence["tracker_status"] == "needs_completion"
    assert evidence["submit_evidence_level"] == "validation_blocked"


def test_submit_success_response_ignores_analytics_upload_and_challenge_posts():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://apply.workable.com/example/apply/",
            "page_text_excerpt": "Submit application",
            "validation": [],
            "responses": [
                {
                    "method": "POST",
                    "status": 204,
                    "url": "https://workable-application-form.s3.us-east-1.amazonaws.com/",
                },
                {
                    "method": "POST",
                    "status": 200,
                    "url": "https://region1.analytics.google.com/measurement/conversion?url=https%3A%2F%2Fapply.workable.com%2Fexample%2Fapply%2F",
                },
                {
                    "method": "POST",
                    "status": 200,
                    "url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/flow/example",
                },
            ],
        }
    )

    assert evidence["application_status"] == "blocked_captcha"
    assert evidence["tracker_status"] == "needs_completion"
    assert evidence["submit_evidence_level"] == "captcha_blocked"


def test_submit_success_response_requires_application_like_post():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://careers.example/apply",
            "page_text_excerpt": "Submit application",
            "validation": [],
            "responses": [
                {
                    "method": "POST",
                    "status": 201,
                    "url": "https://career-pages-api.personio.de/api/v1/jobs/2516869/application?companyId=11537",
                }
            ],
        }
    )

    assert evidence["application_status"] == "applied"
    assert evidence["tracker_status"] == "applied"
    assert evidence["submit_evidence_level"] == "post_success"


def test_application_error_response_is_not_applied_proof():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://careers.example/apply",
            "page_text_excerpt": "Error submitting application",
            "validation": [],
            "responses": [
                {
                    "method": "POST",
                    "status": 400,
                    "url": "https://career-pages-api.personio.de/api/v1/jobs/2628493/application?companyId=12783",
                }
            ],
        }
    )

    assert evidence["application_status"] == "needs_completion"
    assert evidence["tracker_status"] == "needs_completion"
    assert evidence["submit_evidence_level"] == "post_error"


def test_submit_captcha_blocks_tracker_applied_status():
    evidence = evaluate_submit_evidence(
        {
            "submit_requested": True,
            "submit_allowed": True,
            "submit": "clicked",
            "final_url": "https://jobs.example/apply",
            "page_text_excerpt": "Please complete the reCAPTCHA challenge.",
            "validation": [],
            "responses": [
                {
                    "method": "POST",
                    "status": 428,
                    "url": "https://jobs.example/apply",
                }
            ],
        }
    )

    assert evidence["application_status"] == "blocked_captcha"
    assert evidence["tracker_status"] == "needs_completion"
