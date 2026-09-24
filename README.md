# Loxone MHI WF-RAC Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3](https://img.shields.io/badge/Python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/Linux-DietPi%20%2F%20Debian-informational.svg)
![Control model](https://img.shields.io/badge/Polling-read--only-success.svg)

<!-- project-meta -->
> **Status:** Stable · **Current release:** `v3.3.0` · **License:** MIT · **Documentation:** English · **Issues/PRs:** English preferred

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Loxone integration](docs/loxone.md) · [Troubleshooting](docs/troubleshooting.md) · [Project collection](https://github.com/therealb4n4na/loxone-smart-home-projects)
<!-- /project-meta -->

A local Python bridge for integrating Mitsubishi Heavy Industries air conditioners equipped with WF-RAC Wi-Fi adapters into Loxone.

A core design rule of this project is: **polling is read-only**. The bridge only changes an air conditioner when an explicit control request is received. This keeps the physical remote control and other control paths usable instead of constantly forcing the last Loxone state back onto the device.

## What this project gives you

- local polling of multiple WF-RAC devices
- power, operating mode, target temperature, fan speed, and airflow control
- HTTP API designed for Loxone
- explicit write verification by reading the device state back
- retry and timeout handling
- offline / recovery detection
- persistent Loxone intent state
- detection of external changes via `EXTERNAL_DRIFT`
- group logic for multiple indoor units sharing one outdoor unit
- rotating logs
- write access restricted to the configured controller IP

## Architecture

```text
                      ┌──────── Remote control / other control path
                      │
MHI WF-RAC devices ◄──┼──── local WF-RAC communication
        ▲             │
        │             │
        │       mhi_bridge.py :8091
        │         ├─ passive poller
        │         ├─ state cache
        │         ├─ intent cache
        │         ├─ write verification
        │         └─ group / conflict logic
        │                    ▲
        └────────────────────┤
                             │ HTTP
                           Loxone
```

## Why keep a separate intent state?

The actual device state and the state most recently requested by Loxone are not always identical. A user may operate the physical remote, another application may change the unit, or the device may temporarily report standby-related values after power-off.

The bridge therefore tracks two separate concepts:

- **actual device state**
- **Loxone intent**, meaning the last state explicitly requested by Loxone

This separation avoids unnecessary writes during normal polling and makes external/manual control predictable.

## Shared outdoor unit

In multi-split systems, indoor units connected to the same outdoor unit cannot always select operating modes independently. The bridge supports device groups and rejects conflicting mode combinations, such as heating and cooling at the same time within one configured group.

## Tested hardware

This project is developed and operated with **Mitsubishi Heavy Industries air conditioners using WF-RAC Wi-Fi adapters**. Multiple indoor units are used in the tested installation, including shared-outdoor-unit logic.

The bridge intentionally documents the WF-RAC interface rather than claiming compatibility with every MHI indoor-unit model. Firmware and supported commands can differ, so additional hardware variants should be verified before being marked as tested.

## Requirements

- Linux; developed and tested on DietPi / Debian
- Python 3
- local network connectivity to the WF-RAC adapters
- a compatible WF-RAC parser / protocol library
- device/operator identity data required for local WF-RAC communication

## Configuration

Production files are intentionally not published:

```text
config.json
identity.json
state/
logs/
```

Templates are provided as:

- [`config.example.json`](config.example.json)
- [`identity.example.json`](identity.example.json)

Copy and adapt them for your own installation.

`service.write_client_ip` defines the only remote IP address allowed to send control commands, typically the Loxone Miniserver. Status, health, and diagnostic endpoints remain readable.

## HTTP API

### Overall status

```text
GET http://<HOST>:8091/status
GET http://<HOST>:8091/api/v1/status
```

### Health

```text
GET http://<HOST>:8091/health
```

`/health` reports `online=true` when at least one configured device is reachable. For complete monitoring of multi-device systems, evaluate `/status` and the state of every device.

### List configured devices

```text
GET http://<HOST>:8091/api/v1/devices
```

### Per-device status

```text
GET http://<HOST>:8091/ac1/status
GET http://<HOST>:8091/ac2/status
```

### Control

Examples:

```text
GET http://<HOST>:8091/ac1/power?value=1
GET http://<HOST>:8091/ac1/temperature?value=22.5
GET http://<HOST>:8091/ac1/mode?value=cool
GET http://<HOST>:8091/ac1/fan?value=auto
GET http://<HOST>:8091/ac1/direction?value=...
```

Multiple parameters can be combined through the `set` endpoint.

All write endpoints accept requests only from the configured controller IP and localhost. Status and diagnostic endpoints remain readable.

## Important log markers

- `CMD_START` – control command started
- `CMD_OK` – command completed and was verified
- `CMD_FAIL` – command failed
- `CMD_CONFLICT` – group/mode conflict prevented
- `DEVICE_OFFLINE` – device marked offline after communication errors
- `DEVICE_RECOVERED` – communication restored
- `EXTERNAL_DRIFT` – actual state changed outside Loxone

## Passive polling guarantee

The polling path must never generate control writes by itself. This is a central design goal and changes to polling logic should be reviewed especially carefully.

## Loxone

See [`docs/loxone.md`](docs/loxone.md).

## Troubleshooting

See [`docs/troubleshooting.md`](docs/troubleshooting.md).

## Reverse engineering notes

Protocol findings and device-specific behavior should be labeled as **Verified**, **Experimental**, or **Unknown**. This makes it clear which behavior was reproduced on real hardware and which conclusions are still provisional.

## License

MIT License – see [`LICENSE`](LICENSE).
