import json
from pathlib import Path

from job_application_agent.outlook_status_workflow import (
    WORKFLOW_NAME,
    build_outlook_status_monitor_workflow,
    validate_outlook_status_monitor_workflow,
)


def test_outlook_status_workflow_is_persistent_webhook_and_outlook_only(tmp_path):
    workflow = build_outlook_status_monitor_workflow(
        repo_dir=tmp_path,
        active=True,
        webhook_path="job-agent-outlook-status-feed",
        auth_token="test-token",
    )
    serialized = json.dumps(workflow).lower()

    assert workflow["name"] == WORKFLOW_NAME
    assert workflow["active"] is True
    assert validate_outlook_status_monitor_workflow(workflow) == []
    assert "n8n-nodes-base.webhook" in serialized
    assert "authentication" in serialized
    assert "headerauth" in serialized
    assert "httpheaderauth" in serialized
    assert "test-token" not in serialized
    assert "build outlook folder requests" in serialized
    assert "read requested outlook messages" in serialized
    assert "job-agent-outlook-status-feed" in serialized
    assert "graph.microsoft.com/v1.0/me/mailfolders/${folder}/messages" in serialized
    assert "inbox" in serialized
    assert "sentitems" in serialized
    assert "junkemail" in serialized
    assert "archive" in serialized
    assert "deleteditems" in serialized
    assert "gmail" not in serialized
    assert "imap" not in serialized
    assert "smtp" not in serialized
    assert "emailsend" not in serialized


def test_outlook_status_workflow_returns_payloads_for_local_pull(tmp_path):
    workflow = build_outlook_status_monitor_workflow(
        repo_dir=tmp_path,
        webhook_path="job-agent-outlook-status-feed",
        auth_token="test-token",
    )
    serialized = json.dumps(workflow)

    assert "payloads" in serialized
    assert "scanned" in serialized
    assert "oldest" in serialized
    assert "hasMore" in serialized
    assert "scannedCounts" in serialized
    assert "sourceFolder === 'sentitems' ? firstRecipient : senderAddress" in serialized
    assert "scheduler_decision" in serialized
    assert "manual_review" in serialized
    assert "requestedFolders" in serialized
    assert "allowedFolders.includes(folder)" in serialized
    assert "={{ $json.url }}" in serialized
    assert "$filter=${field}%20gt%20" in serialized
    assert "timestampField(folder)" in serialized
    assert "mode === 'backfill'" in serialized
    assert "responseMode" in serialized
    assert "executeCommand" not in serialized
    assert "body_excerpt" not in serialized
    assert "meeting_url" not in serialized
    assert str(Path(tmp_path)) not in serialized


def test_outlook_status_workflow_validation_blocks_non_outlook_sources(tmp_path):
    workflow = build_outlook_status_monitor_workflow(repo_dir=tmp_path, auth_token="test-token")
    workflow["nodes"].append(
        {
            "name": "Bad Gmail",
            "type": "n8n-nodes-base.gmail",
            "parameters": {},
        }
    )

    errors = validate_outlook_status_monitor_workflow(workflow)

    assert any("non-outlook mail node" in error for error in errors)


def test_outlook_status_workflow_validation_requires_auth_gate(tmp_path):
    workflow = build_outlook_status_monitor_workflow(repo_dir=tmp_path, auth_token="test-token")
    webhook = next(node for node in workflow["nodes"] if node["name"] == "Outlook Status Webhook")
    webhook["parameters"].pop("authentication")
    webhook.pop("credentials")

    errors = validate_outlook_status_monitor_workflow(workflow)

    assert any("auth gate" in error for error in errors)


def test_outlook_status_workflow_validation_requires_folder_gate(tmp_path):
    workflow = build_outlook_status_monitor_workflow(repo_dir=tmp_path, auth_token="test-token")
    workflow["nodes"] = [
        node for node in workflow["nodes"] if node["name"] != "Build Outlook Folder Requests"
    ]

    errors = validate_outlook_status_monitor_workflow(workflow)

    assert any("request builder" in error for error in errors)


def test_outlook_status_workflow_validation_blocks_graph_writes_and_calendar(tmp_path):
    workflow = build_outlook_status_monitor_workflow(repo_dir=tmp_path, auth_token="test-token")
    workflow["nodes"].append(
        {
            "name": "Bad Calendar Write",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST",
                "url": "https://graph.microsoft.com/v1.0/me/events",
                "sendBody": True,
            },
        }
    )

    errors = validate_outlook_status_monitor_workflow(workflow)

    assert any("must use GET" in error for error in errors)
    assert any("must not send a body" in error for error in errors)
    assert any("outside allowed Outlook mail folders" in error for error in errors)
    assert any("calendar endpoint blocked" in error for error in errors)
