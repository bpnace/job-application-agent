# Job Application Agent instructions

Use the controlled workflow for all browser-capable coding agents, including Codex and Claude Code:

1. Run `uv sync --frozen --all-groups`, then `uv run python -m playwright install chromium` and `uv run job-agent doctor`.
2. Use `uv run job-agent search` to collect public listings only. Review the shortlist before package creation.
3. Create packages only for explicit shortlist entries. Run `uv run job-agent research-company JOB_JSON --write`, then inspect each live target with `uv run job-agent inspect-apply JOB_JSON --browser --write`.
4. Use `uv run job-agent approve JOB_JSON...` only after a human has reviewed the source-bound research artifact, form plan and document set.
5. Use `uv run job-agent apply-approved MANIFEST --execute` only for unchanged, unexpired approvals.

Never bypass CAPTCHA, login, MFA, consent screens, or new sensitive questions. Do not send email. Login, CAPTCHA, changed artifacts, unsupported portals, intermediate pages and missing final success evidence must remain `needs_completion` or blocked.

Local candidate data, approvals, browser state, tracker and runs belong in `.job-agent/` and must not be committed.
Private German Humanizer rules belong in `.job-agent/humanizer/private.de.md` (or a local `JOB_AGENT_HUMANIZER_PATH` override) and must not be committed.
