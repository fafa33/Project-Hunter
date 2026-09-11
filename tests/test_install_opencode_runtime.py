from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "install_opencode_runtime.py"
_SPEC = importlib.util.spec_from_file_location("install_opencode_runtime", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


def _archive(path: Path, *, name: str = "opencode", content: bytes = b"binary") -> None:
    with tarfile.open(path, mode="w:gz") as bundle:
        member = tarfile.TarInfo(name=name)
        member.size = len(content)
        member.mode = 0o755
        bundle.addfile(member, io.BytesIO(content))


def test_extract_binary_installs_only_expected_member(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.tar.gz"
    destination = tmp_path / "bin" / "opencode"
    _archive(archive, content=b"trusted-opencode")

    installer._extract_binary(archive, destination)

    assert destination.read_bytes() == b"trusted-opencode"
    assert destination.stat().st_mode & 0o111


def test_extract_binary_fails_closed_when_expected_member_is_missing(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.tar.gz"
    _archive(archive, name="unexpected")

    with pytest.raises(installer.OpenCodeInstallError, match="does not contain one opencode binary"):
        installer._extract_binary(archive, tmp_path / "opencode")


def test_release_is_immutable_and_checksum_pinned() -> None:
    assert installer.OPENCODE_URL.endswith(
        f"/releases/download/v{installer.OPENCODE_VERSION}/{installer.OPENCODE_ASSET}"
    )
    assert len(installer.OPENCODE_SHA256) == 64
    int(installer.OPENCODE_SHA256, 16)
