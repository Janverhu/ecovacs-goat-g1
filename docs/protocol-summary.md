# Protocol Summary

This document intentionally contains only sanitized, high-level protocol notes suitable for a public repository.

## Design

The integration is based on observed official app behavior for ECOVACS **GOAT G1-line** mowers (retail names such as **GOAT G1**, **G1-2000**, **G1-800** / **G-800**). The same H5 bundle and N-GIoT command set apply across these SKUs; differences are mainly **coverage, hardware bundles, and** `getRobotFeature` **flags** (e.g. 4G, GPS), not separate protocols.

Primary capture reference: **GOAT G1-800** (also written **G1-800**).

## Model families and map dialects

The official app ships one H5 bundle that supports the whole GOAT range, but the
mowers speak **two dialects**. The integration models this with a per-device
*capability profile* (`mower_profiles.py`) selected from the cloud `deviceName`:

- **GOAT G1 line** (`family = goat_g1`): UWB beacons with the `*_V2` dialect —
  `clean_V2`, `getCleanInfo_V2`, `getMapInfo_V2` / `getMapTrace_V2`, and
  `uwbPos` positions. This is the validated path and the default for unknown
  models, so existing setups never regress.
- **GOAT O-series** (`family = goat_o_series`, **experimental**): confirmed
  against a decrypted **GOAT O800 RTK** capture (class `9bts2s`, model
  `GOAT_O800_LC`, fw `1.9.10`):
  - `clean` (not `clean_V2`); every act carries `content:{type:"auto"}` and the
    stop body uses `content.type = "auto"` (G1 uses `""`).
  - `getCleanInfo` (not `getCleanInfo_V2`) — **same status fields**
    (`state`, `cleanState.motionState`, `trigger`, nested `cleanState.cid`).
  - `getPos` returns `deebotPos` / `chargePos` / **`rtkPos`** (no `uwbPos`).
  - `getRTK` returns the single fixed **base station** (`rtks[0].x/y/sn`) plus
    GNSS signal `observations`; the station is shown on the map where the G1
    shows UWB beacons.
  - Map dialect `getMapState` / `getMI` / `getMapTrack` / `getAreaSet` /
    `getSpecialContour`, plus RTK-specific reads (`getRTK`, `getMoveCtrlState`).

Dock (`charge {act:"go"}`), `appping`, `getLifeSpan`, battery, and error are
shared. The capability profile picks the command names / map dialect; the runtime
profile in `mower_compat.py` still adapts on failures.

The O-series area-outline / trace blobs (`getMapTrack`, `getMI`, `getAreaSet`)
are **not decoded**: the only available O-series capture was taken while docked,
where `getMapTrack` returns `fail`, so there is no validated geometry sample. The
O-series live map is driven by the shared position stream (`deebotPos` + `rtkPos`)
instead; the map id (`mid`) is still learned so a future active-mowing capture can
complete the outline decode.

- Device commands use the N-GIoT endpoint `/api/iot/endpoint/control` with `apn=<command>` and `fmt=j`.
- Command bodies use the app-style envelope with header version `0.0.22`.
- Live updates arrive over ECOVACS MQTT topics shaped like `iot/atr/on.../<device>/<class>/<resource>/j`.
- Startup uses grouped `getInfo` readbacks to populate state.
- Runtime state changes should come from MQTT pushes where possible.
- Readback before commands is stale-only and guarded to avoid unnecessary polling.

## Intentional Omissions

Raw captures, local lab commands, device identifiers, IP addresses, account details, and packet files are not included in this repository.
