# Contributing

Beiträge sind willkommen. Issues und Pull Requests können auf **Deutsch oder Englisch** erstellt werden.

## Bevor du ein Issue erstellst

Bitte prüfe zuerst:

1. ob das Verhalten mit der aktuellen Version reproduzierbar ist,
2. ob [`docs/troubleshooting.md`](docs/troubleshooting.md) den Fehler bereits beschreibt,
3. ob Logs oder Statusdaten den Fehler eingrenzen,
4. ob Zugangsdaten, Tokens, interne IP-Adressen oder andere private Daten aus Logs/Screenshots entfernt wurden.

## Bug Reports

Ein guter Bug Report enthält möglichst:

- verwendete Hardware / Gateway / Adapter,
- Betriebssystem und Python-Version,
- Projektversion bzw. Git-Tag,
- genaue Schritte zum Reproduzieren,
- erwartetes und tatsächliches Verhalten,
- relevante Logzeilen,
- bereits durchgeführte Tests.

Bitte **niemals** Passwörter, API-Keys, Tokens, private Schlüssel oder vollständige produktive Konfigurationsdateien veröffentlichen.

## Pull Requests

Änderungen sollten:

- einen klar abgegrenzten Zweck haben,
- bestehendes Verhalten nicht unbeabsichtigt verändern,
- verständlich kommentiert sein, wenn die Logik nicht offensichtlich ist,
- bei neuen Funktionen die README bzw. passende Datei unter `docs/` aktualisieren,
- keine produktiven Zugangsdaten, Logs, State-Dateien oder Backups enthalten.

Vor einem Pull Request bitte mindestens die Python-Syntax prüfen:

```bash
python3 -m py_compile <script.py>
```

Bei Änderungen an Steuerbefehlen oder Gerätekommunikation bitte zusätzlich beschreiben, **wie die Änderung real getestet wurde**.

## Verifizierungsgrad

Bei Reverse Engineering oder gerätespezifischen Erkenntnissen bitte möglichst kennzeichnen:

- **Verifiziert** – mehrfach am realen Gerät bestätigt
- **Experimentell** – plausibel und getestet, aber noch nicht ausreichend breit bestätigt
- **Unbekannt** – beobachtet, Bedeutung oder Ursache noch offen

Das erleichtert anderen Nutzern die Einschätzung und verhindert, dass Vermutungen später als gesicherte Fakten weitergegeben werden.
