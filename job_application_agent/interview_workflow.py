from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .outlook_graph_draft import DEFAULT_N8N_ENV_PATH, _N8nClient, _load_env_file


WORKFLOW_NAME = "Bewerbung Interview Scheduler - Inactive"


SCHEDULER_JS = r"""
const input = $input.first().json || {};
if (input.autoSend === true || String(input.autoSend || '').toLowerCase() === 'true') {
  throw new Error('autoSend=true is blocked for Bewerbung Interview Scheduler v1');
}
const mode = String(input.mode || 'dry_run');
if (!['dry_run', 'draft_only', 'calendar_write'].includes(mode)) {
  throw new Error('Unsupported mode: ' + mode);
}
const message = input.message || {};
const text = String((message.subject || '') + '\n' + (message.body || message.normalizedText || '')).toLowerCase();
const registry = Array.isArray(input.calendarRegistry) ? input.calendarRegistry : [];
const busy = Array.isArray(input.busy) ? input.busy : [];
const leakCanaries = Array.isArray(input.leakCanaries) ? input.leakCanaries.map(String) : [];
function classify() {
  if (/(leider|absage|rejection|unfortunately|nicht weiter)/.test(text)) return 'rejection';
  if (/(lebenslauf|resume|cv|unterlagen|dokument)/.test(text) && !/(interview|gespr|termin|meeting|call)/.test(text)) return 'docs_request';
  if (/(interview|gespr[aä]ch|erstgespr[aä]ch|kennenlernen|termin|meeting|call)/.test(text)) return 'interview_offer';
  return 'generic_reply';
}
function targetMapped() {
  return registry.some((entry) => String(entry.alias || '').toLowerCase() === 'geschaeftlich' && entry.calendarId && entry.isTarget);
}
function redactBusy() {
  return busy.map((entry) => ({
    start: String(entry.start || ''),
    end: String(entry.end || ''),
    busy: true,
    sourceAliasRedacted: 'blocking-calendar',
  })).filter((entry) => entry.start && entry.end);
}
function includesCanary(value) {
  const lower = String(value || '').toLowerCase();
  return leakCanaries.some((canary) => canary && lower.includes(canary.toLowerCase()));
}
const classification = classify();
const slots = Array.isArray(input.candidateSlots) ? input.candidateSlots : [];
const redactedBusy = redactBusy();
let decision = 'manual_review';
let reason = 'default_review';
let allowedSideEffects = [];
if (classification === 'docs_request' || classification === 'rejection') {
  decision = 'ignored';
  reason = classification;
} else if (classification !== 'interview_offer') {
  decision = 'manual_review';
  reason = 'not_an_interview_offer';
} else if (!slots.length) {
  decision = 'manual_review';
  reason = 'no_concrete_slot';
} else if (mode === 'calendar_write' && !targetMapped()) {
  decision = 'manual_review';
  reason = 'missing_geschaeftlich_mapping';
} else {
  decision = mode === 'draft_only' ? 'create_reschedule_draft_plan' : 'created_event_plan';
  reason = mode === 'draft_only' ? 'draft_only_requested' : 'slot_requires_calendar_check';
  allowedSideEffects = mode === 'calendar_write' ? ['calendar_event'] : (mode === 'draft_only' ? ['reply_draft'] : []);
}
const company = String(input.company || 'Unknown Company');
const role = String(input.role || 'Interview');
const sourceMessageId = String(message.id || input.messageId || '');
const conversationId = String(message.conversationId || '');
const output = {
  workflow: 'Bewerbung Interview Scheduler - Inactive',
  mode,
  decision,
  reason,
  classification,
  sendStatus: 'NOT_SENT',
  allowedSideEffects,
  sourceMessageId,
  conversationId,
  redactedBusy,
  calendarEventPlan: decision === 'created_event_plan' ? {
    calendarAlias: 'geschaeftlich',
    summary: `[Interview] ${company} - ${role}`,
    start: slots[0] && slots[0].start || '',
    end: slots[0] && slots[0].end || '',
    attendees: [],
  } : null,
  replyDraftPlan: decision === 'create_reschedule_draft_plan' ? {
    sourceMessageId,
    conversationId,
    sendStatus: 'NOT_SENT',
    body: 'Vielen Dank fuer die Einladung. Der vorgeschlagene Termin passt leider nicht. Bitte senden Sie mir alternative Terminvorschlaege.',
  } : null,
  obsidianNotePlan: {
    folder: '14 - Arbeit/Angestellt/Interviews',
    requiredClaimFields: ['claim', 'sourceType', 'sourceRef', 'confidence', 'verifiedAt'],
  },
  telegramPlan: {
    chatId: '5920909215',
    decision,
    company,
    role,
  },
};
const serialized = JSON.stringify(output);
for (const canary of leakCanaries) {
  if (includesCanary(serialized)) {
    throw new Error('Private busy leak canary detected in scheduler output: ' + canary);
  }
}
return [{ json: output }];
""".strip()


SAFETY_JS = r"""
const data = $input.first().json || {};
if (data.sendStatus !== 'NOT_SENT') {
  throw new Error('sendStatus must remain NOT_SENT');
}
if (data.replyDraftPlan && data.replyDraftPlan.sendStatus !== 'NOT_SENT') {
  throw new Error('replyDraftPlan must remain NOT_SENT');
}
if (data.calendarEventPlan && Array.isArray(data.calendarEventPlan.attendees) && data.calendarEventPlan.attendees.length) {
  throw new Error('External attendees are blocked in v1');
}
return [{ json: { ...data, safetyChecked: true, activeExpected: false } }];
""".strip()


def build_interview_scheduler_workflow(
    *, name: str = WORKFLOW_NAME, active: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "active": active,
        "nodes": [
            {
                "parameters": {"inputSource": "passthrough"},
                "id": "interview-scheduler-trigger",
                "name": "Interview Scheduler - Execute Workflow Trigger",
                "type": "n8n-nodes-base.executeWorkflowTrigger",
                "typeVersion": 1.1,
                "position": [-720, 0],
            },
            {
                "parameters": {"jsCode": SCHEDULER_JS},
                "id": "interview-scheduler-decision",
                "name": "Build Safe Interview Decision",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-440, 0],
            },
            {
                "parameters": {"jsCode": SAFETY_JS},
                "id": "interview-scheduler-safety",
                "name": "Enforce Draft Only Safety",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-160, 0],
            },
            {
                "parameters": {
                    "assignments": {
                        "assignments": [
                            {
                                "id": "decision",
                                "name": "decision",
                                "value": "={{ $json.decision }}",
                                "type": "string",
                            },
                            {
                                "id": "send-status",
                                "name": "sendStatus",
                                "value": "={{ $json.sendStatus }}",
                                "type": "string",
                            },
                            {
                                "id": "payload",
                                "name": "payload",
                                "value": "={{ $json }}",
                                "type": "object",
                            },
                        ]
                    },
                    "options": {},
                },
                "id": "interview-scheduler-output",
                "name": "Format Scheduler Output",
                "type": "n8n-nodes-base.set",
                "typeVersion": 3.4,
                "position": [120, 0],
            },
        ],
        "connections": {
            "Interview Scheduler - Execute Workflow Trigger": {
                "main": [
                    [
                        {
                            "node": "Build Safe Interview Decision",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Build Safe Interview Decision": {
                "main": [
                    [{"node": "Enforce Draft Only Safety", "type": "main", "index": 0}]
                ]
            },
            "Enforce Draft Only Safety": {
                "main": [
                    [{"node": "Format Scheduler Output", "type": "main", "index": 0}]
                ]
            },
        },
        "settings": {
            "executionOrder": "v1",
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "none",
            "availableInMCP": False,
        },
        "tags": [],
    }


def validate_interview_scheduler_workflow(workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if workflow.get("active"):
        errors.append("workflow must be inactive")
    for node in workflow.get("nodes", []):
        node_type = str(node.get("type") or "").lower()
        node_name = str(node.get("name") or "").lower()
        if "schedule" in node_type or "webhook" in node_type:
            errors.append(f"blocked trigger node: {node.get('name')}")
        if any(
            token in node_type for token in ("emailsend", "smtp", "gmail", "telegram")
        ):
            errors.append(
                f"blocked side-effect node type: {node.get('name')} {node.get('type')}"
            )
        if "send mail" in node_name or "sendmail" in node_name:
            errors.append(f"blocked send node name: {node.get('name')}")
    return errors


def deploy_workflow(
    workflow: dict[str, Any],
    *,
    n8n_env_path: Path = DEFAULT_N8N_ENV_PATH,
    timeout: float = 120.0,
) -> dict[str, Any]:
    env = _load_env_file(n8n_env_path)
    api_url = os.getenv("N8N_API_URL") or env.get("N8N_API_URL", "")
    api_key = os.getenv("N8N_API_KEY") or env.get("N8N_API_KEY", "")
    if not api_url or not api_key:
        raise ValueError("Missing N8N_API_URL or N8N_API_KEY")
    errors = validate_interview_scheduler_workflow(workflow)
    if errors:
        raise ValueError(f"workflow failed safety validation: {errors}")
    with httpx.Client(timeout=timeout) as client:
        n8n = _N8nClient(client, api_url, api_key)
        existing = _find_workflow_by_name(n8n, str(workflow["name"]))
        if existing:
            workflow_id = str(existing["id"])
            merged = {
                "name": workflow["name"],
                "nodes": workflow["nodes"],
                "connections": workflow["connections"],
                "settings": workflow["settings"],
            }
            updated = n8n.request(
                "PUT", f"/api/v1/workflows/{workflow_id}", json=merged
            )
            if updated.get("active"):
                n8n.request("POST", f"/api/v1/workflows/{workflow_id}/deactivate")
                updated = n8n.request("GET", f"/api/v1/workflows/{workflow_id}")
            return {"action": "updated", "workflow": updated}
        create_payload = {
            "name": workflow["name"],
            "nodes": workflow["nodes"],
            "connections": workflow["connections"],
            "settings": workflow["settings"],
        }
        created = n8n.request("POST", "/api/v1/workflows", json=create_payload)
        workflow_id = str(created.get("id") or "")
        if created.get("active") and workflow_id:
            n8n.request("POST", f"/api/v1/workflows/{workflow_id}/deactivate")
            created = n8n.request("GET", f"/api/v1/workflows/{workflow_id}")
        return {"action": "created", "workflow": created}


def _find_workflow_by_name(n8n: _N8nClient, name: str) -> dict[str, Any] | None:
    body = n8n.request("GET", "/api/v1/workflows")
    for workflow in body.get("data", []):
        if str(workflow.get("name") or "") == name:
            return workflow
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, default=None)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--n8n-env", type=Path, default=DEFAULT_N8N_ENV_PATH)
    args = parser.parse_args(argv)
    workflow = build_interview_scheduler_workflow()
    errors = validate_interview_scheduler_workflow(workflow)
    if errors:
        raise SystemExit("\n".join(errors))
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(
            json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"export={args.export}")
    if args.deploy:
        result = deploy_workflow(workflow, n8n_env_path=args.n8n_env)
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
