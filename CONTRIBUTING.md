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

## Pre-commit

Hooks run **gitleaks** (secrets), **semgrep** (static analysis), and **Trivy** filesystem scans in Docker, **pip-audit** on `requirements-audit.txt` (Python CVEs), plus basic whitespace fixes. **Docker** is required for those scanners (Semgrep does not publish a supported Windows wheel). Install [pre-commit](https://pre-commit.com) and Docker, then:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

To skip a hook once (e.g. Docker not running): `SKIP=gitleaks-docker,semgrep-docker,trivyfs-docker git commit -m "..."` (comma-separated hook ids).
