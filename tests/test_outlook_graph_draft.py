import json

import httpx

from job_application_agent.outlook_graph_draft import (
    DEFAULT_GRAPH_CREDENTIAL_NAME,
    DEFAULT_GRAPH_CREDENTIAL_TYPE,
    GRAPH_BATCH_URL,
    GRAPH_MESSAGES_URL,
    _api_root_url,
    _cleanup_workflow,
    _N8nClient,
    _public_url_from_api_url,
    body_text_to_html,
    build_attachment_payload,
    build_graph_proxy_workflow,
)


class _FailingCleanupClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, endpoint, *, json=None):
        self.calls.append((method, endpoint))
        raise RuntimeError(f"{method} {endpoint} failed")


def test_body_text_to_html_escapes_and_preserves_line_breaks():
    html = body_text_to_html("Dear Marie,\nthanks & hello.\n\nBest\nCandidate")

    assert html == ("<p>Dear Marie,<br>thanks &amp; hello.</p>\n<p>Best<br>Candidate</p>")


def test_build_attachment_payload_uses_graph_file_attachment(tmp_path):
    attachment = tmp_path / "Test_Candidate_Lebenslauf.pdf"
    attachment.write_bytes(b"%PDF-1.4\n%%EOF\n")

    payload = build_attachment_payload(attachment)

    assert payload["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert payload["name"] == "Test_Candidate_Lebenslauf.pdf"
    assert payload["contentType"] == "application/pdf"
    assert payload["contentBytes"]


def test_graph_proxy_workflow_is_draft_only_and_credentialed():
    workflow = build_graph_proxy_workflow(
        webhook_path="codex-outlook-draft-test",
        secret="test-secret",
        credential_name=DEFAULT_GRAPH_CREDENTIAL_NAME,
        credential_type=DEFAULT_GRAPH_CREDENTIAL_TYPE,
        credential_id="cred-123",
    )
    serialized = json.dumps(workflow)
    graph_node = next(
        node for node in workflow["nodes"] if node["name"] == "Graph Request"
    )

    assert "/createReply" not in serialized
    assert "/attachments" not in serialized
    assert "/sendmail" in serialized
    assert "/send" in serialized
    assert "/reply" in serialized
    assert GRAPH_MESSAGES_URL not in serialized
    assert GRAPH_BATCH_URL in serialized
    assert graph_node["credentials"][DEFAULT_GRAPH_CREDENTIAL_TYPE] == {
        "id": "cred-123",
        "name": DEFAULT_GRAPH_CREDENTIAL_NAME,
    }
    assert workflow["settings"]["saveDataSuccessExecution"] == "none"
    assert workflow["settings"]["saveDataErrorExecution"] == "none"


def test_n8n_url_normalization_accepts_root_or_api_urls():
    assert _api_root_url("https://automation.example.com/api/v1") == (
        "https://automation.example.com"
    )
    assert _api_root_url("https://automation.example.com/api") == (
        "https://automation.example.com"
    )
    assert _public_url_from_api_url("https://automation.example.com/api/v1") == (
        "https://automation.example.com"
    )


def test_cleanup_workflow_reports_deactivate_and_delete_failures():
    client = _FailingCleanupClient()

    errors = _cleanup_workflow(client, "wf-123")

    assert client.calls == [
        ("POST", "/api/v1/workflows/wf-123/deactivate"),
        ("DELETE", "/api/v1/workflows/wf-123"),
    ]
    assert len(errors) == 2
    assert all("workflow_id=wf-123" in error for error in errors)


def test_credential_lookup_ignores_missing_data_list():
    def handler(_request):
        return httpx.Response(200, json={"data": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        n8n = _N8nClient(client, "https://automation.example.com/api/v1", "key")

        credential_id = n8n.find_credential_id(
            credential_name=DEFAULT_GRAPH_CREDENTIAL_NAME,
            credential_type=DEFAULT_GRAPH_CREDENTIAL_TYPE,
        )

    assert credential_id == ""
