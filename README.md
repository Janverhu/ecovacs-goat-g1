# Ecovacs GOAT G1 for Home Assistant

A mower-only Home Assistant custom integration for ECOVACS GOAT lawn mowers, built and tested with a GOAT G1-800.

This project exists because the regular Ecovacs/Home Assistant path and its `deebot-client` based command stack caused my mower to become unreachable, including from the official ECOVACS app, until the mower was physically restarted. The upstream Ecovacs ecosystem also carries a lot of vacuum, XMPP, and multi-device complexity that I did not want to adapt without owning an Ecovacs vacuum to test against.

The intent of this repository is deliberately narrow: support one mower family using behavior observed from the official app, with conservative communication patterns. It may work with other GOAT G1 variants or related GOAT models, but those are not tested.

## Status

- Tested device: ECOVACS GOAT G1-800.
- Other GOAT models: possibly compatible, untested.
- Ecovacs vacuums: not supported.
- Official integration replacement: no. This is a separate mower-only custom integration.

## Why No `deebot-client`?

This integration does not depend on `deebot-client` or `py-sucks`.

Instead it uses a small, mower-focused driver based on captured official app behavior:

- N-GIoT control endpoint: `/api/iot/endpoint/control?...&apn=<command>&fmt=j`
- App-style payload envelope version: `0.0.22`
- MQTT push updates from `iot/atr/on...` topics
- One grouped startup state refresh
- Stale-only readback before commands when MQTT/readback data is old
- No periodic HTTP polling loop

That design keeps the dependency surface small and avoids repeatedly sending broad or vacuum-oriented commands to the mower.

## Features

- Lawn mower entity: start or resume mowing, stop mowing, return to charge.
- Button entities: refresh state, stop mowing.
- Sensors: battery, error, Wi-Fi diagnostics, current/total mowing stats, blade and lens brush lifespan.
- Settings: rain sensor and delay, animal protection and time window, AI recognition, edge mowing, safer mode, warning switches, cut direction, mowing efficiency, obstacle avoidance.
- Services: `ecovacs_goat_g1.refresh_state` for stale-only readbacks, plus opt-in debug capture services for troubleshooting unsupported models.
- Optional Lovelace card with explicit Start/Resume, Stop, Dock, and Refresh controls.

## Branding

Custom brand icons are in `custom_components/ecovacs_goat_g1/brand/`: `icon.png`, `dark_icon.png`, and matching `icon@2x.png` / `dark_icon@2x.png`. They are produced from a square-cropped side-view product photo of the GOAT G1 (large treaded wheel, antenna, red button), with background made transparent. The dark variant uses increased contrast for visibility on dark themes. Not official ECOVACS assets.

## Installation With HACS

Until this is accepted into any default HACS list, add it as a custom repository:

1. In Home Assistant, open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/Janverhu/ecovacs-goat-g1`.
4. Select category **Integration**.
5. Install **Ecovacs GOAT G1**.
6. Restart Home Assistant.
7. Add the integration from **Settings -> Devices & services -> Add integration**.

## Optional Lovelace Card

This repository includes a no-build dashboard card at `www/ecovacs-goat-card.js`. It is optional, but useful because Home Assistant's built-in lawn mower card does not expose a native stop button.

To install the card:

1. Copy `www/ecovacs-goat-card.js` from this repository to your Home Assistant config directory as `www/ecovacs_goat/ecovacs-goat-card.js`.
2. In Home Assistant, open **Settings -> Dashboards -> Resources**.
3. Add a JavaScript module resource:

```text
/local/ecovacs_goat/ecovacs-goat-card.js
```

After the resource is loaded, the card is available from **Edit dashboard -> Add card -> Custom cards -> Ecovacs GOAT Card**.

Example YAML:

```yaml
type: custom:ecovacs-goat-card
entity: lawn_mower.mower
battery_entity: sensor.mower_battery_level
error_entity: sensor.mower_error
area_entity: sensor.mower_mowing_area
progress_entity: sensor.mower_mowing_progress
direction_entity: number.mower_cut_direction
stop_button: button.mower_end_mowing
name: Mower
```

## Configuration

You need your ECOVACS account username, password, and country. The integration uses the ECOVACS cloud; there is no self-hosted mode and no custom REST/MQTT endpoint option.

During setup, choose a Home Assistant device name. A generated default such as `Ecovacs-GOAT-1` is provided.

## Communication And Polling

The integration is intentionally conservative:

- MQTT is used for live state and setting updates where possible.
- Startup performs grouped `getInfo` readbacks to populate entities.
- Commands perform a stale-only grouped refresh first if no recent MQTT/readback update was seen.
- The card's keepalive button starts a 10-minute app-style live-map keepalive and shows a countdown while it is active.
- Routine live-position polling is only used as a sparse fallback when MQTT position updates are stale.

This is meant to reduce load on the mower and cloud path after an earlier failure mode where the mower itself became unreachable.

### Experimental App-Presence MQTT

Cold-start Android captures showed that the official ECOVACS app opens two MQTT
sessions before the map screen is shown:

- An Aliyun IoT bootstrap session to `public.itls.<region>.aliyuncs.com:1883`
  that binds an app client and subscribes to `/sys/.../app/down/#`.
- An ECOVACS N-GIoT user/app session to
  `jmq-ngiot-<region>.dc.robotww.ecouser.net:443`.

The second session uses a client id shaped like `<uid>@USER/<realm>|...|`, a
username built from the mower DID plus app metadata, and the normal ECOVACS
account JWT as the password. This appears to be an app-presence signal rather
than the same mower push channel used by the integration's normal MQTT client.

The optional Lovelace card calls `ecovacs_goat_g1.request_live_position_stream`
while it is visible and the mower is mowing or returning. During those requests,
the integration now keeps an experimental N-GIoT app-presence MQTT connection
open for a short TTL and lets it expire when card requests stop. The goal is to
mimic the official app's "someone is watching the live map" presence without
leaving the extra session connected in the background.

## Debug Capture

For unsupported GOAT models, start an explicit debug capture before reproducing the issue. Captures are disabled by default and are stored locally under `/config/ecovacs_goat_g1_debug/` as bounded JSONL sessions. Account tokens, user IDs, device IDs, and resource IDs are redacted; map and position payloads are kept because they are usually required for mower debugging.

Recommended app-driven workflow:

1. Open **Developer Tools -> Services** and call `ecovacs_goat_g1.start_debug_capture` with a short reason, for example `pressed app map button`.
2. Perform the action in Home Assistant or the official ECOVACS app.
3. Optionally call `ecovacs_goat_g1.mark_debug_capture` at important moments, such as after opening the app map.
4. Call `ecovacs_goat_g1.stop_debug_capture`.
5. Download Home Assistant diagnostics for the integration, or call `ecovacs_goat_g1.export_debug_capture` to create a zip at `/local/ecovacs_goat/debug/`.

The normal capture path can record MQTT pushes received by the integration and commands sent by the integration. It cannot directly see commands sent from the official mobile app to ECOVACS unless those commands result in MQTT pushes back to this client. For true mobile-app outbound command capture, use an advanced MITM/proxy or Android packet-capture workflow outside this integration.

## Safety Notes

Only use commands that have been tested with your mower in a safe outdoor state. Stop using the integration if the official ECOVACS app also loses contact with the mower, commands repeatedly time out, or the mower requires a restart to recover.

The integration treats cloud payloads as JSON data. It parses known fields into Home Assistant state and stores unknown payloads as raw diagnostic data; it does not execute payload content.

## Security Research Context

Dennis Giese and braelynn's DEF CON 32 talk, [Reverse engineering and hacking Ecovacs robots](https://dontvacuum.me/talks/DEFCON32/DEFCON32_reveng_hacking_ecovacs_robots.pdf), covers multiple Ecovacs robots, including the GOAT G1. The talk is not documentation for the cloud APIs used here, but it is useful context for how this integration should behave.

Relevant takeaways for this project:

- The GOAT G1 is described as a Linux-based robot with cameras, ToF, UWB beacons, optional LTE, Rockchip RK3588-class hardware, and multiple MCUs. Treat it as a capable networked computer, not a simple appliance.
- The official mobile app and robots are chatty. The talk calls out telemetry such as live coordinates, Wi-Fi/network data, maps, stuck-state data, and possibly images on supported models. This supports the integration's conservative design: prefer MQTT pushes, avoid broad polling loops, and keep debug capture opt-in and redacted.
- The Ecovacs app uses a native shell plus dynamically downloaded robot-specific plugin code. That matches our reverse-engineering experience: behavior can differ by model/plugin/app version, so captured app behavior should be documented with context and not assumed universal.
- The talk reports TLS and plugin weaknesses in parts of the Ecovacs ecosystem. For our work, this is a reminder to avoid capture workflows on untrusted networks and to treat app/session tokens as highly sensitive.
- It reports GOAT/lawnmower-specific BLE provisioning risks and a GOAT G1 BLE RCE demonstration. This integration does not use BLE, does not root the mower, and should not add local provisioning/control paths without a separate security review.
- It also discusses cloud/device data retention and used-device risks. Users should remove shared account access, change passwords when ownership changes, factory-reset before disposal, and understand that a factory reset may not erase all data from flash.

The practical policy for this integration remains: use only the minimum cloud/MQTT behavior needed for mower entities and the optional visible-card live map, do not expose camera/live video features, do not store unredacted credentials in diagnostics, and do not attempt firmware, root, BLE, or local-device modification workflows.

## Dependencies

The only extra Python package declared by this integration is pinned exactly:

```json
"paho-mqtt==2.1.0"
```

Other imports are Python standard library or Home Assistant/Core-provided packages such as `aiohttp` and `voluptuous`.

Exact pinning reduces surprise upgrades, but HACS custom integration installs do not provide lockfile/hash verification by default.

## Troubleshooting

- If entities are unknown after setup, press the **Refresh state** button once the mower is online.
- If commands fail, check whether the official ECOVACS app can still control the mower.
- If the mower becomes unreachable in both Home Assistant and the official app, stop testing and recover the mower first.
- Open an issue with Home Assistant logs, integration version, mower model, and which command or setting was used. Do not include passwords, access tokens, device IDs, or private network details.

## Acknowledgements

This project builds on public knowledge from the Home Assistant and HACS ecosystems, the official Home Assistant Ecovacs integration, and the `deebot-client` community project. Those projects helped identify the existing Ecovacs integration landscape and command names. This repository does not vendor or depend on `deebot-client`.

Thanks also to the maintainers of `paho-mqtt` and Home Assistant's custom integration APIs.

## License

MIT License. See [LICENSE](LICENSE).

## Disclaimer

This project is unofficial and is not affiliated with, endorsed by, or supported by ECOVACS. Use it at your own risk.
