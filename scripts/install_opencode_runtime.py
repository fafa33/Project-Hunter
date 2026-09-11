#!/usr/bin/env python3
"""Install the pinned OpenCode runtime used by the Railway Issue Agent.

The Railway image must contain a real ``opencode`` executable for the governed
OpenCode provider adapter. Download only the repository-approved immutable
release asset and verify its SHA-256 before installing it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

OPENCODE_VERSION = "1.18.30"
OPENCODE_ASSET = "opencode-linux-x64.tar.gz"
OPENCODE_SHA256 = "55007246858165496ff85ba1c2b648f7421e8e2013bf4189a680c9ff8e699d17"
OPENCODE_URL = f"https://github.com/anomalyco/opencode/releases/download/v{OPENCODE_VERSION}/{OPENCODE_ASSET}"


class OpenCodeInstallError(RuntimeError):
    """Raised when the pinned runtime cannot be installed safely."""


def _verify_platform() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise OpenCodeInstallError("pinned OpenCode runtime supports Linux x86_64 only")


def _download(destination: Path) -> None:
    digest = hashlib.sha256()
    try:
        with urlopen(OPENCODE_URL, timeout=60) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as error:
        raise OpenCodeInstallError(f"OpenCode download failed: {type(error).__name__}") from error
    if digest.hexdigest() != OPENCODE_SHA256:
        raise OpenCodeInstallError("OpenCode archive SHA-256 mismatch")


def _extract_binary(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = [member for member in bundle.getmembers() if member.name.rstrip("/") == "opencode"]
            if len(members) != 1 or not members[0].isfile():
                raise OpenCodeInstallError("pinned OpenCode archive does not contain one opencode binary")
            source = bundle.extractfile(members[0])
            if source is None:
                raise OpenCodeInstallError("pinned OpenCode binary could not be read")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            with temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(temporary, 0o755)
            temporary.replace(destination)
    except (OSError, tarfile.TarError) as error:
        raise OpenCodeInstallError(f"OpenCode archive extraction failed: {type(error).__name__}") from error


def install(destination: Path) -> None:
    _verify_platform()
    with tempfile.TemporaryDirectory(prefix="hunter-opencode-install-") as directory:
        archive = Path(directory) / OPENCODE_ASSET
        _download(archive)
        _extract_binary(archive, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="install-opencode-runtime")
    parser.add_argument("--destination", type=Path, default=Path("/app/bin/opencode"))
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        install(arguments.destination)
    except OpenCodeInstallError as error:
        print(f"OpenCode runtime install failed closed: {error}")
        return 2
    print(f"installed pinned OpenCode v{OPENCODE_VERSION} at {arguments.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
