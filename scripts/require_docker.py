#!/usr/bin/env python
"""Fail the commit when the Docker daemon is not reachable."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _docker_executable() -> str | None:
    # Docker Desktop ships an extensionless shell wrapper named "docker" next to
    # docker.exe. shutil.which("docker") returns that wrapper, which Windows
    # cannot execute via CreateProcess.
    if sys.platform == "win32":
        return shutil.which("docker.exe")
    return shutil.which("docker")


def main() -> int:
    docker = _docker_executable()
    if docker is None:
        print(
            "Docker is not installed. Install Docker Desktop before committing.",
            file=sys.stderr,
        )
        return 1

    result = subprocess.run(
        [docker, "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        print(
            "Docker is not running. Start Docker Desktop before committing.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
