# Job Application Agent

[![CI](https://github.com/bpnace/job-application-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/bpnace/job-application-agent/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/bpnace/job-application-agent?display_name=tag&sort=semver)](https://github.com/bpnace/job-application-agent/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.13-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/github/license/bpnace/job-application-agent)](LICENSE)
[![Playwright](https://img.shields.io/badge/Browser-Playwright-45BA4B?logo=playwright&logoColor=white)](https://playwright.dev/)
[![Status](https://img.shields.io/badge/status-beta-F59E0B)](#projektstatus)

Ein lokaler, kontrollierter Bewerbungsagent für die öffentliche Stellensuche in Deutschland. Er erstellt aus einem **privaten Such- und Kandidatenprofil** eine prüfbare Shortlist, Bewerbungspakete und – nach einer konkreten Freigabe – eine eng begrenzte Browser-Automation für unterstützte öffentliche Formulare.

**Version 0.3.0** · Python-Paket mit der CLI **job-agent** · Lizenz: [AGPL-3.0-only](LICENSE)

> Der Agent ist kein Massenbewerbungswerkzeug. Jede Bewerbung bleibt an eine konkrete, lokale Freigabe gebunden. Login, MFA, CAPTCHA, neue sensible Angaben und mehrdeutige Formularschritte führen nicht zu einem Submit.

## Inhalt

- [Für wen ist das?](#für-wen-ist-das)
- [Was der Agent macht](#was-der-agent-macht)
- [Projektstatus](#projektstatus)
- [Sicherheits- und Datenschutzmodell](#sicherheits--und-datenschutzmodell)
- [Schnellstart](#schnellstart)
- [Einrichtung mit Codex, Claude Code oder CLI](#einrichtung-mit-codex-claude-code-oder-cli)
- [Der tägliche Ablauf](#der-tägliche-ablauf)
- [Suchprofil und Quellen](#suchprofil-und-quellen)
- [Browser-Automation und Freigaben](#browser-automation-und-freigaben)
- [Manuelle Nachbearbeitung](#manuelle-nachbearbeitung)
- [Projektstruktur und Ausgaben](#projektstruktur-und-ausgaben)
- [Tests und Entwicklung](#tests-und-entwicklung)
- [Versionierung](#versionierung)
- [Mitwirken und Sicherheit](#mitwirken-und-sicherheit)
- [Troubleshooting](#troubleshooting)

## Für wen ist das?

Das Projekt richtet sich an Personen, die ihre Stellensuche lokal, nachvollziehbar und datensparsam organisieren möchten – insbesondere für den deutschen Markt und öffentliche Unternehmens- oder Portalstellen.

Es eignet sich, wenn du:

- Positionen, Länder, Orte, Remote-Präferenzen, Keywords und Sperrwörter selbst bestimmen möchtest;
- eine erklärbare Shortlist statt undurchsichtiger Rankings brauchst;
- Anschreiben und Dokumente vor einer Bewerbung prüfen willst;
- ohne vorhandenen Lebenslauf einen faktischen, privaten CV im minimalistischen PDF-Design erstellen möchtest;
- öffentliche Formulare bis zu einem klar belegten Abschluss bearbeiten möchtest;
- persönliche Daten, Lebensläufe, Humanizer-Regeln, Tracker und Runs außerhalb von Git halten willst.

## Was der Agent macht

| Phase | Ergebnis | Automatisierung |
| --- | --- | --- |
| Einrichten | Privates Profil und Suchkriterien unter .job-agent/ | Lokal, interaktiv |
| Suchen | Normalisierte, deduplizierte Shortlist öffentlicher Stellen | Öffentlich und lesend |
| Prüfen | search_results.md, Scorecards und Quellenstatus | Menschliche Auswahl |
| Pakete | Anschreiben, PDF, Checkliste und Formularplan | Nur für ausgewählte Ränge |
| Recherche | Quellengebundene Firmenfakten und optionale Kontaktperson | Öffentlich und lesend |
| Formularanalyse | Sichtbare Felder, Portalstatus und Folge-Schritte | Browser, ohne Submit |
| Freigabe | Lokales Manifest mit Datei-Fingerprints | Menschlich |
| Ausführen | Befüllen und nur bei finaler Evidenz submitten | Eng begrenzt |

Der Agent nutzt für das Ranking ausschließlich das lokale Suchprofil. Ein frischer Clone enthält bewusst **keine** vorgegebenen Zielrollen, Städte oder Karriere-Keywords.

## Projektstatus

Das Projekt befindet sich in einer öffentlichen **Beta-Phase**. Suche, Paketgenerierung, lokale Freigaben, Browser-Fixtures und die dokumentierten Sicherheitsstopps sind automatisiert getestet. Reale Portale können ihre Struktur und Nutzungsbedingungen jederzeit ändern; ein erfolgreicher Fixture-Test ist deshalb keine Garantie für ein bestimmtes Live-Portal.

| Bereich | Status |
| --- | --- |
| Lokales Setup und Diagnose | Stabil |
| Öffentliche Stellensuche | Beta, mit Source-Health-Bericht |
| CV- und Anschreiben-Erstellung | Beta, lokal und prüfbar |
| Personio und direkte öffentliche Formulare | Kontrollierte Beta |
| Login, MFA und CAPTCHA | Bewusst nicht unterstützt |
| Automatischer E-Mail-Versand | Bewusst nicht unterstützt |

Änderungen werden im [Changelog](CHANGELOG.md) dokumentiert. Sicherheitsprobleme bitte nicht als öffentliches Issue melden, sondern nach [SECURITY.md](SECURITY.md).

## Sicherheits- und Datenschutzmodell

### Was bewusst nicht unterstützt wird

- Kein Login, keine MFA- oder CAPTCHA-Umgehung.
- Kein automatischer E-Mail-Versand.
- Kein Upload oder Submit in einem lesenden Portal-Probe.
- Kein Submit bei neuen, sensiblen oder mehrdeutigen Feldern, zum Beispiel Gehalt, Arbeitserlaubnis, EEO, Einwilligungen oder Umzug.
- Kein Status applied ohne finale Bestätigung oder geeignete POST-Evidenz.

### Was geschützt wird

- .job-agent/, .env, .env.local, Run-Artefakte und Tracker sind git-ignoriert.
- Die versionierten Dateien in config/ sind ausschließlich PII-freie Vorlagen.
- doctor prüft lokale Bereitschaft, ohne Secrets oder Policy-Inhalte auszugeben.
- Die CI führt vor Tests einen Public-Repository-Guard gegen private Pfade, persönliche Dokumenttypen, lokale Benutzerpfade und nicht freigegebene E-Mail-Adressen aus.
- Ein Approval-Manifest bindet Job, Formularplan, Recherche und Upload-Dokumente über Fingerprints. Bei Änderungen stoppt apply-approved vor dem Browserstart.
- Der Humanizer liegt lokal unter .job-agent/humanizer/. Die optionale öffentliche Baseline wird nur auf ausdrücklichen Befehl, Commit-gepinnt und mit Hash-Lock gespeichert.

## Schnellstart

### Voraussetzungen

- Git
- [uv](https://docs.astral.sh/uv/)
- Python 3.10 bis 3.13, wird durch uv verwaltet
- Für Browser-Prüfungen: Chromium über Playwright

~~~bash
git clone https://github.com/bpnace/job-application-agent.git
cd job-application-agent

uv sync --frozen --all-groups
uv run python -m playwright install chromium

uv run job-agent init --interactive
uv run job-agent doctor
~~~

Wenn doctor den Wert `"ready": true` und für alle Checks `"ok": true` meldet, ist die lokale Umgebung für Suche und Browser-Prüfung bereit. Fehlen CV-Dateien oder Chromium, zeigt der Befehl konkrete, sichere Schritte an.

### Erste risikofreie Prüfung

Die Fixtures sind vollständig lokal und verschicken nichts:

~~~bash
uv run job-agent search --fixtures --top 5
~~~

Danach liegt ein Run-Ordner unter .job-agent/runs/ mit search_results.md.

### Kein Lebenslauf vorhanden?

Der Agent erfindet keine Berufserfahrung oder Qualifikationen. Falls beim interaktiven Setup kein CV vorliegt, fragt er die belegbaren Stationen, Ausbildung, Kompetenzen, Sprachen und Zertifikate ab und erzeugt daraus sofort einen lokalen Basis-Lebenslauf. Die Antworten bleiben ausschließlich unter `.job-agent/`.

Ergänzungen oder eine spätere Neugenerierung erfolgen über die optionalen Daten unter `resume:` in `.job-agent/candidate.yaml`:

~~~bash
uv run job-agent resume render
~~~

Der Befehl erstellt einen klassischen, großzügig gesetzten CV mit einer zurückhaltenden Bordeaux-Akzentfarbe sowie Markdown- und JSON-Quellen unter `.job-agent/documents/`. CV und Anschreiben teilen sich dieselbe Typografie, Rasterung, Kopfzeile und Fußzeile. Er setzt den neuen CV anschließend als lokalen Standard für Anschreiben und Upload-Pläne, aber nur, wenn bisher kein CV-PDF konfiguriert ist. Ein vorhandener CV wird niemals gelöscht. Für den bewussten Wechsel:

~~~bash
uv run job-agent resume render --replace-configured-cv
~~~

### Eigenes CV-Design übernehmen

Liegt bereits ein PDF-Lebenslauf vor, verwendet jedes neu erzeugte Anschreiben dessen sichtbare Akzentfarbe. Der Agent liest dafür ausschließlich lokale PDF-Zeichenoperationen; er exportiert, teilt oder analysiert keine CV-Inhalte. Die Reihenfolge ist absichtlich nachvollziehbar:

1. Der in `candidate.yaml` oder über `JOB_AGENT_CV_PDF_PATH` konfigurierte CV.
2. Ein eindeutig benannter PDF-CV (`Lebenslauf`, `CV` oder `Resume`) unter `.job-agent/documents/`. Gibt es dort nur ein PDF, wird dieses ebenfalls verwendet.
3. Die private Einstellung `resume.accent_color` in `candidate.yaml`.
4. Der klassische Standard `#7A3E38`.

Mehrere nicht eindeutig benannte PDFs (etwa Zeugnisse und Portfolios) werden nie geraten. In diesem Fall wird der gewünschte CV über den Setup-Dialog oder `documents.cv_pdf_path` festgelegt. Eingescannte oder rein schwarz-weiße CVs behalten die gemeinsame Typografie und das Raster; für die Akzentfarbe greift der Agent sicher auf `resume.accent_color` zurück.

`resume:` unterstützt Berufserfahrung, Ausbildung, Kompetenzgruppen, Sprachen und Zertifikate. Optionale PDF-Zeugnisse oder Arbeitsproben bleiben getrennte Quellunterlagen. Nur wenn sie ausdrücklich unter `attachments:` eingetragen sind, erstellt dieser Befehl zusätzlich eine lokale Anlagenmappe – ohne Upload oder Versand:

~~~bash
uv run job-agent resume render --include-attachments
~~~

## Einrichtung mit Codex, Claude Code oder CLI

Alle drei Wege verwenden dieselben Dateien und Befehle. Nur die Steuerung unterscheidet sich.

### Direkte CLI

~~~bash
uv run job-agent init --interactive
~~~

Die Einrichtung fragt nacheinander nach:

1. Kontakt- und Profilangaben für Anschreiben.
2. CV-Text- und PDF-Pfad.
3. Gewünschten Positionen oder Titeln.
4. Zusätzlichen Keywords.
5. Zielland, Stadt sowie Remote-/Hybrid-Präferenz.
6. Sperrwörtern und ausgeschlossenen Arbeitgebern.
7. Sachlicher Kurzbeschreibung, Kompetenzen und belegten Arbeitsproben.

Diese Daten landen ausschließlich in:

~~~text
.job-agent/candidate.yaml
.job-agent/search_profile.yaml
.job-agent/humanizer/private.de.md
~~~

Bei einer erneuten Einrichtung wird das vorhandene Profil geschützt. Nur wenn du es bewusst ersetzen möchtest:

~~~bash
uv run job-agent init --interactive --overwrite
~~~

### Codex oder Claude Code

Nach dem Clone kannst du dem jeweiligen Coding-Agenten diese Aufgabe geben:

> Richte den Job Application Agent lokal ein. Frage mich nur die Felder aus job-agent init --interactive ab. Schreibe persönliche Daten ausschließlich in .job-agent/, prüfe anschließend job-agent doctor und starte keine echte Bewerbung.

Danach bleibt der Ablauf identisch mit der CLI. Teile keine persönlichen Daten in README.md, config/, Commits oder Issues.

### Optional: öffentliche Humanizer-Baseline

Die private deutsche Policy funktioniert ohne Download. Optional kannst du eine fest gepinnte MIT-lizenzierte Baseline lokal ergänzen:

~~~bash
uv run job-agent humanizer bootstrap
~~~

Die private Policy hat Vorrang. Der Download wird nie automatisch wiederholt.

## Der tägliche Ablauf

Die folgenden Schritte unterscheiden bewusst zwischen **Suche** und **Bewerbungsnavigation**.

### 1. Öffentliche Stellen suchen

~~~bash
uv run job-agent search --live --top 20 --no-serpapi
~~~

- --no-serpapi sorgt für einen quota-freien Lauf ohne Google-API.
- Ohne dieses Flag ergänzt SerpApi die Quellen nur, wenn du lokal einen Key hinterlegt hast.
- Der Befehl erstellt keine Anschreiben und keine Bewerbungspakete.

Öffne anschließend:

~~~text
.job-agent/runs/<run>-search/search_results.md
.job-agent/runs/<run>-search/portal_results.md
~~~

portal_results.md zeigt die Treffer nach Quelle beziehungsweise Portal. search_results.md erklärt Score, Ausschlüsse und den Gesundheitsstatus jeder Quelle.

### 2. Nur konkrete Treffer als Pakete auswählen

Wähle Ränge aus search_results.md:

~~~bash
uv run job-agent create-packages \
  .job-agent/runs/<run>-search/search_results.json \
  --approve 1,3
~~~

Dieser Schritt erzeugt nur für die Ränge 1 und 3 eigene Paketordner. Eine erneute Policy-Prüfung und der Tracker verhindern versehentliche Pakete für bereits behandelte oder inzwischen ausgeschlossene Stellen.

### 3. Firmenfakten und Anschreiben prüfen

~~~bash
uv run job-agent research-company \
  .job-agent/runs/<run>-approved/<package>/job.json \
  --write
~~~

Die Recherche liest nur die öffentliche Stellenanzeige und höchstens eine direkt verlinkte Unternehmensseite. Sie speichert Quellen, Zeitstempel und belegte Fakten. Unbelegte Behauptungen werden nicht in das Anschreiben übernommen.

Prüfe anschließend mindestens:

~~~text
job.json
scorecard.md
company_research.md
cover_letter.md
cover_letter_quality.md
apply_checklist.md
~~~

### 4. Formular vor dem Ausführen analysieren

Für eine lokale Fixture:

~~~bash
uv run job-agent inspect-apply \
  .job-agent/runs/<run>-approved/<package>/job.json \
  --html fixtures/application_form_sample.html --write
~~~

Für eine öffentliche Bewerbungsseite, noch ohne Befüllen:

~~~bash
uv run job-agent inspect-apply \
  .job-agent/runs/<run>-approved/<package>/job.json \
  --browser --headed --write
~~~

Der Befehl schreibt form_fill_plan.md. Prüfe insbesondere Uploads, Pflichtfelder, Folge-Schritte und die Markierungen manual.

### 5. Freigeben und nur dann ausführen

~~~bash
uv run job-agent approve \
  .job-agent/runs/<run>-approved/<package>/job.json

uv run job-agent apply-approved \
  .job-agent/approvals/<approval>.json --execute --headed
~~~

apply-approved akzeptiert ausschließlich unveränderte und nicht abgelaufene Manifeste. Ohne --execute findet kein Submit statt.

## Suchprofil und Quellen

Das interaktive Setup erzeugt zum Beispiel folgende lokale Struktur:

~~~yaml
search:
  profile_configured: true
  target_roles:
    - Produktmanager
    - Product Owner
  keywords:
    - B2B
    - SaaS
  hard_exclusions:
    - Praktikum
    - Werkstudent
  employer_blacklist:
    - Beispielunternehmen
  preferred_locations:
    - Deutschland
    - remote
  required_location_terms:
    - Deutschland
    - remote
~~~

### So wirken die Filter

| Feld | Wirkung |
| --- | --- |
| target_roles | Stärkster Treffer für passende Stellentitel |
| keywords | Zusätzliche Relevanzsignale im Jobtext |
| hard_exclusions | Schließt Treffer beim Vorkommen aus |
| employer_blacklist | Schließt konkrete Unternehmen aus |
| preferred_locations | Bewertet bevorzugte Orte oder Remote höher |
| required_location_terms | Schließt klar unpassende Standorte aus |

### Deutsche und öffentliche Quellen

Das Setup konfiguriert öffentliche Quellen wie Arbeitsagentur, StepStone, LinkedIn, Arbeitnow, Freelancermap, RemoteOK, Remotive und ausgewählte deutsche Jobboards. Verfügbarkeit und HTML-Struktur einzelner Portale können sich ändern. Deshalb enthält jeder Run einen Source-Health-Report, statt fehlende Treffer stillschweigend zu verschweigen.

Für die Arbeitsagentur nutzt das Projekt die öffentliche, servergerenderte Jobsuche. Der frühere nicht dokumentierte Backend-Endpunkt kann mit HTTP 403 antworten, obwohl die öffentliche Suche verfügbar ist. Ein einzelner Ausfall blockiert weder die übrigen Quellen noch erzeugt er ein falsches „keine Stellen“-Ergebnis.

### Personio-Feeds

Für eine bekannte Firma kannst du einen öffentlichen Personio-XML-Feed lokal ergänzen:

~~~yaml
sources:
  personio:
    enabled: true
    feed_urls:
      - https://beispiel.jobs.personio.de/xml?language=de
~~~

Nutze nur öffentliche Feeds und dokumentiere neue Quellen mit Parser-Fixtures und Host-Policy.

## Browser-Automation und Freigaben

### Lesender Personio-Probe

Ein Probe navigiert nur lesend durch eine passende, bereits gefundene Personio-Stelle. Nicht-GET-Anfragen werden blockiert:

~~~bash
uv run job-agent probe-live \
  --from-search-results .job-agent/runs/<run>-search/search_results.json \
  --platform personio --read-only
~~~

Er meldet Erreichbarkeit, sichtbare Felder und Portalstatus. Er lädt kein Kandidatenprofil, befüllt nichts, lädt nichts hoch und sendet nicht ab.

### Unterstützte autonome Grenze

Der Submit-Pfad ist absichtlich eng:

- Direkte öffentliche Firmenformulare und Personio können nur verarbeitet werden, wenn jedes Pflichtfeld einen freigegebenen, sicheren Wert hat.
- Bei Personio darf ein weiterer Schritt nur mit einem geprüften Selektor und sicheren Folge-Instruktionen weitergehen.
- CAPTCHA, Login, MFA, JOIN, nicht unterstützte ATS, fehlende Finalevidenz und neue sensible Fragen stoppen.
- applied wird nur nach finaler Bestätigung oder geeigneter Anwendungsevidenz gesetzt.

E-Mail-Bewerbungen sind Entwürfe. Einen E-Mail-Versand gibt es nicht.

## Manuelle Nachbearbeitung

Wenn ein Lauf bewusst stoppt, sammelt die CLI offene Fälle zentral:

~~~bash
uv run job-agent needs-completion
~~~

Die Ausgabe enthält needs_completion, blocked_manual und blocked_captcha – jeweils mit Jobidentität, Paketpfad, URL und letzter Begründung. Diese Fälle bleiben als bestehende Bewerbungen im lokalen Tracker und werden bei späteren Suchläufen nicht erneut als neue Treffer angelegt. Jeder Suchlauf erinnert an ihre Anzahl.

Für die interaktive Auflösung fragt die CLI pro bestehendem Fall, ob er manuell abgeschlossen, verworfen, gezielt wieder in einen späteren Suchlauf eingereiht oder offen gehalten werden soll:

~~~bash
uv run job-agent needs-completion --review
~~~

Ein leeres Ergebnis lässt den Fall offen und sorgt für die nächste Erinnerung. `requeue` ist der normale Weg, einen offenen Fall bewusst wieder für einen späteren Suchlauf freizugeben. Alternativ kann ein manuell abgeschlossener Fall direkt dokumentiert werden:

~~~bash
uv run job-agent mark-status \
  .job-agent/runs/<run>-approved/<package>/job.json \
  --status applied \
  --method manual_user_reported \
  --evidence "Finale Bestätigung manuell geprüft"
~~~

Ein CAPTCHA oder Login bleibt damit sichtbar und nachverfolgbar, statt heimlich als Bewerbung markiert oder erneut als neue Stelle angelegt zu werden.

## Projektstruktur und Ausgaben

~~~text
job_application_agent/     Paket und CLI
config/                    PII-freie versionierte Vorlagen
fixtures/                  Lokale Parser- und Browser-Fixtures
templates/                 Anschreiben-Template
tests/                     Unit-, Integrations- und Browser-Tests
docs/                      Detaildokumentation
.job-agent/                Private lokale Daten und Runs, git-ignoriert
~~~

Ein Suchlauf schreibt unter .job-agent/runs/<run>-search/:

~~~text
search_results.md          Erklärte Shortlist
search_results.json        Maschinenlesbare Shortlist
portal_results.md          Treffer und Health nach Portal
cache/sanitized_jobs.json  Normalisierte Jobdaten ohne rohe HTML-Seiten
~~~

Ein freigegebenes Paket enthält unter anderem:

~~~text
job.json
scorecard.md
company_research.md
cover_letter.md
cover_letter.pdf
cover_letter_quality.md
application_route.md
form_fill_plan.md
apply_checklist.md
~~~

Ein lokal erzeugter CV liegt unter `.job-agent/documents/` als PDF, Markdown und JSON. Eine optionale `Bewerbungsunterlagen_<Nachname>.pdf` ist nur eine lokale Anlagenmappe und wird nicht automatisch in Portale hochgeladen.

## Tests und Entwicklung

Der CI-Workflow prüft Lizenz- und Paketmetadaten, private Dateipfade, Ruff, Pyright, die vollständige Testsuite und einen echten Chromium-Fixture-Smoke-Test.

Lokal:

~~~bash
uv sync --frozen --all-groups
uv run python -m playwright install chromium

uv run pytest
uv run ruff check .
uv run python -m pyright
uv build
~~~

Nur der Browser-Smoke-Test:

~~~bash
uv run pytest tests/test_browser_apply.py::test_local_browser_fixture_captures_final_submit_evidence
~~~

## Versionierung

Dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/):

- **MAJOR**: inkompatible Änderungen an CLI, Approval- oder Sicherheitsvertrag.
- **MINOR**: neue rückwärtskompatible Funktionen, Quellen oder Portalregeln.
- **PATCH**: rückwärtskompatible Fehlerbehebungen und Dokumentationskorrekturen.

Die Paketversion steht an zwei Stellen und wird gemeinsam gepflegt:

~~~text
pyproject.toml
job_application_agent/__init__.py
~~~

### Aktuelle Version: 0.3.0

- Portables, interaktives lokales Setup für Profil, Suchkriterien und Dokumentpfade.
- Keine versionierten persönlichen Such- oder Karrierepräferenzen.
- Öffentliche Arbeitsagentur-Suche statt des fehleranfälligen alten Backend-Endpunkts.
- Separate Liste für manuelle Nachbearbeitung mit needs-completion.
- Privater, faktischer CV-Generator mit Markdown-, JSON- und minimalistischem PDF-Export.
- Optionales, lokales PDF-Bündel für ausdrücklich angegebene Zeugnisse und Zertifikate.
- Approval- und Browser-Grenzen bleiben unverändert fail-closed.

## Erweiterungen

Neue Quellen oder Portal-Automationen dürfen nur ergänzt werden, wenn sie:

1. öffentlich ohne Login erreichbar sind;
2. eine Host-Allowlist und Redirect-Prüfung erhalten;
3. Parser-Fixtures und negative Sicherheitsfälle abdecken;
4. keine CAPTCHA- oder Login-Umgehung erfordern;
5. bei unbekannten Feldern oder fehlender Finalevidenz stoppen.

Weitere Hinweise stehen in [docs/portable-workflow.md](docs/portable-workflow.md) und [docs/source-roadmap.md](docs/source-roadmap.md).

## Mitwirken und Sicherheit

- Fehlerberichte und Funktionsvorschläge folgen [CONTRIBUTING.md](CONTRIBUTING.md).
- Sicherheitsrelevante Hinweise folgen [SECURITY.md](SECURITY.md) und gehören nicht in öffentliche Issues.
- Änderungen an Quellen oder Browserregeln benötigen positive und negative Tests.
- Persönliche Profile, Dokumente, Humanizer-Regeln und Laufzeitdaten dürfen niemals committed werden.
- Der Versionsverlauf steht in [CHANGELOG.md](CHANGELOG.md).

## Troubleshooting

### doctor meldet fehlendes Chromium

~~~bash
uv run python -m playwright install chromium
uv run job-agent doctor
~~~

### Suche liefert keine Ergebnisse

1. Öffne portal_results.md und prüfe den Source-Health-Status.
2. Prüfe Rollen, Ort und Sperrwörter in .job-agent/search_profile.yaml.
3. Starte bei Bedarf einen kontrollierten Testlauf mit --fixtures.
4. Ein 403 einer Quelle bedeutet nicht, dass alle anderen Portale keine Treffer liefern.

### Bestehendes Setup ersetzen

~~~bash
uv run job-agent init --interactive --overwrite
~~~

Sichere eigene Dokumente vorher außerhalb von .job-agent/. Der Befehl ersetzt nur die lokalen Profil- und Suchprofil-Dateien, wenn --overwrite angegeben ist.

## Lizenz

Dieses Projekt steht unter der [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).
