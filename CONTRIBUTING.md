# Contributing

Contributions are welcome. **English is preferred** for issues, pull requests, documentation, and code review so that the projects remain accessible to an international audience.

## Before opening an issue

Please check first:

1. whether the behavior is reproducible with the current version,
2. whether [`docs/troubleshooting.md`](docs/troubleshooting.md) already covers the problem,
3. whether logs or status data help narrow down the cause,
4. whether credentials, tokens, internal IP addresses, serial numbers, or other private information have been removed from logs and screenshots.

## Bug reports

A useful bug report should include, where applicable:

- hardware / gateway / adapter model,
- firmware version,
- operating system and Python version,
- project version or Git tag,
- exact steps to reproduce,
- expected and actual behavior,
- relevant log lines,
- tests already performed.

Never publish passwords, API keys, tokens, private keys, full production configuration files, or session data.

## Pull requests

Changes should:

- have a clearly defined purpose,
- avoid unintentionally changing existing behavior,
- include comments where the logic is not obvious,
- update the README or relevant file under `docs/` when behavior changes,
- never include production credentials, logs, state files, captures, or backups.

Before opening a pull request, perform at least a Python syntax check for modified Python files:

```bash
python3 -m py_compile <script.py>
```

For changes to device communication or control commands, also describe **how the change was tested on real hardware**.

## Verification level

For reverse-engineered or device-specific findings, please distinguish between:

- **Verified** – reproduced multiple times on real hardware
- **Experimental** – plausible and tested, but not yet broadly confirmed
- **Unknown** – observed, but meaning or cause is still unclear

This helps prevent assumptions from later being repeated as established facts.
