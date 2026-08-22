# Mitwirken

Danke für dein Interesse am Job Application Agent. Beiträge sollen die kontrollierte, datensparsame Automatisierung für öffentliche Stellenangebote verbessern.

## Entwicklungsumgebung

```bash
uv sync --frozen --all-groups
uv run python -m playwright install chromium
uv run job-agent doctor
```

## Qualitätsprüfung

Führe vor einem Pull Request mindestens aus:

```bash
uv run ruff check .
uv run python -m pyright
uv run python -m pytest
uv build
```

Vor einem öffentlichen Push, Tag oder Release ist zusätzlich der vollständige Gate verpflichtend:

```bash
uv run job-agent init
uv run python scripts/prepublish_audit.py
```

Nach dem Push wird der öffentliche Zustand aus einem anonymen Mirror geprüft:

```bash
uv run python scripts/prepublish_audit.py \
  --remote-only \
  --remote-url https://github.com/OWNER/REPOSITORY.git \
  --github-repo OWNER/REPOSITORY
```

Der Ablauf und die Ursachen der früheren Schutzlücke sind in [docs/publication-safety.md](docs/publication-safety.md) dokumentiert.

## Anforderungen an Beiträge

- Keine echten Kandidatenprofile, Lebensläufe, Anschreiben, Kontaktdaten oder Portalzugänge committen.
- Neue Quellen benötigen eine Host-Policy, Parser-Fixtures und nachvollziehbare Fehlerzustände.
- Neue Browserregeln benötigen Tests für Erfolg, unbekannte Felder, Login und CAPTCHA.
- Sicherheitsstopps dürfen nicht abgeschwächt werden.
- Öffentliche Firmenfakten müssen eine Quelle behalten; unbelegte Angaben dürfen nicht erzeugt werden.
- Änderungen an CLI oder Approval-Vertrag müssen dokumentiert und semantisch versioniert werden.

## Pull Requests

Beschreibe Problem, Lösung, Testnachweis und Sicherheitsauswirkungen. Halte Änderungen fokussiert und vermeide unverbundene Refactorings.

Sicherheitsprobleme werden vertraulich nach [SECURITY.md](SECURITY.md) gemeldet.
