"""Socket-activated Codex fallback launcher (ADR 0041 F5/F6). Installed root-owned; launchd runs it as ``hunteragent``.

Trusted, stdlib-only and pinned by hash: the runner attests these exact bytes before every use. It holds no
GitHub, ledger or provider secret. It accepts exactly one fixed-schema request per activation, only from the
``hunterrunner`` uid, builds every Codex argument itself, and runs Codex only under the outer Seatbelt profile.

Never forwarded to the caller: the device-code text (it goes only to the owner-only local inbox), model output,
or stderr text. The caller receives closed categories and, for the result secret scan only, the per-run session
token values, after the session has been logged out and its ``CODEX_HOME`` deleted.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

FALLBACK_ROOT = Path("/private/var/hunter-fallback")
WORK_ROOT = FALLBACK_ROOT / "work"
OWNER_INBOX = FALLBACK_ROOT / "owner-inbox"
CODEX_BINARY = Path("/opt/hunter/codex/bin/codex")
PROFILE_PATH = Path("/Library/Hunter/codex-fallback.sb")
CONFIG_PATH = Path("/Library/Hunter/codex-fallback.json")
GATEWAY_PORT = 18443
SOCKET_NAME = "Listener"
MAX_HEADER = 4096
MAX_PROMPT = 256 * 1024
# Not an approval deadline (ADR 0041 F1): the owner has already approved this run. This only bounds one device code's
# sign-in so a run never holds the host indefinitely. Expiry launches no model; the request returns to pending and
# the owner's next approval issues a fresh code.
DEVICE_CODE_SESSION_CAP_SECONDS = 15 * 60
MODEL_TIMEOUT_SECONDS = 12 * 60
WORKROOT_ID = re.compile(r"[0-9a-f]{32}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SETUP_EXITS = frozenset({65, 71, 126, 127})
# The closed request schema: no argv, environment, path or configuration ever comes from the caller.
REQUEST_FIELDS = {"attest": {"kind", "workroot_id"}, "run": {"kind", "workroot_id", "model"}}
STDERR_CATEGORIES = (
    (b"rate limit", "RATE_LIMIT"),
    (b"too many requests", "RATE_LIMIT"),
    (b"usage limit", "QUOTA_EXCEEDED"),
    (b"quota", "QUOTA_EXCEEDED"),
    (b"unauthorized", "AUTHENTICATION"),
    (b"not logged in", "AUTHENTICATION"),
    (b"connection refused", "NETWORK"),
    (b"timed out", "NETWORK"),
)


#: ADR 0041 F5 (owner decision, Issue #390 comment 6706666538): the served model must be proven, never assumed.
MAX_EVENT_STREAM = 8 * 1024 * 1024
MAX_CATALOG = 4 * 1024 * 1024
_REROUTE_MARKERS = (b"reroute", b"routed to")
_UNKNOWN_MODEL_MARKER = b"model metadata for"


class Refused(Exception):
    pass


def catalog_offers(catalog: bytes, model: str) -> bool:
    """The signed-in session's own model catalog (``codex debug models``) lists exactly this slug, once.

    Read before the prompt exists, so no model output can influence it. Anything unparsable is "not offered".
    """

    if len(catalog) > MAX_CATALOG:
        return False
    try:
        document = json.loads(catalog)
    except ValueError:
        return False
    models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(models, list):
        return False
    return sum(isinstance(entry, dict) and entry.get("slug") == model for entry in models) == 1


def model_identity(events: bytes, model: str) -> str:
    """Verdict on the model that actually served the run, from the pinned CLI's own ``--json`` event stream.

    The stream is the Codex process's stdout pipe: every line is a CLI-built JSON event, and model-generated text
    (agent messages, command output) only ever appears JSON-escaped inside an item. That text is never consulted, so
    it can neither prove nor forge an identity. Only the event structure and the CLI's own warning/error items are.

    ``VERIFIED`` needs a complete, well-formed turn with no reroute and no unknown-model warning. A reroute is
    ``REROUTED``; an unknown model, a malformed or oversized stream or a mismatching model field is ``AMBIGUOUS``; a
    turn that never completed is ``UNVERIFIED``. Only ``VERIFIED`` lets a result leave the Mac.
    """

    if len(events) > MAX_EVENT_STREAM:
        return "AMBIGUOUS"
    started = completed = failed = 0
    ambiguous = False
    for line in events.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return "AMBIGUOUS"
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return "AMBIGUOUS"
        kind = event["type"]
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_kind = item.get("type") if isinstance(item.get("type"), str) else ""
        if "reroute" in kind.lower() or "reroute" in item_kind.lower():
            return "REROUTED"
        for scope in (event, item):
            served = scope.get("model")
            if served is not None and served != model:
                return "REROUTED" if isinstance(served, str) else "AMBIGUOUS"
        if kind == "error" or item_kind == "error":
            message = event.get("message") if kind == "error" else item.get("message")
            text = message.lower().encode() if isinstance(message, str) else b""
            if any(marker in text for marker in _REROUTE_MARKERS):
                return "REROUTED"
            if _UNKNOWN_MODEL_MARKER in text:
                ambiguous = True
        started += kind == "thread.started"
        completed += kind == "turn.completed"
        failed += kind == "turn.failed"
    if ambiguous:
        return "AMBIGUOUS"
    if started != 1 or completed < 1 or failed:
        return "UNVERIFIED"
    return "VERIFIED"


def validate_request(header: object, prompt: bytes) -> dict[str, str]:
    if not isinstance(header, dict) or header.get("kind") not in REQUEST_FIELDS:
        raise Refused("UNKNOWN_REQUEST")
    if set(header) != REQUEST_FIELDS[header["kind"]] or not all(isinstance(v, str) for v in header.values()):
        raise Refused("MALFORMED_REQUEST")
    if WORKROOT_ID.fullmatch(header["workroot_id"]) is None:
        raise Refused("MALFORMED_REQUEST")
    if header["kind"] == "run":
        if MODEL.fullmatch(header["model"]) is None or not 0 < len(prompt) <= MAX_PROMPT:
            raise Refused("MALFORMED_REQUEST")
    elif prompt:
        raise Refused("MALFORMED_REQUEST")
    return dict(header)


def workroot_paths(workroot_id: str) -> tuple[Path, Path, Path]:
    root = WORK_ROOT / workroot_id
    workspace, home = root / "workspace", root / "home"
    for path in (root, workspace, home):
        if path.is_symlink() or not path.is_dir() or path.resolve() != path:
            raise Refused("WORKROOT_INVALID")
    return root, workspace, home


def sandbox_argv(workspace: Path, home: Path, command: list[str]) -> list[str]:
    return [
        "/usr/bin/sandbox-exec",
        "-f",
        str(PROFILE_PATH),
        "-D",
        f"WORKSPACE={workspace}",
        "-D",
        f"HOME={home}",
        "-D",
        f"CODEX_DIR={CODEX_BINARY.parent.parent}",
        *command,
    ]


def model_environment(home: Path) -> dict[str, str]:
    proxy = f"http://127.0.0.1:{GATEWAY_PORT}"
    return {
        "HOME": str(home),
        "PWD": str(home.parent / "workspace"),
        "CODEX_HOME": str(home / ".codex"),
        "TMPDIR": str(home / "tmp"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "ALL_PROXY": proxy,
        "NO_PROXY": "",
    }


def codex_common() -> list[str]:
    # Plan session only, kept in the per-run CODEX_HOME file store (never a keychain, never the owner's ~/.codex).
    return ["-c", 'cli_auth_credentials_store="file"', "-c", 'forced_login_method="chatgpt"']


def codex_exec_argv(model: str, workspace: Path) -> list[str]:
    # The outer profile is the boundary: Codex's own Seatbelt cannot nest under it (ADR 0041 P0), so the inner
    # sandbox is disabled explicitly instead of failing open in an unknown way.
    return [
        str(CODEX_BINARY),
        "exec",
        *codex_common(),
        "--model",
        model,
        "--sandbox",
        "danger-full-access",
        "--cd",
        str(workspace),
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-c",
        'model_reasoning_effort="low"',
        "-",
    ]


def classify(stderr: bytes) -> str:
    lowered = stderr.lower()
    return next((code for marker, code in STDERR_CATEGORIES if marker in lowered), "UNCLASSIFIED")


def _run(argv: list[str], env: dict[str, str], *, stdin: bytes = b"", timeout: float) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(  # noqa: S603 - fixed binaries, launcher-built argv
        argv,
        cwd=env["PWD"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(stdin, timeout=timeout)
        return process.returncode, out, err
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return -1, b"", b""
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # nothing the model started survives its run
        except ProcessLookupError:
            pass


def _inbox_write(workroot_id: str, text: bytes) -> None:
    # Owner-only channel (ADR 0041 F6): the directory is owner-owned 0700 with an add_file ACE for hunteragent.
    printable = bytes(ch for ch in text[:4096] if ch in (9, 10) or 32 <= ch < 127)
    target = OWNER_INBOX / f"{workroot_id}.device-code"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(printable)


def _session_secrets(codex_home: Path) -> list[str]:
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, str) and len(value) >= 16:
            found.append(value)

    try:
        walk(json.loads((codex_home / "auth.json").read_bytes()))
    except (OSError, ValueError):
        pass
    return found


def device_session(workroot_id: str, workspace: Path, home: Path) -> str:
    env = model_environment(home)
    (home / ".codex").mkdir(mode=0o700, exist_ok=True)
    (home / "tmp").mkdir(mode=0o700, exist_ok=True)
    argv = sandbox_argv(workspace, home, [str(CODEX_BINARY), "login", *codex_common(), "--device-auth"])
    process = subprocess.Popen(  # noqa: S603
        argv,
        cwd=workspace,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    captured, deadline = b"", time.monotonic() + DEVICE_CODE_SESSION_CAP_SECONDS
    os.set_blocking(process.stdout.fileno(), False)
    url_seen_at: float | None = None
    written = False
    while process.poll() is None and time.monotonic() < deadline:
        try:
            captured = (captured + (process.stdout.read(4096) or b""))[:8192]
        except BlockingIOError:
            pass
        if url_seen_at is None and b"https://" in captured:
            url_seen_at = time.monotonic()
        # One inbox write, a moment after the URL, so the one-time code printed with it is included.
        if not written and url_seen_at is not None and time.monotonic() - url_seen_at >= 2.0:
            _inbox_write(workroot_id, captured)
            written = True
        time.sleep(0.5)
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return "DEVICE_CODE_EXPIRED"
    if process.returncode in SETUP_EXITS:
        return "SANDBOX_SETUP_FAILED"
    if process.returncode != 0 or not written:
        return "DEVICE_AUTH_FAILED"
    return "AUTHENTICATED"


def end_session(workspace: Path, home: Path) -> list[str]:
    codex_home = home / ".codex"
    secrets = _session_secrets(codex_home)
    _run(
        sandbox_argv(workspace, home, [str(CODEX_BINARY), "logout", *codex_common()]),
        model_environment(home),
        timeout=60,
    )
    shutil.rmtree(codex_home, ignore_errors=True)
    return secrets


def run_codex(request: dict[str, str], prompt: bytes) -> dict[str, object]:
    _, workspace, home = workroot_paths(request["workroot_id"])
    response: dict[str, object] = {"status": "SESSION_NOT_STARTED"}
    try:
        status = device_session(request["workroot_id"], workspace, home)
        response = {"status": status}
        if status == "AUTHENTICATED":
            response = _signed_in_run(request, prompt, workspace, home)
    finally:
        # Every path logs the session out and deletes it, including a timed-out or failed sign-in.
        secrets = end_session(workspace, home)
    if secrets:
        response["session_secrets"] = secrets
    return response


def _signed_in_run(request: dict[str, str], prompt: bytes, workspace: Path, home: Path) -> dict[str, object]:
    # Before any prompt: the signed-in session must offer exactly the pinned model, or no model runs.
    catalog_code, catalog, _ = _run(
        sandbox_argv(workspace, home, [str(CODEX_BINARY), "debug", "models", *codex_common()]),
        model_environment(home),
        timeout=120,
    )
    if catalog_code in SETUP_EXITS:
        return {"status": "SANDBOX_SETUP_FAILED"}
    if catalog_code != 0 or not catalog_offers(catalog, request["model"]):
        return {"status": "MODEL_UNAVAILABLE"}
    code, out, err = _run(
        sandbox_argv(workspace, home, codex_exec_argv(request["model"], workspace)),
        model_environment(home),
        stdin=prompt,
        timeout=MODEL_TIMEOUT_SECONDS,
    )
    if code in SETUP_EXITS:
        return {"status": "SANDBOX_SETUP_FAILED"}
    return {
        "status": "COMPLETED",
        "exit": "TIMEOUT" if code == -1 else ("ZERO" if code == 0 else "NONZERO"),
        "stderr_category": classify(err[:65536]),
        "model_identity": model_identity(out, request["model"]),
    }


def attest(request: dict[str, str]) -> dict[str, object]:
    """Live denial cases under the production profile. A setup error is a failure, never a proven denial."""

    root, workspace, home = workroot_paths(request["workroot_id"])
    canary = root / "attest-canary"  # Unix-readable by hunteragent (ACL), so only the sandbox can deny it
    probe = f"http://127.0.0.1:{GATEWAY_PORT}"
    cases = (
        ("identity", ["/usr/bin/id", "-u"], True),
        ("workspace-write", ["/usr/bin/touch", str(workspace / ".attest")], True),
        ("outside-read", ["/bin/cat", str(canary)], False),
        ("outside-write", ["/usr/bin/touch", str(root / "attest-outside")], False),
        ("owner-inbox-read", ["/bin/ls", str(OWNER_INBOX)], False),
        (
            "direct-egress",
            ["/usr/bin/nc", "-z", "-G", "3", "1.1.1.1", "443"],
            False,
        ),  # NOSONAR: egress-denial probe target
        ("gateway-reachable", ["/usr/bin/nc", "-z", "-G", "3", "127.0.0.1", str(GATEWAY_PORT)], True),
        (
            "gateway-denies-unlisted",
            ["/usr/bin/curl", "-sS", "-o", "/dev/null", "--max-time", "8", "--proxy", probe, "https://example.com/"],
            False,
        ),
    )
    results: dict[str, str] = {}
    for name, command, should_succeed in cases:
        code, out, _err = _run(sandbox_argv(workspace, home, command), model_environment(home), timeout=20)
        passed = code not in SETUP_EXITS and code != -1 and (code == 0) == should_succeed
        if name == "identity":
            passed = passed and out.strip() == str(os.getuid()).encode()
        if name == "outside-read":
            passed = passed and b"HUNTER_ATTEST_CANARY" not in out
        results[name] = "PASS" if passed else "FAIL"
    (workspace / ".attest").unlink(missing_ok=True)
    return {"status": "ATTESTED" if all(v == "PASS" for v in results.values()) else "FAILED", "cases": results}


def _peer_uid(connection: socket.socket) -> int:
    raw = connection.getsockopt(0, 0x001, 76)  # SOL_LOCAL, LOCAL_PEERCRED -> struct xucred
    _version, uid = struct.unpack("=II", raw[:8])
    return int(uid)


def _receive(connection: socket.socket, limit: int) -> bytes:
    size = struct.unpack("!I", _exact(connection, 4))[0]
    if size > limit:
        raise Refused("OVERSIZED")
    return _exact(connection, size)


def _exact(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise Refused("TRUNCATED")
        data += chunk
    return data


def _activated_socket() -> socket.socket:
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t()
    if (
        libc.launch_activate_socket(SOCKET_NAME.encode(), ctypes.byref(fds), ctypes.byref(count)) != 0
        or count.value != 1
    ):
        raise SystemExit("launcher must be socket-activated by launchd")
    return socket.socket(fileno=fds[0])


def handle(connection: socket.socket, runner_uid: int) -> dict[str, object]:
    if _peer_uid(connection) != runner_uid:
        raise Refused("PEER_NOT_RUNNER")
    header = json.loads(_receive(connection, MAX_HEADER))
    prompt = _receive(connection, MAX_PROMPT)
    request = validate_request(header, prompt)
    if request["kind"] == "attest":
        return attest(request)
    return run_codex(request, prompt)


def main() -> int:
    if os.geteuid() == 0:
        raise SystemExit("launcher must not run as root")
    config = json.loads(CONFIG_PATH.read_bytes())
    listener = _activated_socket()
    connection, _ = listener.accept()  # exactly one request per activation
    with connection:
        try:
            response = handle(connection, int(config["runner_uid"]))
        except (Refused, ValueError, OSError) as refusal:
            response = {"status": "REFUSED", "reason": str(refusal)[:64]}
        body = json.dumps(response, sort_keys=True).encode()
        connection.sendall(struct.pack("!I", len(body)) + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
