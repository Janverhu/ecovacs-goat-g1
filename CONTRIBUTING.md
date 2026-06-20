# Contributing

This repository is intentionally scoped to ECOVACS GOAT mower support. Please keep changes conservative and based on observed official-app behavior where possible.

## Guidelines

- Do not add Ecovacs vacuum support or `deebot-client`/XMPP code paths.
- Do not add periodic polling without a clear safety reason.
- Prefer MQTT push handling and grouped readbacks only for startup or stale state.
- Do not include real device IDs, tokens, captures, LAN details, or account information in issues or pull requests.
- Add focused tests for parser or command-shape changes.

## Releases & versioning

Versioning is fully automated by two GitHub Actions workflows. You normally
**never edit `version` in `manifest.json` by hand** — set the starting line once
and let the workflows advance it.

### Branches

- **`develop`** — every push publishes a beta pre-release (`.github/workflows/develop-prerelease.yml`).
- **`main`** — every push promotes the current line to a stable release (`.github/workflows/release.yml`).

### The version

`manifest.json` holds a version like `0.3.0b1`. Only its **base** (`0.3.0`,
the `MAJOR.MINOR.PATCH` part) drives the flow; the `bN` suffix is informational.
The base is set once when a new line begins and then advances automatically.

### Beta pre-releases (push to `develop`)

1. The base is read from `manifest.json` (e.g. `0.3.0`).
2. If that base **already shipped as a stable release**, it advances to the next
   minor (e.g. `0.3.0` released ⇒ work continues at `0.4.0`), so a beta never
   shadows a released version.
3. The beta number increments from existing tags: `0.4.0b1`, `0.4.0b2`, …
4. When the line advances, the new version is written back into `manifest.json`
   on `develop` (committed as `Start <version> development line [skip ci]`).
5. The beta is published as a GitHub pre-release; **older pre-releases are
   deleted** so only the latest beta is visible in HACS.

### Stable releases (push to `main`)

1. The base is read from `manifest.json` with any `bN` suffix stripped
   (`0.3.0b1` → `0.3.0`).
2. If that tag already exists, the run is a **no-op** (no duplicate releases).
3. Otherwise the version is written back into `manifest.json` on `main`
   (committed as `Release <version> [skip ci]`) so Home Assistant reports the
   correct installed version, and a stable GitHub release is published.

### End-to-end example

| Step | Action | Result |
| --- | --- | --- |
| 1 | Push to `develop` (manifest `0.3.0b1`) | beta `0.3.0b1`, `0.3.0b2`, … |
| 2 | Merge `develop` → `main` | stable **`0.3.0`**; `main` manifest synced to `0.3.0` |
| 3 | Push to `develop` again | line advances → beta **`0.4.0b1`**; `develop` manifest synced to `0.4.0b1` |
| 4 | Merge `develop` → `main` | stable **`0.4.0`** |

To **skip a minor** (e.g. jump to `1.0.0`) or cut a **patch** (e.g. `0.3.1`),
manually set the base in `develop`'s `manifest.json`; the workflows pick it up
from there.

### Notes

- Workflow pushes use the default `GITHUB_TOKEN`, which does **not** re-trigger
  workflows; `[skip ci]` is an additional safeguard. Both `develop` and `main`
  must allow the token to push (branch protection bypass) for the write-backs.
- The dashboard card is cache-busted by a content hash of the card file (see
  `custom_components/ecovacs_goat_g1/frontend.py`), so it refreshes whenever the
  file changes — independent of the version number.

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
