import json

import httpx

from job_application_agent.outlook_status_sync import (
    default_webhook_url,
    sync_outlook_statuses,
)


def test_default_webhook_url_uses_env_file_webhook_path(tmp_path, monkeypatch):
    env_path = tmp_path / ".env.n8n-mcp"
    env_path.write_text(
        "\n".join(
            [
                "N8N_API_URL=https://automation.example",
                "JOB_AGENT_OUTLOOK_STATUS_WEBHOOK_PATH=live-outlook-path",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("N8N_API_URL", raising=False)
    monkeypatch.delenv("JOB_AGENT_OUTLOOK_STATUS_WEBHOOK_PATH", raising=False)

    assert default_webhook_url(n8n_env_path=env_path) == (
        "https://automation.example/webhook/live-outlook-path"
    )


def test_sync_outlook_statuses_imports_payloads_from_n8n_feed(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    checkpoint_path = tmp_path / "outlook_checkpoint.json"

    def handler(request):
        assert (
            str(request.url)
            == "https://automation.example/webhook/job-agent-outlook-status-feed"
        )
        assert request.headers["X-Job-Agent-Token"] == "test-token"
        posted = json.loads(request.content)
        assert posted["mode"] == "backfill"
        assert posted["folders"] == ["junkemail"]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "scanned": {"junkemail": "2026-06-26T10:00:00Z"},
                "payloads": [
                    {
                        "classification": "rejection",
                        "message_id": "reply-sync",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "apply_url": "https://example.com/jobs/123",
                        "received_at": "2026-06-26T10:00:00Z",
                        "source_folder": "junkemail",
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        checkpoint_path=checkpoint_path,
        backfill=True,
        folders=["junkemail"],
        auth_token="test-token",
        client=client,
    )

    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["review_required"] == 0
    assert result["scanned"] == {"junkemail": "2026-06-26T10:00:00Z"}
    assert result["cursor"] == {"junkemail": "2026-06-26T10:00:00Z"}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["mode"] == "backfill"
    assert checkpoint["cursor"] == {"junkemail": "2026-06-26T10:00:00Z"}
    event = json.loads(tracker_path.read_text(encoding="utf-8"))
    assert event["status"] == "rejected"


def test_sync_outlook_statuses_writes_unmatched_payloads_for_review(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "scanned": {"inbox": "2026-06-26T09:30:00Z"},
                "payloads": [
                    {"classification": "rejection"},
                    {
                        "classification": "response_received",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                    },
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["review_required"] == 1
    assert result["errors"] == []
    events = [
        json.loads(line)
        for line in tracker_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["status"] == "review_required"
    assert events[0]["method"] == "mail_response_review"
    assert "Mail response payload needs company/title" in events[0]["review_reason"]
    assert events[0]["source_url"] == "mail-response:review-required"
    assert events[1]["status"] == "response_received"


def test_sync_outlook_statuses_skips_duplicate_review_payloads(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "payloads": [
                    {
                        "classification": "rejection",
                        "message_id": "review-duplicate",
                        "received_at": "2026-06-26T09:00:00Z",
                        "source_folder": "inbox",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    first = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )
    second = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert first["review_required"] == 1
    assert second["review_required"] == 0
    assert second["skipped"] == 1
    event = json.loads(tracker_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["source_url"].startswith("mail-response:")
    assert "review-duplicate" not in event["source_url"]
    assert tracker_path.read_text(encoding="utf-8").count("\n") == 1


def test_sync_outlook_statuses_does_not_advance_cursor_for_unpersisted_failures(
    tmp_path,
    monkeypatch,
):
    tracker_path = tmp_path / "applications.jsonl"
    checkpoint_path = tmp_path / "outlook_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({"cursor": {"inbox": "2026-06-25T09:00:00Z"}}),
        encoding="utf-8",
    )

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "payloads": [
                    {
                        "classification": "rejection",
                        "message_id": "unpersisted",
                        "received_at": "2026-06-26T09:00:00Z",
                        "source_folder": "inbox",
                    }
                ]
            },
        )

    def fail_review_event(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "job_application_agent.outlook_status_sync.record_review_event",
        fail_review_event,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        checkpoint_path=checkpoint_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert result["imported"] == 0
    assert result["review_required"] == 0
    assert len(result["errors"]) == 1
    assert result["cursor"] == {"inbox": "2026-06-25T09:00:00Z"}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["cursor"] == {"inbox": "2026-06-25T09:00:00Z"}


def test_sync_outlook_statuses_skips_duplicate_payloads(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "scanned": {"inbox": "2026-06-26T09:00:00Z"},
                "payloads": [
                    {
                        "classification": "rejection",
                        "message_id": "reply-sync-duplicate",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                    },
                    {
                        "classification": "rejection",
                        "message_id": "reply-sync-duplicate",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                    },
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert tracker_path.read_text(encoding="utf-8").count("\n") == 1


def test_sync_outlook_statuses_uses_saved_cursor_for_incremental_scan(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    checkpoint_path = tmp_path / "outlook_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "cursor": {"inbox": "2026-06-25T09:00:00Z"},
                "folders": ["inbox"],
                "top": 25,
            }
        ),
        encoding="utf-8",
    )

    def handler(request):
        posted = json.loads(request.content)
        assert posted["mode"] == "incremental"
        assert posted["cursor"] == {"inbox": "2026-06-25T09:00:00Z"}
        assert posted["folders"] == ["inbox"]
        assert posted["top"] == 25
        return httpx.Response(
            200,
            json={
                "payloads": [
                    {
                        "classification": "response_received",
                        "message_id": "reply-incremental",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "received_at": "2026-06-26T09:00:00Z",
                        "source_folder": "inbox",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        checkpoint_path=checkpoint_path,
        folders=["inbox"],
        top=25,
        auth_token="test-token",
        client=client,
    )

    assert result["cursor"] == {"inbox": "2026-06-26T09:00:00Z"}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["mode"] == "incremental"
    assert checkpoint["cursor"] == {"inbox": "2026-06-26T09:00:00Z"}


def test_sync_outlook_statuses_advances_cursor_from_scanned_empty_payloads(tmp_path):
    checkpoint_path = tmp_path / "outlook_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({"cursor": {"inbox": "2026-06-25T09:00:00Z"}}),
        encoding="utf-8",
    )

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "payloads": [],
                "scanned": {"inbox": "2026-06-26T10:00:00Z"},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        checkpoint_path=checkpoint_path,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert result["seen"] == 0
    assert result["cursor"] == {"inbox": "2026-06-26T10:00:00Z"}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["cursor"] == {"inbox": "2026-06-26T10:00:00Z"}


def test_sync_outlook_statuses_backfill_pages_older_messages(tmp_path):
    tracker_path = tmp_path / "applications.jsonl"
    checkpoint_path = tmp_path / "outlook_checkpoint.json"
    requests = []

    def handler(request):
        posted = json.loads(request.content)
        requests.append(posted)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "payloads": [
                        {
                            "classification": "rejection",
                            "message_id": "page-1",
                            "company": "Example GmbH",
                            "title": "Frontend Engineer",
                            "received_at": "2026-06-26T10:00:00Z",
                            "source_folder": "inbox",
                        }
                    ],
                    "scanned": {"inbox": "2026-06-26T10:00:00Z"},
                    "oldest": {"inbox": "2026-06-26T09:30:00Z"},
                    "hasMore": {"inbox": True},
                    "scannedCounts": {"inbox": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "payloads": [
                    {
                        "classification": "rejection",
                        "message_id": "page-2",
                        "company": "Example GmbH",
                        "title": "Frontend Engineer",
                        "received_at": "2026-06-25T08:00:00Z",
                        "source_folder": "inbox",
                    }
                ],
                "scanned": {"inbox": "2026-06-25T08:00:00Z"},
                "oldest": {"inbox": "2026-06-25T08:00:00Z"},
                "hasMore": {"inbox": False},
                "scannedCounts": {"inbox": 1},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = sync_outlook_statuses(
        "https://automation.example/webhook/job-agent-outlook-status-feed",
        tracker_path=tracker_path,
        checkpoint_path=checkpoint_path,
        backfill=True,
        folders=["inbox"],
        auth_token="test-token",
        client=client,
    )

    assert result["imported"] == 2
    assert result["pages"] == 2
    assert result["scanned_counts"] == {"inbox": 2}
    assert result["cursor"] == {"inbox": "2026-06-26T10:00:00Z"}
    assert requests[1]["before"] == {"inbox": "2026-06-26T09:30:00Z"}
