# Security Policy

These projects typically run inside a local smart-home network, and some endpoints can control physical devices. Security should therefore not rely on the network firewall alone.

## Basic rules

- never commit production credentials or tokens,
- publish example configuration files instead of real configuration,
- restrict write access to the intended controller (for example Loxone) where possible,
- expose services only inside trusted networks,
- avoid forwarding control ports directly to the public Internet,
- review logs and screenshots for secrets, identifiers, and personal information before sharing them.

## Reporting a security issue

Please do **not** publish an obvious vulnerability together with complete exploit details in a public issue first. Prefer GitHub's private vulnerability reporting / Security Advisory functionality when available. If private reporting is unavailable, open a minimal public issue without secrets or actionable exploit details so that a private communication channel can be arranged.

## Supported versions

While these projects remain small, only the latest release and the current `main` branch are actively maintained.
