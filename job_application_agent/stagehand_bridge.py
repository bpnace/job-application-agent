from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import FormFillInstruction, FormFillPlan, utc_now_iso


SAFE_STAGEHAND_ACTIONS = {"fill", "select", "check"}
MANUAL_STAGEHAND_ACTIONS = {"manual", "skip", "upload"}


def action_prompt(instruction: FormFillInstruction) -> str:
    target = instruction.selector or instruction.field_label
    if instruction.action == "fill":
        return f"Fill {instruction.field_label} at {target} with the provided value."
    if instruction.action == "select":
        return f"Select {instruction.value} for {instruction.field_label} at {target}."
    if instruction.action == "check":
        return f"Check {instruction.field_label} at {target} only if it is required and already human-approved."
    return f"Review {instruction.field_label} manually."


def instruction_payload(instruction: FormFillInstruction) -> dict[str, Any]:
    return {
        "field_label": instruction.field_label,
        "selector": instruction.selector,
        "classification": instruction.classification,
        "action": instruction.action,
        "value": instruction.value,
        "file_path": instruction.file_path,
        "field_type": instruction.field_type,
        "frame_url": instruction.frame_url,
        "required": instruction.required,
        "confidence": instruction.confidence,
        "safety_note": instruction.safety_note,
        "instruction": action_prompt(instruction),
    }


def build_stagehand_plan_payload(plan: FormFillPlan) -> dict[str, Any]:
    safe_actions = []
    manual_review = []
    for instruction in plan.instructions:
        payload = instruction_payload(instruction)
        if instruction.action in SAFE_STAGEHAND_ACTIONS and instruction.selector and not instruction.safety_note:
            safe_actions.append(payload)
        elif instruction.action in MANUAL_STAGEHAND_ACTIONS:
            manual_review.append(payload)
        else:
            manual_review.append(payload)

    return {
        "generated_at": utc_now_iso(),
        "company": plan.company,
        "job_title": plan.job_title,
        "apply_url": plan.apply_url,
        "route": plan.route.model_dump(mode="json"),
        "submit_allowed": False,
        "blocked_actions": [
            "click submit",
            "send email",
            "create account",
            "solve captcha",
            "accept unexpected legal terms",
        ],
        "safe_actions": safe_actions,
        "manual_review": manual_review,
    }


def render_stagehand_preview_ts(plan_filename: str = "stagehand_apply_plan.json") -> str:
    return f"""import fs from "node:fs";
import path from "node:path";
import {{ fileURLToPath }} from "node:url";
import {{ Stagehand }} from "@browserbasehq/stagehand";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const planPath = path.join(__dirname, "{plan_filename}");
const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));

if (plan.submit_allowed !== false) {{
  throw new Error("Refusing to run: submit_allowed must be false.");
}}

const stagehand = new Stagehand({{
  env: process.env.BROWSERBASE_API_KEY ? "BROWSERBASE" : "LOCAL",
  verbose: 1,
}});

await stagehand.init();
const page = stagehand.page;
await page.goto(plan.apply_url, {{ waitUntil: "domcontentloaded" }});

for (const action of plan.safe_actions) {{
  if (action.action === "fill") {{
    await stagehand.act(`${{action.instruction}} Value: "${{action.value}}"`);
  }} else if (action.action === "select") {{
    await stagehand.act(action.instruction);
  }} else if (action.action === "check") {{
    await stagehand.act(action.instruction);
  }}
}}

console.log("Safe actions attempted:", plan.safe_actions.length);
console.log("Manual review fields:", plan.manual_review.length);
console.log("Submit remains blocked. Review the page manually.");
await page.pause();
await stagehand.close();
"""


def write_stagehand_artifacts(package_dir: Path, plan: FormFillPlan) -> tuple[Path, Path]:
    plan_path = package_dir / "stagehand_apply_plan.json"
    preview_path = package_dir / "stagehand_apply_preview.ts"
    payload = build_stagehand_plan_payload(plan)
    plan_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview_path.write_text(render_stagehand_preview_ts(plan_path.name), encoding="utf-8")
    return plan_path, preview_path
