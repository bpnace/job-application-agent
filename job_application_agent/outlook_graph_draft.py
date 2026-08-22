from __future__ import annotations

from dataclasses import dataclass
from html import escape
import base64
import mimetypes
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.parse import urlencode
import uuid

import httpx

from .config import default_agent_home


DEFAULT_N8N_ENV_PATH = default_agent_home() / "n8n.env"
DEFAULT_GRAPH_CREDENTIAL_TYPE = "microsoftOAuth2Api"
DEFAULT_GRAPH_CREDENTIAL_NAME = "Bewerbung Outlook"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
GRAPH_BATCH_URL = "https://graph.microsoft.com/v1.0/$batch"
MAX_SIMPLE_ATTACHMENT_BYTES = 3 * 1024 * 1024
BLOCKED_GRAPH_ACTIONS = ("/send", "/reply", "/forward", "/sendmail")


@dataclass(frozen=True)
class OutlookReplyDraftResult:
    status: str
    draft_id: str
    web_link: str
    subject: str
    is_draft: bool
    source_message_id: str
    attachments_added: int
    result_path: Path


def body_text_to_html(value: str) -> str:
    paragraphs = value.rstrip().split("\n\n")
    return "\n".join(
        f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
        if paragraph.strip()
    )


def build_attachment_payload(path: Path) -> dict[str, str]:
    expanded = path.expanduser().resolve()
    if not expanded.exists():
        raise FileNotFoundError(f"Attachment does not exist: {expanded}")
    size = expanded.stat().st_size
    if size > MAX_SIMPLE_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment is too large for simple Graph upload: {expanded} ({size} bytes)"
        )
    content_type, _encoding = mimetypes.guess_type(str(expanded))
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": expanded.name,
        "contentType": content_type or "application/octet-stream",
        "contentBytes": base64.b64encode(expanded.read_bytes()).decode("ascii"),
    }


def build_graph_proxy_workflow(
    *,
    webhook_path: str,
    secret: str,
    credential_name: str = DEFAULT_GRAPH_CREDENTIAL_NAME,
    credential_type: str = DEFAULT_GRAPH_CREDENTIAL_TYPE,
    credential_id: str = "",
    workflow_name: str = "Codex Outlook Graph Draft Bridge",
) -> dict[str, Any]:
    credential_payload: dict[str, str] = {"name": credential_name}
    if credential_id:
        credential_payload["id"] = credential_id

    validate_code = f"""
const input = $input.first().json || {{}};
const payload = input.body && typeof input.body === 'object' ? input.body : input;
if (String(payload.secret || '') !== {secret!r}) {{
  throw new Error('Unauthorized Graph draft bridge request');
}}
const method = String(payload.method || '').toUpperCase();
const url = String(payload.url || '');
if (!['GET', 'POST', 'PATCH'].includes(method)) {{
  throw new Error('Unsupported Graph method ' + method);
}}
const graphPrefix = 'https://graph.microsoft.com/v1.0';
if (!url.startsWith(graphPrefix + '/me/messages')) {{
  throw new Error('Graph URL outside /me/messages is blocked');
}}
const relativeUrl = url.slice(graphPrefix.length);
const path = relativeUrl.split('?')[0].toLowerCase();
for (const blocked of {list(BLOCKED_GRAPH_ACTIONS)!r}) {{
  if (path.endsWith(blocked)) {{
    throw new Error('Sending Graph action is blocked: ' + blocked);
  }}
}}
const batchRequest = {{
  id: '1',
  method,
  url: relativeUrl,
}};
if (method !== 'GET') {{
  batchRequest.headers = {{ 'Content-Type': 'application/json' }};
  batchRequest.body = payload.body || {{}};
}}
return [{{
  json: {{
    batchBody: {{ requests: [batchRequest] }},
    requestId: String(payload.requestId || ''),
  }}
}}];
""".strip()

    format_code = """
const request = $('Validate Graph Request').first().json || {};
const response = (($input.first().json || {}).responses || [])[0] || {};
if (Number(response.status || 0) >= 400) {
  throw new Error('Graph batch request failed: ' + JSON.stringify(response).slice(0, 900));
}
return [{
  json: {
    ok: true,
    requestId: request.requestId || '',
    status: response.status || 0,
    data: response.body || {},
  },
}];
""".strip()

    return {
        "name": workflow_name,
        "nodes": [
            {
                "parameters": {
                    "httpMethod": "POST",
                    "path": webhook_path,
                    "responseMode": "lastNode",
                    "responseData": "firstEntryJson",
                    "options": {"responseCode": 200},
                },
                "id": "outlook-graph-draft-webhook",
                "name": "Outlook Graph Draft Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-720, 0],
                "webhookId": webhook_path,
            },
            {
                "parameters": {"jsCode": validate_code},
                "id": "validate-graph-request",
                "name": "Validate Graph Request",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-460, 0],
            },
            {
                "parameters": {
                    "method": "POST",
                    "url": GRAPH_BATCH_URL,
                    "authentication": "predefinedCredentialType",
                    "nodeCredentialType": credential_type,
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": "={{ JSON.stringify($json.batchBody || {}) }}",
                    "options": {"timeout": 120000},
                },
                "id": "graph-request",
                "name": "Graph Request",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [-200, 0],
                "credentials": {credential_type: credential_payload},
            },
            {
                "parameters": {"jsCode": format_code},
                "id": "format-graph-response",
                "name": "Format Graph Response",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [60, 0],
            },
        ],
        "connections": {
            "Outlook Graph Draft Webhook": {
                "main": [
                    [{"node": "Validate Graph Request", "type": "main", "index": 0}]
                ]
            },
            "Validate Graph Request": {
                "main": [[{"node": "Graph Request", "type": "main", "index": 0}]]
            },
            "Graph Request": {
                "main": [
                    [{"node": "Format Graph Response", "type": "main", "index": 0}]
                ]
            },
        },
        "settings": {
            "executionOrder": "v1",
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "none",
            "availableInMCP": False,
        },
    }


def create_outlook_reply_draft_via_n8n(
    *,
    body_html: str,
    output_dir: Path,
    message_id: str = "",
    internet_message_id: str = "",
    attachments: list[Path] | None = None,
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
    n8n_api_url: str = "",
    n8n_api_key: str = "",
    n8n_public_url: str = "",
    credential_name: str = DEFAULT_GRAPH_CREDENTIAL_NAME,
    credential_type: str = DEFAULT_GRAPH_CREDENTIAL_TYPE,
    credential_id: str = "",
    timeout: float = 120.0,
) -> OutlookReplyDraftResult:
    if not message_id and not internet_message_id:
        raise ValueError("Pass either message_id or internet_message_id.")
    if message_id and internet_message_id:
        raise ValueError("Pass only one of message_id or internet_message_id.")
    if not body_html.strip():
        raise ValueError("Reply body must not be empty.")

    env = _load_env_file(n8n_env_path)
    api_url = n8n_api_url or os.getenv("N8N_API_URL") or env.get("N8N_API_URL", "")
    api_key = n8n_api_key or os.getenv("N8N_API_KEY") or env.get("N8N_API_KEY", "")
    public_url = (
        n8n_public_url
        or os.getenv("N8N_PUBLIC_URL")
        or env.get("N8N_PUBLIC_URL", "")
        or _public_url_from_api_url(api_url)
    )
    if not api_url or not api_key:
        raise ValueError("Missing N8N_API_URL or N8N_API_KEY.")
    if not public_url:
        raise ValueError("Missing n8n public URL for webhook execution.")

    attachment_payloads = [build_attachment_payload(path) for path in attachments or []]
    webhook_path = f"codex-outlook-draft-{uuid.uuid4()}"
    secret = secrets.token_urlsafe(32)
    workflow_id = ""
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()

    with httpx.Client(timeout=timeout) as client:
        n8n = _N8nClient(client, api_url, api_key)
        if not credential_id:
            credential_id = n8n.find_credential_id(
                credential_name=credential_name,
                credential_type=credential_type,
            )
        workflow = build_graph_proxy_workflow(
            webhook_path=webhook_path,
            secret=secret,
            credential_name=credential_name,
            credential_type=credential_type,
            credential_id=credential_id,
            workflow_name=f"Codex Outlook Graph Draft Bridge {started_at:.0f}",
        )
        try:
            created = n8n.request("POST", "/api/v1/workflows", json=workflow)
            workflow_id = str(created.get("id") or "")
            if not workflow_id:
                raise RuntimeError(f"n8n did not return a workflow id: {created}")
            n8n.request("POST", f"/api/v1/workflows/{workflow_id}/activate")
            webhook_url = f"{public_url.rstrip('/')}/webhook/{webhook_path}"
            graph = _GraphViaWebhook(client, webhook_url, secret, timeout=timeout)

            source_message_id = message_id or _resolve_internet_message_id(
                graph, internet_message_id
            )
            draft = graph.request(
                "POST",
                f"{GRAPH_MESSAGES_URL}/{source_message_id}/createReply",
                {},
            )
            draft_id = str(draft.get("id") or "")
            if not draft_id:
                raise RuntimeError(
                    f"Graph createReply did not return draft id: {draft}"
                )
            updated = graph.request(
                "PATCH",
                f"{GRAPH_MESSAGES_URL}/{draft_id}",
                {"body": {"contentType": "HTML", "content": body_html}},
            )
            for attachment in attachment_payloads:
                graph.request(
                    "POST",
                    f"{GRAPH_MESSAGES_URL}/{draft_id}/attachments",
                    attachment,
                )
        finally:
            cleanup_errors = _cleanup_workflow(n8n, workflow_id)

    result_path = output_dir / "outlook_reply_draft_NOT_SENT.json"
    result = {
        "status": "NOT_SENT",
        "send_allowed": False,
        "source_message_id": source_message_id,
        "draft_id": draft_id,
        "temporary_workflow_id": workflow_id,
        "cleanup_errors": cleanup_errors,
        "web_link": str(updated.get("webLink") or draft.get("webLink") or ""),
        "subject": str(updated.get("subject") or draft.get("subject") or ""),
        "is_draft": bool(updated.get("isDraft", draft.get("isDraft", True))),
        "attachments_added": len(attachment_payloads),
    }
    result_path.write_text(_json_dumps(result), encoding="utf-8")
    return OutlookReplyDraftResult(
        status=result["status"],
        draft_id=result["draft_id"],
        web_link=result["web_link"],
        subject=result["subject"],
        is_draft=result["is_draft"],
        source_message_id=result["source_message_id"],
        attachments_added=result["attachments_added"],
        result_path=result_path,
    )


def _cleanup_workflow(n8n: Any, workflow_id: str) -> list[str]:
    if not workflow_id:
        return []
    errors: list[str] = []
    for method, endpoint, action in (
        ("POST", f"/api/v1/workflows/{workflow_id}/deactivate", "deactivate"),
        ("DELETE", f"/api/v1/workflows/{workflow_id}", "delete"),
    ):
        try:
            n8n.request(method, endpoint)
        except Exception as exc:
            errors.append(
                f"workflow_id={workflow_id} cleanup_action={action} failed: {exc}"
            )
    return errors


class _N8nClient:
    def __init__(self, client: httpx.Client, api_url: str, api_key: str) -> None:
        self.client = client
        self.api_url = _api_root_url(api_url)
        self.headers = {
            "X-N8N-API-KEY": api_key,
            "Content-Type": "application/json",
        }

    def request(
        self, method: str, endpoint: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = self.client.request(
            method,
            _join_url(self.api_url, endpoint),
            headers=self.headers,
            json=json,
        )
        body = _response_json(response)
        if response.status_code >= 400:
            raise RuntimeError(
                f"n8n {method} {endpoint} failed: {response.status_code} {body}"
            )
        return body

    def find_credential_id(self, *, credential_name: str, credential_type: str) -> str:
        try:
            body = self.request("GET", "/api/v1/credentials")
        except Exception:
            return ""
        raw_candidates = body.get("data")
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        for credential in candidates:
            if not isinstance(credential, dict):
                continue
            if (
                str(credential.get("name") or "") == credential_name
                and str(credential.get("type") or "") == credential_type
            ):
                return str(credential.get("id") or "")
        for credential in candidates:
            if not isinstance(credential, dict):
                continue
            if str(credential.get("name") or "") == credential_name:
                return str(credential.get("id") or "")
        return ""


class _GraphViaWebhook:
    def __init__(
        self,
        client: httpx.Client,
        webhook_url: str,
        secret: str,
        *,
        timeout: float,
    ) -> None:
        self.client = client
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout = timeout

    def request(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = self.client.post(
            self.webhook_url,
            json={
                "secret": self.secret,
                "requestId": str(uuid.uuid4()),
                "method": method,
                "url": url,
                "body": body or {},
            },
            timeout=self.timeout,
        )
        payload = _response_json(response)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Graph bridge request failed: {response.status_code} {payload}"
            )
        if payload.get("ok") is False:
            raise RuntimeError(f"Graph bridge returned an error: {payload}")
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload


def _resolve_internet_message_id(
    graph: _GraphViaWebhook, internet_message_id: str
) -> str:
    candidates = [internet_message_id.strip()]
    if candidates[0] and not candidates[0].startswith("<"):
        candidates.append(f"<{candidates[0]}>")
    for candidate in candidates:
        query = urlencode(
            {
                "$top": "1",
                "$select": "id,subject,from,internetMessageId,webLink",
                "$filter": f"internetMessageId eq '{candidate.replace(chr(39), chr(39) * 2)}'",
            }
        )
        body = graph.request("GET", f"{GRAPH_MESSAGES_URL}?{query}")
        messages = body.get("value") if isinstance(body.get("value"), list) else []
        if messages:
            message_id = str(messages[0].get("id") or "")
            if message_id:
                return message_id
    raise RuntimeError(f"No Outlook message found for Message-ID {internet_message_id}")


def _load_env_file(path: Path) -> dict[str, str]:
    expanded = path.expanduser()
    if not expanded.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in expanded.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _public_url_from_api_url(api_url: str) -> str:
    value = api_url.rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _api_root_url(api_url: str) -> str:
    value = api_url.rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _join_url(base: str, endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"{base.rstrip('/')}/{endpoint.lstrip('/')}"


def _response_json(response: httpx.Response) -> dict[str, Any]:
    text = response.text
    if not text:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {"text": text}
    return payload if isinstance(payload, dict) else {"data": payload}


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
