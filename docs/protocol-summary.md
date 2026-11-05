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
  models, so existing setups never regress. The GOAT map screen requests the
  lawn with `getMapInfo_V2` and body `{"type": "0"}` only. Active pieces have
  `using` `1`. A set is complete when `serial` is the piece count and indexes
  `0` .. `serial - 1` are all present. Those `info` strings are joined in index
  order and decoded once as base64 plus LZMA.
- **GOAT O-series** (`family = goat_o_series`, **experimental**): confirmed
  against a decrypted **GOAT O800 RTK** capture (class `9bts2s`, model
  `GOAT_O800_LC`, fw `1.9.10`):
  - `clean` (not `clean_V2`); every act carries `content:{type:"auto"}` and the
    stop body uses `content.type = "auto"` (G1 uses `""`).
  - `getCleanInfo` (not `getCleanInfo_V2`) — **same status fields**
    (`state`, `cleanState.motionState`, `trigger`, nested `cleanState.cid`).
  - `getPos` asks for `chargePos`, `deebotPos`, and `rtkPos` (no `uwbPos`).
    On the O1200 LiDAR Pro those fields stay at the origin with no RTK fix.
    Its map is in millimetres with the dock at `0,0`. Live points are the
    `robotPos` string on `onFwBuryPoint-bd_basicinfo` (rare), the `pose` of
    each `signalInfos` sample on `onFwBuryPoint-bd_sysinfo` (five per batch,
    about every five minutes), and `prev` / `curr` on
    `onFwBuryPoint-bd_locationjump`, which are in metres. A sample with
    `charger` `1`, or `chargerPos` on `onFwBuryPoint-bd_task-charge-stop`,
    places the dock.
  - Return to dock is `charge` with `act` `go` and leaves the Auto task open.
    Ending the task, which lets the next schedule start, is `clean` with
    `act` `stop`. A null HTTP body on `clean` is a successful fire-and-forget.
  - `getRTK` returns the single fixed **base station** (`rtks[0].x/y/sn`) plus
    GNSS signal `observations`; the station is shown on the map where the G1
    shows UWB beacons.
  - Map dialect `getMapState` / `getMI` / `getMapTrack` / `getAreaSet` /
    `getSpecialContour`, plus RTK-specific reads (`getRTK`, `getMoveCtrlState`).

Dock (`charge {act:"go"}`), `appping`, `getLifeSpan`, battery, and error are
shared. The capability profile picks the command names / map dialect; the runtime
profile in `mower_compat.py` still adapts on failures.

`getAreaSet` type `ar` decodes to area anchor points. The O-series lawn
outline arrives as `onMI` pieces when the app map opens. They use the same
`batid` / `serial` / `index` / `using` / `info` fields and base64 plus LZMA
wrapper as `onMapInfo_V2`. Inside, layer `1` holds one
`s<n>;<flag>;<x>,<y>;<chain>` string per boundary. The chain is a start point
followed by 50-unit steps. Digits `1` to `8` run clockwise from north-west
(`1` NW, `2` N, `3` NE, `4` E, `5` SE, `6` S, `7` SW, `8` W), and `d(n)` is
direction `d` taken `n` times in total. `onArI` repeats the same boundary in
`<id>;<x>,<y>;<chain>` pieces and is not needed for the outline. The `getMI`
request body is not captured yet. The integration tries `{"type": "0"}` and
`{}` in turn while the outline is missing. An RTK fix uses `deebotPos` and
`rtkPos`. A LiDAR `getPos` stuck at the origin is ignored in favor of the
telemetry points above.

- Device commands use the N-GIoT endpoint `/api/iot/endpoint/control` with `apn=<command>` and `fmt=j`.
- Command bodies use the app-style envelope with header version `0.0.22`.
- Live updates arrive over ECOVACS MQTT topics shaped like `iot/atr/on.../<device>/<class>/<resource>/j`.
- Startup uses grouped `getInfo` readbacks to populate state.
- Runtime state changes should come from MQTT pushes where possible.
- Readback before commands is stale-only and guarded to avoid unnecessary polling.

## Intentional Omissions

Raw captures, local lab commands, device identifiers, IP addresses, account details, and packet files are not included in this repository.
