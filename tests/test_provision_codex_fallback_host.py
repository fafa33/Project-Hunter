"""ADR 0041 F5: the one-time macOS fallback host provisioning script (pure, unprivileged checks only)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from hunter.automation import issue_agent_mac_fallback as mac

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "issue_agent" / "provision_codex_fallback_host.py"
SOURCE = SCRIPT.read_text()


def load() -> Any:
    spec = importlib.util.spec_from_file_location("provision_codex_fallback_host", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provision = load()


def test_the_script_provisions_exactly_what_the_runner_attests() -> None:
    assert {str(k): v for k, v in provision.SOURCES.items()} == {str(k): v for k, v in mac.INSTALLED_SOURCES.items()}
    assert provision.PLIST_TEMPLATE == mac.PLIST_TEMPLATE
    pairs = [
        (provision.WORK_ROOT, mac.WORK_ROOT),
        (provision.SOCKET_PATH, mac.SOCKET_PATH),
        (provision.OWNER_INBOX, mac.OWNER_INBOX),
        (provision.CODEX_BINARY, mac.CODEX_BINARY),
        (provision.PLIST_PATH, mac.PLIST_PATH),
        (provision.INSTALLED_CONFIG_PATH, mac.INSTALLED_CONFIG_PATH),
    ]
    assert all(str(a) == str(b) for a, b in pairs) and provision.GATEWAY_PORT == mac.GATEWAY_PORT


def test_rendered_daemon_and_config_are_byte_identical_to_the_attested_rendering() -> None:
    config = mac.HostConfig("hunterrunner", "hunteragent", 503, 601, 502, "0.167.0", "c" * 64, "gpt-5.6")
    template = (ROOT / mac.PLIST_TEMPLATE).read_text()
    assert provision.render_plist(template, 503, 601) == mac.render_plist(ROOT, config)
    assert provision.render_installed_config(503) == mac.render_installed_config(config)
    plist = provision.render_plist(template, 503, 601).decode()
    assert "@RUNNER_" not in plist and "<string>hunteragent</string>" in plist and "<integer>384</integer>" in plist


def tarball(binary: bytes, *, name: str = "package/vendor/x86_64-apple-darwin/bin/codex", link: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(member)
        else:
            member.size, member.mode = len(binary), 0o755
            archive.addfile(member, io.BytesIO(binary))
    return buffer.getvalue()


def pin(monkeypatch: Any, data: bytes, binary: bytes) -> None:
    monkeypatch.setattr(provision, "CODEX_TARBALL_INTEGRITY", provision.tarball_integrity(data))
    monkeypatch.setattr(provision, "CODEX_BINARY_SHA256", hashlib.sha256(binary).hexdigest())


def test_a_pinned_tarball_yields_the_pinned_binary(monkeypatch: Any) -> None:
    data = tarball(b"codex-binary")
    pin(monkeypatch, data, b"codex-binary")
    assert provision.codex_from_tarball(data) == b"codex-binary"


def test_a_byte_outside_the_registry_integrity_refuses(monkeypatch: Any) -> None:
    good = tarball(b"codex-binary")
    pin(monkeypatch, good, b"codex-binary")
    with pytest.raises(provision.ProvisionError, match="official npm package"):
        provision.codex_from_tarball(good + b"\0")


def test_an_archive_with_a_different_binary_refuses_even_if_its_digest_were_pinned(monkeypatch: Any) -> None:
    evil = tarball(b"evil")
    pin(monkeypatch, evil, b"codex-binary")
    with pytest.raises(provision.ProvisionError, match="pinned build"):
        provision.codex_from_tarball(evil)


# Stable ids: the gzip header embeds the build time, so byte-derived ids differ across xdist workers.
@pytest.mark.parametrize(
    "data",
    [tarball(b"codex-binary", name="package/bin/codex"), tarball(b"", link=True)],
    ids=["missing-member", "symlinked-member"],
)
def test_a_missing_or_symlinked_binary_member_refuses(monkeypatch: Any, data: bytes) -> None:
    pin(monkeypatch, data, b"codex-binary")
    with pytest.raises(provision.ProvisionError):
        provision.codex_from_tarball(data)


def test_the_installed_binary_pin_and_the_attested_host_pin_cannot_drift() -> None:
    config = json.loads((ROOT / mac.HOST_CONFIG_PATH).read_text())
    assert (
        config["codex_sha256"] == provision.CODEX_BINARY_SHA256 and config["codex_version"] == provision.CODEX_VERSION
    )
    assert provision.CODEX_TARBALL_INTEGRITY.startswith("sha512-")


def test_free_ids_never_reuse_and_start_above_the_existing_accounts() -> None:
    assert provision.free_id({503, 504, 506}) == 505
    assert provision.free_id(set()) == 503


@pytest.mark.parametrize("owner", [None, "", "root", "hunterrunner", "hunteragent", "a;rm -rf /", "../x", "A" * 40])
def test_only_a_real_owner_account_receives_the_device_code_inbox(owner: str | None) -> None:
    with pytest.raises(provision.ProvisionError):
        provision.validate_owner(owner)


CREDENTIAL_PATH = re.compile(r"(^|[/\"'~])\.codex(/|\b)")


def code_strings(tree: ast.AST) -> list[tuple[str, str]]:
    """Every string constant the script executes with, by enclosing function; docstrings are prose, not code."""

    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, ast.FunctionDef) else scope
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docstrings:
                found.append((scope, child.value))
            visit(child, inner)

    visit(tree, "<module>")
    return found


TREE = ast.parse(SOURCE)
STRINGS = code_strings(TREE)


def test_the_script_never_grants_sudo_copies_credentials_or_registers_a_runner() -> None:
    texts = [text for _, text in STRINGS]
    assert not any("NOPASSWD" in text or "ALL=(" in text for text in texts)
    assert not any(CREDENTIAL_PATH.search(text) or "auth.json" in text for text in texts)
    assert not any(token in text for text in texts for token in ("config.sh", "--token", "registration-token"))
    # The sudoers files are only ever read, by the verifier.
    assert {scope for scope, text in STRINGS if text.startswith("/etc/sudoers")} == {"verify"}


def test_the_only_installed_files_are_the_binary_and_the_attested_set() -> None:
    targets = [
        ast.unparse(node.args[0])
        for node in ast.walk(TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "write_root_file"
    ]
    assert targets == ["CODEX_BINARY", "path"]  # ``path`` iterates expected_files: the attested sources only


def test_the_guards_see_code_and_not_prose() -> None:
    # Paired fixtures: a forbidden token inside a docstring is prose; the same token as an argument is code.
    prose = ast.parse('def f():\n    """never config.sh or ~/.codex"""\n    return 1\n')
    code = ast.parse('def f():\n    run(["./config.sh", "--token", "x"], "~/.codex/auth.json")\n')
    assert code_strings(prose) == []
    assert {text for _, text in code_strings(code)} == {"./config.sh", "--token", "x", "~/.codex/auth.json"}


@pytest.mark.parametrize("text", ['Path.home() / ".codex"', "'~/.codex/auth'", '"/Users/x/.codex"'])
def test_the_credential_path_guard_catches_every_spelling(text: str) -> None:
    assert CREDENTIAL_PATH.search(text) is not None


def test_a_normal_owner_login_is_accepted() -> None:
    assert provision.validate_owner("farhadafshari") == "farhadafshari"


def test_a_non_macos_host_refuses_before_anything_else(capsys: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(provision.os, "uname", lambda: type("U", (), {"sysname": "Linux"})())
    assert provision.main(["--checkout", str(ROOT)]) == 2
    assert "macOS only" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.skipif(provision.os.uname().sysname != "Darwin", reason="dry run provisions macOS only")
def test_the_dry_run_changes_nothing_and_lists_what_it_never_does(capsys: Any) -> None:
    assert provision.main(["--checkout", str(ROOT)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "DRY_RUN" and "add a sudoers rule" in report["never"]


def test_apply_without_root_refuses_before_touching_anything(capsys: Any, tmp_path: Path) -> None:
    if provision.os.geteuid() == 0:
        pytest.skip("must run unprivileged")
    assert provision.main(["--apply", "--checkout", str(ROOT), "--codex-tarball", str(tmp_path / "x.tgz")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"


@pytest.fixture(autouse=True)
def staging_root(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(provision, "_archive_staging_roots", lambda: (os.path.realpath(tmp_path),))


def test_pinned_archive_reader_rejects_symlinks_and_nonregular_files(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tgz"
    archive.write_bytes(b"pinned-archive-placeholder")
    assert provision.read_pinned_tarball(archive) == b"pinned-archive-placeholder"
    link = tmp_path / "archive-link.tgz"
    link.symlink_to(archive)
    with pytest.raises(provision.ProvisionError):
        provision.read_pinned_tarball(link)
    with pytest.raises(provision.ProvisionError):
        provision.read_pinned_tarball(tmp_path)


def test_pinned_archive_reader_refuses_untrusted_permissions(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tgz"
    archive.write_bytes(b"archive")
    archive.chmod(0o666)
    with pytest.raises(provision.ProvisionError, match="writable"):
        provision.read_pinned_tarball(archive)


@pytest.mark.skipif(provision.os.uname().sysname != "Darwin", reason="dry run provisions macOS only")
def test_a_tampered_tarball_refuses_in_the_dry_run(capsys: Any, tmp_path: Path) -> None:
    bad = tmp_path / "codex.tgz"
    bad.write_bytes(tarball(b"not codex"))
    assert provision.main(["--checkout", str(ROOT), "--codex-tarball", str(bad)]) == 2
    assert "pinned official npm package" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.skipif(shutil.which("/usr/bin/python3") is None, reason="macOS system Python only")
def test_the_script_runs_under_the_system_python_sudo_will_use() -> None:
    completed = subprocess.run(["/usr/bin/python3", "-I", "-m", "py_compile", str(SCRIPT)], check=False)
    assert completed.returncode == 0


def test_pinned_archive_reader_refuses_paths_outside_staging_roots(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "a.tgz").write_bytes(b"x")
    for outside in (Path("/etc/hosts"), nested / "a.tgz"):
        with pytest.raises(provision.ProvisionError, match="staged"):
            provision.read_pinned_tarball(outside)


WORK_ROOT_LISTING = "drwx------+ 2 hunterrunner hunterrunner 64 Nov  6 22:00 /private/var/hunter-fallback/work\n"


def test_the_work_root_grants_hunteragent_traversal_and_nothing_else() -> None:
    assert provision.work_root_acl_is_exact(WORK_ROOT_LISTING + " 0: user:hunteragent allow search\n")


@pytest.mark.parametrize(
    "entries",
    [
        "",
        " 0: user:hunteragent allow search,list\n",
        " 0: user:hunterrunner allow search\n",
        " 0: user:hunteragent allow search inherited\n",
        " 0: user:hunteragent allow search\n 1: everyone allow search\n",
        " 0: group:staff allow search\n 1: user:hunteragent allow search\n",
    ],
)
def test_any_other_work_root_acl_fails_verification(entries: str) -> None:
    assert not provision.work_root_acl_is_exact(WORK_ROOT_LISTING + entries)


def _world_writable_mode_literals(path: Path) -> list[str]:
    """Literal modes handed to chmod/mkdir calls that would grant write to every local user."""

    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in {"chmod", "mkdir", "makedirs"}:
            continue
        for arg in [*node.args, *(keyword.value for keyword in node.keywords)]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and arg.value & 0o002:
                found.append(f"{path.name}:{node.lineno} {oct(arg.value)}")
    return found


@pytest.mark.parametrize(
    "script",
    [
        SCRIPT,
        ROOT / "scripts" / "hunter_codex_sandbox_probe.py",
        ROOT / "scripts/issue_agent/codex_fallback_launcher.py",
    ],
)
def test_no_fallback_host_script_grants_world_write(script: Path) -> None:
    assert _world_writable_mode_literals(script) == []


def test_the_world_write_guard_sees_code_not_prose(tmp_path: Path) -> None:
    trap = tmp_path / "trap.py"
    trap.write_text(
        "import os\n# os.chmod(p, 0o1777)\nNOTE = 'os.chmod(p, 0o777)'\nos.chmod(p, 0o700)\n"
        "os.chmod(p, mode=0o777)\nPath(p).mkdir(0o1777)\n"
    )
    assert _world_writable_mode_literals(trap) == ["trap.py:5 0o777", "trap.py:6 0o1777"]


#: Measured size of the official pinned archive (npm @openai/codex@0.167.0-darwin-x64, integrity CODEX_TARBALL_INTEGRITY).
PINNED_CODEX_TARBALL_BYTES = 158_115_646


def test_the_read_bound_admits_the_pinned_official_archive() -> None:
    # A bound below the pinned archive made --apply refuse the only archive it is pinned to accept.
    assert provision.CODEX_TARBALL_MAX_BYTES >= PINNED_CODEX_TARBALL_BYTES


def test_the_read_bound_is_exact_at_its_edge(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(provision, "CODEX_TARBALL_MAX_BYTES", 64)
    at_bound, over_bound = tmp_path / "at.tgz", tmp_path / "over.tgz"
    at_bound.write_bytes(b"a" * 64)
    over_bound.write_bytes(b"a" * 65)
    assert provision.read_pinned_tarball(at_bound) == b"a" * 64
    with pytest.raises(provision.ProvisionError, match="bounded regular file"):
        provision.read_pinned_tarball(over_bound)
