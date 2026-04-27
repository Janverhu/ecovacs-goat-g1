# Protocol Summary

This document intentionally contains only sanitized, high-level protocol notes suitable for a public repository.

## Design

The integration is based on observed official app behavior for an ECOVACS GOAT G1-800 mower:

- Device commands use the N-GIoT endpoint `/api/iot/endpoint/control` with `apn=<command>` and `fmt=j`.
- Command bodies use the app-style envelope with header version `0.0.22`.
- Live updates arrive over ECOVACS MQTT topics shaped like `iot/atr/on.../<device>/<class>/<resource>/j`.
- Startup uses grouped `getInfo` readbacks to populate state.
- Runtime state changes should come from MQTT pushes where possible.
- Readback before commands is stale-only and guarded to avoid unnecessary polling.

## Intentional Omissions

Raw captures, local lab commands, device identifiers, IP addresses, account details, and packet files are not included in this repository.
