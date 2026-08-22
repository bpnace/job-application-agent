# Sicherheitsrichtlinie

## Unterstützte Versionen

Sicherheitskorrekturen werden für die jeweils aktuelle veröffentlichte Minor-Version bereitgestellt.

| Version | Unterstützt |
| --- | --- |
| 0.3.x | Ja |
| < 0.3 | Nein |

## Sicherheitsproblem vertraulich melden

Bitte veröffentliche Sicherheitsprobleme, Datenschutzfunde, Zugangsdaten oder Umgehungsmöglichkeiten **nicht** als öffentliches Issue.

Nutze stattdessen auf GitHub den Bereich **Security → Advisories → Report a vulnerability**. Beschreibe dort:

- betroffene Version und Plattform;
- reproduzierbare Schritte;
- mögliche Auswirkungen;
- ob Kandidatendaten, Dokumente, Portalzugänge oder Submit-Grenzen betroffen sind.

Es werden keine echten Kandidatendaten oder Zugangsdaten zur Reproduktion benötigt. Verwende synthetische Fixtures und entferne Secrets aus Logs und Screenshots.

## Schutz vor unbeabsichtigter Veröffentlichung

Jeder öffentliche Push und jedes Release muss den in [docs/publication-safety.md](docs/publication-safety.md) beschriebenen Tiefen-Audit bestehen. Der Gate prüft Git-Historie, Commit-Metadaten, Dateinamen, Inhalte, Notebooks, undurchsichtige Binärdateien und Build-Artefakte. Eine private lokale Sperrliste ergänzt generische Secret- und PII-Muster. Fundmeldungen nennen nur Kategorie und Ort, nie den gefundenen Wert.

## Sicherheitsgrenzen

Dieses Projekt unterstützt ausdrücklich keine Umgehung von CAPTCHA, Login, MFA oder Portal-Schutzmaßnahmen. Ein Befund, der nur durch das Aufheben dieser Grenzen entsteht, wird nicht als unterstützter Anwendungsfall behandelt.
