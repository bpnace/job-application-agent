from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from .config import ROOT
from .outlook_graph_draft import (
    DEFAULT_GRAPH_CREDENTIAL_NAME,
    DEFAULT_GRAPH_CREDENTIAL_TYPE,
    DEFAULT_N8N_ENV_PATH,
    _N8nClient,
    _load_env_file,
)


WORKFLOW_NAME = "Bewerbung Outlook Status Monitor - Persistent"
DEFAULT_WEBHOOK_PATH = "job-agent-outlook-status-feed"
DEFAULT_HEADER_AUTH_CREDENTIAL_NAME = "Job Agent Outlook Status Header Auth"
HEADER_AUTH_CREDENTIAL_TYPE = "httpHeaderAuth"
HEADER_AUTH_HEADER_NAME = "X-Job-Agent-Token"
AUTH_TOKEN_PLACEHOLDER = "__JOB_AGENT_OUTLOOK_STATUS_TOKEN_DO_NOT_SERIALIZE__"
AUTH_TOKEN_ENV_KEYS = (
    "JOB_AGENT_OUTLOOK_STATUS_TOKEN",
    "N8N_OUTLOOK_STATUS_TOKEN",
    "OUTLOOK_STATUS_WEBHOOK_TOKEN",
)
DEFAULT_OUTLOOK_STATUS_FOLDERS = (
    "inbox",
    "sentitems",
    "junkemail",
    "archive",
    "deleteditems",
)
GRAPH_SELECT = (
    "id,internetMessageId,conversationId,subject,from,toRecipients,"
    "receivedDateTime,sentDateTime,bodyPreview"
)


REQUEST_BUILDER_JS = r"""
const body = (($json || {}).body || {});
const allowedFolders = ['inbox', 'sentitems', 'junkemail', 'archive', 'deleteditems'];
const rawFolders = Array.isArray(body.folders) ? body.folders : allowedFolders;
const requestedFolders = rawFolders
  .map((folder) => String(folder).toLowerCase().trim())
  .filter((folder, index, folders) => allowedFolders.includes(folder) && folders.indexOf(folder) === index);

if (!requestedFolders.length) {
  throw new Error('No allowed Outlook folders requested');
}

const cursor = body.cursor && typeof body.cursor === 'object' ? body.cursor : {};
const before = body.before && typeof body.before === 'object' ? body.before : {};
const top = Math.min(Math.max(Number(body.top || 50), 1), 500);
const mode = String(body.mode || 'incremental');

function timestampField(folder) {
  return folder === 'sentitems' ? 'sentDateTime' : 'receivedDateTime';
}

function graphUrl(folder) {
  const field = timestampField(folder);
  const filter = mode === 'backfill'
    ? (before[folder] ? `&$filter=${field}%20lt%20${encodeURIComponent(String(before[folder]))}` : '')
    : (cursor[folder] ? `&$filter=${field}%20gt%20${encodeURIComponent(String(cursor[folder]))}` : '');
  return `https://graph.microsoft.com/v1.0/me/mailFolders/${folder}/messages?$top=${top}&$orderby=${field} desc&$select=__GRAPH_SELECT__${filter}`;
}

return requestedFolders.map((folder) => ({
  json: {
    folder,
    timestampField: timestampField(folder),
    url: graphUrl(folder),
  },
}));
""".strip()


CLASSIFIER_JS = r"""
function itemsFor(nodeName) {
  try {
    return $items(nodeName);
  } catch (error) {
    return [];
  }
}
const requestItems = itemsFor('Build Outlook Folder Requests');
const responseItems = itemsFor('Read Requested Outlook Messages');
const folderSources = requestItems.map((item, index) => {
  const request = (item || {}).json || {};
  const response = ((responseItems[index] || {}).json || {});
  return [String(request.folder || ''), Array.isArray(response.value) ? response.value : [], response];
}).filter(([folder]) => folder);

function textOf(message) {
  return String((message.subject || '') + '\n' + (message.bodyPreview || '')).toLowerCase();
}
function sender(message) {
  return String(message.from && message.from.emailAddress && message.from.emailAddress.address || '');
}
function recipients(message) {
  const items = Array.isArray(message.toRecipients) ? message.toRecipients : [];
  return items.map((item) => String(item.emailAddress && item.emailAddress.address || '')).filter(Boolean);
}
function domainCompany(address) {
  const domain = String(address || '').split('@')[1] || '';
  const stem = domain.split('.')[0] || '';
  return stem ? stem.replace(/[-_]+/g, ' ') : '';
}
function companyFromSubject(subject) {
  const text = String(subject || '');
  const patterns = [
    /bewerbung\s+(?:bei|an)\s+([^|,\n]+)/i,
    /application\s+(?:at|to)\s+([^|,\n]+)/i,
    /bei\s+([^|,\n]+)$/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) return match[1].trim();
  }
  return '';
}
function titleFromSubject(subject) {
  const text = String(subject || '');
  const patterns = [
    /bewerbung\s+als\s+([^|,\n]+)/i,
    /application\s+for\s+([^|,\n]+)/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) return match[1].trim();
  }
  return '';
}
function classifyInbox(message) {
  const text = textOf(message);
  if (/(leider|absage|nicht weiter|unfortunately|regret|rejection|declined)/.test(text)) return 'rejection';
  if (/(interview|gespr[aä]ch|erstgespr[aä]ch|kennenlernen|termin|meeting|call|next step|naechster schritt|nächster schritt)/.test(text)) return 'response_received';
  if (/(unterlagen|dokument|lebenslauf|resume|cv|nachreichen|complete your application)/.test(text)) return 'needs_completion';
  return '';
}
function classifySent(message) {
  const text = textOf(message);
  if (/(bewerbung|application)\s+(als|for|bei|to|at)/.test(text)) return 'sent_application';
  return '';
}
function signals(message, classification) {
  const text = textOf(message);
  const excerpt = String(message.bodyPreview || '');
  const hasInterview = /(interview|gespr[aä]ch|erstgespr[aä]ch|kennenlernen|meeting|call|termin)/.test(text);
  const proposedTimes = excerpt.match(/\b(?:mo|di|mi|do|fr|sa|so|mon|tue|wed|thu|fri|sat|sun)[a-zä]*\.?,?\s+\d{1,2}[:.]\d{2}(?:\s*uhr)?|\b\d{1,2}\.\d{1,2}\.?(?:\d{2,4})?\s+(?:um\s+)?\d{1,2}[:.]\d{2}/gi) || [];
  const deadline = (excerpt.match(/(?:bis|by|deadline)\s+[^.!\n]{1,80}/i) || [''])[0];
  return {
    reply_category: hasInterview ? 'interview_or_scheduling' : classification,
    reply_type: classification === 'rejection' ? 'rejection' : hasInterview ? 'interview' : classification,
    action_required: /(bitte|please|complete|nachreichen|choose|select|antworten|reply|confirm)/.test(text) ? 'yes' : 'unknown',
    deadline,
    interview_detected: hasInterview ? 'yes' : 'no',
    interview_stage: /(erstgespr[aä]ch|intro|screening|recruiter)/.test(text) ? 'screening' : hasInterview ? 'interview' : '',
    proposed_times: proposedTimes.slice(0, 5).join(' | '),
    timezone: (excerpt.match(/\b(?:cet|cest|utc|gmt|mez|mesz)\b/i) || [''])[0],
    scheduler_decision: hasInterview ? 'manual_review' : '',
    scheduling_summary: hasInterview ? 'Outlook reply mentions interview or scheduling; no calendar event created.' : '',
  };
}
function buildPayload(message, sourceFolder, classification) {
  const subject = String(message.subject || '');
  const firstRecipient = recipients(message)[0] || '';
  const senderAddress = sender(message);
  const companyAddress = sourceFolder === 'sentitems' ? firstRecipient : senderAddress;
  const company = companyFromSubject(subject) || domainCompany(companyAddress);
  const title = titleFromSubject(subject);
  const payload = {
    classification,
    source_folder: sourceFolder,
    message_id: String(message.id || ''),
    internet_message_id: String(message.internetMessageId || ''),
    conversation_id: String(message.conversationId || ''),
    received_at: String(message.receivedDateTime || message.sentDateTime || ''),
    company,
    title,
  };
  Object.assign(payload, signals(message, classification));
  return payload;
}

const payloads = [];
const scanned = {};
const oldest = {};
const hasMore = {};
const scannedCounts = {};
for (const [folder, items, response] of folderSources) {
  const timestampKey = folder === 'sentitems' ? 'sentDateTime' : 'receivedDateTime';
  scannedCounts[folder] = items.length;
  hasMore[folder] = Boolean(response['@odata.nextLink']);
  for (const message of items) {
    const timestamp = String(message[timestampKey] || message.receivedDateTime || message.sentDateTime || '');
    if (timestamp && timestamp > String(scanned[folder] || '')) scanned[folder] = timestamp;
    if (timestamp && (!oldest[folder] || timestamp < String(oldest[folder]))) oldest[folder] = timestamp;
    const classification = folder === 'sentitems' ? classifySent(message) : classifyInbox(message);
    if (classification) payloads.push(buildPayload(message, folder, classification));
  }
}
return [{ json: { ok: true, payloads, scanned, oldest, hasMore, scannedCounts, folders: folderSources.map(([folder]) => folder) } }];
""".strip()


def _request_builder_js() -> str:
    return REQUEST_BUILDER_JS.replace("__GRAPH_SELECT__", GRAPH_SELECT)


def build_outlook_status_monitor_workflow(
    *,
    repo_dir: Path = ROOT,
    active: bool = False,
    webhook_path: str = DEFAULT_WEBHOOK_PATH,
    credential_name: str = DEFAULT_GRAPH_CREDENTIAL_NAME,
    credential_type: str = DEFAULT_GRAPH_CREDENTIAL_TYPE,
    credential_id: str = "",
    auth_credential_name: str = DEFAULT_HEADER_AUTH_CREDENTIAL_NAME,
    auth_credential_id: str = "",
    auth_token: str = "",
) -> dict[str, Any]:
    credentials = _credential_payload(
        credential_name=credential_name,
        credential_type=credential_type,
        credential_id=credential_id,
    )
    header_auth_credentials = _credential_payload(
        credential_name=auth_credential_name,
        credential_type=HEADER_AUTH_CREDENTIAL_TYPE,
        credential_id=auth_credential_id,
    )
    return {
        "name": WORKFLOW_NAME,
        "active": active,
        "nodes": [
            {
                "parameters": {
                    "httpMethod": "POST",
                    "path": webhook_path,
                    "authentication": "headerAuth",
                    "responseMode": "lastNode",
                    "responseData": "firstEntryJson",
                    "options": {"responseCode": 200},
                },
                "id": "outlook-status-webhook",
                "name": "Outlook Status Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-900, 0],
                "webhookId": webhook_path,
                "credentials": header_auth_credentials,
            },
            {
                "parameters": {"jsCode": _request_builder_js()},
                "id": "build-outlook-folder-requests",
                "name": "Build Outlook Folder Requests",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-760, 0],
            },
            _graph_request_node(
                name="Read Requested Outlook Messages",
                node_id="read-requested-outlook-messages",
                url="={{ $json.url }}",
                credential_type=credential_type,
                credentials=credentials,
                position=[-520, 0],
            ),
            {
                "parameters": {"jsCode": CLASSIFIER_JS},
                "id": "classify-outlook-application-mails",
                "name": "Classify Outlook Application Mails",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-280, 0],
            },
        ],
        "connections": {
            "Outlook Status Webhook": {
                "main": [
                    [
                        {
                            "node": "Build Outlook Folder Requests",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Build Outlook Folder Requests": {
                "main": [
                    [
                        {
                            "node": "Read Requested Outlook Messages",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Read Requested Outlook Messages": {
                "main": [
                    [
                        {
                            "node": "Classify Outlook Application Mails",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Classify Outlook Application Mails": {"main": [[]]},
        },
        "settings": {
            "executionOrder": "v1",
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "none",
            "availableInMCP": False,
        },
        "tags": [],
    }


def validate_outlook_status_monitor_workflow(workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    serialized = json.dumps(workflow).lower()
    nodes = workflow.get("nodes", [])
    allowed_graph_url = re.compile(
        r"graph\.microsoft\.com/v1\.0/me/mailfolders/"
        r"(inbox|sentitems|junkemail|archive|deleteditems)/messages"
    )
    webhook_nodes = [
        node
        for node in nodes
        if str(node.get("name") or "") == "Outlook Status Webhook"
    ]
    request_builder_nodes = [
        node
        for node in nodes
        if str(node.get("name") or "") == "Build Outlook Folder Requests"
    ]
    graph_request_nodes = []
    for node in nodes:
        node_type = str(node.get("type") or "").lower()
        node_name = str(node.get("name") or "")
        parameters = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
        if any(token in node_type for token in ("gmail", "imap", "smtp", "emailsend")):
            errors.append(f"non-outlook mail node blocked: {node_name} {node_type}")
        if "executecommand" in node_type:
            errors.append(f"local command node blocked for remote n8n: {node_name}")
        if node_type == "n8n-nodes-base.httprequest":
            graph_request_nodes.append(node)
            method = str(parameters.get("method") or "GET").upper()
            url = str(parameters.get("url") or "").lower()
            if method != "GET":
                errors.append(f"Graph node must use GET: {node_name}")
            if parameters.get("sendBody") is not False:
                errors.append(f"Graph node must not send a body: {node_name}")
            dynamic_requested_messages_url = (
                node_name == "Read Requested Outlook Messages"
                and url.strip() == "={{ $json.url }}"
            )
            if not dynamic_requested_messages_url and not allowed_graph_url.search(url):
                errors.append(f"Graph node URL is outside allowed Outlook mail folders: {node_name}")
            if any(
                blocked in url
                for blocked in (
                    "/events",
                    "/calendar",
                    "/sendmail",
                    "/reply",
                    "/forward",
                    "/attachments",
                    "/move",
                    "/copy",
                )
            ):
                errors.append(f"Graph write or calendar endpoint blocked: {node_name}")
    webhook: dict[str, Any] = webhook_nodes[0] if webhook_nodes else {}
    webhook_parameters = _dict_value(webhook.get("parameters"))
    webhook_credentials = _dict_value(webhook.get("credentials"))
    if webhook_parameters.get("authentication") != "headerAuth":
        errors.append("missing Outlook status header auth gate")
    if HEADER_AUTH_CREDENTIAL_TYPE not in webhook_credentials:
        errors.append("missing Outlook status header auth credential")
    if not request_builder_nodes:
        errors.append("missing Outlook folder request builder")
    else:
        builder_serialized = json.dumps(request_builder_nodes[0]).lower()
        for folder in DEFAULT_OUTLOOK_STATUS_FOLDERS:
            if folder not in builder_serialized:
                errors.append(f"missing allowed Outlook folder in request builder: {folder}")
        if "allowedfolders.includes(folder)" not in builder_serialized:
            errors.append("missing Outlook folder allow-list gate before Graph reads")
    if len(graph_request_nodes) != 1:
        errors.append("workflow must use exactly one request-scoped Graph node")
    if "graph.microsoft.com/v1.0/me/mailfolders/${folder}/messages" not in serialized:
        errors.append("missing dynamic Outlook mail folder Graph read")
    if "payloads" not in serialized:
        errors.append("missing response payload array")
    if "scanned" not in serialized:
        errors.append("missing scanned high-water cursor metadata")
    if "/send" in serialized or "sendmail" in serialized or "/reply" in serialized or "/forward" in serialized:
        errors.append("mail sending action is blocked")
    if AUTH_TOKEN_PLACEHOLDER.lower() in serialized:
        errors.append("workflow must not serialize Outlook status auth token placeholder")
    return errors


def deploy_workflow(
    workflow: dict[str, Any],
    *,
    activate: bool = False,
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
    auth_token: str = "",
    auth_credential_name: str = DEFAULT_HEADER_AUTH_CREDENTIAL_NAME,
    timeout: float = 120.0,
) -> dict[str, Any]:
    errors = validate_outlook_status_monitor_workflow(workflow)
    if errors:
        raise ValueError(f"workflow failed safety validation: {errors}")
    if AUTH_TOKEN_PLACEHOLDER in json.dumps(workflow):
        raise ValueError("workflow auth token placeholder cannot be deployed")
    env = _load_env_file(n8n_env_path)
    api_url = os.getenv("N8N_API_URL") or env.get("N8N_API_URL", "")
    api_key = os.getenv("N8N_API_KEY") or env.get("N8N_API_KEY", "")
    if not api_url or not api_key:
        raise ValueError("Missing N8N_API_URL or N8N_API_KEY")
    with httpx.Client(timeout=timeout) as client:
        n8n = _N8nClient(client, api_url, api_key)
        if auth_token:
            credential_id, credential_name = _ensure_header_auth_credential(
                n8n,
                auth_token=auth_token,
                credential_name=auth_credential_name,
            )
            _set_header_auth_credential(
                workflow,
                credential_id=credential_id,
                credential_name=credential_name,
            )
        elif not _webhook_header_auth_credential_id(workflow):
            raise ValueError("deploy requires an Outlook status header auth credential id")
        errors = validate_outlook_status_monitor_workflow(workflow)
        if errors:
            raise ValueError(f"workflow failed safety validation after credential setup: {errors}")
        payload = {
            "name": workflow["name"],
            "nodes": workflow["nodes"],
            "connections": workflow["connections"],
            "settings": workflow["settings"],
        }
        existing = _find_workflow_by_name(n8n, str(workflow["name"]))
        if existing:
            workflow_id = str(existing["id"])
            saved = n8n.request("PUT", f"/api/v1/workflows/{workflow_id}", json=payload)
            action = "updated"
        else:
            saved = n8n.request("POST", "/api/v1/workflows", json=payload)
            workflow_id = str(saved.get("id") or "")
            action = "created"
        if activate and workflow_id:
            saved = n8n.request("POST", f"/api/v1/workflows/{workflow_id}/activate")
        return {"action": action, "workflow": saved}


def _graph_request_node(
    *,
    name: str,
    node_id: str,
    url: str,
    credential_type: str,
    credentials: dict[str, dict[str, str]],
    position: list[int],
) -> dict[str, Any]:
    return {
        "parameters": {
            "method": "GET",
            "url": url,
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": credential_type,
            "sendBody": False,
            "options": {"timeout": 120000},
        },
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.4,
        "position": position,
        "credentials": credentials,
    }


def _credential_payload(
    *,
    credential_name: str,
    credential_type: str,
    credential_id: str = "",
) -> dict[str, dict[str, str]]:
    credential: dict[str, str] = {"name": credential_name}
    if credential_id:
        credential["id"] = credential_id
    return {credential_type: credential}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _header_auth_credential_name(base_name: str, auth_token: str) -> str:
    digest = hashlib.sha256(auth_token.encode("utf-8")).hexdigest()[:8]
    return f"{base_name} {digest}"


def _ensure_header_auth_credential(
    n8n: _N8nClient,
    *,
    auth_token: str,
    credential_name: str,
) -> tuple[str, str]:
    token = auth_token.strip()
    if not token:
        raise ValueError("Missing Outlook status webhook auth token")
    resolved_name = _header_auth_credential_name(credential_name, token)
    existing_id = _find_credential_id(
        n8n,
        credential_name=resolved_name,
        credential_type=HEADER_AUTH_CREDENTIAL_TYPE,
    )
    if existing_id:
        return existing_id, resolved_name
    created = n8n.request(
        "POST",
        "/api/v1/credentials",
        json={
            "name": resolved_name,
            "type": HEADER_AUTH_CREDENTIAL_TYPE,
            "data": {
                "name": HEADER_AUTH_HEADER_NAME,
                "value": token,
                "allowedHttpRequestDomains": "none",
            },
        },
    )
    credential_id = str(created.get("id") or "")
    if not credential_id:
        raise ValueError("n8n did not return an id for the Outlook status auth credential")
    return credential_id, resolved_name


def _find_credential_id(
    n8n: _N8nClient,
    *,
    credential_name: str,
    credential_type: str,
) -> str:
    body = n8n.request("GET", "/api/v1/credentials")
    candidates = body.get("data")
    for credential in candidates if isinstance(candidates, list) else []:
        if not isinstance(credential, dict):
            continue
        if (
            str(credential.get("name") or "") == credential_name
            and str(credential.get("type") or "") == credential_type
        ):
            return str(credential.get("id") or "")
    return ""


def _webhook_header_auth_credential_id(workflow: dict[str, Any]) -> str:
    for node in workflow.get("nodes", []):
        if str(node.get("name") or "") != "Outlook Status Webhook":
            continue
        credentials = node.get("credentials") if isinstance(node.get("credentials"), dict) else {}
        header_auth = credentials.get(HEADER_AUTH_CREDENTIAL_TYPE)
        if isinstance(header_auth, dict):
            return str(header_auth.get("id") or "")
    return ""


def _set_header_auth_credential(
    workflow: dict[str, Any],
    *,
    credential_id: str,
    credential_name: str,
) -> None:
    for node in workflow.get("nodes", []):
        if str(node.get("name") or "") != "Outlook Status Webhook":
            continue
        node["credentials"] = _credential_payload(
            credential_name=credential_name,
            credential_type=HEADER_AUTH_CREDENTIAL_TYPE,
            credential_id=credential_id,
        )
        return
    raise ValueError("Outlook Status Webhook node not found")


def _find_workflow_by_name(n8n: _N8nClient, name: str) -> dict[str, Any] | None:
    body = n8n.request("GET", "/api/v1/workflows")
    for workflow in body.get("data", []):
        if isinstance(workflow, dict) and str(workflow.get("name") or "") == name:
            return workflow
    return None


def _resolve_auth_token(
    *,
    explicit_token: str = "",
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
) -> str:
    if explicit_token.strip():
        return explicit_token.strip()
    env = _load_env_file(n8n_env_path)
    for key in AUTH_TOKEN_ENV_KEYS:
        value = os.getenv(key) or env.get(key, "")
        if value.strip():
            return value.strip()
    raise ValueError(
        "Missing Outlook status webhook token. Set JOB_AGENT_OUTLOOK_STATUS_TOKEN "
        "in the environment or n8n env file."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=None)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--repo-dir", type=Path, default=ROOT)
    parser.add_argument("--webhook-path", default=DEFAULT_WEBHOOK_PATH)
    parser.add_argument("--n8n-env", type=Path, default=DEFAULT_N8N_ENV_PATH)
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--auth-credential-name", default=DEFAULT_HEADER_AUTH_CREDENTIAL_NAME)
    args = parser.parse_args(argv)
    workflow = build_outlook_status_monitor_workflow(
        repo_dir=args.repo_dir,
        active=args.activate,
        webhook_path=args.webhook_path,
        auth_credential_name=args.auth_credential_name,
    )
    errors = validate_outlook_status_monitor_workflow(workflow)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(
            json.dumps(workflow, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"export={args.export}")
    if args.deploy:
        auth_token = _resolve_auth_token(
            explicit_token=args.auth_token,
            n8n_env_path=args.n8n_env,
        )
        result = deploy_workflow(
            workflow,
            activate=args.activate,
            n8n_env_path=args.n8n_env,
            auth_token=auth_token,
            auth_credential_name=args.auth_credential_name,
        )
        deployed = result["workflow"]
        print(f"action={result['action']}")
        print(f"workflow_id={deployed.get('id')}")
        print(f"active={str(bool(deployed.get('active'))).lower()}")
        print(f"name={deployed.get('name')}")
    if not args.export and not args.deploy:
        print(json.dumps(workflow, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
