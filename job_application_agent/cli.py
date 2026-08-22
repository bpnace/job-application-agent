from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal

from .application import (
    build_form_fill_plan,
    extract_form_fields_from_html,
    format_form_fill_plan,
    infer_application_route,
)
from .approval import apply_approved, approve_packages
from .bootstrap import doctor_report, initialize_local_state
from .browser_apply import (
    fill_form_with_playwright,
    inspect_form_fields_with_playwright,
    probe_public_form_read_only,
)
from .company_research import research_company, write_company_research
from .humanizer_policy import bootstrap_public_baseline
from .package import refresh_application_package_from_research
from .models import JobListing, SearchReport
from .config import default_runs_dir, default_tracker_path
from .mail_draft import write_mail_draft
from .mail_response import import_mail_response, load_mail_response_payload
from .mail_response_server import (
    DEFAULT_HOST as DEFAULT_MAIL_RESPONSE_HOST,
    DEFAULT_PORT as DEFAULT_MAIL_RESPONSE_PORT,
    run_server as run_mail_response_server,
)
from .outlook_graph_draft import (
    DEFAULT_GRAPH_CREDENTIAL_NAME,
    DEFAULT_GRAPH_CREDENTIAL_TYPE,
    DEFAULT_N8N_ENV_PATH,
    body_text_to_html,
    create_outlook_reply_draft_via_n8n,
)
from .outlook_status_workflow import (
    DEFAULT_OUTLOOK_STATUS_FOLDERS,
    DEFAULT_WEBHOOK_PATH as DEFAULT_OUTLOOK_STATUS_WEBHOOK_PATH,
    build_outlook_status_monitor_workflow,
    deploy_workflow as deploy_outlook_status_monitor_workflow,
    validate_outlook_status_monitor_workflow,
)
from .outlook_status_sync import (
    DEFAULT_CHECKPOINT_PATH as DEFAULT_OUTLOOK_STATUS_CHECKPOINT_PATH,
    default_webhook_url as default_outlook_status_webhook_url,
    resolve_outlook_status_token,
    sync_outlook_statuses,
)
from .pipeline import create_packages_from_search_report, run_search
from .preflight import run_pre_application_check
from .profile import configured_cv_pdf_path, load_candidate_profile, profile_status
from .resume import render_resume
from .stagehand_bridge import write_stagehand_artifacts
from .tracker import (
    ApplicationTracker,
    manual_completion_queue,
    record_status_event,
    resolve_manual_completion,
)
from .utils import write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create ignored local state and optional private first-run setup.")
    init.add_argument("--agent-home", type=Path, default=None, help="Optional local state directory. Defaults to .job-agent.")
    init.add_argument(
        "--interactive",
        action="store_true",
        help="Ask for private candidate and search criteria, then write them only to .job-agent/.",
    )
    init.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow --interactive to replace existing ignored candidate and search profile files.",
    )

    sub.add_parser("doctor", help="Check local profile, documents and actual Chromium launch without printing secrets.")

    humanizer = sub.add_parser("humanizer", help="Manage local-only Humanizer policy support.")
    humanizer_sub = humanizer.add_subparsers(dest="humanizer_command", required=True)
    humanizer_bootstrap = humanizer_sub.add_parser(
        "bootstrap",
        help="Optionally download the pinned MIT public baseline once into ignored local state.",
    )
    humanizer_bootstrap.add_argument("--agent-home", type=Path, default=None)

    sub.add_parser("inspect-profile")

    resume = sub.add_parser(
        "resume",
        help="Create a local factual CV from the private candidate profile.",
    )
    resume_sub = resume.add_subparsers(dest="resume_command", required=True)
    resume_render = resume_sub.add_parser(
        "render",
        help="Render a minimal CV PDF plus Markdown and JSON source files.",
    )
    resume_render.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Local output directory. Defaults to .job-agent/documents.",
    )
    resume_render.add_argument(
        "--include-attachments",
        action="store_true",
        help="Create a separate PDF bundle from explicitly listed local PDF supporting documents.",
    )
    resume_render.add_argument(
        "--replace-configured-cv",
        action="store_true",
        help="Set the generated CV as default even when a CV PDF is already configured. The existing source file is never deleted.",
    )

    run = sub.add_parser("run")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixtures", action="store_true", help="Run against local fixtures."
    )
    mode.add_argument(
        "--live", action="store_true", help="Run against public live sources."
    )
    run.add_argument(
        "--top",
        type=int,
        default=None,
        help="Number of ranked search results to write.",
    )
    run.add_argument("--config", type=Path, default=None, help="Config YAML path.")
    run.add_argument(
        "--output-base",
        type=Path,
        default=None,
        help="Directory for generated run artifacts. Overrides JOB_AGENT_RUNS_DIR.",
    )
    run.add_argument(
        "--no-serpapi",
        action="store_true",
        help="Disable SerpAPI for this run even if a key is configured.",
    )
    run.add_argument(
        "--include-tracked",
        action="store_true",
        help="Include existing tracker entries, including open manual-completion cases, for a deliberate review run.",
    )

    search = sub.add_parser(
        "search", help="Collect and rank jobs without writing application packages."
    )
    search_mode = search.add_mutually_exclusive_group(required=True)
    search_mode.add_argument(
        "--fixtures", action="store_true", help="Search local fixtures."
    )
    search_mode.add_argument(
        "--live", action="store_true", help="Search public live sources."
    )
    search.add_argument(
        "--top", type=int, default=None, help="Number of ranked results to write."
    )
    search.add_argument("--config", type=Path, default=None, help="Config YAML path.")
    search.add_argument(
        "--output-base",
        type=Path,
        default=None,
        help="Directory for generated run artifacts. Overrides JOB_AGENT_RUNS_DIR.",
    )
    search.add_argument(
        "--no-serpapi",
        action="store_true",
        help="Disable SerpAPI for this search even if a key is configured.",
    )
    search.add_argument(
        "--include-tracked",
        action="store_true",
        help="Include existing tracker entries, including open manual-completion cases, for a deliberate review run.",
    )

    create_packages = sub.add_parser(
        "create-packages",
        help="Create application packages only for approved search results.",
    )
    create_packages.add_argument(
        "search_results",
        type=Path,
        help="Path to search_results.json from the search command.",
    )
    create_packages.add_argument(
        "--approve",
        action="append",
        default=[],
        help="Approved rank/key list. Accepts comma-separated ranks or listing keys. Can be repeated.",
    )
    create_packages.add_argument(
        "--all",
        action="store_true",
        help="Approve all ranked search results from the report.",
    )
    create_packages.add_argument(
        "--allow-tracked",
        action="store_true",
        help="Allow package creation for jobs already recorded as existing or requiring manual completion.",
    )
    create_packages.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config YAML path for current hard-exclusion validation.",
    )
    create_packages.add_argument(
        "--output-base",
        type=Path,
        default=None,
        help="Directory for generated run artifacts. Overrides JOB_AGENT_RUNS_DIR.",
    )

    inspect_apply = sub.add_parser("inspect-apply")
    inspect_apply.add_argument(
        "job_json", type=Path, help="Path to a package job.json file."
    )

    approve = sub.add_parser(
        "approve",
        help="Bind reviewed package listings, documents and form plans into a local immutable approval manifest.",
    )
    approve.add_argument("job_json", type=Path, nargs="+", help="Reviewed package job.json path(s).")
    approve.add_argument("--output", type=Path, default=None, help="Local approval manifest path.")
    approve.add_argument("--expires-hours", type=int, default=24, help="Approval validity, from 1 to 168 hours.")

    research_company_command = sub.add_parser(
        "research-company",
        help="Collect public company facts and an optional named public contact for one package.",
    )
    research_company_command.add_argument("job_json", type=Path, help="Package job.json path.")
    research_company_command.add_argument(
        "--write",
        action="store_true",
        help="Write company_research.json/md and refresh the cover letter only when research is sufficient.",
    )

    probe_live = sub.add_parser(
        "probe-live",
        help="Read-only temporary-browser probe of the first matching public supported portal listing.",
    )
    probe_live.add_argument("--from-search-results", type=Path, required=True)
    probe_live.add_argument("--platform", default="personio")
    probe_live.add_argument("--read-only", action="store_true", help="Required safety gate; blocks all non-GET requests.")
    probe_live.add_argument("--headed", action="store_true")
    probe_live.add_argument("--output", type=Path, default=None)

    apply_approved_command = sub.add_parser(
        "apply-approved",
        help="Apply only unchanged items bound by a local approval manifest.",
    )
    apply_approved_command.add_argument("approval_manifest", type=Path)
    apply_approved_command.add_argument("--execute", action="store_true", help="Actually fill and submit the approved public forms.")
    apply_approved_command.add_argument("--headed", action="store_true", help="Run Playwright in visible mode.")
    apply_approved_command.add_argument("--tracker-path", type=Path, default=None)
    apply_approved_command.add_argument("--output", type=Path, default=None, help="Write local JSON result to this path.")

    needs_completion = sub.add_parser(
        "needs-completion",
        help="List applications that stopped for a human-only form step, CAPTCHA or manual review.",
    )
    needs_completion.add_argument(
        "--tracker-path",
        type=Path,
        action="append",
        default=None,
        help="Optional tracker path. Repeat to merge multiple local trackers.",
    )
    needs_completion.add_argument(
        "--output", type=Path, default=None, help="Write the local JSON queue to this path."
    )
    needs_completion.add_argument(
        "--review",
        action="store_true",
        help="Interactively ask whether each existing case was completed, should be ignored, requeued, or kept open.",
    )
    inspect_apply.add_argument(
        "--html",
        type=Path,
        default=None,
        help="Optional local HTML form snapshot to inspect.",
    )
    inspect_apply.add_argument(
        "--browser",
        action="store_true",
        help="Inspect the live apply URL with Playwright.",
    )
    inspect_apply.add_argument(
        "--headed", action="store_true", help="Run Playwright in visible mode."
    )
    inspect_apply.add_argument(
        "--write",
        action="store_true",
        help="Write form_fill_plan.json/md into the package directory.",
    )
    inspect_apply.add_argument(
        "--single-upload-verified",
        action="store_true",
        help="Confirm that the inspected current/next form steps expose no cover-letter upload field, allowing the combined three-page application PDF fallback.",
    )

    stagehand_plan = sub.add_parser("stagehand-plan")
    stagehand_plan.add_argument(
        "job_json", type=Path, help="Path to a package job.json file."
    )
    stagehand_plan.add_argument(
        "--html",
        type=Path,
        default=None,
        help="Optional local HTML form snapshot to inspect.",
    )
    stagehand_plan.add_argument(
        "--browser",
        action="store_true",
        help="Inspect the live apply URL with Playwright.",
    )
    stagehand_plan.add_argument(
        "--headed", action="store_true", help="Run Playwright in visible mode."
    )
    stagehand_plan.add_argument(
        "--write",
        action="store_true",
        help="Write Stagehand preview artifacts into the package directory.",
    )
    stagehand_plan.add_argument(
        "--single-upload-verified",
        action="store_true",
        help="Confirm that the inspected current/next form steps expose no cover-letter upload field, allowing the combined three-page application PDF fallback.",
    )

    fill_form = sub.add_parser("fill-form")
    fill_form.add_argument(
        "job_json", type=Path, help="Path to a package job.json file."
    )
    fill_form.add_argument(
        "--headed", action="store_true", help="Run Playwright in visible mode."
    )
    fill_form.add_argument(
        "--confirm-fill",
        action="store_true",
        help="Actually fill visible fields. Does not submit by itself.",
    )
    fill_form.add_argument(
        "--confirm-submit",
        action="store_true",
        help="Deprecated and refused. Use `approve` followed by `apply-approved --execute`.",
    )
    fill_form.add_argument(
        "--single-upload-verified",
        action="store_true",
        help="Confirm that the inspected current/next form steps expose no cover-letter upload field, allowing the combined three-page application PDF fallback.",
    )
    fill_form.add_argument(
        "--tracker-path",
        type=Path,
        default=None,
        help="Optional tracker JSONL path for submit status events. Defaults to .job-agent/data/applications.jsonl.",
    )

    mark_status = sub.add_parser(
        "mark-status",
        help="Record a reviewed application status event for a package job.json.",
    )
    mark_status.add_argument(
        "job_json", type=Path, help="Path to a package job.json file."
    )
    mark_status.add_argument(
        "--status",
        required=True,
        choices=[
            "applied",
            "rejected",
            "ignored",
            "closed_unavailable",
            "needs_completion",
            "response_received",
            "in_progress",
            "blocked_manual",
            "blocked_captcha",
            "requeued",
        ],
        help="Reviewed status to append to the tracker.",
    )
    mark_status.add_argument(
        "--method",
        default="manual_reported_by_user",
        help="How this status was established, e.g. manual_join_by_user.",
    )
    mark_status.add_argument(
        "--provenance",
        default="manual_user_reported",
        help="Evidence provenance for this status event.",
    )
    mark_status.add_argument(
        "--note",
        action="append",
        default=[],
        help="Status note. Can be repeated.",
    )
    mark_status.add_argument(
        "--evidence",
        default="",
        help="Short evidence string, such as final submit user-reported.",
    )
    mark_status.add_argument(
        "--tracker-path",
        type=Path,
        default=None,
        help="Optional tracker JSONL path. Defaults to .job-agent/data/applications.jsonl.",
    )

    import_mail_response_command = sub.add_parser(
        "import-mail-response",
        help="Import a classified n8n email response payload into the application tracker.",
    )
    import_mail_response_command.add_argument(
        "payload",
        type=Path,
        help="Path to a JSON payload produced by the n8n mail monitor.",
    )
    import_mail_response_command.add_argument(
        "--tracker-path",
        type=Path,
        default=None,
        help="Optional tracker JSONL path. Defaults to .job-agent/data/applications.jsonl.",
    )
    mail_response_server = sub.add_parser(
        "serve-mail-response-webhook",
        help="Run a local HTTP receiver for persistent n8n Outlook status monitor POSTs.",
    )
    mail_response_server.add_argument("--host", default=DEFAULT_MAIL_RESPONSE_HOST)
    mail_response_server.add_argument(
        "--port", type=int, default=DEFAULT_MAIL_RESPONSE_PORT
    )
    mail_response_server.add_argument("--tracker-path", type=Path, default=None)

    mail_draft = sub.add_parser(
        "mail-draft",
        help="Write a NOT SENT email draft, .eml file and optional Apple Mail draft script for a package.",
    )
    mail_draft.add_argument(
        "job_json", type=Path, help="Path to a package job.json file."
    )
    mail_draft.add_argument(
        "--to",
        default="",
        help="Recipient email address. Required when the job page did not expose an email address.",
    )
    mail_draft.add_argument(
        "--subject",
        default="",
        help="Email subject. Defaults to a Bewerbung/Application subject.",
    )
    mail_draft.add_argument(
        "--body-file",
        type=Path,
        default=None,
        help="Optional markdown/plaintext body to use for the email.",
    )
    mail_draft.add_argument(
        "--cv-pdf",
        type=Path,
        default=None,
        help="CV PDF to attach. Defaults to JOB_AGENT_CV_PDF_PATH when set.",
    )
    mail_draft.add_argument(
        "--cover-pdf",
        type=Path,
        default=None,
        help="Cover-letter PDF to attach. Defaults to cover_letter.pdf next to job.json.",
    )
    mail_draft.add_argument(
        "--apple-mail-script",
        action="store_true",
        help="Also write an AppleScript that creates a visible Apple Mail draft only. It does not send.",
    )
    outlook_reply = sub.add_parser(
        "outlook-reply-draft",
        help="Create a true Outlook reply draft through Microsoft Graph via n8n. It never sends.",
    )
    source = outlook_reply.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--message-id",
        default="",
        help="Microsoft Graph message id of the original Outlook message.",
    )
    source.add_argument(
        "--internet-message-id",
        default="",
        help="RFC Internet Message-ID of the original message. Angle brackets are optional.",
    )
    outlook_reply.add_argument(
        "--body-file",
        type=Path,
        required=True,
        help="Reply body file. Defaults to plain text unless --body-format html is passed.",
    )
    outlook_reply.add_argument(
        "--body-format",
        choices=["text", "html"],
        default="text",
        help="Format of --body-file.",
    )
    outlook_reply.add_argument(
        "--attachment",
        action="append",
        type=Path,
        default=[],
        help="Attachment to add to the Outlook draft. Can be repeated.",
    )
    outlook_reply.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where outlook_reply_draft_NOT_SENT.json should be written. Defaults to body file directory.",
    )
    outlook_reply.add_argument(
        "--n8n-env",
        type=Path,
        default=DEFAULT_N8N_ENV_PATH,
        help="Path to .env file containing N8N_API_URL and N8N_API_KEY.",
    )
    outlook_reply.add_argument(
        "--n8n-api-url",
        default="",
        help="Override N8N_API_URL.",
    )
    outlook_reply.add_argument(
        "--n8n-api-key",
        default="",
        help="Override N8N_API_KEY. Prefer env files over shell history.",
    )
    outlook_reply.add_argument(
        "--n8n-public-url",
        default="",
        help="Public n8n base URL for webhook calls. Defaults to N8N_API_URL without /api/v1.",
    )
    outlook_reply.add_argument(
        "--credential-name",
        default=os.getenv("N8N_GRAPH_CREDENTIAL_NAME", DEFAULT_GRAPH_CREDENTIAL_NAME),
        help="n8n Microsoft Graph OAuth credential name.",
    )
    outlook_reply.add_argument(
        "--credential-type",
        default=os.getenv("N8N_GRAPH_CREDENTIAL_TYPE", DEFAULT_GRAPH_CREDENTIAL_TYPE),
        help="n8n credential type for the HTTP Request node.",
    )
    outlook_reply.add_argument(
        "--credential-id",
        default="",
        help="Optional n8n credential id. If omitted, the CLI tries to resolve it by name.",
    )
    outlook_status = sub.add_parser(
        "outlook-status-workflow",
        help="Export or deploy the persistent Outlook-only n8n status monitor.",
    )
    outlook_status.add_argument("--export", type=Path, default=None)
    outlook_status.add_argument("--deploy", action="store_true")
    outlook_status.add_argument("--activate", action="store_true")
    outlook_status.add_argument("--repo-dir", type=Path, default=Path.cwd())
    outlook_status.add_argument(
        "--webhook-path",
        default=DEFAULT_OUTLOOK_STATUS_WEBHOOK_PATH,
        help="Persistent n8n webhook path the local agent should pull from.",
    )
    outlook_status.add_argument(
        "--n8n-env",
        type=Path,
        default=DEFAULT_N8N_ENV_PATH,
        help="Path to .env file containing N8N_API_URL and N8N_API_KEY.",
    )
    outlook_status.add_argument(
        "--auth-token",
        default="",
        help="Shared secret used to create the n8n header-auth credential on deploy. Defaults to JOB_AGENT_OUTLOOK_STATUS_TOKEN.",
    )
    outlook_status.add_argument(
        "--auth-credential-name",
        default="Job Agent Outlook Status Header Auth",
        help="Base n8n Header Auth credential name for the Outlook status webhook.",
    )
    outlook_sync = sub.add_parser(
        "sync-outlook-statuses",
        help="Pull classified Outlook status payloads from the persistent n8n workflow and import them locally.",
    )
    outlook_sync.add_argument(
        "--webhook-url",
        default="",
        help="Full n8n webhook URL. Defaults to N8N_API_URL plus the persistent webhook path.",
    )
    outlook_sync.add_argument(
        "--webhook-path",
        default="",
        help="Persistent n8n webhook path.",
    )
    outlook_sync.add_argument(
        "--n8n-env",
        type=Path,
        default=DEFAULT_N8N_ENV_PATH,
        help="Path to .env file containing N8N_API_URL.",
    )
    outlook_sync.add_argument("--tracker-path", type=Path, default=None)
    outlook_sync.add_argument(
        "--checkpoint-path",
        type=Path,
        default=DEFAULT_OUTLOOK_STATUS_CHECKPOINT_PATH,
        help="Persisted cursor JSON. Defaults to data/outlook_status_checkpoint.json.",
    )
    outlook_sync.add_argument(
        "--backfill",
        action="store_true",
        help="Ignore the saved cursor for this run, then persist the newest seen timestamps.",
    )
    outlook_sync.add_argument(
        "--folder",
        action="append",
        default=[],
        help="Outlook well-known folder to request. Can be repeated.",
    )
    outlook_sync.add_argument(
        "--top",
        type=int,
        default=50,
        help="Maximum messages per folder requested by the n8n workflow.",
    )
    outlook_sync.add_argument(
        "--auth-token",
        default="",
        help="Shared secret sent as X-Job-Agent-Token. Defaults to JOB_AGENT_OUTLOOK_STATUS_TOKEN.",
    )
    return parser


def load_job(path: Path) -> JobListing:
    return JobListing.model_validate(json.loads(path.read_text(encoding="utf-8")))


def force_disable_serpapi() -> None:
    os.environ["JOB_AGENT_DISABLE_SERPAPI"] = "1"
    os.environ["SERPAPI_MAX_QUERIES_PER_RUN"] = "0"
    os.environ["SERPAPI_API_KEY"] = ""
    os.environ["SERP_API_KEY"] = ""


def parse_approval_refs(values: list[str]) -> list[str]:
    refs: list[str] = []
    for value in values:
        refs.extend(item.strip() for item in value.split(",") if item.strip())
    return refs


def review_manual_completion_entries(entries, *, tracker_path: Path) -> list[dict[str, str]]:
    """Ask the user how each existing manual-completion case should proceed."""
    choices: dict[str, Literal["applied", "ignored", "requeued"]] = {
        "a": "applied",
        "i": "ignored",
        "r": "requeued",
    }
    resolved: list[dict[str, str]] = []
    for entry in entries:
        prompt = (
            f"{entry.company} | {entry.title}: completed manually? "
            "[a]pplied, [i]gnore, [r]equeue for a later search, [Enter] keep open: "
        )
        try:
            answer = input(prompt).strip().casefold()
        except EOFError:
            break
        if not answer:
            continue
        if answer not in choices:
            print("Unrecognised choice; keeping this existing case open.")
            continue
        event = resolve_manual_completion(entry, choices[answer], path=tracker_path)
        resolved.append(
            {
                "listing_key": str(event["listing_key"]),
                "status": str(event["status"]),
            }
        )
    return resolved


def build_plan_for_job(
    job_json_path: Path,
    html_path: Path | None = None,
    browser: bool = False,
    headed: bool = False,
    submit_allowed: bool = False,
    single_upload_verified: bool = False,
    action: str = "build-form-plan",
):
    job_json_path = job_json_path.expanduser().resolve()
    listing = load_job(job_json_path)
    profile = load_candidate_profile()
    package_dir = job_json_path.parent
    run_pre_application_check(package_dir, action)
    cover_letter_path = package_dir / "cover_letter.md"
    cover_letter_text = (
        cover_letter_path.read_text(encoding="utf-8")
        if cover_letter_path.exists()
        else ""
    )
    fields = None
    if html_path:
        fields = extract_form_fields_from_html(html_path.read_text(encoding="utf-8"))
    elif browser:
        fields = inspect_form_fields_with_playwright(
            listing.apply_url or listing.source_url, headless=not headed
        )
    return build_form_fill_plan(
        profile,
        listing,
        package_dir=package_dir,
        fields=fields,
        cover_letter_text=cover_letter_text,
        submit_allowed=submit_allowed,
        single_upload_verified=single_upload_verified,
    )


def write_plan(package_dir: Path, plan) -> tuple[Path, Path]:
    package_dir = package_dir.expanduser().resolve()
    write_json(package_dir / "form_fill_plan.json", plan)
    (package_dir / "form_fill_plan.md").write_text(
        format_form_fill_plan(plan), encoding="utf-8"
    )
    return write_stagehand_artifacts(package_dir, plan)


def print_search_report(report: SearchReport, include_results: bool = False) -> None:
    print(f"search_run_id={report.run_id}")
    print(f"mode={report.mode}")
    print(f"results={len(report.results)}")
    print("packages=0")
    print(f"results_json={report.results_json_path}")
    print(f"results_md={report.results_md_path}")
    print(f"output_dir={report.output_dir}")
    print(f"tracked_skipped={report.tracked_skipped_count}")
    print(f"open_manual_completions={report.open_manual_completion_count}")
    if report.open_manual_completion_count:
        print(
            "reminder=Existing manual-completion cases remain excluded. "
            "Run `job-agent needs-completion --review` to resolve or explicitly requeue them."
        )
    if not include_results:
        return
    for result in report.results:
        listing = result.listing
        scorecard = result.scorecard
        print(
            f"{result.rank:02d}. {scorecard.score} {scorecard.recommendation} | {listing.title} | {listing.company}"
        )
        print(f"    key={scorecard.listing_key}")
        print(
            f"    application={listing.application_method} via {listing.apply_platform or 'unknown'} resume={listing.resume_upload}"
        )
        print(f"    url={listing.apply_url or listing.source_url}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        print(
            json.dumps(
                initialize_local_state(
                    agent_home=args.agent_home,
                    interactive=args.interactive,
                    overwrite=args.overwrite,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "doctor":
        report = doctor_report()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ready"] else 1
    if args.command == "resume":
        if args.resume_command == "render":
            result = render_resume(
                output_dir=args.output_dir,
                include_attachments=args.include_attachments,
                replace_configured_cv=args.replace_configured_cv,
            )
            print(
                json.dumps(
                    {
                        "cv_pdf": str(result.pdf_path),
                        "cv_markdown": str(result.markdown_path),
                        "cv_json": str(result.json_path),
                        "attachments_manifest": str(result.attachments_manifest_path),
                        "bundle_pdf": str(result.bundle_path) if result.bundle_path else "",
                        "attachment_count": result.attachment_count,
                        "generated_from_private_profile": True,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
    if args.command == "humanizer":
        if args.humanizer_command == "bootstrap":
            result = bootstrap_public_baseline(args.agent_home)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
    if args.command == "research-company":
        if not args.write:
            parser.error("research-company requires --write so the reviewed source artifact is explicit.")
        job_path = args.job_json.expanduser().resolve()
        listing = load_job(job_path)
        research = research_company(listing)
        json_path, md_path = write_company_research(job_path.parent, research)
        profile = load_candidate_profile()
        refresh = refresh_application_package_from_research(job_path.parent, profile, research)
        print(json.dumps({
            "research_json": str(json_path),
            "research_markdown": str(md_path),
            "company": research.company,
            "source_count": len(research.source_urls),
            "fact_count": len(research.facts),
            "contact_found": bool(research.contact_name),
            **refresh,
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "probe-live":
        if not args.read_only:
            parser.error("probe-live requires --read-only. It never fills, uploads, or submits.")
        report = SearchReport.model_validate_json(args.from_search_results.read_text(encoding="utf-8"))
        platform = args.platform.casefold()
        result = next(
            (
                item
                for item in report.results
                if item.listing.apply_platform.casefold() == platform
                or platform in (item.listing.apply_url or item.listing.source_url).casefold()
            ),
            None,
        )
        if result is None:
            payload = {"status": "skipped", "read_only": True, "reason": f"No public {args.platform} listing in search results."}
        else:
            payload = probe_public_form_read_only(
                result.listing.apply_url or result.listing.source_url, headless=not args.headed
            )
            payload["listing_key"] = f"{result.listing.company}|{result.listing.title}"
            payload["platform"] = args.platform
        if args.output:
            write_json(args.output, payload)
            payload["output"] = str(args.output)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "inspect-profile":
        print(profile_status())
        return 0
    if args.command == "approve":
        manifest = approve_packages(
            args.job_json,
            output_path=args.output,
            expires_hours=args.expires_hours,
        )
        print(f"approval_manifest={manifest}")
        return 0
    if args.command == "apply-approved":
        result = apply_approved(
            args.approval_manifest,
            execute=args.execute,
            headed=args.headed,
            tracker_path=args.tracker_path,
        )
        output_path = args.output or (
            default_runs_dir() / f"apply-{result['approval_id']}.json"
        )
        write_json(output_path, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"result_path={output_path}")
        return 0
    if args.command == "needs-completion":
        if args.review and args.tracker_path and len(args.tracker_path) > 1:
            parser.error("needs-completion --review accepts at most one --tracker-path.")
        resolved: list[dict[str, str]] = []
        if args.review:
            tracker = ApplicationTracker.load(args.tracker_path)
            tracker_path = (
                args.tracker_path[0].expanduser().resolve()
                if args.tracker_path
                else default_tracker_path()
            )
            resolved = review_manual_completion_entries(
                tracker.manual_completion_entries(), tracker_path=tracker_path
            )
        entries = manual_completion_queue(args.tracker_path)
        payload = {
            "count": len(entries),
            "entries": entries,
            "resolved": len(resolved),
            "resolutions": resolved,
            "hint": (
                "These are existing applications, excluded from fresh search results until resolved. "
                "Use `job-agent needs-completion --review` to mark them applied, ignore them, "
                "explicitly requeue them, or keep them open for the next reminder."
            ),
        }
        if args.output:
            write_json(args.output, payload)
            payload["output"] = str(args.output)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "run":
        if args.no_serpapi:
            force_disable_serpapi()
        mode = "fixtures" if args.fixtures else "live"
        report = run_search(
            mode=mode,
            top_n=args.top,
            config_path=args.config,
            output_base=args.output_base,
            include_tracked=args.include_tracked,
        )
        print(
            "run is search-only; create packages with create-packages --approve after review."
        )
        print_search_report(report)
        return 0
    if args.command == "search":
        if args.no_serpapi:
            force_disable_serpapi()
        mode = "fixtures" if args.fixtures else "live"
        report = run_search(
            mode=mode,
            top_n=args.top,
            config_path=args.config,
            output_base=args.output_base,
            include_tracked=args.include_tracked,
        )
        print_search_report(report, include_results=True)
        return 0
    if args.command == "create-packages":
        if args.all:
            search_report = SearchReport.model_validate_json(
                args.search_results.read_text(encoding="utf-8")
            )
            approvals = [str(result.rank) for result in search_report.results]
        else:
            approvals = parse_approval_refs(args.approve)
        if not approvals:
            parser.error("create-packages requires --approve ranks/keys or --all")
        report = create_packages_from_search_report(
            args.search_results,
            approvals,
            output_base=args.output_base,
            allow_tracked=args.allow_tracked,
            config_path=args.config,
        )
        print(f"run_id={report.run_id}")
        print(f"mode={report.mode}")
        print(f"packages={len(report.packages)}")
        print(f"output_dir={report.output_dir}")
        for package in report.packages:
            print(f"package={package.package_dir}")
            print(f"cover_letter_quality={package.cover_letter_quality_md_path}")
        return 0
    if args.command == "inspect-apply":
        plan = build_plan_for_job(
            args.job_json,
            html_path=args.html,
            browser=args.browser,
            headed=args.headed,
            single_upload_verified=args.single_upload_verified,
            action="inspect-apply",
        )
        package_dir = args.job_json.expanduser().resolve().parent
        stagehand_json: Path | None = None
        stagehand_ts: Path | None = None
        if args.write:
            stagehand_json, stagehand_ts = write_plan(package_dir, plan)
        route = infer_application_route(plan.apply_url)
        print(f"company={plan.company}")
        print(f"role={plan.job_title}")
        print(f"apply_url={plan.apply_url}")
        print(f"application_method={route.method}")
        print(f"apply_platform={route.platform or 'unknown'}")
        print(f"resume_upload={route.resume_upload}")
        print(f"agent_can_fill={route.can_agent_fill}")
        print(f"fields_detected={len(plan.fields)}")
        print(f"submit_allowed={plan.submit_allowed}")
        if args.write:
            print(f"plan_path={package_dir / 'form_fill_plan.md'}")
            print(f"stagehand_plan_path={stagehand_json or ''}")
            print(f"stagehand_preview_path={stagehand_ts or ''}")
        return 0
    if args.command == "stagehand-plan":
        plan = build_plan_for_job(
            args.job_json,
            html_path=args.html,
            browser=args.browser,
            headed=args.headed,
            single_upload_verified=args.single_upload_verified,
            action="stagehand-plan",
        )
        package_dir = args.job_json.expanduser().resolve().parent
        if args.write:
            stagehand_json, stagehand_ts = write_stagehand_artifacts(package_dir, plan)
        else:
            stagehand_json = package_dir / "stagehand_apply_plan.json"
            stagehand_ts = package_dir / "stagehand_apply_preview.ts"
        print(f"company={plan.company}")
        print(f"role={plan.job_title}")
        print(f"apply_url={plan.apply_url}")
        print(f"submit_allowed={plan.submit_allowed}")
        print(
            f"safe_actions={sum(1 for item in plan.instructions if item.action in {'fill', 'select', 'check'} and item.selector)}"
        )
        print(
            f"manual_review={sum(1 for item in plan.instructions if item.action in {'manual', 'skip', 'upload'})}"
        )
        if args.write:
            print(f"stagehand_plan_path={stagehand_json}")
            print(f"stagehand_preview_path={stagehand_ts}")
        return 0
    if args.command == "fill-form":
        if args.confirm_submit:
            parser.error(
                "Direct submission is disabled. Review the package, run `job-agent approve`, then use `job-agent apply-approved MANIFEST --execute`."
            )
        plan = build_plan_for_job(
            args.job_json,
            browser=True,
            headed=args.headed,
            submit_allowed=False,
            single_upload_verified=args.single_upload_verified,
            action="fill-form",
        )
        package_dir = args.job_json.expanduser().resolve().parent
        _stagehand_json, _stagehand_ts = write_plan(package_dir, plan)
        if not args.confirm_fill:
            print("dry_run=true")
            print(f"plan_path={package_dir / 'form_fill_plan.md'}")
            print(
                "Run again with --confirm-fill to fill visible fields. Autonomous submission requires an approved manifest."
            )
            return 0
        result = fill_form_with_playwright(
            plan, headless=not args.headed, submit=False
        )
        tracker_status = str(result.get("tracker_status") or "")
        if tracker_status:
            job_json_path = args.job_json.expanduser().resolve()
            listing = load_job(job_json_path)
            event = record_status_event(
                listing,
                tracker_status,
                method="agent_playwright_fill",
                provenance="agent_browser_safety_gate",
                evidence=_submit_status_evidence(result),
                notes=[str(result.get("status_reason") or "")],
                package_dir=package_dir,
                path=args.tracker_path,
            )
            result["tracker_event"] = {
                "status": event["status"],
                "status_at": event["status_at"],
                "method": event["method"],
            }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "mark-status":
        job_json_path = args.job_json.expanduser().resolve()
        listing = load_job(job_json_path)
        package_dir = job_json_path.parent
        run_pre_application_check(package_dir, "mark-status")
        event = record_status_event(
            listing,
            args.status,
            method=args.method,
            provenance=args.provenance,
            evidence=args.evidence or None,
            notes=args.note,
            package_dir=package_dir,
            path=args.tracker_path,
        )
        print(f"status={event['status']}")
        print(f"method={event['method']}")
        print(f"tracker_path={args.tracker_path or default_tracker_path()}")
        print(f"status_at={event['status_at']}")
        return 0
    if args.command == "import-mail-response":
        payload = load_mail_response_payload(
            args.payload.expanduser(),
            stdin_text=sys.stdin.read() if str(args.payload) == "-" else None,
        )
        result = import_mail_response(payload, tracker_path=args.tracker_path)
        print(f"status={result.status}")
        print(f"matched_by={result.matched_by}")
        print(f"tracker_path={result.tracker_path}")
        print(f"status_at={result.event['status_at']}")
        return 0
    if args.command == "serve-mail-response-webhook":
        run_mail_response_server(
            host=args.host,
            port=args.port,
            tracker_path=args.tracker_path,
        )
        return 0
    if args.command == "mail-draft":
        job_json_path = args.job_json.expanduser().resolve()
        listing = load_job(job_json_path)
        profile = load_candidate_profile()
        package_dir = job_json_path.parent
        run_pre_application_check(package_dir, "mail-draft")
        body = (
            args.body_file.expanduser().read_text(encoding="utf-8")
            if args.body_file
            else ""
        )
        attachments = _mail_attachments(
            package_dir,
            cv_pdf=args.cv_pdf,
            cover_pdf=args.cover_pdf,
        )
        result = write_mail_draft(
            package_dir,
            profile,
            listing,
            to=args.to,
            subject=args.subject,
            body=body,
            attachments=attachments,
            write_apple_mail_script=args.apple_mail_script,
        )
        print("status=NOT_SENT")
        print(f"email_draft_md={result.markdown_path}")
        print(f"email_draft_eml={result.eml_path}")
        if result.apple_mail_script_path:
            print(f"apple_mail_script={result.apple_mail_script_path}")
        print("send_allowed=false")
        return 0
    if args.command == "outlook-reply-draft":
        body_raw = args.body_file.expanduser().read_text(encoding="utf-8")
        body_html = (
            body_raw if args.body_format == "html" else body_text_to_html(body_raw)
        )
        output_dir = args.output_dir or args.body_file.expanduser().resolve().parent
        result = create_outlook_reply_draft_via_n8n(
            message_id=args.message_id,
            internet_message_id=args.internet_message_id,
            body_html=body_html,
            attachments=args.attachment,
            output_dir=output_dir,
            n8n_env_path=args.n8n_env,
            n8n_api_url=args.n8n_api_url,
            n8n_api_key=args.n8n_api_key,
            n8n_public_url=args.n8n_public_url,
            credential_name=args.credential_name,
            credential_type=args.credential_type,
            credential_id=args.credential_id,
        )
        print(f"status={result.status}")
        print(f"draft_id={result.draft_id}")
        print(f"web_link={result.web_link}")
        print(f"subject={result.subject}")
        print(f"is_draft={str(result.is_draft).lower()}")
        print(f"attachments_added={result.attachments_added}")
        print(f"result_path={result.result_path}")
        print("send_allowed=false")
        return 0
    if args.command == "outlook-status-workflow":
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
            auth_token = resolve_outlook_status_token(
                explicit_token=args.auth_token,
                n8n_env_path=args.n8n_env,
            )
            result = deploy_outlook_status_monitor_workflow(
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
    if args.command == "sync-outlook-statuses":
        webhook_url = args.webhook_url or default_outlook_status_webhook_url(
            webhook_path=args.webhook_path,
            n8n_env_path=args.n8n_env,
        )
        result = sync_outlook_statuses(
            webhook_url,
            tracker_path=args.tracker_path,
            checkpoint_path=args.checkpoint_path,
            backfill=args.backfill,
            folders=args.folder or DEFAULT_OUTLOOK_STATUS_FOLDERS,
            top=args.top,
            auth_token=args.auth_token,
            n8n_env_path=args.n8n_env,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    parser.error("unknown command")
    return 2


def _mail_attachments(
    package_dir: Path,
    *,
    cv_pdf: Path | None = None,
    cover_pdf: Path | None = None,
) -> list[Path]:
    configured_cv = configured_cv_pdf_path()
    cv_candidate = cv_pdf or configured_cv
    cover_candidate = cover_pdf or package_dir / "cover_letter.pdf"
    if cv_candidate is None:
        raise FileNotFoundError(
            "Email applications require a separate CV PDF. Configure documents.cv_pdf_path, "
            "set JOB_AGENT_CV_PDF_PATH or pass --cv-pdf."
        )
    cv_path = _required_separate_email_pdf(cv_candidate, "CV")
    cover_path = _required_separate_email_pdf(cover_candidate, "cover-letter")
    if cv_path == cover_path:
        raise ValueError(
            "Email applications require two separate PDFs: cover letter and CV."
        )
    return [cv_path, cover_path]


def _submit_status_evidence(result: dict[str, object]) -> dict[str, object]:
    validation = result.get("validation")
    responses = result.get("responses")
    return {
        "application_status": result.get("application_status"),
        "submit_evidence_level": result.get("submit_evidence_level"),
        "status_reason": result.get("status_reason"),
        "submit": result.get("submit"),
        "final_url": result.get("final_url"),
        "validation_count": len(validation) if isinstance(validation, list) else 0,
        "responses": responses[:5] if isinstance(responses, list) else [],
    }


def _required_separate_email_pdf(path: Path, document_kind: str) -> Path:
    expanded = path.expanduser().resolve()
    if not expanded.exists():
        raise FileNotFoundError(
            f"Email applications require a separate {document_kind} PDF: {expanded}"
        )
    if expanded.suffix.lower() != ".pdf":
        raise ValueError(
            f"Email applications require a separate {document_kind} PDF, not {expanded.name}."
        )
    if _looks_like_combined_application_pdf(expanded):
        raise ValueError(
            f"Email applications require a separate {document_kind} PDF, not a combined application PDF: {expanded.name}"
        )
    return expanded


def _looks_like_combined_application_pdf(path: Path) -> bool:
    stem = path.stem.lower()
    combined_tokens = ("combined", "bewerbung", "application")
    separate_tokens = (
        "anschreiben",
        "cover_letter",
        "cover-letter",
        "lebenslauf",
        "resume",
        "cv",
    )
    return any(token in stem for token in combined_tokens) and not any(
        token in stem for token in separate_tokens
    )


if __name__ == "__main__":
    raise SystemExit(main())
