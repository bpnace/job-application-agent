from __future__ import annotations

import re
from typing import Any

from .models import FormFillPlan


# These are deliberately narrow. Other ATS platforms remain review-only until a
# portal-specific rule and fixture test have been added.
SUPPORTED_AUTONOMOUS_PORTALS = {"personio"}
REVIEW_ONLY_CLASSIFICATIONS = {
    "salary",
    "availability",
    "work_authorization",
    "relocation",
    "eeo",
    "screening_question",
    "consent",
}


def submission_blockers(plan: FormFillPlan, *, reviewed_artifact: bool = False) -> list[str]:
    """Return deterministic reasons why a plan may not submit autonomously."""
    route = plan.route
    if not route.can_agent_fill:
        return [f"Route {route.method} is not an autonomous public-form route."]
    if route.method == "ats_form" and route.platform not in SUPPORTED_AUTONOMOUS_PORTALS:
        return [
            f"ATS platform {route.platform or 'unknown'} has no explicit autonomous portal rule."
        ]
    if route.method not in {"ats_form", "company_form"}:
        return [f"Route {route.method} requires human completion."]
    if not plan.fields:
        return ["Form plan has no observed browser fields; inspect the live form first."]
    if reviewed_artifact and plan.submit_allowed:
        return [
            "Reviewed form plan must not carry submit authority; approval activates it only in memory."
        ]

    blockers: list[str] = []
    for field, instruction in zip(plan.fields, plan.instructions, strict=False):
        if field.requires_manual_review:
            blockers.append(f"Sensitive field requires manual review: {field.label}")
        if instruction.classification in REVIEW_ONLY_CLASSIFICATIONS:
            blockers.append(
                f"Sensitive field requires manual review: {instruction.field_label}"
            )
        if field.required and instruction.action in {"manual", "skip"}:
            blockers.append(f"Required field has no approved action: {field.label}")
        if field.required and not instruction.selector:
            blockers.append(f"Required field has no stable selector: {field.label}")
        if instruction.action == "upload" and not instruction.file_path:
            blockers.append(f"Upload path is missing: {field.label}")
    if len(plan.instructions) != len(plan.fields):
        blockers.append("Form plan fields and instructions do not match.")
    if plan.portal_steps and route.platform != "personio":
        blockers.append("Only Personio has an explicit multi-step portal rule.")
    for step in plan.portal_steps:
        if not step.continue_selector:
            blockers.append(f"Portal step has no explicit continue selector: {step.name}")
        for instruction in step.instructions:
            if instruction.classification in REVIEW_ONLY_CLASSIFICATIONS:
                blockers.append(
                    f"Sensitive portal-step field requires manual review: {step.name} / {instruction.field_label}"
                )
            if instruction.action in {"manual", "skip"}:
                blockers.append(
                    f"Portal step requires manual handling: {step.name} / {instruction.field_label}"
                )
            if instruction.required and not instruction.selector:
                blockers.append(
                    f"Portal step required field has no stable selector: {step.name} / {instruction.field_label}"
                )
            if instruction.action == "upload" and not instruction.file_path:
                blockers.append(
                    f"Portal step upload path is missing: {step.name} / {instruction.field_label}"
                )
    return list(dict.fromkeys(blockers))


def classify_portal_state(
    *, url: str, page_text: str, plan: FormFillPlan | None = None
) -> dict[str, str]:
    """Classify only hard stop conditions. Never attempt login or CAPTCHA handling."""
    text = f"{url} {page_text}".lower()
    if re.search(
        r"captcha|hcaptcha|recaptcha|turnstile|anti[- ]bot|robot check|"
        r"verify you are human|challenges\.cloudflare\.com|/cdn-cgi/challenge",
        text,
    ):
        return {
            "state": "blocked_captcha",
            "reason": "CAPTCHA or anti-bot challenge detected; no fields were filled or submitted.",
        }
    if re.search(
        r"\b(log in|login|sign in|anmelden|passwort|password|two.factor|mfa|one.time code)\b|"
        r"/(?:login|signin|sign-in|auth)(?:[/?#]|$)",
        text,
    ):
        return {
            "state": "needs_completion",
            "reason": "Login or MFA gate detected; the agent does not authenticate or continue.",
        }
    if plan is not None and plan.route.platform == "join":
        return {
            "state": "needs_completion",
            "reason": "JOIN is not an autonomous portal rule and requires human completion.",
        }
    return {"state": "ready", "reason": "Public form is reachable without login or CAPTCHA."}


def portal_evidence(state: dict[str, str]) -> dict[str, Any]:
    return {"portal_state": state.get("state", "unknown"), "portal_reason": state.get("reason", "")}
