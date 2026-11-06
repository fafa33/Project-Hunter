"""One-time, idempotent provisioning of the ADR 0041 macOS Codex fallback host.

Modes (stdlib only; runs under the system ``/usr/bin/python3``):

* default    -- dry run: preflight and print the plan. Changes nothing.
* ``--apply``  -- root only (``sudo``). Creates/repairs exactly the ADR 0041 F5 host, then runs every security check,
  including a live sandbox attestation through the installed launcher as ``hunterrunner``.
* ``--verify`` -- read-only, unprivileged. Re-checks the installed host state.

It never reads or copies any user's Codex credentials, never adds a sudoers rule, never weakens the sandbox
profile, and never registers a GitHub runner (that needs a separately verified registration token).
"""

from __future__ import annotations

import argparse
import base64
import grp
import hashlib
import io
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RUNNER_USER, AGENT_USER = "hunterrunner", "hunteragent"
ADMIN_GID, STAFF_GID = 80, 20
FALLBACK_ROOT = Path("/private/var/hunter-fallback")
WORK_ROOT, RUN_DIR, OWNER_INBOX = FALLBACK_ROOT / "work", FALLBACK_ROOT / "run", FALLBACK_ROOT / "owner-inbox"
WORK_ROOT_ACL = f"user:{AGENT_USER} allow search"
SOCKET_PATH = RUN_DIR / "launcher.sock"
CODEX_BINARY = Path("/opt/hunter/codex/bin/codex")
HUNTER_LIBRARY = Path("/Library/Hunter")
PLIST_PATH = Path("/Library/LaunchDaemons/com.hunter.codex-fallback.plist")
INSTALLED_CONFIG_PATH = HUNTER_LIBRARY / "codex-fallback.json"
DAEMON_LABEL = "com.hunter.codex-fallback"
GATEWAY_PORT = 18443
#: Installed path -> repository source (byte-identical; the runner attests these bytes before every use).
SOURCES = {
    HUNTER_LIBRARY / "codex-fallback-launcher.py": "scripts/issue_agent/codex_fallback_launcher.py",
    HUNTER_LIBRARY / "codex-fallback.sb": "scripts/issue_agent/codex-fallback.sb",
    HUNTER_LIBRARY / "model_egress_gateway.py": "scripts/issue_agent/model_egress_gateway.py",
}
PLIST_TEMPLATE = "scripts/issue_agent/com.hunter.codex-fallback.plist.template"
#: The official npm package for this host (registry dist.integrity) and the binary inside it, verified 2026-11-06.
CODEX_VERSION = "0.167.0"
CODEX_TARBALL_INTEGRITY = (
    "sha512-wKoBiI6SBSg8fqiEYEOJs/NM/ZzKaCgy1cseX9LJbuDq3GCCKkeW9BUGe1ZEkyhhZ5Yfj+HzdUfp698abP2tDA=="
)
CODEX_TARBALL_MEMBER = "package/vendor/x86_64-apple-darwin/bin/codex"
CODEX_BINARY_SHA256 = "a3b4df639ed5808100fe9d3dacdb324c9fb50b2804d72d5ab59def67bb2d6ab6"
MIN_ID = 503  # never the first users (501) or the existing hunteragent (502)


class ProvisionError(RuntimeError):
    pass


# --- pure helpers (unit-tested) ------------------------------------------------------------------------------


def tarball_integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def codex_from_tarball(data: bytes) -> bytes:
    """Verify the whole tarball against the pinned registry integrity, then return the pinned binary."""

    if tarball_integrity(data) != CODEX_TARBALL_INTEGRITY:
        raise ProvisionError("the Codex tarball is not the pinned official npm package")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        try:
            member = archive.getmember(CODEX_TARBALL_MEMBER)
        except KeyError:
            raise ProvisionError("the Codex tarball has no pinned binary member") from None
        if not member.isfile():
            raise ProvisionError("the Codex tarball member is not a regular file")
        extracted = archive.extractfile(member)
        binary = extracted.read() if extracted is not None else b""
    if hashlib.sha256(binary).hexdigest() != CODEX_BINARY_SHA256:
        raise ProvisionError("the Codex binary is not the pinned build")
    return binary


def render_plist(template: str, runner_uid: int, runner_gid: int) -> bytes:
    return template.replace("@RUNNER_UID@", str(runner_uid)).replace("@RUNNER_GID@", str(runner_gid)).encode()


def render_installed_config(runner_uid: int) -> bytes:
    return json.dumps({"runner_uid": runner_uid}, sort_keys=True).encode() + b"\n"


def free_id(taken: set[int], minimum: int = MIN_ID) -> int:
    candidate = minimum
    while candidate in taken:
        candidate += 1
    return candidate


def validate_owner(owner: str | None) -> str:
    if (
        not owner
        or owner in ("root", RUNNER_USER, AGENT_USER)
        or re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", owner) is None
    ):
        raise ProvisionError("run through sudo from the owner's own account (SUDO_USER)")
    return owner


def expected_files(checkout: Path, runner_uid: int, runner_gid: int) -> dict[Path, bytes]:
    files = {path: (checkout / source).read_bytes() for path, source in SOURCES.items()}
    files[PLIST_PATH] = render_plist((checkout / PLIST_TEMPLATE).read_text(), runner_uid, runner_gid)
    files[INSTALLED_CONFIG_PATH] = render_installed_config(runner_uid)
    return files


# --- system access -------------------------------------------------------------------------------------------


def run(argv: list[str], *, check: bool = True, user: str | None = None, **kwargs: object) -> str:
    command = (["/usr/bin/sudo", "-n", "-u", user, "--"] if user else []) + argv
    completed = subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)  # type: ignore[call-overload]
    if check and completed.returncode != 0:
        raise ProvisionError(
            f"{' '.join(argv[:3])} failed (exit {completed.returncode}): {completed.stderr.strip()[:200]}"
        )
    return str(completed.stdout)


def directory_ids(kind: str, attribute: str) -> set[int]:
    output = run(["/usr/bin/dscl", ".", "-list", f"/{kind}", attribute])
    return {
        int(fields[-1]) for fields in (line.split() for line in output.splitlines()) if fields and fields[-1].isdigit()
    }


def member_of(user: str, group: str) -> bool:
    output = run(["/usr/sbin/dseditgroup", "-o", "checkmember", "-m", user, group], check=False)
    return output.strip().startswith("yes")


def group_ids(user: str) -> set[int]:
    return {int(value) for value in run(["/usr/bin/id", "-G", user]).split()}


def _archive_staging_roots() -> tuple[str, ...]:
    operator = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
    roots = [tempfile.gettempdir(), pwd.getpwnam(operator).pw_dir]
    return tuple(os.path.realpath(root) for root in roots)


def read_pinned_tarball(path: Path) -> bytes:
    """Read only a regular, non-symlink file through a no-follow descriptor.

    CLI input is untrusted even during privileged provisioning. The descriptor is
    validated before reading, and the complete archive is checked against its
    pinned digest by codex_from_tarball before installation.
    """
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise ProvisionError("Codex archive requires an absolute, traversal-free path")
    # The archive must sit directly in a trusted staging directory; only its base name is used.
    staging = next((root for root in _archive_staging_roots() if root == os.path.realpath(path.parent)), None)
    if staging is None:
        raise ProvisionError("Codex archive must be staged directly in the operator home or a temporary directory")
    name = os.path.basename(path.name)
    try:
        directory = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            # O_NOFOLLOW makes a symlinked archive leaf fail instead of being followed.
            descriptor = os.open(
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory
            )
        finally:
            os.close(directory)
    except OSError as error:
        raise ProvisionError("Codex archive cannot be opened safely") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 150 * 1024 * 1024:
            raise ProvisionError("Codex archive must be a bounded regular file")
        if info.st_mode & 0o022:
            raise ProvisionError("Codex archive cannot be group- or world-writable")
        if info.st_uid not in (0, os.getuid(), int(os.environ.get("SUDO_UID", os.getuid()))):
            raise ProvisionError("Codex archive has an unexpected owner")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read(150 * 1024 * 1024 + 1)
    finally:
        os.close(descriptor)


def checkout_head(checkout: Path) -> str:
    git = ["/usr/bin/git", "-c", f"safe.directory={checkout}", "-C", str(checkout)]
    if run([*git, "status", "--porcelain", "--untracked-files=no"]).strip():
        raise ProvisionError("the checkout has uncommitted changes; provision only from a committed head")
    return run([*git, "rev-parse", "HEAD"]).strip()


def write_root_file(path: Path, data: bytes, mode: int) -> bool:
    """Atomic root:wheel write. Returns whether anything changed (idempotent)."""

    if path.is_symlink():
        raise ProvisionError(f"{path} is a symlink")
    if path.exists() and path.read_bytes() == data:
        os.chown(path, 0, 0)
        os.chmod(path, mode)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".hunter-")
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return True


def root_directory(path: Path, mode: int = 0o755) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProvisionError(f"{path} is a symlink")
    os.chown(path, 0, 0)
    os.chmod(path, mode)


# --- apply ---------------------------------------------------------------------------------------------------


def ensure_group(name: str, gid_hint: set[int]) -> int:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        gid = free_id(gid_hint | directory_ids("Groups", "PrimaryGroupID"))
        run(["/usr/sbin/dseditgroup", "-o", "create", "-i", str(gid), "-r", f"Hunter {name}", name])
        return gid


def ensure_agent(gid: int) -> int:
    try:
        agent = pwd.getpwnam(AGENT_USER)
    except KeyError:
        raise ProvisionError(
            "hunteragent does not exist; it is the account the owner's sandbox probe verified"
        ) from None
    if member_of(AGENT_USER, "admin"):
        raise ProvisionError("hunteragent is an administrator; remove that before provisioning")
    if agent.pw_gid != gid:
        run(["/usr/bin/dscl", ".", "-create", f"/Users/{AGENT_USER}", "PrimaryGroupID", str(gid)])
    return agent.pw_uid


def ensure_runner(gid: int) -> int:
    path = f"/Users/{RUNNER_USER}"
    try:
        uid = pwd.getpwnam(RUNNER_USER).pw_uid
    except KeyError:
        uid = free_id(directory_ids("Users", "UniqueID") | {gid})
        for attribute, value in (
            ("UniqueID", str(uid)),
            ("PrimaryGroupID", str(gid)),
            ("UserShell", "/usr/bin/false"),
            ("RealName", "Hunter fallback runner"),
            ("NFSHomeDirectory", path),
            ("Password", "*"),  # no password: never an interactive login, never sudo
            ("IsHidden", "1"),
        ):
            run(["/usr/bin/dscl", ".", "-create", path, attribute, value])
    if member_of(RUNNER_USER, "admin"):
        raise ProvisionError("hunterrunner is an administrator; refusing")
    run(["/usr/bin/dscl", ".", "-create", path, "PrimaryGroupID", str(gid)])
    home = Path(path)
    home.mkdir(mode=0o700, exist_ok=True)
    os.chown(home, uid, gid)
    os.chmod(home, 0o700)
    return uid


def apply(checkout: Path, tarball: Path, owner: str) -> dict[str, object]:
    if os.geteuid() != 0:
        raise ProvisionError("--apply must run as root through sudo")
    head = checkout_head(checkout)
    binary = codex_from_tarball(read_pinned_tarball(tarball))  # verified in memory before anything is written
    agent_gid = ensure_group(AGENT_USER, set())
    runner_gid = ensure_group(RUNNER_USER, {agent_gid})
    if runner_gid == agent_gid or STAFF_GID in (runner_gid, agent_gid):
        raise ProvisionError("the two principals must have distinct dedicated primary groups")
    ensure_agent(agent_gid)
    runner_uid = ensure_runner(runner_gid)

    for directory in (Path("/opt/hunter"), Path("/opt/hunter/codex"), CODEX_BINARY.parent, HUNTER_LIBRARY):
        root_directory(directory)
    write_root_file(CODEX_BINARY, binary, 0o755)
    for path, data in expected_files(checkout, runner_uid, runner_gid).items():
        write_root_file(path, data, 0o644)
    run(["/usr/bin/plutil", "-lint", str(PLIST_PATH)])

    root_directory(FALLBACK_ROOT)
    root_directory(RUN_DIR)
    WORK_ROOT.mkdir(exist_ok=True)
    os.chown(WORK_ROOT, runner_uid, runner_gid)
    os.chmod(WORK_ROOT, 0o700)
    run(["/bin/chmod", "-N", str(WORK_ROOT)])
    run(["/bin/chmod", "+a", WORK_ROOT_ACL, str(WORK_ROOT)])  # only hunteragent traverses; nobody else, never lists
    OWNER_INBOX.mkdir(exist_ok=True)
    owner_account = pwd.getpwnam(owner)
    os.chown(OWNER_INBOX, owner_account.pw_uid, owner_account.pw_gid)
    os.chmod(OWNER_INBOX, 0o700)
    run(["/bin/chmod", "-N", str(OWNER_INBOX)])
    run(["/bin/chmod", "+a", f"user:{AGENT_USER} allow add_file,search", str(OWNER_INBOX)])

    run(["/bin/launchctl", "bootout", f"system/{DAEMON_LABEL}"], check=False)
    run(["/bin/launchctl", "bootstrap", "system", str(PLIST_PATH)])
    report = verify(checkout, privileged=True)
    report.update(head=head, live_attestation=live_attestation(runner_uid))
    return report


# --- verify --------------------------------------------------------------------------------------------------


def _owned_by_root_and_locked(path: Path) -> bool:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        return False
    return all(
        not stat.S_ISLNK(os.lstat(parent).st_mode)
        and os.lstat(parent).st_uid == 0
        and not os.lstat(parent).st_mode & 0o022
        for parent in path.parents
    )


def verify(checkout: Path, *, privileged: bool) -> dict[str, object]:
    checks: dict[str, bool] = {}
    try:
        agent, runner = pwd.getpwnam(AGENT_USER), pwd.getpwnam(RUNNER_USER)
    except KeyError:
        return {"status": "NOT_PROVISIONED", "checks": {"principals_exist": False}}
    agent_groups, runner_groups = group_ids(AGENT_USER), group_ids(RUNNER_USER)
    checks["agent_not_admin"] = ADMIN_GID not in agent_groups and not member_of(AGENT_USER, "admin")
    checks["runner_not_admin"] = ADMIN_GID not in runner_groups and not member_of(RUNNER_USER, "admin")
    checks["dedicated_primary_groups"] = (
        agent.pw_gid not in (STAFF_GID, runner.pw_gid)
        and agent.pw_gid not in runner_groups
        and runner.pw_gid not in agent_groups
    )
    checks["runner_has_no_login_shell"] = runner.pw_shell == "/usr/bin/false"
    for path, data in expected_files(checkout, runner.pw_uid, runner.pw_gid).items():
        checks[f"installed:{path}"] = path.exists() and _owned_by_root_and_locked(path) and path.read_bytes() == data
    checks["codex_pinned"] = (
        CODEX_BINARY.exists()
        and _owned_by_root_and_locked(CODEX_BINARY)
        and hashlib.sha256(CODEX_BINARY.read_bytes()).hexdigest() == CODEX_BINARY_SHA256
    )
    work = os.lstat(WORK_ROOT) if WORK_ROOT.exists() else None
    checks["work_root"] = bool(work and work.st_uid == runner.pw_uid and stat.S_IMODE(work.st_mode) == 0o700)
    checks["work_root_acl"] = work_root_acl_is_exact(run(["/bin/ls", "-led", str(WORK_ROOT)], check=False))
    inbox = os.lstat(OWNER_INBOX) if OWNER_INBOX.exists() else None
    checks["owner_inbox_private"] = bool(inbox and inbox.st_uid not in (0, runner.pw_uid, agent.pw_uid))
    checks["owner_inbox_mode"] = bool(inbox and stat.S_IMODE(inbox.st_mode) == 0o700)
    acl = run(["/bin/ls", "-led", str(OWNER_INBOX)], check=False)
    checks["owner_inbox_acl"] = f"user:{AGENT_USER} allow add_file,search" in acl and acl.count(" allow ") == 1
    checks["daemon_loaded"] = run(["/bin/launchctl", "print", f"system/{DAEMON_LABEL}"], check=False).strip() != ""
    sock = os.lstat(SOCKET_PATH) if SOCKET_PATH.exists() else None
    checks["socket_runner_only"] = bool(
        sock and stat.S_ISSOCK(sock.st_mode) and sock.st_uid == runner.pw_uid and stat.S_IMODE(sock.st_mode) == 0o600
    )
    if privileged:
        sudoers = [Path("/etc/sudoers"), *sorted(Path("/etc/sudoers.d").glob("*"))]
        checks["no_hunter_sudoers_rule"] = not any(
            "hunter" in path.read_text(errors="replace") for path in sudoers if path.is_file()
        )
        checks["live_sandbox_denials"] = sandbox_denials(agent)
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "status": status,
        "checks": checks,
        "pin": {
            "runner_uid": runner.pw_uid,
            "runner_gid": runner.pw_gid,
            "agent_uid": agent.pw_uid,
            "codex_version": CODEX_VERSION,
            "codex_sha256": CODEX_BINARY_SHA256,
        },
    }


def work_root_acl_is_exact(listing: str) -> bool:
    """`ls -led` output grants exactly one ACL entry: hunteragent may traverse the work root and nothing else."""

    entries = [line.split(":", 1)[1].strip() for line in listing.splitlines()[1:] if re.match(r"^\s*\d+:", line)]
    return entries == [WORK_ROOT_ACL]


def sandbox_denials(agent: pwd.struct_passwd) -> bool:
    """The production profile, applied as hunteragent: allowed writes work, everything else is denied."""

    root = Path(tempfile.mkdtemp(prefix="provision-", dir=WORK_ROOT))
    workspace, home = root / "workspace", root / "home"
    try:
        for directory in (workspace, home):
            directory.mkdir()
            os.chown(directory, agent.pw_uid, agent.pw_gid)
        canary = root / "canary"
        canary.write_text("HUNTER_ATTEST_CANARY\n")
        os.chmod(canary, 0o600)
        # Unix grants hunteragent alone the canary read and an outside write (ACLs, no world bits),
        # so only the sandbox can deny them.
        run(["/bin/chmod", "+a", f"user:{AGENT_USER} allow read", str(canary)])
        run(["/bin/chmod", "+a", f"user:{AGENT_USER} allow search,add_file", str(root)])
        profile = ["/usr/bin/sandbox-exec", "-f", str(HUNTER_LIBRARY / "codex-fallback.sb")]
        params = ["-D", f"WORKSPACE={workspace}", "-D", f"HOME={home}", "-D", f"CODEX_DIR={CODEX_BINARY.parent.parent}"]
        cases = (
            (["/usr/bin/id", "-u"], True),
            (["/usr/bin/touch", str(workspace / "ok")], True),
            (["/bin/cat", str(canary)], False),
            (["/usr/bin/touch", str(root / "outside")], False),
            (["/usr/bin/nc", "-z", "-G", "3", "1.1.1.1", "443"], False),  # NOSONAR: egress-denial probe target
            ([str(CODEX_BINARY), "--version"], True),
        )
        for argv, allowed in cases:
            done = subprocess.run(
                ["/usr/bin/sudo", "-n", "-u", AGENT_USER, "--", *profile, *params, *argv],
                capture_output=True,
                check=False,
                timeout=30,
                cwd=str(workspace),
            )
            if done.returncode in (65, 71, 126, 127) or (done.returncode == 0) != allowed:
                return False
            if argv[0] == "/usr/bin/id" and done.stdout.strip() != str(agent.pw_uid).encode():
                return False
        return True
    finally:
        subprocess.run(["/bin/rm", "-rf", str(root)], check=False)


ATTEST_CLIENT = r"""
import json, os, socket, struct, subprocess, sys, time
work, sock, agent = sys.argv[1], sys.argv[2], sys.argv[3]
root = os.path.join(work, os.urandom(16).hex())
os.mkdir(root, 0o700)
rights = ("list,add_file,search,add_subdirectory,delete_child,read,write,append,readattr,writeattr,readextattr,"
          "writeextattr,readsecurity,delete,file_inherit,directory_inherit")
subprocess.run(["/bin/chmod", "+a", "user:%s allow search" % agent, root], check=True)
for name in ("workspace", "home"):
    path = os.path.join(root, name)
    os.mkdir(path, 0o700)
    for user in (agent, os.environ["USER"]):
        subprocess.run(["/bin/chmod", "+a", "user:%s allow %s" % (user, rights), path], check=True)
with open(os.path.join(root, "attest-canary"), "w") as handle:
    handle.write("HUNTER_ATTEST_CANARY\n")
os.chmod(os.path.join(root, "attest-canary"), 0o644)
gateway = subprocess.Popen(["/usr/bin/python3", "-I", "/Library/Hunter/model_egress_gateway.py"],
                           env={"HUNTER_GATEWAY_PROFILE": "codex", "PATH": "/usr/bin:/bin"},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(1.5)
    body = json.dumps({"kind": "attest", "workroot_id": os.path.basename(root)}).encode()
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(120)
        client.connect(sock)
        client.sendall(struct.pack("!I", len(body)) + body + struct.pack("!I", 0))
        size = struct.unpack("!I", client.recv(4, socket.MSG_WAITALL))[0]
        print(client.recv(size, socket.MSG_WAITALL).decode())
finally:
    gateway.kill()
    subprocess.run(["/bin/rm", "-rf", root], check=False)
"""


def live_attestation(runner_uid: int) -> dict[str, object]:
    """The installed launcher's own attestation, requested exactly as the fallback runner requests it."""

    del runner_uid
    output = run(
        ["/usr/bin/python3", "-I", "-c", ATTEST_CLIENT, str(WORK_ROOT), str(SOCKET_PATH), AGENT_USER],
        user=RUNNER_USER,
        env={"USER": RUNNER_USER, "PATH": "/usr/bin:/bin", "HOME": f"/Users/{RUNNER_USER}"},
        cwd="/",
        timeout=180,
    )
    result = json.loads(output.strip().splitlines()[-1])
    passed = result.get("status") == "ATTESTED" and set((result.get("cases") or {}).values()) == {"PASS"}
    return {"status": "PASS" if passed else "FAIL", "launcher": result}


# --- entry ---------------------------------------------------------------------------------------------------


def plan(checkout: Path, tarball: Path | None) -> dict[str, object]:
    missing = [source for source in [*SOURCES.values(), PLIST_TEMPLATE] if not (checkout / source).is_file()]
    if missing:
        raise ProvisionError(f"repository sources missing: {missing}")
    preflight = {"sources": "present", "codex_tarball": "not given"}
    if tarball is not None:
        codex_from_tarball(read_pinned_tarball(tarball))
        preflight["codex_tarball"] = "verified against the pinned registry integrity and binary hash"
    return {
        "status": "DRY_RUN",
        "preflight": preflight,
        "would": [
            f"create dedicated primary groups {AGENT_USER} and {RUNNER_USER} (gid >= {MIN_ID}) if absent",
            f"move {AGENT_USER} to its dedicated primary group (refuse if it is an administrator)",
            f"create hidden non-admin {RUNNER_USER} (no password, /usr/bin/false shell, 0700 home) if absent",
            f"install Codex {CODEX_VERSION} at {CODEX_BINARY} (root:wheel 0755) from the verified tarball",
            f"install the launcher, profile, gateway and host config in {HUNTER_LIBRARY} (root:wheel 0644)",
            f"install {PLIST_PATH} (root:wheel 0644) and bootstrap it (socket-activated, idle until used)",
            f"create {WORK_ROOT} ({RUNNER_USER} 0700, {AGENT_USER} search only), {RUN_DIR} (root 0755), {OWNER_INBOX} (owner 0700, "
            f"{AGENT_USER} add_file only)",
            "verify everything, run live sandbox denials as hunteragent and a launcher attestation as hunterrunner",
        ],
        "never": [
            "copy any Codex credentials",
            "add a sudoers rule",
            "register a GitHub runner",
            "change the sandbox profile",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--checkout", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--codex-tarball")
    arguments = parser.parse_args(argv)
    checkout = Path(arguments.checkout).resolve()
    tarball = Path(arguments.codex_tarball) if arguments.codex_tarball else None
    try:
        if os.uname().sysname != "Darwin":
            raise ProvisionError("macOS only")
        if arguments.apply:
            if tarball is None:
                raise ProvisionError("--apply needs --codex-tarball")
            result = apply(checkout, tarball, validate_owner(os.environ.get("SUDO_USER")))
            if result.get("live_attestation", {}).get("status") != "PASS":  # type: ignore[union-attr]
                result["status"] = "FAIL"
        elif arguments.verify:
            result = verify(checkout, privileged=os.geteuid() == 0)
        else:
            result = plan(checkout, tarball)
    except (ProvisionError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "REFUSED", "reason": str(error)[:300]}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result["status"] in ("PASS", "DRY_RUN") else 1


if __name__ == "__main__":
    sys.exit(main())
