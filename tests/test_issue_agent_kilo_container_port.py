"""Runner's Kilo Isolation port must fail closed on wrong runtime or workspace."""

from pathlib import Path

import pytest

from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
from hunter.automation.issue_agent_roles import RoleRefused, require_governed_kilo_isolation


def test_kilo_requires_exact_container_port(tmp_path: Path) -> None:
    isolation = KiloContainerIsolation(Path(__file__).resolve().parents[1] / "scripts/issue_agent")
    require_governed_kilo_isolation(("kilo", "run", "--model", "kilo/kilo-auto/free"), isolation)
    with pytest.raises(RoleRefused, match="not prepared"):
        isolation.run(("kilo", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)


def test_wrong_isolation_refused() -> None:
    with pytest.raises(RoleRefused, match="governed container"):
        require_governed_kilo_isolation(("kilo", "run", "--model", "free"), object())


def test_unrelated_provider_keeps_existing_port() -> None:
    require_governed_kilo_isolation(("opencode", "run"), object())


# --- Issue #682: container boundary invariants (no Docker daemon required) -------------------------------
import json  # noqa: E402
import subprocess  # noqa: E402
from unittest.mock import patch  # noqa: E402

from hunter.automation import issue_agent_kilo_container as kilo_port  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/issue_agent"
POLICY = json.dumps({"agent": {"build": {}}, "instructions": ["x"]})


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace, home = tmp_path / "workspace", tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    return workspace, home


def _recorder(calls: list[tuple[str, ...]]):
    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"BROKER_GIT_SNAPSHOT: ready\n" if args[0] == "logs" else b"", b"")

    return fake


def test_missing_container_runtime_fails_closed_without_host_fallback(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    with patch.object(kilo_port.subprocess, "run", side_effect=FileNotFoundError("docker")) as run:
        with pytest.raises(RoleRefused, match="runtime unavailable") as caught:
            isolation.prepare(workspace, home)
    assert caught.value.code == "EXECUTION_NOT_STARTED"
    assert all(call.args[0][0] == "docker" for call in run.call_args_list)  # nothing else was ever launched
    assert not isolation.started


def test_failed_setup_tears_down_everything(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[:2] == ("volume", "create") else 0, b"", b"boom")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(RoleRefused):
            isolation.prepare(workspace, home)
    removed = {c[-1] for c in calls if c[0] in {"rm", "network", "volume"} and "rm" in c[:2]}
    assert {isolation.agent, isolation.broker, isolation.gateway, isolation.network}.issubset(removed)


def test_prepared_containers_have_no_docker_socket_host_home_or_privileges(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_recorder(calls))):
        isolation.prepare(workspace, home)
    flat = " ".join(" ".join(c) for c in calls)
    assert "docker.sock" not in flat and "--privileged" not in flat and "--network host" not in flat
    assert "--internal" in " ".join(next(c for c in calls if c[:2] == ("network", "create")))
    broker_run = next(c for c in calls if isolation.broker in c and c[0] == "run")
    assert broker_run[broker_run.index("--network") + 1] == "none"
    assert f"type=bind,src={workspace},dst=/workspace,readonly" in broker_run  # broker cannot write the workspace
    assert f"HUNTER_BROKER_MAX_COMMANDS={kilo_port.BROKER_MAX_COMMANDS}" in broker_run
    assert f"HUNTER_BROKER_DEADLINE_SECONDS={kilo_port.BROKER_DEADLINE_SECONDS}" in broker_run
    assert kilo_port.BROKER_DEADLINE_SECONDS < 12 * 60  # strictly inside the model wall clock
    sources = {part.split("src=")[1].split(",")[0] for part in broker_run if part.startswith("type=bind")}
    assert sources == {
        str(workspace),
        str(isolation.scripts / "real_broker_server.py"),
        str(isolation.policy),
    }  # no home directory, Docker socket or any other host path reaches the broker
    for run_call in (c for c in calls if c[0] == "run" and "--rm" not in c):
        assert "--cap-drop" in run_call and "no-new-privileges" in run_call and "--read-only" in run_call


def test_agent_container_is_bounded_and_secrets_stay_out_of_argv(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_recorder(calls))):
        isolation.run(
            ("kilo", "run", "--model", "kilo/x:free"),
            cwd=workspace,
            public_env={"KILO_CONFIG_CONTENT": POLICY},
            secret_env={"KILO_API_KEY": "secret-sentinel-123"},
            stdin=b"prompt",
            timeout=5,
        )
    (agent,) = calls
    joined = " ".join(agent)
    assert "secret-sentinel-123" not in joined and "KILO_API_KEY" not in joined
    assert "docker.sock" not in joined and "--privileged" not in joined
    assert agent[agent.index("--network") + 1] == isolation.network
    for flag in ("--read-only", "--cap-drop", "--pids-limit", "--memory", "--cpus", "--env-file"):
        assert flag in agent, flag
    assert agent[agent.index("--user") + 1] == "65534:65532"  # never root
    assert f"type=volume,src={isolation.socket_volume},dst=/run/hunter,readonly" in agent
    mounts = [agent[i + 1] for i, part in enumerate(agent) if part == "--mount"]
    assert {m.split("dst=")[1].split(",")[0] for m in mounts} == {"/run/hunter", "/workspace", "/hunter-home"}


def test_only_governed_kilo_run_argv_is_accepted(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    for argv in (("bash", "-c", "id"), ("kilo", "auth"), ("sh",), ("kilo",)):
        with pytest.raises(RoleRefused, match="only Kilo"):
            isolation.run(argv, cwd=workspace, public_env={}, secret_env={}, stdin=b"", timeout=1)
    with pytest.raises(RoleRefused, match="not prepared"):  # cwd outside the bound workspace
        isolation.run(("kilo", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)


def test_model_timeout_propagates_and_stop_removes_every_container(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        if args[0] == "run":
            raise subprocess.TimeoutExpired("docker", timeout)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(subprocess.TimeoutExpired):
            isolation.run(
                ("kilo", "run"),
                cwd=workspace,
                public_env={"KILO_CONFIG_CONTENT": POLICY},
                secret_env={},
                stdin=b"",
                timeout=1,
            )
        isolation.stop()
    assert ("rm", "-f", isolation.agent) in calls and ("rm", "-f", isolation.broker) in calls
    assert not isolation.started
    isolation.stop()  # idempotent


def test_host_isolation_is_refused_for_kilo() -> None:
    class SudoIsolation:  # same name as the legacy host launcher, different module
        pass

    with pytest.raises(RoleRefused, match="governed container"):
        require_governed_kilo_isolation(("kilo", "run", "--model", "kilo/x:free"), SudoIsolation())


def test_broker_trace_is_parsed_from_log_without_command_output() -> None:
    tree_a, tree_b = "a" * 64, "b" * 64
    log = (
        "BROKER_RECEIVED: 'python -m pytest -q test_sample.py'\n"
        f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q', 'test_sample.py'] EXIT: 1 TREE: {tree_a} STDOUT_BYTES: 321\n"
        "BROKER_DENIED: ShellDenied\n"
        f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q', 'test_sample.py'] EXIT: 0 TREE: {tree_b} STDOUT_BYTES: 40\n"
        "BROKER_EXECUTED: [__import__('os').system('id')] EXIT: 0 TREE: " + tree_a + " STDOUT_BYTES: 1\n"
        "forged BROKER_EXECUTED: nonsense\n"
    )
    trace = kilo_port.parse_broker_trace(log)
    assert [(t["seq"], t["exit"], t["tree"]) for t in trace] == [(1, 1, tree_a), (2, 0, tree_b)]
    assert trace[0]["cmd"] == "python -m pytest -q test_sample.py" and trace[0]["stdout_bytes"] == 321


def test_stop_collects_broker_trace_before_removal(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []
    line = f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q'] EXIT: 0 TREE: {'c' * 64} STDOUT_BYTES: 5\n"

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, line.encode() if args[0] == "logs" else b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        isolation.stop()
    assert len(isolation.trace) == 1
    assert calls.index(("logs", isolation.broker)) < calls.index(("rm", "-f", isolation.broker))


def test_agent_is_not_started_until_the_broker_snapshot_is_ready(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []

    def never_ready(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with (
        patch.object(KiloContainerIsolation, "_docker", staticmethod(never_ready)),
        patch.object(kilo_port.time, "sleep"),
    ):
        with pytest.raises(RoleRefused, match="did not become ready"):
            isolation.prepare(workspace, home)
    assert not isolation.started  # torn down: the agent was never launched
    assert sum(1 for c in calls if c[:2] == ("logs", isolation.broker)) >= 60
