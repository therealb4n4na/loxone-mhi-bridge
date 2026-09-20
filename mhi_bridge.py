#!/usr/bin/env python3
"""
Mitsubishi Heavy Industries WF-RAC -> Loxone Bridge
===================================================

Zweck
-----
Dauerhaft laufende HTTP-Bridge fuer die beiden MHI-Klimageraete. Sie liest die
WF-RAC-Adapter zyklisch PASSIV aus und setzt nur dann Werte, wenn ein expliziter
HTTP-Befehl von Loxone kommt. Die Bridge merkt sich ausserdem den zuletzt von
Loxone gewollten Sollzustand (Intent), damit Ein/Aus-Sequenzen und parallele
Befehle stabil bleiben.

Datenfluss
----------
Loxone -> HTTP :8091 -> RequestHandler -> set_device() -> WF-RAC
WF-RAC -> polling_loop() -> fetch_status() -> interner state -> Loxone /status

Wichtige Endpunkte
------------------
/status bzw. /api/v1/status   -> Gesamtstatus aller Geraete
/health                       -> online=true, wenn mindestens ein Geraet erreichbar
/api/v1/devices               -> konfigurierte Geraete auflisten
/ac1/status, /ac2/status      -> kompakter Einzelgeraete-Status
/ac1/set?...                  -> mehrere Werte in einem Befehl setzen
/ac1/power?value=...          -> Einzelwert setzen; analog mode/fan/temperature/direction

Code-Leseplan / Fehlersuche
---------------------------
load_config()                 -> config.json laden, Defaults setzen und validieren
api_call_unlocked()           -> eigentliche HTTP-Kommunikation zum WF-RAC
fetch_status()                -> Status eines Innengeraets lesen und speichern
set_device()                  -> zentrale Befehlslogik inkl. Retry/Verifikation
record_loxone_intent()        -> Loxone-Sollzustand merken
persist_loxone_intents()      -> Sollzustand fuer Neustarts auf Disk sichern
enforce_group_conflict()      -> ungueltige Betriebsarten am gemeinsamen AG verhindern
merge_power_on_query()        -> beim Einschalten passende Sollwerte zusammenfassen
verify_requested_changes()    -> nach Befehlen Soll/Ist vergleichen
polling_loop()                -> ausschliesslich passives zyklisches Lesen
mark_offline()                -> Kommunikationsfehler im Status/Log markieren
RequestHandler.do_GET()       -> HTTP-Routing und Fehlercodes
main()                        -> Polling-Thread + HTTP-Server starten

Wichtig bei Fehlern
-------------------
- Polling ist bewusst PASSIV: Ein normaler Poll darf niemals selbst einen
  Schreibbefehl ausloesen. Fernbedienung/Smart-M-Air bleiben daher grundsaetzlich
  moeglich; die Bridge protokolliert Abweichungen als EXTERNAL_DRIFT.
- systemd "active" beweist nur, dass die Bridge laeuft. Fuer die Geraete sind die
  Felder online/consecutive_errors bzw. die DEVICE_OFFLINE/RECOVERED-Logs relevant.
- Kurze WF-RAC-Aussetzer werden mit Retries abgefangen. Erst wiederholte Fehler
  sind ein belastbarer Hinweis auf WLAN/Adapter-Probleme.
- Beide Innengeraete haengen an derselben Ausseneinheit (Gruppe "outdoor1").
  enforce_group_conflict() verhindert deshalb widerspruechliche Modi, z.B.
  gleichzeitiges Heizen und Kuehlen.
- Power-Befehle werden nach dem Schreiben real zurueckgelesen/verifiziert. Das
  verhindert, dass Loxone einen Zustand zeigt, den das Geraet nicht uebernommen hat.
- Alle schreibenden Geräte-Endpunkte akzeptieren nur die konfigurierte Steuer-IP
  (typisch Loxone) sowie localhost. Status-, Health- und Diagnose-Endpunkte
  bleiben lesbar.
- Beim Einschalten werden kurz zuvor von Loxone gesendete Mode/Fan/Temperaturwerte
  gesammelt und gemeinsam angewendet. Nach AUS werden typische Eco/Standby-Werte
  fuer ein definiertes Zeitfenster ignoriert.
- Der persistente Intent liegt standardmaessig in state/loxone-intent.json.
- Ausfuehrliche Logs liegen rotierend unter logs/mhi-bridge.log und zusaetzlich
  im journal. Besonders nuetzliche Marker: CMD_START, CMD_OK, CMD_FAIL,
  CMD_CONFLICT, DEVICE_OFFLINE, DEVICE_RECOVERED und EXTERNAL_DRIFT.
"""

import json
import logging
import os
import re
import signal
import sys
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(
    os.environ.get("MHI_BRIDGE_CONFIG", SCRIPT_DIR / "config.json")
).resolve()

MODE_VALUES = {
    "auto": 0,
    "cool": 1,
    "heat": 2,
    "fan_only": 3,
    "dry": 4,
}
MODE_NAMES = {value: key for key, value in MODE_VALUES.items()}

FAN_VALUES = {
    "auto": 0,
    "quiet": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
}
FAN_NAMES = {value: key for key, value in FAN_VALUES.items()}

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
BRIDGE_VERSION = "3.3.0"

# Schreibbefehle werden zusätzlich zur Firewall auf die in config.json
# konfigurierte Steuer-IP begrenzt. Status-/Diagnose-Endpunkte bleiben lesbar.

CONTROL_STATE_KEYS = (
    "power",
    "mode",
    "target_temperature",
    "fan",
    "vertical_swing_raw",
    "horizontal_swing_raw",
    "three_d_auto",
    "error",
)


class ConfigurationError(RuntimeError):
    pass


class ModeConflictError(RuntimeError):
    def __init__(
        self,
        *,
        requested_device,
        requested_mode,
        active_device,
        active_name,
        active_mode,
        group_id,
    ):
        super().__init__(
            f"Moduskonflikt: {requested_device}={requested_mode}, "
            f"aber {active_name} ({active_device}) läuft auf {active_mode}"
        )
        self.details = {
            "success": False,
            "error": "mode_conflict",
            "requested_device": requested_device,
            "requested_mode": requested_mode,
            "active_device": active_device,
            "active_name": active_name,
            "active_mode": active_mode,
            "group": group_id,
        }


def load_json(path):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as error:
        raise ConfigurationError(
            f"Konfigurationsdatei fehlt: {path}"
        ) from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Ungültiges JSON in {path}: {error}"
        ) from error


def resolve_config_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = CONFIG_FILE.parent / path
    return path.resolve()


def validate_identifier(value, field_name):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ConfigurationError(
            f"{field_name} muss mit einem Kleinbuchstaben beginnen und darf "
            "nur a-z, 0-9, _ und - enthalten"
        )
    return value


def load_config():
    config = load_json(CONFIG_FILE)

    if int(config.get("schema_version", 0)) != 1:
        raise ConfigurationError(
            "Nicht unterstützte schema_version; erwartet wird 1"
        )

    service = config.setdefault("service", {})
    service.setdefault("listen_address", "0.0.0.0")
    service.setdefault("listen_port", 8091)
    service.setdefault("write_client_ip", "192.168.1.50")
    service.setdefault("poll_interval", 15)
    service.setdefault("min_request_gap", 1.1)
    service.setdefault("request_timeout", 30)
    service.setdefault("inter_device_delay", 2.0)
    service.setdefault("command_dedupe_window", 2.0)
    # Zusätzliche Versuche nach einem fehlgeschlagenen Steuerbefehl.
    # 2 bedeutet: maximal 3 Versuche insgesamt.
    service.setdefault("command_retries", 2)
    service.setdefault("retry_delay", 1.5)
    # Power-Befehle werden nach dem Schreiben durch ein echtes getAirconStat
    # verifiziert. Das verhindert, dass Loxone AUS anzeigt, obwohl das
    # Innengerät wegen eines kurzen WF-RAC-Aussetzers weiterläuft.
    service.setdefault("power_verify_delay", 1.0)
    # Beim Einschalten kurz warten, damit parallel von Loxone kommende
    # Mode/Fan/Temperatur-Befehle zuerst als Sollzustand erfasst werden.
    service.setdefault("power_on_sync_delay", 0.75)
    # Steuerwerte, die Loxone kurz VOR power=1 sendet, gelten als Start-Kandidaten.
    # Ältere Werte aus dem ausgeschalteten Standby (z.B. Eco/Heizen) werden nicht
    # für einen späteren Start verwendet.
    service.setdefault("pre_power_control_window", 20.0)
    # Direkt nach einem expliziten AUS gibt Loxone bei AC-Control offenbar
    # Standby/Eco-Werte (z.B. Heizen + Eco-Temperatur) aus. Diese Werte dürfen
    # niemals als Vorstart-Sollzustand interpretiert werden.
    service.setdefault("post_power_off_ignore_window", 30.0)
    # Wenn Loxone AUS verlangt, wird dieser Wunsch nach einer späteren
    # Wiedererreichbarkeit des WF-RAC automatisch durchgesetzt.
    # Wenn Loxone EIN verlangt und eine Fernbedienung Mode/Temperatur/Fan
    # verändert, korrigiert die Bridge bekannte Sollwerte beim nächsten Poll.
    # Beim Einschalten den zuletzt von Loxone befohlenen Sollzustand
    # (Mode/Temperatur/Fan/Luftstrom) gemeinsam mit Power=1 anwenden.
    service.setdefault("sync_controls_on_power_on", True)
    # Persistenter Loxone-Sollzustand. Dadurch bleiben Power-Intent und
    # Steuerwerte auch nach einem Bridge-Neustart bekannt.
    service.setdefault("intent_file", "state/loxone-intent.json")
    # Ausführliches, rotierendes Log zusätzlich zu systemd/journald.
    service.setdefault("log_level", "INFO")
    service.setdefault("log_file", "logs/mhi-bridge.log")
    service.setdefault("log_max_bytes", 10 * 1024 * 1024)
    service.setdefault("log_backup_count", 5)

    service["listen_port"] = int(service["listen_port"])
    service["poll_interval"] = float(service["poll_interval"])
    service["min_request_gap"] = float(service["min_request_gap"])
    service["request_timeout"] = float(service["request_timeout"])
    service["inter_device_delay"] = float(service["inter_device_delay"])
    service["command_dedupe_window"] = float(service["command_dedupe_window"])
    service["command_retries"] = int(service["command_retries"])
    service["retry_delay"] = float(service["retry_delay"])
    service["power_verify_delay"] = float(service["power_verify_delay"])
    service["power_on_sync_delay"] = float(service["power_on_sync_delay"])
    service["pre_power_control_window"] = float(service["pre_power_control_window"])
    service["post_power_off_ignore_window"] = float(service["post_power_off_ignore_window"])
    service["sync_controls_on_power_on"] = bool(service["sync_controls_on_power_on"])
    service["intent_file"] = str(resolve_config_path(service["intent_file"]))
    service["log_level"] = str(service["log_level"]).upper()
    service["log_file"] = str(resolve_config_path(service["log_file"]))
    service["log_max_bytes"] = int(service["log_max_bytes"])
    service["log_backup_count"] = int(service["log_backup_count"])

    if not 1 <= service["listen_port"] <= 65535:
        raise ConfigurationError("listen_port ist ungültig")
    if service["poll_interval"] < 10:
        raise ConfigurationError("poll_interval darf nicht kleiner als 10 sein")
    if service["min_request_gap"] < 1.0:
        raise ConfigurationError("min_request_gap darf nicht kleiner als 1.0 sein")
    if service["request_timeout"] < 1:
        raise ConfigurationError("request_timeout darf nicht kleiner als 1 sein")
    if service["inter_device_delay"] < 0:
        raise ConfigurationError("inter_device_delay darf nicht negativ sein")
    if service["command_dedupe_window"] < 0:
        raise ConfigurationError("command_dedupe_window darf nicht negativ sein")
    if not 0 <= service["command_retries"] <= 10:
        raise ConfigurationError("command_retries muss zwischen 0 und 10 liegen")
    if service["retry_delay"] < 0:
        raise ConfigurationError("retry_delay darf nicht negativ sein")
    if service["power_verify_delay"] < 0:
        raise ConfigurationError("power_verify_delay darf nicht negativ sein")
    if service["power_on_sync_delay"] < 0:
        raise ConfigurationError("power_on_sync_delay darf nicht negativ sein")
    if service["pre_power_control_window"] < 0:
        raise ConfigurationError("pre_power_control_window darf nicht negativ sein")
    if service["post_power_off_ignore_window"] < 0:
        raise ConfigurationError("post_power_off_ignore_window darf nicht negativ sein")
    if service["log_level"] not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError("log_level ist ungültig")
    if service["log_max_bytes"] < 0:
        raise ConfigurationError("log_max_bytes darf nicht negativ sein")
    if service["log_backup_count"] < 0:
        raise ConfigurationError("log_backup_count darf nicht negativ sein")

    parser_path = config.get(
        "parser_path",
        "/opt/mhi-wfrac/custom_components/mitsubishi_wf_rac",
    )
    config["parser_path"] = str(resolve_config_path(parser_path))

    identity_file = config.get("identity_file", "identity.json")
    config["identity_file"] = str(resolve_config_path(identity_file))

    groups = config.get("groups", [])
    if not isinstance(groups, list) or not groups:
        raise ConfigurationError("Mindestens eine Außengerätegruppe ist erforderlich")

    group_map = {}
    for group in groups:
        group_id = validate_identifier(group.get("id"), "groups[].id")
        if group_id in group_map:
            raise ConfigurationError(f"Doppelte Gruppen-ID: {group_id}")
        policy = group.get("conflict_policy", "reject")
        if policy not in {"reject", "off"}:
            raise ConfigurationError(
                f"Ungültige conflict_policy für {group_id}: {policy}"
            )
        group["name"] = str(group.get("name") or group_id)
        group["conflict_policy"] = policy
        group_map[group_id] = group

    devices = config.get("devices", [])
    if not isinstance(devices, list) or not devices:
        raise ConfigurationError("Mindestens ein Klimagerät ist erforderlich")

    device_map = {}
    alias_map = {}
    for device in devices:
        device_id = validate_identifier(device.get("id"), "devices[].id")
        if device_id in device_map:
            raise ConfigurationError(f"Doppelte Geräte-ID: {device_id}")

        alias = device.get("alias")
        if alias:
            alias = validate_identifier(alias, f"Alias von {device_id}")
            if alias in alias_map or alias in device_map:
                raise ConfigurationError(f"Doppelter Alias: {alias}")
            alias_map[alias] = device_id
        else:
            alias = None

        group_id = device.get("group")
        if group_id not in group_map:
            raise ConfigurationError(
                f"Gerät {device_id} verweist auf unbekannte Gruppe {group_id}"
            )

        ip = str(device.get("ip", "")).strip()
        aircon_id = str(device.get("aircon_id", "")).strip()
        if not ip:
            raise ConfigurationError(f"IP-Adresse für {device_id} fehlt")
        if not aircon_id:
            raise ConfigurationError(f"aircon_id für {device_id} fehlt")

        device["id"] = device_id
        device["alias"] = alias
        device["name"] = str(device.get("name") or device_id)
        device["ip"] = ip
        device["port"] = int(device.get("port", 51443))
        device["aircon_id"] = aircon_id

        if not 1 <= device["port"] <= 65535:
            raise ConfigurationError(f"Ungültiger Port für {device_id}")

        device_map[device_id] = device

    config["group_map"] = group_map
    config["device_map"] = device_map
    config["alias_map"] = alias_map
    return config


CONFIG = load_config()
SERVICE = CONFIG["service"]
WRITE_CLIENT_IP = str(SERVICE.get("write_client_ip", "192.168.1.50"))
DEVICES = CONFIG["device_map"]
GROUPS = CONFIG["group_map"]
ALIASES = CONFIG["alias_map"]


def setup_logging():
    logger = logging.getLogger("mhi_bridge")
    logger.setLevel(getattr(logging, SERVICE["log_level"]))
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_path = Path(SERVICE["log_file"])
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=SERVICE["log_max_bytes"],
            backupCount=SERVICE["log_backup_count"],
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as error:
        # Der Dienst soll niemals nur wegen eines nicht beschreibbaren
        # Log-Verzeichnisses ausfallen. journald bleibt weiterhin aktiv.
        logger.warning(
            "Datei-Logging konnte nicht aktiviert werden: path=%s error=%s",
            log_path,
            error,
        )

    return logger


LOGGER = setup_logging()

sys.path.insert(0, CONFIG["parser_path"])

from wfrac.models.aircon import AirconStat  # noqa: E402
from wfrac.rac_parser import RacParser  # noqa: E402

parser = RacParser()
state_lock = threading.Lock()
stop_event = threading.Event()


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_identity():
    identity_file = Path(CONFIG["identity_file"])
    identity_file.parent.mkdir(parents=True, exist_ok=True)

    if identity_file.exists():
        identity = load_json(identity_file)
    else:
        identity = {
            "operator_id": f"dietpi-{str(uuid.uuid4())[7:]}",
            "device_id": f"dietpi-device-{uuid.uuid4().hex[21:]}",
        }
        with identity_file.open("w", encoding="utf-8") as file:
            json.dump(identity, file, indent=2)
            file.write("\n")
        identity_file.chmod(0o600)

    if not identity.get("operator_id") or not identity.get("device_id"):
        raise ConfigurationError(
            f"operator_id oder device_id fehlt in {identity_file}"
        )

    return identity


IDENTITY = load_identity()

INTENT_KEYS = ("mode", "temperature", "fan", "direction")
intent_file_lock = threading.Lock()


def load_persisted_intents():
    path = Path(SERVICE["intent_file"])
    if not path.exists():
        return {}

    try:
        data = load_json(path)
        if not isinstance(data, dict):
            raise ValueError("Root muss ein JSON-Objekt sein")

        version = int(data.get("version", 1))
        devices = data.get("devices", data)
        if not isinstance(devices, dict):
            raise ValueError("devices muss ein JSON-Objekt sein")

        if version == 1:
            # 3.2.0 speicherte auch Mode/Temperaturänderungen, die Loxone im
            # ausgeschalteten Standby ausgab. Diese Werte dürfen wir nicht als
            # sicheren Startzustand übernehmen. Nur der Power-Intent wird
            # migriert; Steuerwerte werden beim nächsten echten Start neu gelernt.
            migrated = {}
            for device_id, device_state in devices.items():
                if isinstance(device_state, dict):
                    migrated[device_id] = {
                        "power": device_state.get("power"),
                        "committed_controls": {},
                    }
            LOGGER.warning(
                "INTENT_MIGRATE version=1->2 file=%s controls_discarded=true",
                path,
            )
            return migrated

        if version != 2:
            raise ValueError(f"Nicht unterstützte Intent-Version: {version}")

        return devices
    except Exception as error:
        LOGGER.warning(
            "INTENT_LOAD_FAIL file=%s error=%s",
            path,
            error,
        )
        return {}


PERSISTED_INTENTS = load_persisted_intents()

runtime = {
    device_id: {
        "lock": threading.Lock(),
        "next_request": 0.0,
        "registered": False,
        "last_command_signature": None,
        "last_command_at": 0.0,
        # Separater Lock nur für den von Loxone befohlenen Sollzustand.
        # Damit können parallele HTTP-Requests ihren Intent bereits erfassen,
        # während ein Power-Befehl noch auf die eigentliche Geräteoperation wartet.
        "intent_lock": threading.Lock(),
        # Letzter expliziter /power-Wunsch von Loxone. Er wird persistent
        # gespeichert und niemals aus einem Polling-Ergebnis überschrieben.
        "last_power_intent": PERSISTED_INTENTS.get(device_id, {}).get("power"),
        # Nur Steuerwerte, die während eines aktiven Loxone-Betriebs bzw. bei
        # einem bestätigten Power-On verwendet wurden, werden dauerhaft als
        # Sollzustand gespeichert. Standby-Ausgaben (z.B. Eco-Heizen) landen
        # zunächst nur flüchtig in pending_controls.
        "committed_controls": {
            key: str(PERSISTED_INTENTS.get(device_id, {}).get("committed_controls", {}).get(key))
            for key in INTENT_KEYS
            if PERSISTED_INTENTS.get(device_id, {}).get("committed_controls", {}).get(key) is not None
        },
        "pending_controls": {},
        "pending_control_at": {},
        # Zeitpunkt des letzten echten Übergangs auf Loxone-AUS. Während des
        # anschließenden Quarantänefensters werden Standby/Eco-Ausgänge nicht
        # als Vorstart-Kandidaten akzeptiert. Nach Bridge-Neustart mit
        # persistentem AUS gilt dasselbe für die ersten Sekunden.
        "last_power_off_at": (
            time.monotonic()
            if PERSISTED_INTENTS.get(device_id, {}).get("power") is False
            else None
        ),
        # Bleibt True, wenn ein explizites AUS noch nicht bestätigt werden
        # konnte. Solange das gesetzt ist, dürfen Folgekommandos ein noch
        # laufendes Gerät nicht auf einen anderen Modus umschalten.
        "shutdown_pending": (
            PERSISTED_INTENTS.get(device_id, {}).get("power") is False
        ),
        "consecutive_errors": 0,
        "last_error_at": None,
    }
    for device_id in DEVICES
}

group_locks = {
    group_id: threading.Lock()
    for group_id in GROUPS
}

states = {
    device_id: {
        "device_id": device_id,
        "alias": device.get("alias"),
        "name": device["name"],
        "ip": device["ip"],
        "aircon_id": device["aircon_id"],
        "group": device["group"],
        "online": False,
        "online_value": 0,
        "registered": False,
        "last_update": None,
        "last_error": "Noch keine Abfrage durchgeführt",
        "last_error_at": None,
        "loxone_power_intent": runtime[device_id].get("last_power_intent"),
        "loxone_desired_controls": deepcopy(runtime[device_id].get("committed_controls", {})),
        "loxone_pending_controls": {},
        "shutdown_pending": runtime[device_id].get("shutdown_pending", False),
    }
    for device_id, device in DEVICES.items()
}


def api_call_unlocked(device_id, command, contents=None):
    device = DEVICES[device_id]
    run = runtime[device_id]

    wait = run["next_request"] - time.monotonic()
    if wait > 0:
        time.sleep(wait)

    payload = {
        "apiVer": "1.0",
        "command": command,
        "deviceId": IDENTITY["device_id"],
        "operatorId": IDENTITY["operator_id"],
        "timestamp": round(time.time()),
    }

    if contents is not None:
        payload["contents"] = contents

    request = Request(
        f"http://{device['ip']}:{device['port']}/beaver/command/{command}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "smartmair_app[1.4.008]",
            "Accept": "*/*",
            "Connection": "close",
        },
        method="POST",
    )

    started = time.monotonic()
    try:
        with urlopen(request, timeout=SERVICE["request_timeout"]) as response:
            result = json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code} bei {command}: {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Netzwerkfehler bei {command}: {error}"
        ) from error
    finally:
        run["next_request"] = time.monotonic() + SERVICE["min_request_gap"]
        LOGGER.debug(
            "WF_RAC device=%s command=%s elapsed=%.3fs",
            device_id,
            command,
            time.monotonic() - started,
        )

    return result


def ensure_success(result, command):
    code = int(result.get("result", -1))

    if code == 0:
        return

    if command == "updateAccountInfo" and code == 2:
        raise RuntimeError(
            "Zu viele Bedienkonten am WF-RAC-Modul registriert"
        )

    raise RuntimeError(f"WF-RAC API-Fehler bei {command}: {result}")


def register_device_unlocked(device_id):
    """
    Die aktuell verwendete WF-RAC-Firmware unterstützt updateAccountInfo nicht.
    Verwendet wird die bereits vorhandene Operator-ID aus remoteList.
    """
    runtime[device_id]["registered"] = True


def mode_to_loxone(mode_raw):
    return {
        MODE_VALUES["auto"]: 1,
        MODE_VALUES["heat"]: 2,
        MODE_VALUES["cool"]: 3,
        MODE_VALUES["dry"]: 4,
        MODE_VALUES["fan_only"]: 5,
    }.get(mode_raw, 0)


def fan_to_loxone(fan_raw):
    return {
        FAN_VALUES["auto"]: 1,
        FAN_VALUES["quiet"]: 2,
        FAN_VALUES["low"]: 4,
        FAN_VALUES["medium"]: 5,
        FAN_VALUES["high"]: 6,
    }.get(fan_raw, 0)


def build_state(device_id, contents, aircon):
    device = DEVICES[device_id]

    with state_lock:
        old_state = states.get(device_id, {})

    return {
        "device_id": device_id,
        "alias": device.get("alias"),
        "name": device["name"],
        "ip": device["ip"],
        "aircon_id": device["aircon_id"],
        "group": device["group"],
        "online": True,
        "online_value": 1,
        "registered": runtime[device_id]["registered"],
        "power": bool(aircon.Operation),
        "power_value": 1 if aircon.Operation else 0,
        "mode": MODE_NAMES.get(
            aircon.OperationMode,
            f"unknown_{aircon.OperationMode}",
        ),
        "mode_raw": aircon.OperationMode,
        "mode_value": mode_to_loxone(aircon.OperationMode),
        "target_temperature": aircon.PresetTemp,
        "indoor_temperature": aircon.IndoorTemp,
        "outdoor_temperature": aircon.OutdoorTemp,
        "fan": FAN_NAMES.get(
            aircon.AirFlow,
            f"unknown_{aircon.AirFlow}",
        ),
        "fan_raw": aircon.AirFlow,
        "fan_value": fan_to_loxone(aircon.AirFlow),
        "vertical_swing_raw": aircon.WindDirectionUD,
        "airflow_direction_value": aircon.WindDirectionUD + 1,
        "horizontal_swing_raw": aircon.WindDirectionLR,
        "three_d_auto": bool(aircon.Entrust),
        "three_d_auto_value": 1 if aircon.Entrust else 0,
        "energy_kwh": aircon.Electric,
        "error": aircon.ErrorCode,
        "error_value": 0 if aircon.ErrorCode == "00" else 1,
        "model_raw": aircon.ModelNrRaw,
        "updated_by": contents.get(
            "updatedBy",
            old_state.get("updated_by"),
        ),
        "accounts": contents.get(
            "numOfAccount",
            old_state.get("accounts"),
        ),
        "last_update": timestamp(),
        "last_error": None,
        "last_error_at": runtime[device_id].get("last_error_at"),
        "loxone_power_intent": runtime[device_id].get("last_power_intent"),
        "loxone_desired_controls": deepcopy(
            runtime[device_id].get("committed_controls", {})
        ),
        "loxone_pending_controls": deepcopy(
            runtime[device_id].get("pending_controls", {})
        ),
        "shutdown_pending": runtime[device_id].get("shutdown_pending", False),
    }


def control_state_snapshot(state):
    return {key: state.get(key) for key in CONTROL_STATE_KEYS}


def detect_loxone_drift(device_id, state):
    """Nur Diagnose: erkennt Abweichungen zwischen Loxone-Intent und Istzustand."""
    run = runtime[device_id]
    mismatches = {}

    with run["intent_lock"]:
        expected_power = run.get("last_power_intent")
        desired = deepcopy(run.get("committed_controls", {}))

    if expected_power is not None and bool(state.get("power")) != expected_power:
        mismatches["power"] = {
            "expected": expected_power,
            "actual": bool(state.get("power")),
        }

    # Steuerwerte sind nur relevant, solange Loxone die Anlage EIN haben will.
    if expected_power is True:
        if "mode" in desired:
            try:
                raw_mode, _ = parse_mode_value(desired["mode"])
                actual_mode = state.get("mode_raw")
                if raw_mode is not None and actual_mode != raw_mode:
                    mismatches["mode"] = {
                        "expected": MODE_NAMES.get(raw_mode, raw_mode),
                        "actual": state.get("mode"),
                    }
            except ValueError:
                pass

        if "fan" in desired:
            try:
                raw_fan = parse_fan_value(desired["fan"])
                if state.get("fan_raw") != raw_fan:
                    mismatches["fan"] = {
                        "expected": FAN_NAMES.get(raw_fan, raw_fan),
                        "actual": state.get("fan"),
                    }
            except ValueError:
                pass

        if "temperature" in desired:
            try:
                expected_temp = float(desired["temperature"].replace(",", "."))
                actual_temp = state.get("target_temperature")
                if (
                    actual_temp is not None
                    and abs(float(actual_temp) - expected_temp) >= 0.001
                ):
                    mismatches["target_temperature"] = {
                        "expected": expected_temp,
                        "actual": actual_temp,
                    }
            except (TypeError, ValueError):
                pass

    return mismatches


def store_state(device_id, new_state, source):
    with state_lock:
        old_state = deepcopy(states.get(device_id, {}))
        states[device_id] = new_state

    run = runtime[device_id]
    previous_errors = run.get("consecutive_errors", 0)
    run["consecutive_errors"] = 0

    if not old_state.get("online"):
        if old_state.get("last_update") is None:
            LOGGER.info("DEVICE_ONLINE device=%s source=%s", device_id, source)
        else:
            LOGGER.info(
                "DEVICE_RECOVERED device=%s source=%s previous_errors=%s",
                device_id,
                source,
                previous_errors,
            )

    old_control = control_state_snapshot(old_state)
    new_control = control_state_snapshot(new_state)
    changes = {
        key: {"from": old_control.get(key), "to": new_control.get(key)}
        for key in CONTROL_STATE_KEYS
        if old_control.get(key) != new_control.get(key)
    }

    if changes:
        LOGGER.info(
            "STATE_CHANGE device=%s source=%s updated_by=%r changes=%s",
            device_id,
            source,
            new_state.get("updated_by"),
            json.dumps(changes, ensure_ascii=False, sort_keys=True),
        )

        # Bei einer echten externen Änderung (Polling) protokollieren wir
        # zusätzlich, ob sie dem zuletzt von Loxone befohlenen Zustand
        # widerspricht. Es erfolgt hier bewusst KEINE automatische Korrektur.
        if source == "poll" and old_state.get("last_update") is not None:
            drift = detect_loxone_drift(device_id, new_state)
            if drift:
                LOGGER.warning(
                    "EXTERNAL_DRIFT device=%s updated_by=%r mismatches=%s",
                    device_id,
                    new_state.get("updated_by"),
                    json.dumps(drift, ensure_ascii=False, sort_keys=True),
                )

    return old_state


def fetch_status(device_id):
    device = DEVICES[device_id]
    run = runtime[device_id]

    with run["lock"]:
        if not run["registered"]:
            register_device_unlocked(device_id)

        result = api_call_unlocked(
            device_id,
            "getAirconStat",
            {"airconId": device["aircon_id"]},
        )

        ensure_success(result, "getAirconStat")

        contents = result["contents"]
        aircon = parser.translate_bytes(contents["airconStat"])

    new_state = build_state(device_id, contents, aircon)
    store_state(device_id, new_state, "poll")
    LOGGER.debug(
        "POLL_STATE device=%s state=%s",
        device_id,
        json.dumps(
            {
                **control_state_snapshot(new_state),
                "indoor_temperature": new_state.get("indoor_temperature"),
                "outdoor_temperature": new_state.get("outdoor_temperature"),
                "updated_by": new_state.get("updated_by"),
                "loxone_power_intent": new_state.get("loxone_power_intent"),
                "shutdown_pending": new_state.get("shutdown_pending"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    return new_state


def parse_bool(value):
    value = value.strip().lower()

    if value in {"1", "true", "on", "ein"}:
        return True

    if value in {"0", "false", "off", "aus"}:
        return False

    raise ValueError("power muss 0 oder 1 sein")


def parse_temperature(value, mode):
    temperature = float(value.replace(",", "."))

    if abs((temperature * 2) - round(temperature * 2)) > 0.001:
        raise ValueError(
            "Temperatur ist nur in 0,5-°C-Schritten zulässig"
        )

    minimum = 16.0 if mode == "cool" else 18.0
    maximum = 30.0

    if not minimum <= temperature <= maximum:
        raise ValueError(
            f"Temperatur muss im Modus {mode} zwischen "
            f"{minimum:g} und {maximum:g} °C liegen"
        )

    return temperature


def parse_direction(value):
    numeric = float(value.strip().replace(",", "."))

    if not numeric.is_integer():
        raise ValueError(
            "Luftstromrichtung muss eine ganze Zahl sein"
        )

    direction = int(numeric)

    if not 1 <= direction <= 8:
        raise ValueError(
            "Luftstromrichtung muss zwischen 1 und 8 liegen"
        )

    return direction


def parse_mode_value(value):
    raw = value.strip().lower().replace(",", ".")

    if raw == "off":
        return None, False

    if raw in MODE_VALUES:
        return MODE_VALUES[raw], True

    try:
        numeric = float(raw)
    except ValueError as error:
        raise ValueError(
            "mode: 1–5 oder auto, heat, cool, dry, fan_only, off"
        ) from error

    if not numeric.is_integer():
        raise ValueError("mode muss eine ganze Zahl zwischen 1 und 5 sein")

    mapping = {
        1: MODE_VALUES["auto"],
        2: MODE_VALUES["heat"],
        3: MODE_VALUES["cool"],
        4: MODE_VALUES["dry"],
        5: MODE_VALUES["fan_only"],
    }

    number = int(numeric)

    if number not in mapping:
        raise ValueError("mode muss zwischen 1 und 5 liegen")

    return mapping[number], True


def parse_fan_value(value):
    raw = value.strip().lower().replace(",", ".")

    if raw in FAN_VALUES:
        return FAN_VALUES[raw]

    try:
        numeric = float(raw)
    except ValueError as error:
        raise ValueError(
            "fan: 1, 2, 4, 5, 6 oder auto, quiet, low, medium, high"
        ) from error

    if not numeric.is_integer():
        raise ValueError("fan muss einer der Werte 1, 2, 4, 5 oder 6 sein")

    mapping = {
        1: FAN_VALUES["auto"],
        2: FAN_VALUES["quiet"],
        4: FAN_VALUES["low"],
        5: FAN_VALUES["medium"],
        6: FAN_VALUES["high"],
    }

    number = int(numeric)

    if number not in mapping:
        raise ValueError("fan muss einer der Werte 1, 2, 4, 5 oder 6 sein")

    return mapping[number]


def create_changes(current_aircon, query):
    changes = {}
    resulting_mode = MODE_NAMES.get(
        current_aircon.OperationMode,
        "auto",
    )

    if "mode" in query:
        mode_value, _switch_on = parse_mode_value(query["mode"][-1])

        if mode_value is None:
            # Legacy-Kompatibilität: mode=off darf weiterhin ausschalten.
            changes["Operation"] = False
        else:
            # WICHTIG: Die Moduswahl schaltet das Gerät NICHT mehr ein.
            # Einschalten ist ausschließlich über /power?value=1 möglich.
            # Dadurch können Mode-Initialisierungen aus Loxone nach einem
            # Miniserver-Neustart keine Klimaanlage unbeabsichtigt starten.
            changes["OperationMode"] = mode_value
            resulting_mode = MODE_NAMES.get(mode_value, "auto")

    if "power" in query:
        changes["Operation"] = parse_bool(query["power"][-1])

    if "fan" in query:
        changes["AirFlow"] = parse_fan_value(query["fan"][-1])

    if "direction" in query:
        direction = parse_direction(query["direction"][-1])

        if direction == 1:
            changes["WindDirectionUD"] = 0
        elif 2 <= direction <= 5:
            changes["WindDirectionUD"] = direction - 1
        elif direction == 6:
            changes["WindDirectionLR"] = 0
        elif direction == 7:
            changes["WindDirectionUD"] = 0
            changes["WindDirectionLR"] = 0
        elif direction == 8:
            changes["WindDirectionLR"] = 4

    if "temperature" in query:
        changes["PresetTemp"] = parse_temperature(
            query["temperature"][-1],
            resulting_mode,
        )

    if not changes:
        raise ValueError("Kein gültiger Steuerwert übergeben")

    return changes


def mode_family(mode_name):
    if mode_name == "heat":
        return "heat"
    if mode_name in {"cool", "dry"}:
        return "cool"
    return None


def enforce_group_conflict(device_id, command_state):
    device = DEVICES[device_id]
    group_id = device["group"]
    group = GROUPS[group_id]

    if group["conflict_policy"] != "reject":
        return

    if not command_state.Operation:
        return

    requested_mode = MODE_NAMES.get(command_state.OperationMode)
    requested_family = mode_family(requested_mode)

    # Auto und reiner Lüfter werden bewusst nicht als Heiz-/Kühlkonflikt
    # gewertet. Das Außengerät bleibt die letzte technische Schutzebene.
    if requested_family is None:
        return

    with state_lock:
        snapshot = deepcopy(states)

    for peer_id, peer_device in DEVICES.items():
        if peer_id == device_id or peer_device["group"] != group_id:
            continue

        peer_state = snapshot.get(peer_id, {})
        if not peer_state.get("online") or not peer_state.get("power"):
            continue

        peer_mode = peer_state.get("mode")
        peer_family = mode_family(peer_mode)

        if peer_family and peer_family != requested_family:
            raise ModeConflictError(
                requested_device=device_id,
                requested_mode=requested_mode,
                active_device=peer_id,
                active_name=peer_device["name"],
                active_mode=peer_mode,
                group_id=group_id,
            )


def command_signature(query):
    normalized = {}

    for key in sorted(query):
        values = query[key]
        if not isinstance(values, list):
            values = [values]
        normalized[key] = [str(value).strip().lower() for value in values]

    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    )


def values_equal(current, requested):
    # bool ist in Python auch int, deshalb zuerst explizit behandeln.
    if isinstance(current, bool) or isinstance(requested, bool):
        return bool(current) == bool(requested)

    if isinstance(current, (int, float)) and isinstance(requested, (int, float)):
        return abs(float(current) - float(requested)) < 0.001

    return current == requested



def persist_loxone_intents():
    path = Path(SERVICE["intent_file"])
    snapshot = {}

    for device_id, run in runtime.items():
        with run["intent_lock"]:
            device_state = {
                "power": run.get("last_power_intent"),
                "committed_controls": deepcopy(run.get("committed_controls", {})),
            }
        snapshot[device_id] = device_state

    payload = {
        "version": 2,
        "updated_at": timestamp(),
        "devices": snapshot,
    }

    with intent_file_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temp_path.replace(path)
        except OSError as error:
            LOGGER.warning(
                "INTENT_SAVE_FAIL file=%s error=%s",
                path,
                error,
            )


def _validate_intent_query(query):
    if "power" in query:
        parse_bool(query["power"][-1])
    if "mode" in query:
        parse_mode_value(query["mode"][-1])
    if "fan" in query:
        parse_fan_value(query["fan"][-1])
    if "direction" in query:
        parse_direction(query["direction"][-1])
    if "temperature" in query:
        value = float(str(query["temperature"][-1]).replace(",", "."))
        if abs((value * 2) - round(value * 2)) > 0.001 or not 16.0 <= value <= 30.0:
            raise ValueError(
                "temperature muss zwischen 16 und 30 °C in 0,5-°C-Schritten liegen"
            )


def record_loxone_intent(device_id, query):
    """
    Trennt aktiven Loxone-Sollzustand von Standby-Ausgaben.

    Solange power_intent=True ist, werden Steuerwerte dauerhaft committed.
    Solange power_intent=False/None ist, werden sie nur als kurzlebige Kandidaten
    gespeichert. Direkt nach einem Übergang auf AUS werden Steuerwerte jedoch
    für post_power_off_ignore_window vollständig ignoriert, weil AC-Control in
    diesem Zeitfenster reproduzierbar Standby/Eco-Werte (z.B. Heat) ausgibt.
    """
    run = runtime[device_id]
    _validate_intent_query(query)
    committed_changes = {}
    candidate_changes = {}
    ignored_changes = {}
    persist_needed = False
    now = time.monotonic()

    with run["intent_lock"]:
        if "power" in query:
            power_value = parse_bool(query["power"][-1])
            previous_power = run.get("last_power_intent")
            if previous_power is not power_value:
                committed_changes["power"] = {
                    "from": previous_power,
                    "to": power_value,
                }
            run["last_power_intent"] = power_value
            persist_needed = True

            if power_value is False and previous_power is not False:
                run["last_power_off_at"] = now
                # Vorherige Kandidaten gehören zu einem alten Startversuch und
                # dürfen nach AUS nicht weiterleben.
                run["pending_controls"].clear()
                run["pending_control_at"].clear()
            elif power_value is True:
                # Quarantäne endet mit einem echten Power-On-Intent.
                run["last_power_off_at"] = None

        power_active = run.get("last_power_intent") is True
        off_age = None
        if not power_active and run.get("last_power_off_at") is not None:
            off_age = max(0.0, now - run["last_power_off_at"])
        ignore_window = SERVICE["post_power_off_ignore_window"]
        quarantine = (
            not power_active
            and off_age is not None
            and off_age <= ignore_window
        )

        for key in INTENT_KEYS:
            if key not in query:
                continue
            value = str(query[key][-1]).strip()

            if power_active:
                previous = run["committed_controls"].get(key)
                if previous != value:
                    committed_changes[key] = {"from": previous, "to": value}
                run["committed_controls"][key] = value
                run["pending_controls"].pop(key, None)
                run["pending_control_at"].pop(key, None)
                persist_needed = True
            elif quarantine:
                ignored_changes[key] = {
                    "value": value,
                    "off_age": round(off_age, 3),
                }
            else:
                previous = run["pending_controls"].get(key)
                if previous != value:
                    candidate_changes[key] = {"from": previous, "to": value}
                run["pending_controls"][key] = value
                run["pending_control_at"][key] = now

    if committed_changes:
        LOGGER.info(
            "INTENT_UPDATE device=%s changes=%s",
            device_id,
            json.dumps(committed_changes, ensure_ascii=False, sort_keys=True),
        )

    if ignored_changes:
        LOGGER.info(
            "INTENT_STANDBY_IGNORED device=%s changes=%s ignore_window=%ss",
            device_id,
            json.dumps(ignored_changes, ensure_ascii=False, sort_keys=True),
            SERVICE["post_power_off_ignore_window"],
        )

    if candidate_changes:
        LOGGER.info(
            "INTENT_CANDIDATE device=%s changes=%s window=%ss",
            device_id,
            json.dumps(candidate_changes, ensure_ascii=False, sort_keys=True),
            SERVICE["pre_power_control_window"],
        )

    if persist_needed:
        persist_loxone_intents()

    # PASSIVER STANDBY:
    # Solange Loxone das Gerät nicht aktiv auf EIN hält, werden Mode/Temp/Fan/
    # Direction nur als möglicher Vorstart-Sollwert gespeichert, aber NIEMALS
    # an das WF-RAC-Modul geschrieben. Dadurch bleiben Fernbedienung und
    # Smart-M-Air vollständig bedienbar und ein alter Loxone-AUS-Zustand kann
    # keine manuelle Bedienung zurückdrehen.
    control_keys = [key for key in INTENT_KEYS if key in query]
    with run["intent_lock"]:
        power_active_after_record = run.get("last_power_intent") is True

    all_controls_deferred = (
        "power" not in query
        and bool(control_keys)
        and not power_active_after_record
    )

    return {
        "ignored_controls": sorted(ignored_changes),
        "candidate_controls": sorted(candidate_changes),
        "all_controls_deferred": all_controls_deferred,
    }


def committed_controls_snapshot(device_id):
    run = runtime[device_id]
    with run["intent_lock"]:
        return deepcopy(run.get("committed_controls", {}))


def recent_pending_controls_snapshot(device_id):
    run = runtime[device_id]
    now = time.monotonic()
    recent = {}
    ages = {}
    window = SERVICE["pre_power_control_window"]

    with run["intent_lock"]:
        for key, value in run.get("pending_controls", {}).items():
            seen_at = run.get("pending_control_at", {}).get(key)
            if seen_at is None:
                continue
            age = max(0.0, now - seen_at)
            if age <= window:
                recent[key] = value
                ages[key] = round(age, 3)

    return recent, ages


def merge_power_on_query(device_id, query):
    """Power=1 nutzt committed Sollwerte plus nur FRISCHE Vorstart-Kandidaten."""
    merged = {key: list(values) for key, values in query.items()}
    committed = committed_controls_snapshot(device_id)
    pending, pending_ages = recent_pending_controls_snapshot(device_id)

    desired = deepcopy(committed)
    desired.update(pending)

    # Ohne jemals von Loxone gelernten Modus schalten wir aus Sicherheitsgründen
    # nicht ein. So kann der zuletzt per Fernbedienung gespeicherte MHI-Modus
    # niemals zufällig zum Startmodus werden.
    if "mode" not in desired:
        raise RuntimeError(
            "Sicherer Power-On abgelehnt: kein gültiger Loxone-Modus bekannt"
        )

    for key in INTENT_KEYS:
        if key in desired:
            merged[key] = [desired[key]]

    return merged, desired, pending, pending_ages


def commit_power_on_controls(device_id, desired):
    run = runtime[device_id]
    changed = {}

    with run["intent_lock"]:
        for key, value in desired.items():
            if key not in INTENT_KEYS:
                continue
            previous = run["committed_controls"].get(key)
            if previous != value:
                changed[key] = {"from": previous, "to": value}
            run["committed_controls"][key] = str(value)

        run["pending_controls"].clear()
        run["pending_control_at"].clear()

    if changed:
        LOGGER.info(
            "INTENT_COMMIT device=%s changes=%s",
            device_id,
            json.dumps(changed, ensure_ascii=False, sort_keys=True),
        )
    persist_loxone_intents()


def verify_requested_changes(aircon, requested_changes):
    """Prüft nach Power-On zusätzlich Mode/Temperatur/Fan/Luftstrom."""
    mismatches = {}

    for key, expected in requested_changes.items():
        if key == "Operation":
            continue
        actual = getattr(aircon, key)
        if not values_equal(actual, expected):
            mismatches[key] = {
                "expected": expected,
                "actual": actual,
            }

    if mismatches:
        raise RuntimeError(
            "Power-On-Synchronisierung fehlgeschlagen: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )

def set_device(device_id, query, request_id=None, record_intent=True):
    device = DEVICES[device_id]
    run = runtime[device_id]
    group_lock = group_locks[device["group"]]
    signature = command_signature(query)
    request_id = request_id or "internal"

    explicit_power = None
    if "power" in query:
        explicit_power = parse_bool(query["power"][-1])

    # WICHTIG: Externe Loxone-Sollwerte werden vor allen Geräte-Locks erfasst.
    # Interne Recovery-/Drift-Korrekturen dürfen diesen Sollzustand dagegen
    # niemals umschreiben.
    intent_result = {
        "ignored_controls": [],
        "candidate_controls": [],
        "all_controls_deferred": False,
    }
    if record_intent:
        intent_result = record_loxone_intent(device_id, query)

        # Solange Loxone zuletzt nicht EIN befohlen hat, verändern reine
        # Steuerwerte das physische Gerät nicht. Sie werden höchstens als
        # frische Vorstartwerte gemerkt und erst bei einem expliziten
        # power=1 gemeinsam angewendet.
        if intent_result.get("all_controls_deferred"):
            with state_lock:
                cached_state = deepcopy(states[device_id])

            LOGGER.info(
                "CMD_START id=%s device=%s query=%s power_intent=%r shutdown_pending=%s",
                request_id,
                device_id,
                json.dumps(query, ensure_ascii=False, sort_keys=True),
                run.get("last_power_intent"),
                run.get("shutdown_pending"),
            )
            LOGGER.info(
                "CMD_OK id=%s device=%s reason=standby_deferred ignored=%s "
                "candidates=%s state=%s",
                request_id,
                device_id,
                json.dumps(intent_result.get("ignored_controls", [])),
                json.dumps(intent_result.get("candidate_controls", [])),
                json.dumps(control_state_snapshot(cached_state), sort_keys=True),
            )
            return cached_state, False, "standby_deferred"

    # Bei AUS bleibt shutdown_pending so lange gesetzt, bis das echte Gerät
    # den AUS-Zustand bestätigt hat.
    if explicit_power is not None:
        run["shutdown_pending"] = not explicit_power

    # Kurzes Sammelfenster beim Einschalten: Loxone ändert Mode/Temperatur/
    # Fan oft praktisch gleichzeitig mit Status. Das Fenster macht die
    # Reihenfolge der parallelen HTTP-Requests unkritisch.
    if (
        explicit_power is True
        and SERVICE["sync_controls_on_power_on"]
        and SERVICE["power_on_sync_delay"] > 0
    ):
        time.sleep(SERVICE["power_on_sync_delay"])

    LOGGER.info(
        "CMD_START id=%s device=%s query=%s power_intent=%r shutdown_pending=%s",
        request_id,
        device_id,
        json.dumps(query, ensure_ascii=False, sort_keys=True),
        run.get("last_power_intent"),
        run.get("shutdown_pending"),
    )

    # Befehle an Geräte desselben Außengeräts werden serialisiert. Dadurch
    # können zwei fast gleichzeitige Loxone-Befehle die Konfliktprüfung nicht
    # gegenseitig überholen.
    with group_lock:
        with run["lock"]:
            now = time.monotonic()
            dedupe_window = SERVICE["command_dedupe_window"]

            if (
                dedupe_window > 0
                and signature == run["last_command_signature"]
                and now - run["last_command_at"] < dedupe_window
            ):
                with state_lock:
                    cached_state = deepcopy(states[device_id])

                if explicit_power is not None:
                    if bool(cached_state.get("power")) == explicit_power:
                        run["shutdown_pending"] = False

                LOGGER.info(
                    "CMD_OK id=%s device=%s reason=duplicate_suppressed state=%s",
                    request_id,
                    device_id,
                    json.dumps(control_state_snapshot(cached_state), sort_keys=True),
                )
                return cached_state, False, "duplicate_suppressed"

            if not run["registered"]:
                register_device_unlocked(device_id)

            max_attempts = SERVICE["command_retries"] + 1
            last_error = None

            for attempt in range(1, max_attempts + 1):
                try:
                    current = api_call_unlocked(
                        device_id,
                        "getAirconStat",
                        {"airconId": device["aircon_id"]},
                    )
                    ensure_success(current, "getAirconStat")
                    current_aircon = parser.translate_bytes(
                        current["contents"]["airconStat"]
                    )

                    command_query = query
                    power_on_desired = {}
                    if (
                        explicit_power is True
                        and SERVICE["sync_controls_on_power_on"]
                    ):
                        (
                            command_query,
                            power_on_desired,
                            fresh_pending,
                            pending_ages,
                        ) = merge_power_on_query(device_id, query)
                        LOGGER.info(
                            "POWER_ON_SYNC id=%s device=%s desired=%s "
                            "fresh_pending=%s pending_ages=%s",
                            request_id,
                            device_id,
                            json.dumps(power_on_desired, ensure_ascii=False, sort_keys=True),
                            json.dumps(fresh_pending, ensure_ascii=False, sort_keys=True),
                            json.dumps(pending_ages, sort_keys=True),
                        )
                    # Im laufenden Betrieb wird ausschließlich der tatsächlich
                    # von Loxone angeforderte Parameter verändert. Es gibt
                    # bewusst KEIN "ACTIVE_SYNC" mehr: manuelle Änderungen per
                    # Fernbedienung/Smart-M-Air bleiben bestehen, bis Loxone
                    # genau diesen Parameter erneut befiehlt.
                    changes = create_changes(current_aircon, command_query)

                    effective_changes = {
                        key: value
                        for key, value in changes.items()
                        if not values_equal(getattr(current_aircon, key), value)
                    }

                    expected_power = explicit_power

                    if not effective_changes:
                        aircon = current_aircon
                        contents = current["contents"]
                        if expected_power is not None:
                            if bool(aircon.Operation) != expected_power:
                                raise RuntimeError(
                                    "Power-Verifikation fehlgeschlagen: "
                                    f"erwartet={int(expected_power)} "
                                    f"ist={int(bool(aircon.Operation))}"
                                )
                            run["shutdown_pending"] = False

                        current_state = build_state(
                            device_id,
                            contents,
                            aircon,
                        )
                        store_state(device_id, current_state, "command_nochange")

                        if (
                            explicit_power is True
                            and SERVICE["sync_controls_on_power_on"]
                        ):
                            commit_power_on_controls(device_id, power_on_desired)

                        run["last_command_signature"] = signature
                        run["last_command_at"] = time.monotonic()
                        LOGGER.info(
                            "CMD_OK id=%s device=%s attempt=%s/%s "
                            "reason=already_in_state state=%s",
                            request_id,
                            device_id,
                            attempt,
                            max_attempts,
                            json.dumps(control_state_snapshot(current_state), sort_keys=True),
                        )
                        return current_state, False, "already_in_state"

                    command_state = AirconStat.from_aircon(current_aircon)
                    for key, value in effective_changes.items():
                        setattr(command_state, key, value)

                    enforce_group_conflict(device_id, command_state)

                    LOGGER.info(
                        "CMD_WRITE id=%s device=%s attempt=%s/%s before=%s changes=%s",
                        request_id,
                        device_id,
                        attempt,
                        max_attempts,
                        json.dumps({
                            "power": bool(current_aircon.Operation),
                            "mode": MODE_NAMES.get(current_aircon.OperationMode),
                            "target_temperature": current_aircon.PresetTemp,
                            "fan": FAN_NAMES.get(current_aircon.AirFlow),
                        }, sort_keys=True),
                        json.dumps(effective_changes, ensure_ascii=False, sort_keys=True),
                    )

                    encoded = parser.to_base64(command_state)
                    result = api_call_unlocked(
                        device_id,
                        "setAirconStat",
                        {
                            "airconId": device["aircon_id"],
                            "airconStat": encoded,
                        },
                    )
                    ensure_success(result, "setAirconStat")

                    contents = result["contents"]
                    aircon = parser.translate_bytes(contents["airconStat"])

                    # Power wird nicht nur anhand der setAirconStat-Antwort
                    # als erfolgreich betrachtet. Ein separater Readback muss
                    # den realen Zustand bestätigen. Das ist der Kernfix für
                    # den beobachteten 502/AUS-Fehler.
                    if expected_power is not None:
                        if SERVICE["power_verify_delay"] > 0:
                            time.sleep(SERVICE["power_verify_delay"])

                        verify = api_call_unlocked(
                            device_id,
                            "getAirconStat",
                            {"airconId": device["aircon_id"]},
                        )
                        ensure_success(verify, "getAirconStat")
                        contents = verify["contents"]
                        aircon = parser.translate_bytes(contents["airconStat"])

                        if bool(aircon.Operation) != expected_power:
                            raise RuntimeError(
                                "Power-Verifikation fehlgeschlagen: "
                                f"erwartet={int(expected_power)} "
                                f"ist={int(bool(aircon.Operation))}"
                            )

                        if (
                            explicit_power is True
                            and SERVICE["sync_controls_on_power_on"]
                        ):
                            verify_requested_changes(aircon, changes)
                            LOGGER.info(
                                "POWER_ON_SYNC_OK id=%s device=%s mode=%s "
                                "target=%s fan=%s",
                                request_id,
                                device_id,
                                MODE_NAMES.get(aircon.OperationMode),
                                aircon.PresetTemp,
                                FAN_NAMES.get(aircon.AirFlow),
                            )
                            commit_power_on_controls(device_id, power_on_desired)

                        run["shutdown_pending"] = False

                    new_state = build_state(device_id, contents, aircon)
                    store_state(device_id, new_state, "command")

                    run["last_command_signature"] = signature
                    run["last_command_at"] = time.monotonic()

                    reason = (
                        "updated_verified"
                        if expected_power is not None
                        else "updated"
                    )
                    LOGGER.info(
                        "CMD_OK id=%s device=%s attempt=%s/%s reason=%s state=%s",
                        request_id,
                        device_id,
                        attempt,
                        max_attempts,
                        reason,
                        json.dumps(control_state_snapshot(new_state), sort_keys=True),
                    )
                    return new_state, True, reason

                except (ModeConflictError, ValueError):
                    raise
                except Exception as error:
                    last_error = error
                    LOGGER.warning(
                        "CMD_RETRY id=%s device=%s attempt=%s/%s query=%s error=%s",
                        request_id,
                        device_id,
                        attempt,
                        max_attempts,
                        json.dumps(query, ensure_ascii=False, sort_keys=True),
                        error,
                        exc_info=LOGGER.isEnabledFor(logging.DEBUG),
                    )

                    if attempt < max_attempts:
                        if SERVICE["retry_delay"] > 0:
                            time.sleep(SERVICE["retry_delay"])
                        continue
                    break

            if explicit_power is False or run.get("last_power_intent") is False:
                run["shutdown_pending"] = True

            raise RuntimeError(
                f"Steuerbefehl nach {max_attempts} Versuchen fehlgeschlagen: "
                f"{last_error}"
            ) from last_error


def mark_offline(device_id, error, source="unknown"):
    run = runtime[device_id]
    run["registered"] = False
    run["consecutive_errors"] = run.get("consecutive_errors", 0) + 1
    run["last_error_at"] = timestamp()

    with state_lock:
        was_online = states[device_id].get("online", False)
        states[device_id]["online"] = False
        states[device_id]["online_value"] = 0
        states[device_id]["registered"] = False
        states[device_id]["last_error"] = str(error)
        states[device_id]["last_error_at"] = run["last_error_at"]
        states[device_id]["loxone_power_intent"] = run.get("last_power_intent")
        states[device_id]["loxone_desired_controls"] = deepcopy(
            run.get("committed_controls", {})
        )
        states[device_id]["loxone_pending_controls"] = deepcopy(
            run.get("pending_controls", {})
        )
        states[device_id]["shutdown_pending"] = run.get("shutdown_pending", False)

    log_fn = LOGGER.warning if was_online or run["consecutive_errors"] == 1 else LOGGER.debug
    log_fn(
        "DEVICE_OFFLINE device=%s source=%s consecutive_errors=%s error=%s",
        device_id,
        source,
        run["consecutive_errors"],
        error,
    )


def polling_loop():
    while not stop_event.is_set():
        cycle_start = time.monotonic()

        for index, device_id in enumerate(DEVICES):
            try:
                # PASSIVES POLLING: Status nur lesen und protokollieren.
                # Ein Polling-Zyklus darf niemals selbst einen Schreibbefehl
                # auslösen. Damit können Fernbedienung und Smart-M-Air keine
                # "Drift-Korrektur" oder ein verspätetes Recovery-OFF triggern.
                fetch_status(device_id)
            except Exception as error:
                mark_offline(device_id, error, source="poll")
                LOGGER.debug(
                    "POLL_FAIL device=%s error=%s",
                    device_id,
                    error,
                    exc_info=True,
                )

            if index < len(DEVICES) - 1:
                stop_event.wait(SERVICE["inter_device_delay"])

        elapsed = time.monotonic() - cycle_start
        stop_event.wait(max(1, SERVICE["poll_interval"] - elapsed))


def resolve_request(path):
    parts = [part for part in path.strip("/").split("/") if part]

    if len(parts) >= 4 and parts[:3] == ["api", "v1", "devices"]:
        device_id = parts[3]
        if device_id not in DEVICES:
            return None, None
        action = parts[4] if len(parts) > 4 else "status"
        return device_id, action

    if parts and parts[0] in ALIASES:
        device_id = ALIASES[parts[0]]
        action = parts[1] if len(parts) > 1 else "status"
        return device_id, action

    # Auch /ac1/status ist als kompakte technische URL erlaubt.
    if parts and parts[0] in DEVICES:
        device_id = parts[0]
        action = parts[1] if len(parts) > 1 else "status"
        return device_id, action

    return None, None


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RequestHandler(BaseHTTPRequestHandler):

    def send_json(self, payload, status=200):
        body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def write_allowed(self):
        return self.client_address[0] in {
            WRITE_CLIENT_IP,
            "127.0.0.1",
            "::1",
        }

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query, keep_blank_values=True)

        with state_lock:
            current_states = deepcopy(states)

        if path in {"", "/", "/status", "/api/v1/status"}:
            self.send_json({
                "service": "mhi-bridge",
                "version": 3,
                "build_version": BRIDGE_VERSION,
                "api_version": "v1",
                "poll_interval": SERVICE["poll_interval"],
                "command_dedupe_window": SERVICE["command_dedupe_window"],
                "command_retries": SERVICE["command_retries"],
                "power_on_sync_delay": SERVICE["power_on_sync_delay"],
                "pre_power_control_window": SERVICE["pre_power_control_window"],
                "post_power_off_ignore_window": SERVICE["post_power_off_ignore_window"],
                "sync_controls_on_power_on": SERVICE["sync_controls_on_power_on"],
                "intent_file": SERVICE["intent_file"],
                "log_file": SERVICE["log_file"],
                "devices": current_states,
            })
            return

        if path in {"/health", "/api/v1/health"}:
            self.send_json({
                "online": any(
                    device.get("online", False)
                    for device in current_states.values()
                )
            })
            return

        if path == "/api/v1/devices":
            self.send_json({
                "devices": [
                    {
                        "id": device_id,
                        "alias": device.get("alias"),
                        "name": device["name"],
                        "group": device["group"],
                        "status_url": f"/api/v1/devices/{device_id}/status",
                    }
                    for device_id, device in DEVICES.items()
                ]
            })
            return

        device_id, action = resolve_request(path)

        if device_id is None:
            self.send_json(
                {"error": "Endpunkt nicht gefunden"},
                status=404,
            )
            return

        if action == "status":
            self.send_json(current_states[device_id])
            return

        write_actions = {
            "set",
            "power",
            "temperature",
            "mode",
            "fan",
            "direction",
        }

        if action in write_actions and not self.write_allowed():
            LOGGER.warning(
                "WRITE_DENIED client=%s device=%s action=%s",
                self.client_address[0],
                device_id,
                action,
            )
            self.send_json(
                {
                    "success": False,
                    "error": "write_access_denied",
                    "client_ip": self.client_address[0],
                },
                status=403,
            )
            return

        if action in {
            "power",
            "temperature",
            "mode",
            "fan",
            "direction",
        }:
            if "value" not in query:
                self.send_json(
                    {"error": "Parameter value fehlt"},
                    status=400,
                )
                return

            query = {action: query["value"]}

        elif action != "set":
            self.send_json(
                {"error": "Endpunkt nicht gefunden"},
                status=404,
            )
            return

        request_id = uuid.uuid4().hex[:8]

        try:
            new_state, changed, reason = set_device(
                device_id,
                query,
                request_id=request_id,
            )

            self.send_json({
                "success": True,
                "changed": changed,
                "reason": reason,
                "request_id": request_id,
                "device": device_id,
                "state": new_state,
            })

        except ModeConflictError as error:
            LOGGER.warning(
                "CMD_CONFLICT id=%s device=%s error=%s",
                request_id,
                device_id,
                error,
            )
            self.send_json(error.details, status=409)

        except ValueError as error:
            LOGGER.warning(
                "CMD_INVALID id=%s device=%s error=%s",
                request_id,
                device_id,
                error,
            )
            self.send_json(
                {"success": False, "error": str(error), "request_id": request_id},
                status=400,
            )

        except Exception as error:
            mark_offline(device_id, error, source="command")
            LOGGER.exception(
                "CMD_FAIL id=%s device=%s query=%s error=%s",
                request_id,
                device_id,
                json.dumps(query, ensure_ascii=False, sort_keys=True),
                error,
            )

            self.send_json(
                {
                    "success": False,
                    "error": str(error),
                    "request_id": request_id,
                },
                status=502,
            )

    def log_message(self, message_format, *args):
        message = f"{self.address_string()} {message_format % args}"
        # Die 20-s-Statusabfragen von Loxone sind für die Fehlersuche kaum
        # hilfreich und würden das INFO-Log dominieren. Bei DEBUG bleiben sie
        # trotzdem vollständig sichtbar.
        if "/status " in message:
            LOGGER.debug("HTTP %s", message)
        else:
            LOGGER.info("HTTP %s", message)


def request_shutdown(signum, _frame):
    LOGGER.info("Signal %s empfangen, Dienst wird beendet", signum)
    stop_event.set()


def main():
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    thread = threading.Thread(
        target=polling_loop,
        daemon=True,
    )
    thread.start()

    server = BridgeHTTPServer(
        (SERVICE["listen_address"], SERVICE["listen_port"]),
        RequestHandler,
    )
    server.timeout = 1

    LOGGER.info(
        "MHI Bridge %s läuft auf Port %s",
        BRIDGE_VERSION,
        SERVICE["listen_port"],
    )
    LOGGER.info("Konfiguration: %s", CONFIG_FILE)
    LOGGER.info("Geräte: %s", ", ".join(DEVICES))
    LOGGER.info(
        "Logging: level=%s file=%s max_bytes=%s backups=%s",
        SERVICE["log_level"],
        SERVICE["log_file"],
        SERVICE["log_max_bytes"],
        SERVICE["log_backup_count"],
    )
    LOGGER.info(
        "Robustheit: command_retries=%s retry_delay=%ss power_verify_delay=%ss "
        "power_on_sync_delay=%ss pre_power_control_window=%ss "
        "post_power_off_ignore_window=%ss sync_controls_on_power_on=%s "
        "polling_mode=passive",
        SERVICE["command_retries"],
        SERVICE["retry_delay"],
        SERVICE["power_verify_delay"],
        SERVICE["power_on_sync_delay"],
        SERVICE["pre_power_control_window"],
        SERVICE["post_power_off_ignore_window"],
        SERVICE["sync_controls_on_power_on"],
    )
    LOGGER.info(
        "Loxone-Intent: file=%s restored=%s",
        SERVICE["intent_file"],
        json.dumps(PERSISTED_INTENTS, ensure_ascii=False, sort_keys=True),
    )

    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
