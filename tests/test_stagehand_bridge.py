from pathlib import Path

from job_application_agent.models import ApplicationRoute, FormFillInstruction, FormFillPlan
from job_application_agent.stagehand_bridge import build_stagehand_plan_payload, write_stagehand_artifacts


def _plan() -> FormFillPlan:
    return FormFillPlan(
        job_title="AI Automation Engineer",
        company="Example GmbH",
        apply_url="https://example.com/apply",
        route=ApplicationRoute(method="company_form", apply_url="https://example.com/apply", can_agent_fill=True),
        instructions=[
            FormFillInstruction(
                field_label="Email",
                selector="#email",
                classification="email",
                action="fill",
                value="candidate@example.com",
                confidence="high",
            ),
            FormFillInstruction(
                field_label="Resume",
                selector="#resume",
                classification="resume_upload",
                action="upload",
                file_path="/tmp/cv.pdf",
                confidence="medium",
            ),
        ],
        submit_allowed=False,
    )


def test_stagehand_payload_keeps_uploads_manual_and_blocks_submit():
    payload = build_stagehand_plan_payload(_plan())

    assert payload["submit_allowed"] is False
    assert len(payload["safe_actions"]) == 1
    assert payload["safe_actions"][0]["field_label"] == "Email"
    assert payload["manual_review"][0]["classification"] == "resume_upload"
    assert "click submit" in payload["blocked_actions"]


def test_write_stagehand_artifacts(tmp_path: Path):
    plan_path, preview_path = write_stagehand_artifacts(tmp_path, _plan())

    assert plan_path.exists()
    assert preview_path.exists()
    assert "Submit remains blocked" in preview_path.read_text(encoding="utf-8")
