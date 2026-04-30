# Security Policy

Please do not publish account credentials, access tokens, device IDs, private network details, packet captures, or Home Assistant storage files in public issues.

If you believe you found a vulnerability, open a GitHub security advisory or contact the maintainer privately through GitHub before disclosing details publicly.

This integration parses cloud payloads as JSON data and does not execute payload content.

## Automated checks (pre-commit)

The repository defines [pre-commit](https://pre-commit.com) hooks in `.pre-commit-config.yaml`. They are intended to catch accidental secret commits, obvious static-analysis issues, and known vulnerable dependencies before merge. **Docker** must be running locally for the scanner hooks below (Semgrep does not ship a supported Windows wheel, so those hooks run in containers).

| Hook | Role |
|------|------|
| **gitleaks-docker** | Scans staged changes for hardcoded secrets. |
| **semgrep-docker** | Runs Semgrep with `--config auto` (broad static checks). |
| **pip-audit** | Audits the dependency tree in `requirements-audit.txt` for published Python CVEs (similar in spirit to `npm audit`; this project has no `package.json`). |
| **trivyfs-docker** | Filesystem vulnerability scan (`trivy fs`), **vuln** scanner only, **HIGH** and **CRITICAL** severities, with common dev directories skipped and a 15-minute timeout so local virtualenv trees are not traversed. |

Basic file hygiene (trailing whitespace and final newlines) is also enforced by [pre-commit-hooks](https://github.com/pre-commit/pre-commit-hooks).

Install and run: see **Contributing** in `CONTRIBUTING.md`. CI runs `pre-commit run --all-files` on pull requests (`.github/workflows/validate.yml`).
