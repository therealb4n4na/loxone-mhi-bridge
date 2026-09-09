# Loxone-Einbindung

## Status

Gesamtstatus:

```text
http://<DIETPI-IP>:8091/status
```

Einzelgerät:

```text
http://<DIETPI-IP>:8091/ac1/status
```

Empfohlenes Loxone-Polling: etwa 10–30 Sekunden. Die Bridge pollt die Geräte intern selbst und Loxone liest nur den Cache.

## Schreiben

Beispiele:

```text
/ac1/power?value=1
/ac1/temperature?value=22.5
/ac1/mode?value=cool
/ac1/fan?value=auto
```

Die Bridge führt die eigentliche Gerätekommunikation aus und verifiziert den Zustand anschließend.

## Gemeinsame Außeneinheit

Bei Multi-Split-Systemen darf Loxone nicht davon ausgehen, dass jedes Innengerät völlig unabhängig seine Betriebsart wählen kann. Die Bridge prüft Konflikte innerhalb der konfigurierten Gruppe und lehnt unzulässige Kombinationen ab.

## Externe Bedienung

Eine Änderung über Fernbedienung oder eine andere Bedienoberfläche wird vom Poller eingelesen. Die Bridge protokolliert eine Abweichung vom zuletzt bekannten Loxone-Intent als `EXTERNAL_DRIFT`; der Poller schreibt den alten Loxone-Wert nicht automatisch wieder zurück.

Das verhindert einen unerwünschten "Kampf" zwischen Loxone und manueller Bedienung.
