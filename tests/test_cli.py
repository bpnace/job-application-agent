import json

from job_application_agent.cli import main
from job_application_agent.models import JobListing, JobScorecard, SearchReport, SearchResult
from job_application_agent.tracker import manual_completion_queue, record_status_event
from job_application_agent.utils import write_json


def test_search_cli_writes_generated_artifacts_to_explicit_output_base(
    tmp_path, capsys
):
    output_base = tmp_path / "local-runs"

    exit_code = main(
        [
            "search",
            "--fixtures",
            "--top",
            "1",
            "--output-base",
            str(output_base),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    output_dirs = list(output_base.glob("*-search"))
    assert len(output_dirs) == 1
    assert f"output_dir={output_dirs[0]}" in output
    assert (output_dirs[0] / "search_results.md").is_file()


def test_search_cli_reminds_about_existing_manual_completion(tmp_path, capsys, monkeypatch):
    tracker_path = tmp_path / "applications.jsonl"
    record_status_event(
        JobListing(
            source="fixture",
            source_url="https://example.test/jobs/123",
            apply_url="https://example.test/jobs/123",
            title="Frontend Engineer",
            company="Example GmbH",
        ),
        "needs_completion",
        method="agent_playwright_fill",
        path=tracker_path,
    )
    monkeypatch.setenv("JOB_AGENT_TRACKER_PATH", str(tracker_path))

    exit_code = main(
        [
            "search",
            "--fixtures",
            "--top",
            "1",
            "--output-base",
            str(tmp_path / "local-runs"),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "open_manual_completions=1" in output
    assert "needs-completion --review" in output


def test_needs_completion_review_marks_manual_job_as_applied(tmp_path, capsys, monkeypatch):
    tracker_path = tmp_path / "applications.jsonl"
    listing = JobListing(
        source="fixture",
        source_url="https://example.test/jobs/123",
        apply_url="https://example.test/jobs/123",
        title="Frontend Engineer",
        company="Example GmbH",
    )
    record_status_event(
        listing,
        "needs_completion",
        method="agent_playwright_fill",
        path=tracker_path,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "a")

    exit_code = main(
        ["needs-completion", "--tracker-path", str(tracker_path), "--review"]
    )

    assert exit_code == 0
    assert '"resolved": 1' in capsys.readouterr().out
    assert manual_completion_queue([tracker_path]) == []
    events = [json.loads(line) for line in tracker_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["status"] == "applied"
    assert events[-1]["method"] == "manual_completion_review"


def test_mark_status_records_manual_join_completion(tmp_path, capsys):
    package_dir = tmp_path / "caya"
    package_dir.mkdir()
    job_json = package_dir / "job.json"
    listing = JobListing(
        source="join",
        source_url="https://join.com/companies/caya/jobs/csm",
        apply_url="https://join.com/companies/caya/jobs/csm/apply",
        title="Customer Success Manager",
        company="Caya GmbH",
        description="Customer onboarding and automation.",
    )
    job_json.write_text(listing.model_dump_json(), encoding="utf-8")
    tracker_path = tmp_path / "applications.jsonl"

    exit_code = main(
        [
            "mark-status",
            str(job_json),
            "--status",
            "applied",
            "--method",
            "manual_join_by_user",
            "--evidence",
            "final submit user-reported",
            "--tracker-path",
            str(tracker_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=applied" in output
    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert event["method"] == "manual_join_by_user"
    assert event["provenance"] == "manual_user_reported"
    assert event["evidence"] == "final submit user-reported"
    assert event["applied_at"]


def test_probe_live_skips_when_search_report_has_no_personio_listing(tmp_path, capsys):
    report_path = tmp_path / "search_results.json"
    write_json(
        report_path,
        SearchReport(
            run_id="fixture", created_at="2026-08-12T00:00:00Z", mode="fixtures", top_n=1,
            max_candidates=1, output_dir=str(tmp_path), results_json_path=str(report_path),
            results_md_path=str(tmp_path / "search_results.md"),
            results=[SearchResult(rank=1, listing=JobListing(source="fixture", source_url="https://example.test/job", title="Developer", company="Example", apply_platform="other"), scorecard=JobScorecard(listing_key="example", score=80, recommendation="review", selected=True))],
        ),
    )

    exit_code = main(["probe-live", "--from-search-results", str(report_path), "--platform", "personio", "--read-only"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"


def test_import_mail_response_cli_records_n8n_status(tmp_path, capsys):
    package_dir = tmp_path / "example"
    package_dir.mkdir()
    job_json = package_dir / "job.json"
    listing = JobListing(
        source="personio",
        source_url="https://example.jobs.personio.de/job/123",
        apply_url="https://example.jobs.personio.de/job/123?language=de",
        title="Frontend Engineer",
        company="Example GmbH",
        description="Product engineering role.",
    )
    job_json.write_text(listing.model_dump_json(), encoding="utf-8")
    payload_path = tmp_path / "mail_response.json"
    payload_path.write_text(
        json.dumps(
            {
                "classification": "response_received",
                "message_id": "AAMk-response",
                "subject": "Rueckmeldung zu Ihrer Bewerbung",
                "package_dir": str(package_dir),
            }
        ),
        encoding="utf-8",
    )
    tracker_path = tmp_path / "applications.jsonl"

    exit_code = main(
        [
            "import-mail-response",
            str(payload_path),
            "--tracker-path",
            str(tracker_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=response_received" in output
    assert "matched_by=package_dir" in output
    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert event["status"] == "response_received"
    assert event["method"] == "mail_response"
    assert event["provenance"] == "n8n_email_monitor"


def test_import_mail_response_cli_reads_payload_from_stdin(
    tmp_path, capsys, monkeypatch
):
    tracker_path = tmp_path / "applications.jsonl"
    payload = json.dumps(
        {
            "classification": "sent_application",
            "message_id": "sent-stdin",
            "company": "Example GmbH",
            "title": "Frontend Engineer",
            "apply_url": "https://example.com/jobs/123",
        }
    )
    monkeypatch.setattr(
        "sys.stdin", type("FakeStdin", (), {"read": lambda self: payload})()
    )

    exit_code = main(
        [
            "import-mail-response",
            "-",
            "--tracker-path",
            str(tracker_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=applied" in output
    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert event["status"] == "applied"


def test_outlook_status_workflow_cli_exports_persistent_workflow(tmp_path, capsys):
    export_path = tmp_path / "outlook_status_workflow.json"

    exit_code = main(
        [
            "outlook-status-workflow",
            "--export",
            str(export_path),
            "--repo-dir",
            str(tmp_path),
            "--activate",
            "--auth-token",
            "test-token",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"export={export_path}" in output
    workflow = json.loads(export_path.read_text(encoding="utf-8"))
    assert workflow["active"] is True
    assert workflow["name"] == "Bewerbung Outlook Status Monitor - Persistent"
    serialized = json.dumps(workflow)
    assert "job-agent-outlook-status-feed" in serialized
    assert "payloads" in serialized


def test_sync_outlook_statuses_cli_uses_default_webhook(tmp_path, capsys, monkeypatch):
    calls = {}

    def fake_default_webhook_url(*, webhook_path, n8n_env_path):
        calls["webhook_path"] = webhook_path
        calls["n8n_env_path"] = n8n_env_path
        return "https://automation.example/webhook/job-agent-outlook-status-feed"

    def fake_sync(
        webhook_url,
        *,
        tracker_path,
        checkpoint_path,
        backfill,
        folders,
        top,
        auth_token,
        n8n_env_path,
    ):
        calls["webhook_url"] = webhook_url
        calls["tracker_path"] = tracker_path
        calls["checkpoint_path"] = checkpoint_path
        calls["backfill"] = backfill
        calls["folders"] = folders
        calls["top"] = top
        calls["auth_token"] = auth_token
        calls["sync_n8n_env_path"] = n8n_env_path
        return {"imported": 2, "seen": 3, "errors": ["payload[2] failed: ambiguous"]}

    monkeypatch.setattr(
        "job_application_agent.cli.default_outlook_status_webhook_url",
        fake_default_webhook_url,
    )
    monkeypatch.setattr("job_application_agent.cli.sync_outlook_statuses", fake_sync)
    tracker_path = tmp_path / "applications.jsonl"

    exit_code = main(
        [
            "sync-outlook-statuses",
            "--tracker-path",
            str(tracker_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"imported": 2' in output
    assert calls["webhook_path"] == ""
    assert calls["webhook_url"] == (
        "https://automation.example/webhook/job-agent-outlook-status-feed"
    )
    assert calls["tracker_path"] == tracker_path
    assert calls["checkpoint_path"].name == "outlook_status_checkpoint.json"
    assert calls["backfill"] is False
    assert "junkemail" in calls["folders"]
    assert calls["top"] == 50
    assert calls["auth_token"] == ""
    assert calls["sync_n8n_env_path"].name == "n8n.env"
