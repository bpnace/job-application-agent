# Sicherer Veröffentlichungsprozess

Dieses Dokument definiert den verbindlichen Privacy- und Secret-Gate für öffentliche Branches, Tags und Releases. Der Prozess ist fail-closed: Ein unklarer oder nicht automatisch prüfbarer Fund stoppt die Veröffentlichung.

## Warum die frühere Prüfung nicht genügte

Die frühere Datenfreigabe war keine einzelne vergessene Zeichenfolge, sondern eine Kette mehrerer Schutzlücken:

1. Reale Profildaten wurden als Defaults, Beispielwerte und Testdaten verwendet. Dadurch sahen sie wie normaler Quellcode aus.
2. Lokale Pfade sowie ausgeführte Notebook-Ausgaben wurden versioniert. Metadaten und Outputs wurden nicht als eigene Angriffsfläche behandelt.
3. `.gitignore` wurde als Sicherheitsgrenze verstanden. Git ignoriert damit jedoch nur neue, ungetrackte Dateien; bereits committed Inhalte und ältere Commits bleiben erreichbar.
4. Die damalige CI prüfte nur eine kleine Liste riskanter Dateipfade. Inhalte, Commit-Metadaten, Secrets, Binärdateien, Archive und Historie wurden nicht tief geprüft.
5. Der manuelle Audit lief auf einem bereinigten Arbeitsbranch. Der tatsächlich öffentliche Default-Branch, weitere öffentliche Refs und deren voneinander getrennte Historien waren nicht dieselbe Datenmenge.

Die zentrale Lehre lautet: Geprüft werden muss das exakte Objekt, das veröffentlicht wird – nicht nur das aktuelle Arbeitsverzeichnis.

## Mehrstufige Schutzarchitektur

### 1. Private Daten bleiben lokal

`job-agent init` schreibt Kandidatenprofil, Dokumente, Humanizer-Regeln und Laufzeitstatus ausschließlich unter `.job-agent/`. Zusätzlich wird `.job-agent/privacy/blocklist.txt` erzeugt. Sie enthält individuelle Tripwires, beispielsweise Namen und Kontaktdaten aus dem privaten Profil, und bleibt durch `.gitignore` außerhalb des Git-Index.

Die Sperrliste wird nie in Logs ausgegeben. Der Scanner meldet ausschließlich Fundkategorie und Git-Pfad beziehungsweise Commit-ID.

Im Source-Checkout aktiviert `job-agent init` außerdem `.githooks/pre-push`. Der Hook liest die von Git angekündigten Push-Refs und scannt jeden zu übertragenden Commit samt erreichbarer Historie. Damit wird auch ein versehentlicher Push eines alten lokalen Branches blockiert. Ein Hook kann bewusst mit Git-Optionen umgangen werden und ersetzt deshalb weder den dokumentierten Pre-Publish-Audit noch CI.

### 2. Der Git-Baum ist die Quelle der Wahrheit

`scripts/check_public_repo.py` liest Dateien mit `git ls-tree` und `git cat-file` direkt aus den ausgewählten Commits. Dadurch kann weder ein anderer Checkout noch eine lokale Löschung einen historischen Leak verdecken.

Geprüft werden:

- alle Commits, die von den ausgewählten Refs erreichbar sind;
- Autor-, Committer- und Commit-Message-Metadaten;
- Pfadnamen und private Laufzeitverzeichnisse;
- E-Mail-Adressen außerhalb reservierter Beispieldomains;
- private Schlüssel, bekannte Tokenformate, Zugangsdaten in URLs und nicht leere Secret-Zuweisungen;
- lokale macOS-, Linux- und Windows-Benutzerpfade;
- individuelle Begriffe aus der lokalen Sperrliste;
- ausgeführte Jupyter-Outputs und Execution Counts;
- Lebensläufe, PDFs, Office-Dateien, Bilder, Schlüsselcontainer, Datenbanken und unbekannte Binärdateien;
- Archive, die ohne explizite Inhaltsprüfung nicht veröffentlicht werden dürfen.

Eine historische Binärdatei darf nur nach manueller Sichtprüfung und ausschließlich über ihren exakten SHA-256-Hash freigegeben werden. Änderungen am Inhalt verlieren die Freigabe automatisch. Neue und nicht mehr benötigte Binärdateien werden entfernt statt pauschal erlaubt.

### 3. CI prüft die vollständige erreichbare Historie

GitHub Actions verwendet `fetch-depth: 0` und führt den generischen Gate gegen `HEAD` samt Historie aus. Damit wird eine Datei auch dann gefunden, wenn sie im aktuellen Commit bereits gelöscht wurde.

CI besitzt bewusst keine private Sperrliste. Der individuelle Blocklisten-Check muss deshalb lokal vor dem Push laufen.

### 4. Release-Artefakte werden separat geöffnet

`scripts/prepublish_audit.py` baut Wheel und sdist in ein temporäres Verzeichnis und scannt jedes enthaltene Mitglied. So wird geprüft, was Nutzer tatsächlich herunterladen, nicht nur was Git anzeigt.

### 5. Der öffentliche Remote-Zustand wird anonym nachgeprüft

Nach dem Push klont derselbe Audit die öffentliche HTTPS-URL ohne Credential Helper als frischen Mirror. Er holt zusätzlich öffentliche Pull-Request-Refs und scannt alle dort sichtbaren Refs samt Historie. Dadurch werden falsche Annahmen über lokale Branches, veraltete Remote-Refs oder Authentifizierungsrechte vermieden.

## Verbindliche Befehle

Vor einem Push von `HEAD`:

```bash
uv run job-agent init
uv run python scripts/prepublish_audit.py
```

Vor `git push --all`, einer Sichtbarkeitsänderung oder einer Veröffentlichung mehrerer Refs:

```bash
uv run python scripts/prepublish_audit.py --all-local-refs
```

Nach dem öffentlichen Push:

```bash
uv run python scripts/prepublish_audit.py \
  --remote-only \
  --remote-url https://github.com/OWNER/REPOSITORY.git \
  --github-repo OWNER/REPOSITORY
```

Der zweite Schritt ergänzt den ersten; er ersetzt ihn nicht.

## Was weiterhin manuell geprüft wird

Automatische Mustererkennung kann keine vollständige semantische PII-Erkennung garantieren. `--github-repo` prüft Repository-Metadaten, Issues, Kommentare, Pull-Request-Reviews, Actions-Logs und -Artefakte sowie Release-Dateien. Vor der ersten öffentlichen Sichtbarkeit werden darüber hinaus zusätzlich geprüft:

- Wiki, Discussions und GitHub Pages, sofern aktiviert;
- Topics, Homepage und Social Preview;
- Namen und E-Mail-Adressen in sämtlichen öffentlichen Commit-Metadaten.

Private Vulnerability Reports und GitHub Secret Scanning sollen aktiviert bleiben. Diese GitHub-Funktionen sind zusätzliche Detektoren, kein Ersatz für den lokalen Gate.

## Vorgehen bei einem Fund

1. Veröffentlichung sofort stoppen; keine weiteren Tags, Releases oder Mirrors erzeugen.
2. Betroffenen Wert außerhalb öffentlicher Logs identifizieren und, falls es ein Secret ist, sofort widerrufen oder rotieren.
3. Prüfen, ob nur der aktuelle Commit oder bereits öffentliche Historie, PR-Refs, Forks, Caches oder Release-Artefakte betroffen sind.
4. Historie nur mit einem dokumentierten, repo-weiten Bereinigungsplan neu schreiben.
5. Lokalen Audit, CI und anonymen Remote-Audit vollständig erneut ausführen.

Ein grüner normaler Testlauf, ein sauberer `git status` oder ein Eintrag in `.gitignore` ist niemals allein eine Veröffentlichungsfreigabe.
