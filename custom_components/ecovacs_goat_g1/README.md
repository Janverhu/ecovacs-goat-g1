# Ecovacs GOAT G1

Mower-only Home Assistant custom integration for ECOVACS GOAT G1-line lawn mowers.

Built and tested with a GOAT G1-800, with compatibility work for the G1, G1-800 / G-800, and G1-2000 model line.

See the repository root `README.md` for screenshots, installation, setup, support scope, troubleshooting, safety notes, and acknowledgements.

**Live map updates** — The map merges a **trace outline** (completed cuts, from `getMapTrace_V2` / MQTT) with a **live position segment** (recent `onPos` or slow cloud `getPos` while mowing). Trace MQTT is gated until about **90°** of accumulated heading change from live positions; without MQTT, a **~60 s** mowing poll refreshes both position and trace. **`request_live_position_stream`** (used by the optional card with a timed keepalive) mimics the official app watching the map so **MQTT** stays hot. Full explanation: root **README → How It Behaves → Live map**.
