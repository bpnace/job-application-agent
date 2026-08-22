# Portable controlled workflow

The project is an AGPL-3.0-only Python package. It works from Codex, Claude Code or a terminal after the same local setup.

```bash
uv sync --frozen --all-groups
uv run python -m playwright install chromium
uv run job-agent init --interactive
uv run job-agent humanizer bootstrap # optional pinned MIT baseline
uv run job-agent doctor
```

`init --interactive` fragt lokal nach Kandidatenprofil, Dokumentpfaden, gewünschten Titeln, zusätzlichen Keywords, Zielland bzw. Ort, Sperrwörtern und Arbeitgeberausschlüssen. Es schreibt diese Angaben nur in `.job-agent/candidate.yaml` und `.job-agent/search_profile.yaml`; beide Dateien sind ignoriert. Für eine nicht interaktive Einrichtung diese beiden Vorlagen lokal ausfüllen oder `JOB_AGENT_PROFILE_PATH` und `JOB_AGENT_SEARCH_PROFILE_PATH` setzen.

## Kein vorhandener Lebenslauf

Der CV bleibt ein lokales, faktisches Dokument. Nach `init` die Abschnitte unter `resume:` in `.job-agent/candidate.yaml` mit eigenen Stationen, Ausbildung, Kompetenzen, Sprachen und optionalen Zertifikaten ergänzen. Dann erzeugt dieser Befehl einen maschinenlesbaren Markdown- und JSON-Export sowie ein minimalistisches PDF mit konfigurierbarer Akzentfarbe:

```bash
uv run job-agent resume render
```

Die Dateien liegen standardmäßig unter `.job-agent/documents/` und werden als `documents.cv_text_path` und `documents.cv_pdf_path` im privaten Profil eingetragen. Künftige Bewerbungspakete und freigegebene Resume-Uploads verwenden dadurch das erzeugte PDF automatisch. Ein vorhandenes CV-PDF wird nie überschrieben; dafür ist ausdrücklich `--replace-configured-cv` nötig. Mit `--include-attachments` kann zusätzlich ein lokales PDF-Bündel aus ausdrücklich eingetragenen PDF-Zeugnissen oder Zertifikaten erzeugt werden. Es wird weder automatisch hochgeladen noch versendet.

Store German rules only in `.job-agent/humanizer/private.de.md` using YAML frontmatter plus Markdown guidance. `JOB_AGENT_HUMANIZER_PATH` remains a local override. The optional bootstrap stores the pinned `blader/humanizer` MIT baseline with a local license-and-SHA lock and never refreshes it automatically. `doctor` reports only policy status, source IDs and hashes, never policy text, candidate data or secrets.

## Submission workflow

```bash
uv run job-agent search --live --top 20 --no-serpapi
# Human reviews search_results.md and selects individual listings.
uv run job-agent create-packages .job-agent/runs/RUN-search/search_results.json --approve 1,3
uv run job-agent research-company .job-agent/runs/RUN-approved/PACKAGE/job.json --write
uv run job-agent inspect-apply .job-agent/runs/RUN-approved/PACKAGE/job.json --browser --write
# Human reviews the live form plan and documents.
uv run job-agent approve .job-agent/runs/RUN-approved/PACKAGE/job.json
uv run job-agent apply-approved .job-agent/approvals/APPROVAL.json --execute
# Falls ein Formular bewusst stoppt, bleibt es als bestehender Fall im Tracker:
uv run job-agent needs-completion --review
```

`research-company --write` stores only public, source-bound facts, source URLs, a public listing contact when present, and a timestamp. It reads the job page plus at most one explicitly linked company page and never uses credentials. Incomplete research remains reviewable but blocks autonomous approval. `approve` fingerprints each listing, company-research artifact, reviewed form plan and every upload document. `apply-approved` refuses expired manifests and any changed or missing artifact before it starts Playwright. It records `applied` only after a final confirmation or application-specific successful POST is captured. Open `needs_completion`, CAPTCHA and manual-review cases are suppressed from fresh searches until the user marks them applied, ignores them, or explicitly requeues them.

## Read-only live portal probe

```bash
uv run job-agent probe-live --from-search-results .job-agent/runs/RUN-search/search_results.json --platform personio --read-only
```

This chooses the first matching public Personio listing from an existing search result in a temporary browser context. Every non-GET request is aborted. It reports reachability, visible fields and portal state only; it never loads a candidate profile, fills fields, uploads documents or submits. No matching listing is `skipped`.

## Supported automation boundary

The autonomous path is intentionally narrow: direct public company forms and Personio are eligible only if all required fields have approved safe instructions. Personio can continue a later page only when the approved plan contains the exact next-step selector and safe instructions for that step. Salary, availability, work authorization, consent, EEO and other sensitive or new fields require human completion. Login, MFA, CAPTCHA, JOIN, unsupported ATS platforms, intermediate pages and missing final evidence stop without a submit.

Email applications are draft-only. There is no CAPTCHA-solving, authentication bypass or email sending capability.

## Arbeitsagentur

Der Agent liest für die Arbeitsagentur die öffentliche, servergerenderte Jobsuche. Der frühere `pc/v4`-Adapter war ein nicht dokumentierter Backend-Endpunkt und kann mit HTTP 403 antworten, obwohl die öffentliche Suche weiterhin Ergebnisse zeigt. Ein 403 wird deshalb nicht als „keine Stellen“ interpretiert und blockiert die übrigen Portale nicht.
