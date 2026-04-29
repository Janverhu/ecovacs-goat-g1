# Protocol Summary

This document intentionally contains only sanitized, high-level protocol notes suitable for a public repository.

## Design

The integration is based on observed official app behavior for ECOVACS **GOAT G1-line** mowers (retail names such as **GOAT G1**, **G1-2000**, **G1-800** / **G-800**). The same H5 bundle and N-GIoT command set apply across these SKUs; differences are mainly **coverage, hardware bundles, and** `getRobotFeature` **flags** (e.g. 4G, GPS), not separate protocols.

Primary capture reference: **GOAT G1-800** (also written **G1-800**).

- Device commands use the N-GIoT endpoint `/api/iot/endpoint/control` with `apn=<command>` and `fmt=j`.
- Command bodies use the app-style envelope with header version `0.0.22`.
- Live updates arrive over ECOVACS MQTT topics shaped like `iot/atr/on.../<device>/<class>/<resource>/j`.
- Startup uses grouped `getInfo` readbacks to populate state.
- Runtime state changes should come from MQTT pushes where possible.
- Readback before commands is stale-only and guarded to avoid unnecessary polling.

## Intentional Omissions

Raw captures, local lab commands, device identifiers, IP addresses, account details, and packet files are not included in this repository.
