# Loxone MHI WF-RAC Bridge

<!-- project-meta -->
> **Status:** Stable · **Current release:** `v3.3.0` · **License:** MIT · **Documentation:** Deutsch · **Issues/PRs:** Deutsch or English

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Loxone-Doku](docs/loxone.md) · [Troubleshooting](docs/troubleshooting.md)
<!-- /project-meta -->

Lokale Python-Bridge zur Einbindung von Mitsubishi Heavy Industries Klimageräten mit WF-RAC WLAN-Adaptern in Loxone.

Das Projekt verfolgt ein wichtiges Prinzip: **Polling ist ausschließlich lesend.** Die Klimageräte werden nur verändert, wenn ein expliziter Steuerbefehl eingeht. Dadurch bleiben Fernbedienung und andere Bedienwege weiterhin nutzbar.

## Funktionen

- lokales zyklisches Auslesen mehrerer WF-RAC-Geräte
- Power, Betriebsart, Solltemperatur, Lüfterstufe und Luftführung
- HTTP-API für Loxone
- explizite Write-Verifikation durch Rücklesen
- Retry-/Timeout-Logik
- Erkennung von Geräteausfällen und Recovery
- persistenter Loxone-Sollzustand (`intent`)
- Erkennung externer Änderungen (`EXTERNAL_DRIFT`)
- Gruppenlogik für mehrere Innengeräte an einer gemeinsamen Außeneinheit
- rotierende Logs
- Schreibzugriff nur von der freigegebenen Loxone-IP

## Architektur

```text
                   ┌──────── Fernbedienung / andere Bedienung
                   │
MHI WF-RAC Geräte ◄┼──── lokale WF-RAC Kommunikation
        ▲          │
        │          │
        │      mhi_bridge.py :8091
        │        ├─ passiver Poller
        │        ├─ State Cache
        │        ├─ Intent Cache
        │        ├─ Verifikation
        │        └─ Gruppen-/Konfliktlogik
        │                  ▲
        └──────────────────┤
                           │ HTTP
                         Loxone
```

## Warum ein Intent?

Der tatsächliche Gerätezustand und der zuletzt von Loxone gewünschte Zustand sind nicht immer dasselbe. Beispielsweise kann eine Fernbedienung verwendet werden oder ein Gerät nach dem Ausschalten kurz andere interne Werte melden.

Die Bridge speichert deshalb getrennt:

- **Ist-Zustand** des Geräts
- **Loxone-Intent** als zuletzt gewünschten Sollzustand

Diese Trennung verhindert unnötige Schreibbefehle beim normalen Polling.

## Gemeinsame Außeneinheit

Wenn mehrere Innengeräte an derselben Außeneinheit hängen, können bestimmte Kombinationen von Betriebsarten technisch unzulässig sein. Die Bridge kennt deshalb Gerätegruppen und verhindert widersprüchliche Modi, beispielsweise gleichzeitiges Heizen und Kühlen innerhalb derselben Gruppe.

## Voraussetzungen

- Linux, getestet auf DietPi/Debian
- Python 3
- lokale Netzwerkverbindung zu den WF-RAC-Adaptern
- passende WF-RAC Parser-/Protokollbibliothek
- Geräte-/Operator-Identität für die lokale Kommunikation

## Konfiguration

Produktive Dateien werden nicht veröffentlicht:

```text
config.json
identity.json
state/
logs/
```

Vorlagen:

- [`config.example.json`](config.example.json)
- [`identity.example.json`](identity.example.json)

Die Dateien enthalten Platzhalter und müssen für die eigene Anlage angepasst werden.

Unter `service.write_client_ip` wird die einzige entfernte IP eingetragen, die Steuerbefehle senden darf – typischerweise der Loxone Miniserver. Status-, Health- und Diagnose-Endpunkte bleiben lesbar.

## HTTP-API

### Gesamtstatus

```text
GET http://<HOST>:8091/status
GET http://<HOST>:8091/api/v1/status
```

### Health

```text
GET http://<HOST>:8091/health
```

Der Health-Endpunkt meldet `online=true`, wenn mindestens ein konfiguriertes Gerät erreichbar ist. Für eine vollständige Bewertung immer den Gesamtstatus bzw. alle Geräte betrachten.

### Geräte auflisten

```text
GET http://<HOST>:8091/api/v1/devices
```

### Einzelstatus

```text
GET http://<HOST>:8091/ac1/status
GET http://<HOST>:8091/ac2/status
```

### Steuerung

Beispiele:

```text
GET http://<HOST>:8091/ac1/power?value=1
GET http://<HOST>:8091/ac1/temperature?value=22.5
GET http://<HOST>:8091/ac1/mode?value=cool
GET http://<HOST>:8091/ac1/fan?value=auto
GET http://<HOST>:8091/ac1/direction?value=...
```

Mehrere Werte können über den `set`-Endpunkt kombiniert werden.

Alle schreibenden Geräte-Endpunkte akzeptieren nur die freigegebene Steuer-IP sowie localhost. Status- und Diagnose-Endpunkte bleiben lesbar.

## Wichtige Log-Marker

Die Bridge erzeugt bewusst aussagekräftige Marker:

- `CMD_START` – Steuerbefehl begonnen
- `CMD_OK` – Befehl erfolgreich verifiziert
- `CMD_FAIL` – Befehl fehlgeschlagen
- `CMD_CONFLICT` – Gruppen-/Moduskonflikt verhindert
- `DEVICE_OFFLINE` – Gerät nach Kommunikationsfehlern offline
- `DEVICE_RECOVERED` – Kommunikation wiederhergestellt
- `EXTERNAL_DRIFT` – Ist-Zustand wurde außerhalb von Loxone verändert

## Passive Polling-Garantie

Der Polling-Pfad darf selbst keine Steuerbefehle erzeugen. Das ist ein zentrales Designziel dieses Projekts. Änderungen an der Polling-Logik sollten deshalb besonders sorgfältig geprüft werden.

## Loxone

Siehe [`docs/loxone.md`](docs/loxone.md).

## Fehlersuche

Siehe [`docs/troubleshooting.md`](docs/troubleshooting.md).

## Reverse Engineering

Protokollbeobachtungen und neue Funktionen sollten in der Dokumentation immer als **verifiziert**, **experimentell** oder **unbekannt** gekennzeichnet werden. So bleibt nachvollziehbar, was am realen Gerät bestätigt wurde und was nur aus Telegrammen/Verhalten abgeleitet ist.

## Lizenz

MIT License – siehe [`LICENSE`](LICENSE).
