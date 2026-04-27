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

- Lawn mower entity: start or resume mowing, pause, return to charge.
- Button entities: refresh state, end mowing.
- Sensors: battery, error, Wi-Fi diagnostics, current/total mowing stats, blade and lens brush lifespan.
- Settings: rain sensor and delay, animal protection and time window, AI recognition, edge mowing, safer mode, warning switches, cut direction, mowing efficiency, obstacle avoidance.
- Service: `ecovacs_goat_g1.refresh_state`, which only performs a grouped refresh when cached MQTT/readback state is stale.

## Installation With HACS

Until this is accepted into any default HACS list, add it as a custom repository:

1. In Home Assistant, open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/Janverhu/ecovacs-goat-g1`.
4. Select category **Integration**.
5. Install **Ecovacs GOAT G1**.
6. Restart Home Assistant.
7. Add the integration from **Settings -> Devices & services -> Add integration**.

## Configuration

You need your ECOVACS account username, password, and country. The integration uses the ECOVACS cloud; there is no self-hosted mode and no custom REST/MQTT endpoint option.

During setup, choose a Home Assistant device name. A generated default such as `Ecovacs-GOAT-1` is provided.

## Communication And Polling

The integration is intentionally conservative:

- MQTT is used for live state and setting updates where possible.
- Startup performs grouped `getInfo` readbacks to populate entities.
- Commands perform a stale-only grouped refresh first if no recent MQTT/readback update was seen.
- The refresh button and service use the same stale-only guard.
- There is no periodic polling interval.

This is meant to reduce load on the mower and cloud path after an earlier failure mode where the mower itself became unreachable.

## Safety Notes

Only use commands that have been tested with your mower in a safe outdoor state. Stop using the integration if the official ECOVACS app also loses contact with the mower, commands repeatedly time out, or the mower requires a restart to recover.

The integration treats cloud payloads as JSON data. It parses known fields into Home Assistant state and stores unknown payloads as raw diagnostic data; it does not execute payload content.

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
