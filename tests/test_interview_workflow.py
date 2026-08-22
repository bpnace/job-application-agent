import json

from job_application_agent.interview_workflow import (
    WORKFLOW_NAME,
    build_interview_scheduler_workflow,
    validate_interview_scheduler_workflow,
)


def test_interview_scheduler_workflow_is_inactive_and_execute_only():
    workflow = build_interview_scheduler_workflow()

    assert workflow["name"] == WORKFLOW_NAME
    assert workflow["active"] is False
    assert validate_interview_scheduler_workflow(workflow) == []
    node_types = {node["type"] for node in workflow["nodes"]}
    assert "n8n-nodes-base.executeWorkflowTrigger" in node_types
    assert not any("schedule" in node_type.lower() for node_type in node_types)
    assert not any("webhook" in node_type.lower() for node_type in node_types)


def test_workflow_has_no_send_or_telegram_nodes():
    workflow = build_interview_scheduler_workflow()
    serialized = json.dumps(workflow).lower()

    assert "telegram" not in {node["type"].lower() for node in workflow["nodes"]}
    assert "gmail" not in {node["type"].lower() for node in workflow["nodes"]}
    assert "emailsend" not in serialized
    assert "smtp" not in serialized
    assert "sendmail" not in serialized
    assert "/send" not in serialized
    assert "sendstatus" in serialized


def test_workflow_validation_rejects_activation_and_webhook():
    workflow = build_interview_scheduler_workflow(active=True)
    workflow["nodes"].append({"name": "Bad Webhook", "type": "n8n-nodes-base.webhook"})

    errors = validate_interview_scheduler_workflow(workflow)

    assert "workflow must be inactive" in errors
    assert any("blocked trigger node" in error for error in errors)
