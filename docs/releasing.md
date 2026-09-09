# Release-Prozess

Das Projekt verwendet **Semantic Versioning** (`MAJOR.MINOR.PATCH`).

- `MAJOR`: inkompatible Änderungen an API, Konfiguration oder Verhalten
- `MINOR`: rückwärtskompatible neue Funktionen
- `PATCH`: rückwärtskompatible Fehlerbehebungen und Dokumentationskorrekturen

## Ablauf

1. Änderung implementieren und real testen.
2. README und technische Dokumentation aktualisieren.
3. `CHANGELOG.md` unter `Unreleased` ergänzen.
4. Syntax-/Plausibilitätsprüfung durchführen.
5. Commit auf `main` erstellen.
6. Changelog von `Unreleased` in die neue Versionsnummer überführen.
7. Annotierten Git-Tag erstellen und nach GitHub pushen.
8. Optional auf GitHub aus dem Tag ein Release mit den Changelog-Notizen erstellen.

Beispiel:

```bash
git tag -a v1.2.0 -m "Release v1.2.0"
git push origin v1.2.0
```

Releases sollen nur Zustände markieren, die auf der realen Zielhardware ausreichend getestet wurden.
