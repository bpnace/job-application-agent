# Final TODOs

These are intentionally deferred until the search and application workflow is otherwise stable.

## Durable Duplicate And Application Tracker

Baseline implemented.

The agent now reads:

- `data/applications.jsonl` for package-created events.
- `runs/application_ledger.json` for manually verified application outcomes.
- classified Outlook-only n8n mail-response imports through `import-mail-response`.
- the persistent `outlook-status-workflow` n8n monitor for Outlook Inbox and Sent Items, pulled locally through `sync-outlook-statuses`.

Normal searches suppress existing jobs with final statuses and open manual-completion statuses:

- `applied`
- `rejected`
- `ignored`
- `closed_unavailable`
- `needs_completion`
- `blocked_manual`
- `blocked_captcha`
- `in_progress`

The non-final `response_received` status is still loaded as state, so dashboards can show replies/interview feedback without hiding the role from future searches. An open manual-completion case remains in `job-agent needs-completion` for the next-run reminder; `job-agent needs-completion --review` can mark it `applied`, `ignored`, or explicitly `requeued`.

Use `--include-tracked` for deliberate test searches that should show these jobs again.
Use `--allow-tracked` only when package creation should intentionally override the blocker.

Still useful later:

- `listing_key`, normalized `apply_url`, `source_url`, title, company and source.
- `first_seen_at`, `last_seen_at`, `run_ids` and optional `package_dir`.
- `application_method`, `apply_platform`, `resume_upload` and apply URL.
- status values: `seen`, `shortlisted`, `package_created`, `response_received`, `needs_completion`, `blocked_manual`, `blocked_captcha`, `in_progress`, `requeued`, `applied`, `rejected`, `ignored`, `closed_unavailable`.
- `applied_at`, notes and manual-review metadata.

Remaining hardening:

- Add richer mail-response classification once enough reply examples exist.
- Track first-seen/last-seen across repeated search-only runs without hiding non-final statuses.
