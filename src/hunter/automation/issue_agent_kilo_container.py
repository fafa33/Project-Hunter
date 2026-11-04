"""Issue #682: governed Docker Isolation port for the existing Issue Agent Runner.

Only Kilo uses this port. The agent is on an internal-only Docker network;
the dual-homed allowlisted CONNECT gateway is its sole external route.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlunsplit

from hunter.automation.issue_agent_roles import RoleRefused, stderr_category

KILO_IMAGE = "hunter-682-kilo-canary:local"
BROKER_IMAGE = "hunter-682-broker-canary:local"
PYTHON_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


class KiloContainerIsolation:
    """Bounded Docker implementation of the Runner's existing Isolation protocol."""

    def __init__(self, scripts: Path) -> None:
        self.scripts = scripts.resolve(strict=True)
        self.suffix = uuid.uuid4().hex[:12]
        self.network = f"hunter-682-runner-{self.suffix}"
        self.gateway = f"hunter-682-runner-gw-{self.suffix}"
        self.broker = f"hunter-682-runner-broker-{self.suffix}"
        self.agent = f"hunter-682-runner-agent-{self.suffix}"
        self.socket_volume = f"hunter-682-runner-sock-{self.suffix}"
        self.workspace: Path | None = None
        self.home: Path | None = None
        self.started = False

    @staticmethod
    def _docker(*args: str, timeout: float = 40, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["docker", *args], input=stdin, capture_output=True, check=False, timeout=timeout)

    def _checked(self, *args: str) -> None:
        result = self._docker(*args)
        if result.returncode:
            # Only the fixed Docker setup operation and a bounded error category are logged;
            # never echo command arguments, environment files, or raw container stderr.
            from hunter.automation.issue_agent_roles import stderr_category

            print("isolated Docker setup diagnostic: operation=" + args[0] + " stderr_category=" + stderr_category(result.stderr[:65536]), flush=True)
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
                "256m",
                "--mount",
                f"type=volume,src={self.socket_volume},dst=/run/hunter",
                "--mount",
                f"type=bind,src={self.workspace},dst=/workspace,readonly",
                "--mount",
                f"type=bind,src={self.scripts / 'real_broker_server.py'},dst=/opt/broker.py,readonly",
                "--tmpfs",
                "/tmp:rw,nosuid,size=32m",
                BROKER_IMAGE,
                "python",
                "/opt/broker.py",
            )
        except BaseException:
            self.stop()
            raise

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
        print(
            "isolated Kilo container diagnostic: exit="
            + ("ZERO" if result.returncode == 0 else "NONZERO")
            + " stderr_category="
            + stderr_category(result.stderr[:65536]),
            flush=True,
        )
        return result.returncode

    def stop(self) -> None:
        if not self.started:
            return
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
