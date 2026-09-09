# Fehlersuche

## Dienstzustand

```bash
systemctl status mhi-bridge.service
journalctl -u mhi-bridge.service -n 100 --no-pager
```

Zusätzlich das rotierende Bridge-Log prüfen, falls konfiguriert.

## Gerät offline

Im `/status` auf `online`, `consecutive_errors` und den letzten Fehler achten. Danach zuerst Netzwerk prüfen:

```bash
ping -c 10 <WF-RAC-IP>
```

Bei mehreren Access Points kontrollieren, ob das Gerät am sinnvollen AP hängt.

## Wichtige Log-Marker

- `DEVICE_OFFLINE`: Kommunikation mehrfach fehlgeschlagen
- `DEVICE_RECOVERED`: Gerät wieder erreichbar
- `CMD_FAIL`: expliziter Steuerbefehl fehlgeschlagen
- `CMD_CONFLICT`: Betriebsart wegen gemeinsamer Außeneinheit abgewiesen
- `EXTERNAL_DRIFT`: Zustand wurde außerhalb von Loxone verändert

## Health meldet online, aber ein Gerät fehlt

`/health` ist absichtlich kompakt und meldet online, wenn mindestens ein Gerät erreichbar ist. Für Multi-Geräte-Systeme immer `/status` auswerten.

## HTTP 403 bei Steuerbefehlen

Der Aufruf kommt nicht von der freigegebenen Steuer-IP. Lesende Statusabfragen bleiben möglich.

## Nach Änderungen

```bash
python3 -m py_compile mhi_bridge.py
sudo systemctl restart mhi-bridge.service
curl -sS http://127.0.0.1:8091/status
```

Danach beide Geräte einzeln prüfen und erst anschließend einen bewusst harmlosen Steuerbefehl testen.
