# Security Policy

Diese Projekte laufen typischerweise im lokalen Smart-Home-Netz und einige Endpunkte können reale Geräte steuern. Sicherheit sollte deshalb nicht nur auf die Netzwerk-Firewall reduziert werden.

## Grundregeln

- produktive Zugangsdaten und Tokens niemals committen,
- Beispielkonfigurationen statt echter Konfigurationen veröffentlichen,
- Schreibzugriffe nach Möglichkeit auf die Steuerinstanz (z. B. Loxone) begrenzen,
- Dienste nur in vertrauenswürdigen Netzen bereitstellen,
- öffentliche Portfreigaben ins Internet vermeiden,
- Logs und Screenshots vor dem Teilen auf Geheimnisse und personenbezogene Daten prüfen.

## Sicherheitsproblem melden

Bitte offensichtliche Sicherheitslücken **nicht zuerst mit vollständigen Exploit-Details in einem öffentlichen Issue veröffentlichen**. Nutze nach Möglichkeit GitHubs private Security-Advisory-/Reporting-Funktion des Repositorys. Falls diese Funktion nicht verfügbar ist, eröffne zunächst ein knappes Issue ohne Secrets oder ausnutzbare Details, damit ein privater Kommunikationsweg vereinbart werden kann.

## Unterstützte Versionen

Solange das Projekt klein ist, wird grundsätzlich nur der aktuelle Stand des `main`-Branches bzw. das neueste Release aktiv gepflegt.
