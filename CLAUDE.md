# Claude Code usage

Read and follow [AGENTS.md](AGENTS.md). The only autonomous submission path is:

`uv run job-agent search → human review → create-packages → research-company --write → inspect-apply --browser --write → approve → apply-approved --execute`

The approval manifest is the per-listing authority. Do not replace it with a broad confirmation flag, and never work around login, MFA or CAPTCHA.
Keep private Humanizer rules in ignored local state. `uv run job-agent humanizer bootstrap` is optional and downloads only the pinned MIT baseline after an explicit command.
