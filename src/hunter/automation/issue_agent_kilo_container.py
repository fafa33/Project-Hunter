"""Issue #682: governed Docker Isolation port for the existing Issue Agent Runner.

Only Kilo uses this port. The agent is on an internal-only Docker network;
the dual-homed allowlisted CONNECT gateway is its sole external route.
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Final
from urllib.parse import urlunsplit

from hunter.automation.issue_agent_roles import RoleRefused, stderr_category, trusted_git_command

KILO_IMAGE = "hunter-682-kilo-canary:local"
BROKER_IMAGE = "hunter-682-broker-canary:local"
# Explicit repair-loop budgets enforced by the broker (fail closed on exhaustion).
BROKER_MAX_COMMANDS = 32
# The broker must outlive the progress-aware model supervisor (900s). Previously
# 660s silently disabled the supervisor while Kilo was still using the broker.
# A 60s margin covers broker startup and shutdown; the hosted 1200s job remains the backstop.
BROKER_DEADLINE_SECONDS = 960
PYTHON_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"

# Issue #705: hosted run 42739368326 was killed by the flat MODEL_TIMEOUT_SECONDS wall clock while Kilo was still
# actively editing. ``KiloContainerIsolation.run`` instead polls host-trusted evidence (never the agent's own
# stdout/stderr, which grows just as readily on an idle heartbeat) and only extends the deadline on real progress.
# A run that stops producing trusted evidence for this long is STALLED and is killed early, well inside the
# ``execute`` job's 20-minute budget (``.github/workflows/hunter-issue-agent-trigger.yml``). PRH-132 already
# recorded a turn going 235 s without delivering any content while its single provider tunnel stayed open and
# never errored, so this stays comfortably above that observed figure, not just above a guess at turn latency.
KILO_IDLE_STALL_SECONDS: Final = 360.0
# An absolute backstop for a run that keeps producing trusted evidence forever: FAILED, not STALLED, because it
# exceeds a genuine resource/runtime limit rather than going quiet. Kept well under the job's 1200s budget so the
# trusted collection and diagnostics that follow still have time to run.
KILO_HARD_CEILING_SECONDS: Final = 900.0
KILO_POLL_INTERVAL_SECONDS: Final = 10.0
assert KILO_HARD_CEILING_SECONDS < BROKER_DEADLINE_SECONDS < 1200


#: ADR 0041 F8: the gateway's content-free refusal line (``model_egress_gateway.REFUSED_HOST_MARKER``).
_REFUSED = re.compile(r"^GATEWAY_REFUSED_HOST ([a-z0-9.-]{1,253})$")
_DENIED = "GATEWAY_DENIED_CONNECT"


_ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
#: HTTP statuses a provider failure may carry; any other number on an error line is never reported.
_REPORTED_STATUSES = frozenset({400, 401, 402, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504, 529})
_STATUS = re.compile(rb"(?<![0-9])([45][0-9]{2})(?![0-9])")
_STACK_FRAME = re.compile(rb"^\s+at \S")


def stderr_structure(sample: bytes) -> dict[str, str]:
    """Issue #577: a content-free outline of the Kilo CLI's stderr, so a non-zero exit is attributable.

    Only counts of the CLI's own line shapes are reported (tool calls ``→``/``←``, shell ``$``, ``DENIED``, Node
    stack frames, ``Error`` lines) plus one allowlisted HTTP status found on an ``Error`` line. No text, path,
    command, message or other number leaves this function. It is diagnostic only: it never feeds a ledger decision.
    """

    counts = {"tool_calls": 0, "shell": 0, "denied": 0, "error_lines": 0, "stack_frames": 0}
    status = "none"
    for raw in _ANSI.sub(b"", sample[:65536]).splitlines():
        if _STACK_FRAME.match(raw):
            counts["stack_frames"] += 1
            continue
        line = raw.strip()
        if line.startswith(("\u2192 ".encode(), "\u2190 ".encode())):
            counts["tool_calls"] += 1
        elif line.startswith(b"$ "):
            counts["shell"] += 1
        elif line == b"DENIED":
            counts["denied"] += 1
        elif line.startswith(b"Error"):
            counts["error_lines"] += 1
            if status == "none":
                found = next((int(m) for m in _STATUS.findall(line) if int(m) in _REPORTED_STATUSES), None)
                status = str(found) if found is not None else "none"
    return {**{name: str(value) for name, value in counts.items()}, "http_status": status}


#: Issue #577: Kilo's own session store under the isolated home (``XDG_DATA_HOME=/hunter-home/.local/share``).
KILO_SESSION_STORE: Final = (".local", "share", "kilo", "kilo.db")
_SESSION_STORE_LIMIT = 64 * 1024 * 1024
#: The CLI's closed session-error names (OpenCode ``MessageV2``); any other recorded name is reported as ``OTHER``.
_SESSION_ERRORS: Final = frozenset(
    {
        "APIError",
        "ProviderAuthError",
        "UnknownError",
        "MessageOutputLengthError",
        "MessageAbortedError",
        "ContextOverflowError",
        "StructuredOutputError",
    }
)
_FINISH_REASONS: Final = frozenset({"stop", "length", "tool-calls", "content-filter", "error", "other", "unknown"})
#: Issue #718: the per-turn timing outline, when the store has no assistant turn or no readable timing.
_NO_TIMING: Final = {
    "turns": "0",
    "turns_without_content": "0",
    "first_content_max_s": "none",
    "last_progress_s": "none",
    "tokens_out": "none",
}
_NO_SESSION: Final = {
    "error": "none",
    "status": "none",
    "retryable": "none",
    "finish": "none",
    "tool_errors": "0",
    **_NO_TIMING,
}
#: Kilo's assistant turns and their parts, timed by the store's own ``time_created`` milliseconds. Integers only.
_TIMING_QUERY: Final = (
    "SELECT count(*), sum(first IS NULL), max(first - created), max(last) - (SELECT min(time_created) FROM message),"
    " sum(iif(json_type(data, '$.tokens.output') = 'integer', json_extract(data, '$.tokens.output'), 0)"
    " + iif(json_type(data, '$.tokens.reasoning') = 'integer', json_extract(data, '$.tokens.reasoning'), 0))"
    " FROM (SELECT m.time_created AS created, m.data AS data, min(p.time_created) AS first,"
    " max(p.time_created) AS last FROM message m LEFT JOIN part p ON p.message_id = m.id"
    " WHERE json_extract(m.data, '$.role') = 'assistant' GROUP BY m.id)"
)


def _session_timing(connection: sqlite3.Connection) -> dict[str, str]:
    """Issue #718: whether the model's turns ever produced content, and when it last made progress.

    A turn with no part is a request that never delivered anything Kilo recorded (the hosted #705 timeouts and a
    local rehearsal of the same free route both left the budget unexplained). Only integer seconds and counts leave
    this function, never a part or message; a store without the timing columns reads as ``none``.
    """

    def seconds(value: object) -> str:
        return str(max(0, min(int(value) // 1000, 99999))) if type(value) is int else "none"

    try:
        row = connection.execute(_TIMING_QUERY).fetchone()
    except sqlite3.Error:
        return dict(_NO_TIMING)
    if row is None or type(row[0]) is not int or row[0] == 0:
        return dict(_NO_TIMING)
    turns, empty, first_max, last, tokens = row
    return {
        "turns": str(min(turns, 999)),
        "turns_without_content": str(min(empty, 999)) if type(empty) is int else "none",
        "first_content_max_s": seconds(first_max),
        "last_progress_s": seconds(last),
        "tokens_out": str(max(0, min(tokens, 10**9))) if type(tokens) is int else "none",
    }


def _private_copy(source: Path, target: Path) -> bool:
    """Copy one regular file the isolated uid wrote, never following a link and never beyond the size bound."""

    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _SESSION_STORE_LIMIT:
            raise OSError("not a bounded regular file")
        target.write_bytes(handle.read(_SESSION_STORE_LIMIT + 1)[:_SESSION_STORE_LIMIT])
    return True


def kilo_session_outcome(home: Path) -> dict[str, str]:
    """Issue #577: the CLI's own record of why its last turn ended, in a closed vocabulary only.

    Kilo stores each assistant turn with a structured ``error`` (``name``, ``data.statusCode``,
    ``data.isRetryable``) and a ``finish`` reason. The trusted step reads a private copy of that store after the
    container has exited: no link is followed, no directory is trusted, the untrusted file is never opened in place,
    and SQLite runs with ``trusted_schema=OFF`` and ``query_only``. Only allowlisted names, an allowlisted status,
    a boolean and a count leave this function, never a message. The model's uid wrote the store, so the outline is
    diagnostic only: it never feeds a ledger decision or fallback admission.
    """

    try:
        current = home
        for name in KILO_SESSION_STORE[:-1]:
            current = current / name
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                return {**_NO_SESSION, "error": "UNREADABLE"}
        store = current / KILO_SESSION_STORE[-1]
        with tempfile.TemporaryDirectory(prefix="hunter-kilo-session-") as private:
            copy = Path(private) / "kilo.db"
            if not _private_copy(store, copy):
                return {**_NO_SESSION, "error": "ABSENT"}
            _private_copy(store.with_name(store.name + "-wal"), Path(private) / "kilo.db-wal")
            connection = sqlite3.connect(copy)
            try:
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("PRAGMA query_only=ON")
                # Untrusted SQLite views may contain non-terminating recursive queries.
                # Abort after a bounded number of SQLite VM instructions.
                remaining = 2000

                def _query_budget() -> int:
                    nonlocal remaining
                    remaining -= 1
                    return 1 if remaining <= 0 else 0

                connection.set_progress_handler(_query_budget, 1000)
                row = connection.execute(
                    "SELECT json_extract(data, '$.error.name'), json_extract(data, '$.error.data.statusCode'),"
                    " json_extract(data, '$.error.data.isRetryable'), json_extract(data, '$.finish')"
                    " FROM message WHERE json_extract(data, '$.role') = 'assistant'"
                    " ORDER BY time_created DESC, id DESC LIMIT 1"
                ).fetchone()
                (tool_errors,) = connection.execute(
                    "SELECT count(*) FROM part WHERE json_extract(data, '$.type') = 'tool'"
                    " AND json_extract(data, '$.state.status') = 'error'"
                ).fetchone()
                timing = _session_timing(connection)
            finally:
                connection.close()
    except FileNotFoundError:
        return {**_NO_SESSION, "error": "ABSENT"}
    except (OSError, sqlite3.Error, ValueError):
        return {**_NO_SESSION, "error": "UNREADABLE"}
    if row is None:
        return {**_NO_SESSION, "tool_errors": str(min(int(tool_errors), 999)), **timing}
    name, status, retryable, finish = row
    return {
        "error": "none" if name is None else name if name in _SESSION_ERRORS else "OTHER",
        "status": str(status) if type(status) is int and status in _REPORTED_STATUSES else "none",
        "retryable": {1: "true", 0: "false"}.get(retryable, "none") if type(retryable) is int else "none",
        "finish": "none" if finish is None else finish if finish in _FINISH_REASONS else "OTHER",
        "tool_errors": str(min(int(tool_errors), 999)) if type(tool_errors) is int else "none",
        **timing,
    }


#: Issue #577: the home is created ``0700`` by the runner and chowned to the model uid before the run, so on a Linux
#: runner the trusted step cannot even search it (hosted run 42465838010 reported ``error=UNREADABLE`` for a store
#: that existed). This fixed script runs in a one-shot container as root holding only ``CAP_DAC_READ_SEARCH``: it can
#: read, never write. It never follows a link, accepts only bounded regular files, and frames the store and its
#: write-ahead log on stdout. Exit 3: no store. Any other refusal: exit 4.
_SESSION_STAGE_SCRIPT: Final = (
    "import os,stat,sys\n"
    "limit=int(sys.argv[1]);out=sys.stdout.buffer;cur='/h'\n"
    "try:\n"
    " for n in ('.local','share','kilo'):\n"
    "  cur=os.path.join(cur,n)\n"
    "  if not stat.S_ISDIR(os.lstat(cur).st_mode):sys.exit(4)\n"
    "except FileNotFoundError:sys.exit(3)\n"
    "except OSError:sys.exit(4)\n"
    "def read(name,required):\n"
    " try:fd=os.open(os.path.join(cur,name),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)\n"
    " except FileNotFoundError:\n"
    "  if required:sys.exit(3)\n"
    "  return b''\n"
    " except OSError:sys.exit(4)\n"
    " with os.fdopen(fd,'rb') as f:\n"
    "  info=os.fstat(f.fileno())\n"
    "  if not stat.S_ISREG(info.st_mode) or info.st_size>limit:sys.exit(4)\n"
    "  data=f.read(limit+1)\n"
    " if len(data)>limit:sys.exit(4)\n"
    " return data\n"
    "db=read('kilo.db',True);wal=read('kilo.db-wal',False)\n"
    "for part in (db,wal):out.write(len(part).to_bytes(8,'big'));out.write(part)\n"
)


def _unframe_session_store(payload: bytes) -> tuple[bytes, bytes] | None:
    """The store and its write-ahead log from the reader's exact framing, or ``None`` for anything else."""

    parts: list[bytes] = []
    offset = 0
    for _ in range(2):
        if len(payload) < offset + 8:
            return None
        size = int.from_bytes(payload[offset : offset + 8], "big")
        offset += 8
        if size > _SESSION_STORE_LIMIT or len(payload) < offset + size:
            return None
        parts.append(payload[offset : offset + size])
        offset += size
    if offset != len(payload) or not parts[0]:
        return None
    return parts[0], parts[1]


def gateway_refusals(log: str) -> tuple[list[str], int]:
    """The refused hosts, in order, and the count of any other denial, from one gateway instance's log."""
    hosts: list[str] = []
    other = 0
    for line in log[:262144].splitlines():
        match = _REFUSED.fullmatch(line.strip())
        if match is not None:
            hosts.append(match.group(1))
        elif line.startswith(_DENIED):
            other += 1
    return hosts, other


def canary_refusal_evidence(
    *, gateway_instance: str, provider_host: str, primary_hosts: Sequence[str], all_hosts: Sequence[str], other: int
) -> dict[str, object] | None:
    """F8 evidence only when the primary was refused the provider and the probe exactly once more, and nothing else.

    The canary profile is the Kilo profile minus the provider host, so any *other* refused host is one the Kilo
    profile refuses on every run (Kilo 7.8.18 sends telemetry to ``us.i.posthog.com``); it says nothing about the
    canary and is bound into the digest, not counted. A malformed request, or any refusal after the agent exited
    other than the probe's single provider refusal, yields no evidence.
    """
    primary = sum(host == provider_host for host in primary_hosts)
    if primary < 1 or other or list(all_hosts) != [*primary_hosts, provider_host]:
        return None
    digest = hashlib.sha256(
        json.dumps(
            {"gateway_instance": gateway_instance, "provider_host": provider_host, "refused_hosts": list(all_hosts)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {"primary_refusals": primary, "probe_refusals": 1, "refusal_digest": digest}


_TRACE = re.compile(r"BROKER_EXECUTED: (\[[^\n]*?\]) EXIT: (-?\d+) TREE: ([0-9a-f]{64}) STDOUT_BYTES: (\d+)")


#: ``docker logs --timestamps`` prefixes every line with Docker's own RFC 3339 receive time (Issue #718).
_BROKER_STAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z ")
_BROKER_RECEIVED = re.compile(r"BROKER_RECEIVED: ")
_BROKER_DENIED = re.compile(r"BROKER_DENIED: ([A-Za-z]{1,40})$")
#: The only refusal types the broker raises (``real_broker_server.handle``); anything else is reported as OTHER.
_BROKER_DENIAL_TYPES = frozenset(
    {
        "ShellDenied",
        "BudgetExhausted",
        "PermissionError",
        "TimeoutExpired",
        "ValueError",
        "TypeError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "OSError",
        "FileNotFoundError",
        "JSONDecodeError",
    }
)


def _broker_stamp(line: str) -> tuple[float | None, str]:
    match = _BROKER_STAMP.match(line)
    if match is None:
        return None, line
    try:
        whole = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None, line
    return whole + float("0." + (match.group(2) or "0")), line[match.end() :]


def parse_broker_trace(log: str) -> list[dict[str, object]]:
    """Closed-vocabulary execution trace from the broker log; never includes command output.

    Issue #718: when the log carries Docker's timestamps, each command also reports ``at`` (whole seconds since
    the broker's first line) and ``dur`` (whole seconds from its ``BROKER_RECEIVED`` line), so the time between
    commands, which is the model's own turn time, is measurable. Both are ``None`` without timestamps.
    """
    trace: list[dict[str, object]] = []
    origin: float | None = None
    received: float | None = None
    for line in log[:262144].splitlines():
        when, body = _broker_stamp(line)
        if when is not None and origin is None:
            origin = when
        if _BROKER_RECEIVED.match(body):
            received = when
            continue
        if _BROKER_DENIED.match(body):
            received = None  # a refused command never executed: its receive time belongs to no trace entry
            continue
        match = _TRACE.match(body)
        if match is None:
            continue
        try:
            argv = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            continue
        if isinstance(argv, list) and all(isinstance(a, str) for a in argv) and len(trace) < 64:
            trace.append(
                {
                    "seq": len(trace) + 1,
                    "cmd": " ".join(argv),
                    "exit": int(match.group(2)),
                    "tree": match.group(3),
                    "stdout_bytes": int(match.group(4)),
                    "at": None if when is None or origin is None else int(when - origin),
                    "dur": None if when is None or received is None else max(0, int(when - received)),
                }
            )
        received = None
    return trace


def broker_session_summary(log: str, *, origin: float | None = None) -> dict[str, str]:
    """Issue #718: the broker session's closed outline: refusals by type and when its last line was written.

    A broker past its session deadline exits and every later agent command fails, so the time of its last line,
    against the model's own wall clock, shows whether a model timeout was spent with or without tools. With
    ``origin`` (the model start) that time is on the model's clock, else on the broker's first line.
    """

    first: float | None = None
    last: float | None = None
    denied: dict[str, int] = {}
    for line in log[:262144].splitlines():
        when, body = _broker_stamp(line)
        if when is not None:
            first = when if first is None else first
            last = when
        match = _BROKER_DENIED.match(body)
        if match is not None:
            kind = match.group(1) if match.group(1) in _BROKER_DENIAL_TYPES else "OTHER"
            denied[kind] = min(denied.get(kind, 0) + 1, 999)
    base = origin if origin is not None else first
    return {
        "denied": ",".join(f"{kind}:{count}" for kind, count in sorted(denied.items())) or "none",
        "last_line_at": "none" if base is None or last is None else f"+{int(last - base)}s",
    }


#: Issue #718: the gateway's content-free tunnel close line (``model_egress_gateway.TUNNEL_CLOSED_MARKER``).
_GATEWAY_TUNNEL = re.compile(
    r"GATEWAY_TUNNEL_CLOSED host=([a-z0-9.-]{1,253}) reason=(idle|eof|error) open_s=(\d{1,7}) silent_s=(\d{1,7}) "
    r"up_bytes=(\d{1,15}) down_bytes=(\d{1,15})$"
)
#: The gateway's idle cut (``model_egress_gateway.TUNNEL_IDLE_SECONDS``), reported beside the counts.
GATEWAY_TUNNEL_IDLE_SECONDS: Final = 60
_IDLE_REPORT_LIMIT = 16
_HOST_REPORT_LIMIT = 8


def gateway_tunnel_summary(log: str, *, origin: float | None = None) -> dict[str, str]:
    """Issue #718: how every tunnel through this run's gateway ended, counts and times only.

    A provider stream silent for longer than the gateway's idle limit is cut and Kilo re-sends the whole request, so
    each ``idle`` closure on the provider host is model budget lost without any other trace; an idle closure of a
    pooled connection with no request in flight (``models.dev``) is not. Per host, the tunnel count, the longest
    open time and the bytes received separate a stream that delivered from repeated empty requests. Times are seconds from
    ``origin`` (the model start) when given, else from the gateway's first line. A line counts only when it is the
    whole line after an optional ``docker logs --timestamps`` stamp; no host outside the gateway's own fixed marker
    grammar, and nothing a tunnel carried, is ever reported.
    """

    first: float | None = None
    counts = {"idle": 0, "eof": 0, "error": 0}
    longest_silent = 0
    idle_at: list[str] = []
    per_host: dict[str, list[int]] = {}  # host -> [tunnels, longest open seconds, bytes received]
    for line in log[:262144].splitlines():
        when, body = _broker_stamp(line)
        if when is not None and first is None:
            first = when
        match = _GATEWAY_TUNNEL.fullmatch(body.strip())
        if match is None:
            continue
        host, reason, silent = match.group(1), match.group(2), int(match.group(4))
        counts[reason] += 1
        if host in per_host or len(per_host) < _HOST_REPORT_LIMIT:
            entry = per_host.setdefault(host, [0, 0, 0])
            entry[0] += 1
            entry[1] = max(entry[1], int(match.group(3)))
            entry[2] = min(entry[2] + int(match.group(6)), 10**15)
        longest_silent = max(longest_silent, silent)
        if reason == "idle" and len(idle_at) < _IDLE_REPORT_LIMIT:
            base = origin if origin is not None else first
            at = "?" if when is None or base is None else f"+{int(when - base)}s"
            idle_at.append(f"{host}@{at}")
    return {
        "closed": str(sum(counts.values())),
        **{name: str(value) for name, value in counts.items()},
        "longest_silent_s": str(longest_silent),
        "idle_at": ",".join(idle_at) or "none",
        "hosts": ",".join(
            f"{host}:n={n}/max_open_s={longest}/down_bytes={down}"
            for host, (n, longest, down) in sorted(per_host.items())
        )
        or "none",
    }


def _session_diagnostic_cause(session: Mapping[str, str]) -> str:
    """Classify closed session telemetry for logs only; never authorize fallback."""

    if session.get("error") == "APIError" and session.get("status") == "429":
        return "RATE_LIMIT"
    return "UNCLASSIFIED"


class KiloContainerIsolation:
    """Bounded Docker implementation of the Runner's existing Isolation protocol."""

    def __init__(self, scripts: Path) -> None:
        self.scripts = scripts.resolve(strict=True)
        self.policy = Path(__file__).resolve().with_name("issue_agent_governed_shell.py")
        self.suffix = uuid.uuid4().hex[:12]
        self.network = f"hunter-682-runner-{self.suffix}"
        self.gateway = f"hunter-682-runner-gw-{self.suffix}"
        self.broker = f"hunter-682-runner-broker-{self.suffix}"
        self.agent = f"hunter-682-runner-agent-{self.suffix}"
        self.socket_volume = f"hunter-682-runner-sock-{self.suffix}"
        self.workspace: Path | None = None
        self.home: Path | None = None
        self.started = False
        self.trace: list[dict[str, object]] = []
        self.model_started_at: float | None = None
        #: ADR 0041 F2: the closed stderr category of the last isolated run (an untrusted hint, never content).
        self.last_stderr_category: str | None = None
        #: ADR 0041 F8: set only by ``arm_canary`` from the signed ``AUTHORIZED.fallback_canary`` flag.
        self.canary = False

    def arm_canary(self) -> None:
        """Start this run's gateway with the ``kilo-canary`` profile. Only before the gateway exists."""
        if self.started:
            raise RoleRefused("EXECUTION_NOT_STARTED", "the canary is armed only before isolation starts")
        self.canary = True

    def observe_canary(self, probe: Callable[[tuple[str, int]], str], provider_host: str) -> tuple[str, dict | None]:
        """ADR 0041 F8: probe through this run's own gateway after the agent exited, and read its refusals.

        Returns the trusted probe category and the refusal evidence (``None`` unless the refusal is exactly the
        canary's). Called before ``stop``; the agent container has already exited (``docker run --rm``).
        """
        if not (self.started and self.canary):
            raise RoleRefused("EXECUTION_NOT_STARTED", "no armed canary gateway is running")
        before = self._docker("logs", self.gateway, timeout=15)
        primary_hosts, _ = gateway_refusals((before.stdout + before.stderr).decode("utf-8", errors="replace"))
        inspected = self._docker(
            "inspect", "--format", "{{json .NetworkSettings.Networks.bridge.IPAddress}}", self.gateway, timeout=15
        )
        try:
            address = ipaddress.ip_address(json.loads(inspected.stdout.decode("ascii")))
        except (ValueError, UnicodeError):
            return "UNCLASSIFIED", None
        if inspected.returncode or not address.is_private or address.version != 4:
            return "UNCLASSIFIED", None
        category = probe((str(address), 8080))
        for _ in range(10):  # the log driver may lag the gateway's flushed line by a moment
            after = self._docker("logs", self.gateway, timeout=15)
            all_hosts, other = gateway_refusals((after.stdout + after.stderr).decode("utf-8", errors="replace"))
            if len(all_hosts) > len(primary_hosts):
                break
            time.sleep(0.5)
        for index, host in enumerate(all_hosts, start=1):
            print(f"isolated gateway refusal: seq={index} host={host}", flush=True)
        evidence = canary_refusal_evidence(
            gateway_instance=self.gateway,
            provider_host=provider_host,
            primary_hosts=primary_hosts,
            all_hosts=all_hosts,
            other=other,
        )
        print(
            f"isolated gateway canary: instance={self.gateway} refused_before_probe={len(primary_hosts)} "
            f"total_refusals={len(all_hosts)} other_denials={other} "
            f"refusal_digest={'' if evidence is None else evidence['refusal_digest']}",
            flush=True,
        )
        return category, evidence

    @staticmethod
    def _docker(*args: str, timeout: float = 40, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(["docker", *args], input=stdin, capture_output=True, check=False, timeout=timeout)
        except FileNotFoundError:  # no container runtime: never fall back to a host shell
            raise RoleRefused("EXECUTION_NOT_STARTED", "container runtime unavailable") from None

    def _checked(self, *args: str) -> None:
        result = self._docker(*args)
        if result.returncode:
            # Only the fixed Docker setup operation and a bounded error category are logged;
            # never echo command arguments, environment files, or raw container stderr.
            from hunter.automation.issue_agent_roles import stderr_category

            print(
                "isolated Docker setup diagnostic: operation="
                + args[0]
                + " stderr_category="
                + stderr_category(result.stderr[:65536]),
                flush=True,
            )
            # Docker setup has no secrets in argv; expose only a bounded, sanitized error line.
            import re

            diagnostic = result.stderr.decode("utf-8", errors="replace").splitlines()
            detail = diagnostic[-1][:350] if diagnostic else "NO_STDERR"
            detail = re.sub(r"[^a-zA-Z0-9_ /.:()\[\],=-]", "?", detail)
            print("isolated Docker setup detail: " + detail, flush=True)
            raise RoleRefused("EXECUTION_NOT_STARTED", "governed container setup failed")

    def prepare(self, *paths: Path) -> None:
        if len(paths) != 2 or self.started:
            raise RoleRefused("EXECUTION_NOT_STARTED", "invalid Kilo workspace preparation")
        self.workspace, self.home = (p.resolve(strict=True) for p in paths)
        if self.workspace.parent != self.home.parent or self.workspace == self.home:
            raise RoleRefused("EXECUTION_NOT_STARTED", "workspace and home must share trusted workroot")
        try:
            self._checked("network", "create", "--internal", self.network)
            self.started = True
            self._checked("volume", "create", self.socket_volume)
            self._checked(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--mount",
                f"type=volume,src={self.socket_volume},dst=/run/hunter",
                "--mount",
                f"type=bind,src={self.workspace},dst=/workspace",
                "--mount",
                f"type=bind,src={self.home},dst=/hunter-home",
                PYTHON_IMAGE,
                "python",
                "-c",
                "import os;os.chown('/run/hunter',65533,65532);os.chmod('/run/hunter',0o770);"
                "os.chown('/workspace',65534,65532);os.chown('/hunter-home',65534,65532);"
                "os.chown('/hunter-home/hunter-task.txt',65534,65532);"
                "[os.chown(p,65534,65532) for dp,_,fs in os.walk('/workspace') for p in [dp]+[os.path.join(dp,f) for f in fs]];"
                "os.system('true')",
            )
            self._checked(
                "run",
                "-d",
                "--name",
                self.gateway,
                "--network",
                self.network,
                "--user",
                "65534:65534",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "128m",
                "--env",
                f"HUNTER_GATEWAY_PROFILE={'kilo-canary' if self.canary else 'kilo'}",
                "--mount",
                f"type=bind,src={self.scripts / 'model_egress_gateway.py'},dst=/opt/gateway.py,readonly",
                PYTHON_IMAGE,
                "python",
                "/opt/gateway.py",
            )
            self._checked("network", "connect", "bridge", self.gateway)
            self._checked(
                "run",
                "-d",
                "--name",
                self.broker,
                "--network",
                "none",
                "--user",
                "65533:65532",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--memory",
                "512m",
                "--mount",
                f"type=volume,src={self.socket_volume},dst=/run/hunter",
                "--mount",
                f"type=bind,src={self.workspace},dst=/workspace,readonly",
                "--mount",
                f"type=bind,src={self.scripts / 'real_broker_server.py'},dst=/opt/broker.py,readonly",
                "--mount",
                f"type=bind,src={self.policy},dst=/opt/policy/issue_agent_governed_shell.py,readonly",
                "--env",
                "PYTHONPATH=/opt/policy",
                "--env",
                f"HUNTER_BROKER_MAX_COMMANDS={BROKER_MAX_COMMANDS}",
                "--env",
                f"HUNTER_BROKER_DEADLINE_SECONDS={BROKER_DEADLINE_SECONDS}",
                "--tmpfs",
                "/tmp:rw,nosuid,size=160m",
                BROKER_IMAGE,
                "python",
                "/opt/broker.py",
            )
            self._await_broker_snapshot()
        except BaseException:
            self.stop()
            raise

    def _await_broker_snapshot(self, attempts: int = 60) -> None:
        """The agent must not start before the broker's baseline snapshot exists, or ``git diff`` would hide edits."""
        for _ in range(attempts):
            logs = self._docker("logs", self.broker, timeout=15)
            if b"BROKER_GIT_SNAPSHOT:" in logs.stdout + logs.stderr:
                return
            time.sleep(1)
        raise RoleRefused("EXECUTION_NOT_STARTED", "governed broker did not become ready")

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
        if not self.started or self.workspace is None or self.home is None or cwd.resolve() != self.workspace:
            raise RoleRefused("EXECUTION_NOT_STARTED", "Kilo container not prepared")
        if len(argv) < 2 or Path(argv[0]).name != "kilo" or argv[1] != "run":
            raise RoleRefused("EXECUTION_NOT_STARTED", "only Kilo is supported by this isolation")

        # Map the trusted host workspace/home into their fixed container paths.
        def map_paths(value: str) -> str:
            aliases = (
                (str(cwd), "/workspace"),
                (str(cwd.parent / "home"), "/hunter-home"),
                (str(self.workspace), "/workspace"),
                (str(self.home), "/hunter-home"),
            )
            for original, target in aliases:
                value = value.replace(original, target)
            return value

        mapped = [map_paths(str(a)) for a in argv]
        env = {str(k): map_paths(str(v)) for k, v in {**public_env, **secret_env}.items()}
        policy = json.loads(env["KILO_CONFIG_CONTENT"])
        policy["shell"] = "/usr/local/bin/hunter-governed-shell"
        policy.pop("instructions", None)  # external home files are denied to Kilo by design
        for scope in (policy, policy["agent"]["build"]):
            scope["permission"] = {"*": "deny", "bash": "allow", "read": "allow", "edit": "allow"}
        env["KILO_CONFIG_CONTENT"] = json.dumps(policy, separators=(",", ":"))
        # This is a plain HTTP CONNECT *proxy* on a Docker internal network,
        # not a plaintext upstream model connection. The tunnel carries TLS.
        proxy_endpoint = urlunsplit(("http", f"{self.gateway}:8080", "", "", ""))
        env.update(
            {
                "HOME": "/hunter-home",
                "SHELL": "/usr/local/bin/hunter-governed-shell",
                "HTTP_PROXY": proxy_endpoint,
                "HTTPS_PROXY": proxy_endpoint,
                "ALL_PROXY": proxy_endpoint,
                "NO_PROXY": "",
                "NODE_USE_ENV_PROXY": "1",
                "TMPDIR": "/run/hunter-private-tmp",
                "TMP": "/run/hunter-private-tmp",
                "TEMP": "/run/hunter-private-tmp",
            }
        )
        if any("\n" in key or "=" in key or "\n" in value or "\r" in value for key, value in env.items()):
            raise RoleRefused("EXECUTION_NOT_STARTED", "invalid model environment")
        # Secret values never enter docker CLI arguments, process logs, or stdout.
        with tempfile.TemporaryDirectory(prefix="hunter-kilo-env-") as temp:
            envfile = Path(temp) / "env"
            envfile.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
            envfile.chmod(0o600)
            command = [
                "run",
                "--rm",
                "--name",
                self.agent,
                "--network",
                self.network,
                "--user",
                "65534:65532",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=128m",
                "--tmpfs",
                "/run/hunter-private-tmp:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=65534,gid=65532",
                "--env-file",
                str(envfile),
                "--mount",
                f"type=volume,src={self.socket_volume},dst=/run/hunter,readonly",
                "--mount",
                f"type=bind,src={self.workspace},dst=/workspace",
                "--mount",
                f"type=bind,src={self.home},dst=/hunter-home",
                "--workdir",
                "/workspace",
                KILO_IMAGE,
                *mapped[1:],
            ]
            self.model_started_at = time.time()
            try:
                result = self._run_supervised(command, stdin=stdin, timeout=timeout)
            except subprocess.TimeoutExpired as expired:
                self._timeout_diagnostic(expired)
                raise
        # Bounded, content-free telemetry: do not log model text, task attachment, or secrets.
        output = result.stdout[:262144].lower()
        markers = {
            "error": b"error" in output,
            "permission": b"permission" in output or b"denied" in output,
            "unauthorized": b"unauthorized" in output or b"401" in output,
            "rate_limit": b"rate limit" in output or b"429" in output,
            "model": b"model" in output,
            "tool": b"tool" in output,
            "edit": b"edit" in output,
            "pytest": b"pytest" in output,
            "file": b"test_sample.py" in output,
        }
        print(
            "isolated Kilo response diagnostic: stdout_bytes="
            + str(len(result.stdout))
            + " stderr_bytes="
            + str(len(result.stderr))
            + " markers="
            + ",".join(key for key, present in markers.items() if present),
            flush=True,
        )
        self.last_stderr_category = stderr_category(result.stderr[:65536])
        if result.returncode != 0:
            # Kilo can report a structured provider HTTP failure only in its private
            # session store, leaving stderr unclassified. This is diagnostic only:
            # never promote model-writable session data to fallback authorization.
            session = self.session_outcome()
            session_cause = _session_diagnostic_cause(session)
            print("isolated Kilo session diagnostic cause=" + session_cause, flush=True)
            print(
                "isolated Kilo stderr structure: "
                + " ".join(f"{name}={value}" for name, value in stderr_structure(result.stderr).items()),
                flush=True,
            )
            print(
                "isolated Kilo session outcome: " + " ".join(f"{name}={value}" for name, value in session.items()),
                flush=True,
            )
        print(
            "isolated Kilo container diagnostic: exit="
            + ("ZERO" if result.returncode == 0 else "NONZERO")
            + " stderr_category="
            + self.last_stderr_category,
            flush=True,
        )
        return result.returncode

    def _run_supervised(
        self, command: Sequence[str], *, stdin: bytes, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        """Progress-aware supervision of the foreground ``docker run`` (Issue #705).

        ``subprocess.run(timeout=...)`` enforces one flat wall clock, so hosted run 42739368326 was killed mid-edit.
        This instead polls two host-trusted signals -- the workspace's own ``git status`` (the agent's bind mount,
        read by the host without executing or even opening a single model-written byte) and the broker's own
        executed-command trace (a distinct trusted container the host already controls) -- and extends the
        deadline only when one of them changes. Neither the agent's stdout/stderr nor its own claims ever reach
        this decision: both grow just as readily on an idle heartbeat as on real work, and a repeated or denied
        command never advances the broker trace signal (:func:`parse_broker_trace` already omits denials, and
        repeats are deduplicated here) by design. A caller-supplied ``timeout`` shorter than
        :data:`KILO_HARD_CEILING_SECONDS` is still honored as the absolute ceiling; one longer than it is not
        shortened. Reports exactly the same ``subprocess.TimeoutExpired`` an ordinary flat timeout always raised,
        so every existing caller, and ADR 0041 F2's closed cause set, is unchanged.
        """

        started = time.monotonic()
        ceiling = max(float(timeout), KILO_HARD_CEILING_SECONDS)
        last_progress_at = started
        last_changed = self._workspace_changed_entries()
        seen_commands: set[str] = set()
        docker_argv = ["docker", *command]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(  # noqa: S603 - fixed docker launcher, closed argv
                docker_argv, stdin=subprocess.PIPE, stdout=out, stderr=err
            )
            assert process.stdin is not None  # guaranteed by stdin=subprocess.PIPE above
            feeder = threading.Thread(target=self._feed_stdin, args=(process.stdin, stdin), daemon=True)
            feeder.start()
            reason: str | None = None
            now = started
            try:
                while True:
                    try:
                        returncode = process.wait(timeout=KILO_POLL_INTERVAL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.monotonic()
                    changed = self._workspace_changed_entries()
                    progressed = changed != -1 and changed != last_changed
                    if not progressed:
                        progressed = self._new_broker_commands(seen_commands)
                    if progressed:
                        last_changed, last_progress_at = changed, now
                        continue
                    if now - last_progress_at >= KILO_IDLE_STALL_SECONDS:
                        reason = "STALLED"
                        break
                    if now >= started + ceiling:
                        reason = "FAILED_CEILING"
                        break
                if reason is not None:
                    try:
                        self._docker("rm", "-f", self.agent, timeout=15)
                    except (OSError, subprocess.TimeoutExpired, RoleRefused):
                        pass  # a failed stop never masks the stall/ceiling timeout raised below
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
                    out.seek(0, os.SEEK_SET)
                    err.seek(0, os.SEEK_SET)
                    captured_out, captured_err = out.read(), err.read()
                    print(
                        f"isolated Kilo supervision diagnostic: reason={reason} "
                        f"elapsed_s={int(now - started)} idle_s={int(now - last_progress_at)} "
                        f"ceiling_s={int(ceiling)} stall_s={int(KILO_IDLE_STALL_SECONDS)}",
                        flush=True,
                    )
                    raise subprocess.TimeoutExpired(docker_argv, ceiling, output=captured_out, stderr=captured_err)
            finally:
                feeder.join(timeout=5)
            out.seek(0, os.SEEK_SET)
            err.seek(0, os.SEEK_SET)
            return subprocess.CompletedProcess(docker_argv, returncode, out.read(), err.read())

    @staticmethod
    def _feed_stdin(pipe: IO[bytes], payload: bytes) -> None:
        try:
            if payload:
                pipe.write(payload)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def _workspace_changed_entries(self) -> int:
        """Trusted-host count of changed workspace entries, polled live while Kilo runs (Issue #705).

        The workspace is the same bind mount the agent edits directly, so the host's own ``git status`` observes
        an edit as it lands, without ever reading model output or trusting the agent's word. ``-1`` (unreadable)
        never counts as progress -- a probe failure fails closed toward the stall/ceiling path, not toward it.
        """

        if self.workspace is None:
            return -1
        git_dir = self.workspace.parent / "trusted.git"
        try:
            command = trusted_git_command(
                git_dir, self.workspace, ("status", "--porcelain", "-z", "--untracked-files=normal")
            )
            result = subprocess.run(
                command,
                cwd=self.workspace,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": str(git_dir),
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                },
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (RoleRefused, OSError, subprocess.SubprocessError):
            return -1
        if result.returncode != 0:
            return -1
        return sum(bool(item) for item in result.stdout.split(b"\0"))

    def _new_broker_commands(self, seen: set[str]) -> bool:
        """Trusted-host evidence of a new, successfully executed, non-repeated shell command (Issue #705).

        Read from the broker's own log, a distinct trusted container the host already controls: a model stuck
        repeating one command (a directory listing, say) never advances ``seen``, and a denied command never
        reaches :func:`parse_broker_trace` at all, so neither counts as progress.
        """

        try:
            logs = self._docker("logs", self.broker, timeout=15)
        except (OSError, subprocess.TimeoutExpired, RoleRefused):
            return False
        text = (logs.stdout + logs.stderr).decode("utf-8", errors="replace")
        progressed = False
        for entry in parse_broker_trace(text):
            if entry["exit"] == 0:
                cmd = str(entry["cmd"])
                if cmd not in seen:
                    seen.add(cmd)
                    progressed = True
        return progressed

    def _timeout_diagnostic(self, expired: subprocess.TimeoutExpired) -> None:
        """Issue #718: what the agent was doing when the model budget ran out, in the existing closed vocabularies.

        A timeout used to skip every diagnostic a non-zero exit prints, so run 42642658174 left no evidence of
        whether its 720 s went to tool work or to provider retries. The agent container is stopped first so its
        session store is quiescent, then the partial stderr is classified and outlined, and Kilo's own record of
        its last turn is read. It is diagnostic only: the timeout still propagates unchanged, no category is stored
        for a fallback or canary decision, and no text, command or message is ever printed.
        """

        try:
            self._docker("rm", "-f", self.agent, timeout=15)
        except (OSError, subprocess.TimeoutExpired, RoleRefused):
            pass
        stderr = expired.stderr if isinstance(expired.stderr, bytes) else b""
        stdout = expired.output if isinstance(expired.output, bytes) else b""
        print(
            f"isolated Kilo timeout diagnostic: stdout_bytes={len(stdout)} stderr_bytes={len(stderr)} "
            f"stderr_category={stderr_category(stderr[:65536])}",
            flush=True,
        )
        print(
            "isolated Kilo stderr structure: "
            + " ".join(f"{name}={value}" for name, value in stderr_structure(stderr).items()),
            flush=True,
        )
        print(
            "isolated Kilo session outcome: "
            + " ".join(f"{name}={value}" for name, value in self.session_outcome().items()),
            flush=True,
        )

    def session_outcome(self) -> dict[str, str]:
        """Kilo's closed record of its last turn, read after the agent container exited (Issue #577).

        The trusted host cannot search the model uid's private home on a Linux runner, so one read-only reader
        container streams a bounded copy out; the host then parses it with :func:`kilo_session_outcome`. It is one
        attempt, never a retry loop, never a second model run, and diagnostic only: an unavailable record is
        ``UNREADABLE`` or ``ABSENT``, never a success.
        """

        if self.home is None:
            return {**_NO_SESSION, "error": "ABSENT"}
        try:
            result = self._docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "DAC_READ_SEARCH",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "16",
                "--memory",
                "256m",
                "--mount",
                f"type=bind,src={self.home},dst=/h,readonly",
                PYTHON_IMAGE,
                "python",
                "-I",
                "-c",
                _SESSION_STAGE_SCRIPT,
                str(_SESSION_STORE_LIMIT),
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired, RoleRefused):
            return {**_NO_SESSION, "error": "UNREADABLE"}
        if result.returncode == 3:
            return {**_NO_SESSION, "error": "ABSENT"}
        store = _unframe_session_store(result.stdout) if result.returncode == 0 else None
        if store is None:
            return {**_NO_SESSION, "error": "UNREADABLE"}
        with tempfile.TemporaryDirectory(prefix="hunter-kilo-session-stage-") as staged:
            directory = Path(staged).joinpath(*KILO_SESSION_STORE[:-1])
            directory.mkdir(parents=True, mode=0o700)
            (directory / KILO_SESSION_STORE[-1]).write_bytes(store[0])
            if store[1]:
                (directory / (KILO_SESSION_STORE[-1] + "-wal")).write_bytes(store[1])
            return kilo_session_outcome(Path(staged))

    def format_python(self, paths: Sequence[str]) -> str:
        """Issue #577: apply the repository's pinned Black to the agent's changed Python files, before collection.

        The broker mounts the workspace read-only (it also runs the agent's tests), so the agent can run
        ``black --check`` but can never format; hosted run 42496047567 completed the task and was then refused by
        candidate pre-push safety on Black alone. Only after every isolated process has stopped, a one-shot
        container from the already-built broker image (``black==26.3.1``, the version ``ci-constraints.txt`` pins)
        formats the listed files with the trusted control checkout's ``pyproject.toml``: no network, read-only root,
        no capabilities, the agent's own uid. Black parses and rewrites; it never imports or runs the code. Only
        regular ``.py`` files inside the workspace, named by the trusted collector, are passed. The validator still
        checks every gate on the result, so this never turns a refusal into an admission.
        """

        if self.started or self.workspace is None:
            return "SKIPPED"
        config = self.scripts.parents[1] / "pyproject.toml"
        root = self.workspace.resolve()
        safe: list[str] = []
        for path in paths:
            if not path.endswith(".py") or path.startswith(("-", "/")) or "\0" in path or ".." in Path(path).parts:
                continue
            candidate = root / path
            try:
                # A regular file whose resolved path is exactly its workspace path: no link anywhere on the way.
                if not stat.S_ISREG(os.lstat(candidate).st_mode) or candidate.resolve(strict=True) != candidate:
                    continue
            except OSError:
                continue
            safe.append(path)
        if not safe or len(safe) > 500 or not config.is_file():
            return "SKIPPED"
        formatter = f"{self.agent}-format"
        try:
            result = self._docker(
                "run",
                "--rm",
                "--name",
                formatter,
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65532",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "32",
                "--memory",
                "512m",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=32m",
                "--env",
                "HOME=/tmp",
                "--mount",
                f"type=bind,src={root},dst=/workspace",
                "--mount",
                f"type=bind,src={config},dst=/opt/hunter-format/pyproject.toml,readonly",
                "--workdir",
                "/workspace",
                BROKER_IMAGE,
                "python",
                "-I",
                "-m",
                "black",
                "--config",
                "/opt/hunter-format/pyproject.toml",
                "--quiet",
                "--",
                *safe,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Killing the docker CLI does not stop the daemon-managed container (``--rm`` acts only on exit), so a
            # timed-out Black could still be writing while the result is collected. Remove it, prove it is gone,
            # and refuse rather than collect beside a formatter that may still be running.
            self._remove_formatter(formatter)
            return "FAILED"
        return "FORMATTED" if result.returncode == 0 else "FAILED"

    def _remove_formatter(self, name: str) -> None:
        try:
            self._docker("rm", "-f", name, timeout=30)
            gone = self._docker("container", "inspect", name, timeout=15).returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            gone = False
        if not gone:
            raise RoleRefused("EXECUTION_NOT_COMPLETED", "the result formatter could not be stopped")

    def stop(self) -> None:
        if not self.started:
            return
        try:  # evidence of what the broker actually executed, read before the container is removed
            logs = self._docker("logs", "--timestamps", self.broker, timeout=15)
            text = (logs.stdout + logs.stderr).decode("utf-8", errors="replace")
            self.trace = parse_broker_trace(text)
            for entry in self.trace:
                timing = "" if entry["at"] is None else f" at=+{entry['at']}s dur={entry['dur']}s"
                print(
                    f"isolated broker trace: seq={entry['seq']} cmd={entry['cmd']!r} exit={entry['exit']} "
                    f"tree={str(entry['tree'])[:12]} stdout_bytes={entry['stdout_bytes']}{timing}",
                    flush=True,
                )
            summary = broker_session_summary(text, origin=self.model_started_at)
            print(
                f"isolated broker session: executed={len(self.trace)} denied={summary['denied']} "
                f"last_line_at={summary['last_line_at']} deadline={BROKER_DEADLINE_SECONDS}s",
                flush=True,
            )
        except (OSError, subprocess.TimeoutExpired, RoleRefused):
            pass
        try:  # Issue #718: how each model tunnel ended, read before the gateway is removed
            logs = self._docker("logs", "--timestamps", self.gateway, timeout=15)
            tunnels = gateway_tunnel_summary(
                (logs.stdout + logs.stderr).decode("utf-8", errors="replace"), origin=self.model_started_at
            )
            print(
                "isolated gateway tunnels: "
                + " ".join(f"{name}={value}" for name, value in tunnels.items())
                + f" idle_limit={GATEWAY_TUNNEL_IDLE_SECONDS}s",
                flush=True,
            )
        except (OSError, subprocess.TimeoutExpired, RoleRefused):
            pass
        for args in (
            ("rm", "-f", self.agent),
            ("rm", "-f", self.broker),
            ("rm", "-f", self.gateway),
            ("network", "rm", self.network),
            ("volume", "rm", "-f", self.socket_volume),
        ):
            try:
                self._docker(*args, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.started = False
