# Changelog

Alle relevanten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.
Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionierung an [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.3.0] - 2026-08-22

### Hinzugefügt

- Interaktives, wiederaufnehmbares Setup für private Kandidaten- und Suchprofile.
- Reproduzierbare Installation mit `uv` und native CLI `job-agent`.
- Kontrollierte Stellensuche über mehrere öffentliche Quellen mit Source-Health-Bericht.
- Lokale CV-Erstellung mit klassischem PDF-Design sowie stilistisch abgestimmten Anschreiben.
- Quellengebundene Firmenrecherche, Formularinspektion und fingerprint-basierte Freigaben.
- Lesende Personio-Prüfung und Playwright-Smoke-Tests gegen lokale Fixtures.
- Persistente Liste für Fälle mit `needs_completion`, CAPTCHA, Login oder manueller Nacharbeit.
- Private, lokale Humanizer-Policy mit optional gepinnter öffentlicher Baseline.

### Sicherheit

- Kein Submit ohne unveränderte, nicht abgelaufene Freigabe.
- Fail-closed bei CAPTCHA, Login, MFA, neuen sensiblen Feldern und fehlender Finalevidenz.
- Kandidatendaten, Dokumente, Tracker, Browserstatus und Runs bleiben außerhalb von Git.

[Unreleased]: https://github.com/bpnace/job-application-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/bpnace/job-application-agent/releases/tag/v0.3.0
