# Loxone integration

## Status

Overall status:

```text
http://<DIETPI-IP>:8091/status
```

Per-device status:

```text
http://<DIETPI-IP>:8091/ac1/status
```

A Loxone polling interval of roughly 10–30 seconds is usually appropriate. The bridge polls the air conditioners internally; Loxone only reads the cached bridge state.

## Writing values

Examples:

```text
/ac1/power?value=1
/ac1/temperature?value=22.5
/ac1/mode?value=cool
/ac1/fan?value=auto
```

The bridge performs the actual WF-RAC communication and verifies the resulting device state afterwards.

## Shared outdoor unit

With multi-split systems, Loxone must not assume that every indoor unit can freely select an independent mode. The bridge checks conflicts inside the configured group and rejects invalid combinations.

## External control

Changes made with the physical remote or another control interface are picked up by the passive poller. A deviation from the last known Loxone intent is logged as `EXTERNAL_DRIFT`; the poller does not automatically write the old Loxone value back.

This prevents a control "fight" between Loxone and manual operation.
