# Troubleshooting

## Service state

```bash
systemctl status mhi-bridge.service
journalctl -u mhi-bridge.service -n 100 --no-pager
```

Also check the rotating bridge log if file logging is enabled.

## Device offline

Check `online`, `consecutive_errors`, and the most recent error in `/status`. Then verify basic network connectivity first:

```bash
ping -c 10 <WF-RAC-IP>
```

If multiple access points are present, verify that the device is associated with a suitable AP.

## Important log markers

- `DEVICE_OFFLINE`: communication failed repeatedly
- `DEVICE_RECOVERED`: device became reachable again
- `CMD_FAIL`: explicit control command failed
- `CMD_CONFLICT`: operating mode rejected because of a shared outdoor unit
- `EXTERNAL_DRIFT`: state changed outside Loxone

## Health is online but one device is missing

`/health` is intentionally compact and reports online when at least one configured device is reachable. For multi-device installations, always evaluate `/status` as well.

## HTTP 403 on control requests

The request did not originate from the configured controller IP. Read-only status requests can still be allowed.

## After changes

```bash
python3 -m py_compile mhi_bridge.py
sudo systemctl restart mhi-bridge.service
curl -sS http://127.0.0.1:8091/status
```

Then verify each device individually before testing one deliberately harmless control command.
