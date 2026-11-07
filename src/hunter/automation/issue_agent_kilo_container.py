"""Issue #682: governed Docker Isolation port for the existing Issue Agent Runner.

Only Kilo uses this port. The agent is on an internal-only Docker network;
the dual-homed allowlisted CONNECT gateway is its sole external route.
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlunsplit

from hunter.automation.issue_agent_roles import RoleRefused, stderr_category

KILO_IMAGE = "hunter-682-kilo-canary:local"
BROKER_IMAGE = "hunter-682-broker-canary:local"
# Explicit repair-loop budgets enforced by the broker (fail closed on exhaustion).
BROKER_MAX_COMMANDS = 16
BROKER_DEADLINE_SECONDS = 660  # strictly below the 12 minute model timeout
PYTHON_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


#: ADR 0041 F8: the gateway's content-free refusal line (``model_egress_gateway.REFUSED_HOST_MARKER``).
_REFUSED = re.compile(r"^GATEWAY_REFUSED_HOST ([a-z0-9.-]{1,253})$")
_DENIED = "GATEWAY_DENIED_CONNECT"


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
    """F8 evidence only when the primary was refused the provider, the probe exactly once more, and nothing else."""
    primary = len(primary_hosts)
    if (
        primary < 1
        or other
        or len(all_hosts) != primary + 1
        or list(all_hosts[:primary]) != list(primary_hosts)
        or any(host != provider_host for host in all_hosts)
    ):
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


def parse_broker_trace(log: str) -> list[dict[str, object]]:
    """Closed-vocabulary execution trace from the broker log; never includes command output."""
    trace: list[dict[str, object]] = []
    for match in _TRACE.finditer(log[:262144]):
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
                }
            )
    return trace


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
        after = self._docker("logs", self.gateway, timeout=15)
        all_hosts, other = gateway_refusals((after.stdout + after.stderr).decode("utf-8", errors="replace"))
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
            f"isolated gateway canary: instance={self.gateway} primary_refusals={len(primary_hosts)} "
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
            result = self._docker(*command, timeout=timeout, stdin=stdin)
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
        print(
            "isolated Kilo container diagnostic: exit="
            + ("ZERO" if result.returncode == 0 else "NONZERO")
            + " stderr_category="
            + self.last_stderr_category,
            flush=True,
        )
        return result.returncode

    def stop(self) -> None:
        if not self.started:
            return
        try:  # evidence of what the broker actually executed, read before the container is removed
            logs = self._docker("logs", self.broker, timeout=15)
            self.trace = parse_broker_trace((logs.stdout + logs.stderr).decode("utf-8", errors="replace"))
            for entry in self.trace:
                print(
                    f"isolated broker trace: seq={entry['seq']} cmd={entry['cmd']!r} exit={entry['exit']} "
                    f"tree={str(entry['tree'])[:12]} stdout_bytes={entry['stdout_bytes']}",
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
