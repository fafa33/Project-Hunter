"""Runner-side macOS Codex fallback port (ADR 0041 F5/F6). Runs as ``hunterrunner`` in the trusted fallback step.

It never runs Codex itself and holds no sudo grant. It attests the root-owned installation byte-for-byte against
the files pinned at ``control_sha``, asks the socket-activated launcher (as ``hunteragent``) to prove its live
denials, and then submits one fixed-schema request. The launcher builds every Codex argument; nothing the caller's
argv or environment carries reaches it except a validated model name and the sealed prompt bytes.

Until ``config/issue_agent_codex_fallback.json`` is provisioned, attestation refuses and Codex never starts.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_roles import RoleRefused

HOST_CONFIG_PATH: Final = Path("config/issue_agent_codex_fallback.json")
HOST_SCHEMA_VERSION: Final = "hunter-issue-agent-codex-fallback-host-v1"
FALLBACK_ROOT: Final = Path("/private/var/hunter-fallback")
WORK_ROOT: Final = FALLBACK_ROOT / "work"
SOCKET_PATH: Final = FALLBACK_ROOT / "run" / "launcher.sock"
OWNER_INBOX: Final = FALLBACK_ROOT / "owner-inbox"
CODEX_BINARY: Final = Path("/opt/hunter/codex/bin/codex")
GATEWAY_PORT: Final = 18443
#: Installed path → repository source pinned at ``control_sha`` (the plist and host config are rendered).
INSTALLED_SOURCES: Final[Mapping[Path, str]] = {
    Path("/Library/Hunter/codex-fallback-launcher.py"): "scripts/issue_agent/codex_fallback_launcher.py",
    Path("/Library/Hunter/codex-fallback.sb"): "scripts/issue_agent/codex-fallback.sb",
    Path("/Library/Hunter/model_egress_gateway.py"): "scripts/issue_agent/model_egress_gateway.py",
}
PLIST_PATH: Final = Path("/Library/LaunchDaemons/com.hunter.codex-fallback.plist")
PLIST_TEMPLATE: Final = "scripts/issue_agent/com.hunter.codex-fallback.plist.template"
INSTALLED_CONFIG_PATH: Final = Path("/Library/Hunter/codex-fallback.json")
_MODEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA64: Final = re.compile(r"[0-9a-f]{64}")
_ADMIN_GID: Final = 80
LAUNCHER_STATUSES: Final = frozenset(
    {
        "COMPLETED",
        "AUTHENTICATED",
        "DEVICE_CODE_EXPIRED",
        "DEVICE_AUTH_FAILED",
        "SANDBOX_SETUP_FAILED",
        "SESSION_NOT_STARTED",
        "ATTESTED",
        "FAILED",
        "REFUSED",
    }
)


@dataclass(frozen=True, slots=True)
class HostConfig:
    runner_user: str
    agent_user: str
    runner_uid: int
    runner_gid: int
    agent_uid: int
    codex_version: str
    codex_sha256: str


def load_host_config(checkout: Path) -> HostConfig:
    """The fallback host pinned at ``control_sha``. Unprovisioned → ``EXECUTION_NOT_STARTED`` (fallback inert)."""

    try:
        document = json.loads((checkout / HOST_CONFIG_PATH).read_bytes())
    except (OSError, ValueError):
        raise RoleRefused("EXECUTION_NOT_STARTED", "the Codex fallback host configuration is unreadable") from None
    fields = {
        "schema_version",
        "provisioned",
        "runner_user",
        "agent_user",
        "runner_uid",
        "runner_gid",
        "agent_uid",
        "codex_version",
        "codex_sha256",
    }
    if not isinstance(document, dict) or set(document) != fields or document["schema_version"] != HOST_SCHEMA_VERSION:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the Codex fallback host configuration has an unknown schema")
    if document["provisioned"] is not True:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the Codex fallback host is not provisioned (owner action)")
    ids = [document[name] for name in ("runner_uid", "runner_gid", "agent_uid")]
    if not all(type(value) is int and value >= 501 for value in ids) or document["runner_uid"] == document["agent_uid"]:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback principals must be two distinct non-system users")
    if _SHA64.fullmatch(str(document["codex_sha256"])) is None or not document["codex_version"]:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the Codex binary is not pinned")
    return HostConfig(**{name: document[name] for name in fields - {"schema_version", "provisioned"}})


def render_plist(checkout: Path, config: HostConfig) -> bytes:
    template = (checkout / PLIST_TEMPLATE).read_text(encoding="utf-8")
    return (
        template.replace("@RUNNER_UID@", str(config.runner_uid))
        .replace("@RUNNER_GID@", str(config.runner_gid))
        .encode("utf-8")
    )


def render_installed_config(config: HostConfig) -> bytes:
    return json.dumps({"runner_uid": config.runner_uid}, sort_keys=True).encode("utf-8") + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root_owned_file(path: Path) -> bytes:
    """Read an installed file only if it and every parent are root-owned and not writable by anyone else."""

    try:
        info = os.lstat(path)
    except OSError:
        raise RoleRefused("EXECUTION_NOT_STARTED", "a pinned fallback file is not installed") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RoleRefused("EXECUTION_NOT_STARTED", "a pinned fallback file is not a root-owned, unwritable file")
    for parent in path.parents:
        parent_info = os.lstat(parent)
        if stat.S_ISLNK(parent_info.st_mode) or parent_info.st_uid != 0 or parent_info.st_mode & 0o022:
            raise RoleRefused("EXECUTION_NOT_STARTED", "a pinned fallback file has a writable or foreign parent")
    return path.read_bytes()


def installation_digests(
    checkout: Path, config: HostConfig, *, read: Callable[[Path], bytes] = _root_owned_file
) -> dict[str, str]:
    """Compare every installed trusted file to its pinned source; any difference refuses."""

    expected: dict[Path, bytes] = {path: (checkout / source).read_bytes() for path, source in INSTALLED_SOURCES.items()}
    expected[PLIST_PATH] = render_plist(checkout, config)
    expected[INSTALLED_CONFIG_PATH] = render_installed_config(config)
    digests: dict[str, str] = {}
    for path, pinned in sorted(expected.items()):
        if read(path) != pinned:
            raise RoleRefused("EXECUTION_NOT_STARTED", f"installed {path.name} differs from the pinned source")
        digests[str(path)] = _sha256(pinned)
    codex = read(CODEX_BINARY)
    if _sha256(codex) != config.codex_sha256:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the installed Codex binary is not the pinned build")
    digests[str(CODEX_BINARY)] = config.codex_sha256
    return digests


def principal_facts(config: HostConfig) -> dict[str, bool]:
    """Least-privilege facts about the two principals, from the local directory service."""

    try:
        agent, runner = pwd.getpwnam(config.agent_user), pwd.getpwnam(config.runner_user)
        admin_members = set(grp.getgrgid(_ADMIN_GID).gr_mem)
    except KeyError:
        raise RoleRefused("EXECUTION_NOT_STARTED", "a fallback principal does not exist") from None
    runner_groups = set(os.getgrouplist(config.runner_user, runner.pw_gid))
    agent_groups = set(os.getgrouplist(config.agent_user, agent.pw_gid))
    return {
        "agent_uid_pinned": agent.pw_uid == config.agent_uid,
        "runner_uid_pinned": runner.pw_uid == config.runner_uid and os.getuid() == config.runner_uid,
        "agent_not_admin": config.agent_user not in admin_members and _ADMIN_GID not in agent_groups,
        "runner_not_admin": config.runner_user not in admin_members and _ADMIN_GID not in runner_groups,
        "no_shared_primary_group": agent.pw_gid not in runner_groups and runner.pw_gid not in agent_groups,
        "agent_idle": not _agent_processes(config.agent_user),
    }


def _agent_processes(user: str) -> bool:
    result = subprocess.run(["/bin/ps", "-U", user, "-o", "pid="], capture_output=True, check=False)  # noqa: S603
    return bool(result.stdout.strip())


def _send(path: Path, header: Mapping[str, str], prompt: bytes, *, timeout: float) -> dict[str, Any]:
    body = json.dumps(dict(header), sort_keys=True).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(struct.pack("!I", len(body)) + body + struct.pack("!I", len(prompt)) + prompt)
        size = struct.unpack("!I", _exact(connection, 4))[0]
        if size > 1024 * 1024:
            raise RoleRefused("EXECUTION_NOT_COMPLETED", "oversized launcher response")
        response = json.loads(_exact(connection, size))
    if not isinstance(response, dict) or response.get("status") not in LAUNCHER_STATUSES:
        raise RoleRefused("EXECUTION_NOT_COMPLETED", "the launcher answered outside its closed vocabulary")
    return response


def _exact(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise RoleRefused("EXECUTION_NOT_COMPLETED", "the launcher closed the connection early")
        data += chunk
    return data


Launcher = Callable[[Mapping[str, str], bytes, float], dict[str, Any]]


def _socket_launcher(header: Mapping[str, str], prompt: bytes, timeout: float) -> dict[str, Any]:
    return _send(SOCKET_PATH, header, prompt, timeout=timeout)


def model_from_argv(argv: Sequence[str]) -> str:
    """Only the model name crosses to the launcher; it rebuilds every other argument itself."""

    if len(argv) < 4 or Path(argv[0]).name != "codex" or argv[1] != "exec" or argv[2] != "--model":
        raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback port runs only codex exec --model MODEL")
    if _MODEL.fullmatch(argv[3]) is None:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback model name is malformed")
    return argv[3]


class MacCodexIsolation:
    """The only Isolation port ``run_executor`` accepts for Codex, and only after ``attest`` succeeds."""

    def __init__(
        self,
        checkout: Path,
        *,
        launcher: Launcher = _socket_launcher,
        read: Callable[[Path], bytes] = _root_owned_file,
        principals: Callable[[HostConfig], Mapping[str, bool]] = principal_facts,
        gateway: Callable[[Path], subprocess.Popen[bytes] | None] | None = None,
    ) -> None:
        self.checkout = checkout.resolve()
        self.config = load_host_config(self.checkout)
        self._launcher, self._read, self._principals = launcher, read, principals
        self._gateway = gateway if gateway is not None else _start_gateway
        self._gateway_process: subprocess.Popen[bytes] | None = None
        self.workroot_id: str | None = None
        self.attested = False
        self.attestation_sha256: str | None = None
        self.session_secrets: tuple[str, ...] = ()
        self.last_stderr_category: str | None = None

    def attest(self, workroot: Path) -> str:
        """Pinned bytes, least-privilege principals and live denials, before any key or session is used."""

        workroot_id = workroot_identity(workroot)
        digests = installation_digests(self.checkout, self.config, read=self._read)
        facts = dict(self._principals(self.config))
        if not facts or not all(facts.values()):
            raise RoleRefused("EXECUTION_NOT_STARTED", "a fallback principal is not least-privileged or not idle")
        # The canary is Unix-readable by hunteragent (search ACE + 0644), so only the sandbox can deny reading it.
        _chmod_acl(f"user:{self.config.agent_user} allow search", workroot)
        canary = workroot / "attest-canary"
        canary.write_bytes(b"HUNTER_ATTEST_CANARY\n")
        canary.chmod(0o644)
        self._gateway_process = self._gateway(self.checkout)
        live = self._launcher({"kind": "attest", "workroot_id": workroot_id}, b"", 120.0)
        canary.unlink(missing_ok=True)
        cases = live.get("cases")
        if live.get("status") != "ATTESTED" or not isinstance(cases, dict) or set(cases.values()) != {"PASS"}:
            self.stop()
            raise RoleRefused("EXECUTION_NOT_STARTED", "the live sandbox attestation did not pass")
        result = {"installation": digests, "principals": facts, "live": dict(sorted(cases.items()))}
        self.workroot_id = workroot_id
        self.attestation_sha256 = state.sha256_hex(state.canonical_json(result))
        self.attested = True
        print(f"codex fallback attestation: PASS attestation_sha256={self.attestation_sha256}", flush=True)
        return self.attestation_sha256

    def prepare(self, *paths: Path) -> None:
        if not self.attested or self.workroot_id is None:
            raise RoleRefused("EXECUTION_NOT_STARTED", "Codex fallback isolation has not been attested")
        expected = {WORK_ROOT / self.workroot_id / "workspace", WORK_ROOT / self.workroot_id / "home"}
        if {path.resolve() for path in paths} != expected:
            raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback prepares only its own attested workroot")
        for path in sorted(expected):
            grant_agent_access(path, self.config)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        public_env: Mapping[str, str],
        secret_env: Mapping[str, str],
        stdin: bytes,
        timeout: float,
    ) -> int:
        del public_env  # the launcher builds the model environment itself
        if not self.attested or self.workroot_id is None:
            raise RoleRefused("EXECUTION_NOT_STARTED", "Codex fallback isolation has not been attested")
        if secret_env:
            raise RoleRefused("EXECUTION_NOT_STARTED", "the plan-session fallback carries no provider secret")
        if cwd.resolve() != WORK_ROOT / self.workroot_id / "workspace":
            raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback runs only in its attested workspace")
        model = model_from_argv(argv)
        # The device code goes only to the owner-only inbox on this Mac, never to this (public) log.
        print("codex fallback: awaiting owner device approval in the local owner inbox", flush=True)
        response = self._launcher(
            {"kind": "run", "workroot_id": self.workroot_id, "model": model}, stdin, timeout + 15 * 60.0 + 120.0
        )
        secrets = response.get("session_secrets", [])
        self.session_secrets = tuple(s for s in secrets if isinstance(s, str)) if isinstance(secrets, list) else ()
        status = response.get("status")
        print(f"codex fallback launcher: status={status}", flush=True)
        if status != "COMPLETED":
            # Every non-COMPLETED status is decided before Codex exec is launched: no model ran (ADR 0041 F1).
            raise RoleRefused("EXECUTION_NOT_STARTED", f"the Codex session did not start ({status})")
        category = response.get("stderr_category")
        self.last_stderr_category = category if isinstance(category, str) else "UNCLASSIFIED"
        if response.get("exit") == "TIMEOUT":
            raise subprocess.TimeoutExpired(["codex", "exec"], timeout)
        return 0 if response.get("exit") == "ZERO" else 1

    def stop(self) -> None:
        if self._gateway_process is not None:
            self._gateway_process.kill()
            self._gateway_process.wait()
            self._gateway_process = None
        if self.workroot_id is not None:
            shutil.rmtree(WORK_ROOT / self.workroot_id / "home", ignore_errors=True)
        if _agent_processes(self.config.agent_user):
            raise RoleRefused("EXECUTION_NOT_COMPLETED", "a hunteragent process survived the fallback run")


def workroot_identity(workroot: Path) -> str:
    resolved = workroot.resolve()
    if resolved.parent != WORK_ROOT or re.fullmatch(r"[0-9a-f]{32}", resolved.name) is None:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the fallback workroot is not a pinned per-run directory")
    return resolved.name


def grant_agent_access(path: Path, config: HostConfig) -> None:
    """Owner-set inherited ACLs: hunteragent may work in the tree and hunterrunner can still collect it (no root)."""

    rights = "list,add_file,search,add_subdirectory,delete_child,read,write,append,readattr,writeattr,readextattr,"
    rights += "writeextattr,readsecurity,delete,file_inherit,directory_inherit"
    for user in (config.agent_user, config.runner_user):
        _chmod_acl(f"user:{user} allow {rights}", path)


def _chmod_acl(entry: str, path: Path) -> None:
    """Add one ACL entry as the path's owner (``chmod +a`` needs no privilege on one's own files)."""

    subprocess.run(["/bin/chmod", "+a", entry, str(path)], check=True)  # noqa: S603


def _start_gateway(checkout: Path) -> subprocess.Popen[bytes]:
    del checkout  # the installed, attested gateway runs, never a checkout copy
    return subprocess.Popen(  # noqa: S603 - attested root-owned script
        [sys.executable, "-I", "/Library/Hunter/model_egress_gateway.py"],
        env={"HUNTER_GATEWAY_PROFILE": "codex", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
