# Contributing

This repository is intentionally scoped to ECOVACS GOAT mower support. Please keep changes conservative and based on observed official-app behavior where possible.

## Guidelines

- Do not add Ecovacs vacuum support or `deebot-client`/XMPP code paths.
- Do not add periodic polling without a clear safety reason.
- Prefer MQTT push handling and grouped readbacks only for startup or stale state.
- Do not include real device IDs, tokens, captures, LAN details, or account information in issues or pull requests.
- Add focused tests for parser or command-shape changes.

## Development Checks

```bash
python -m pytest tests/ecovacs_goat_g1/
python -m compileall custom_components/ecovacs_goat_g1 tests/ecovacs_goat_g1
```
