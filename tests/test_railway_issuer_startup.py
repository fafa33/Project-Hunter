from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import bootstrap_source_handling_authority as bootstrap
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError

CONFIG_RULE = Path("config/source_handling/authorization_rule_v1.json")
RULE_GOLDEN = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"

_SIGNING_KEY_ENV = "HUNTER_SOURCE_HANDLING_SIGNING_KEY"
_EVIDENCE_DB_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _private_key_bytes() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_bytes(private_key: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(private_key)
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )


def _verification_key_sha256(private_key: bytes) -> str:
    return hashlib.sha256(_public_key_bytes(private_key)).hexdigest()


def _signing_key_hex(private_key: bytes) -> str:
    return private_key.hex()


def _bootstrap(database: Path, private_key: bytes) -> None:
    saved = os.environ.get(_SIGNING_KEY_ENV)
    os.environ[_SIGNING_KEY_ENV] = _signing_key_hex(private_key)
    try:
        bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(_SIGNING_KEY_ENV, None)
        else:
            os.environ[_SIGNING_KEY_ENV] = saved


class _ExecCapture:
    """Captures governed-topology child launches instead of starting processes.

    ``calls`` holds only the issuer launch as ``(program, argv, env)``;
    ``launches`` holds every child as ``(role, argv, env)`` in launch order.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []
        self.launches: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, role: str, argv: list[str], env: dict[str, str]) -> Mock:
        self.launches.append((role, list(argv), dict(env)))
        if role == "issuer":
            self.calls.append((argv[0], list(argv), dict(env)))
        return Mock(poll=Mock(return_value=None))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove signing-key and evidence-db env vars to avoid leakage."""
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(_EVIDENCE_DB_ENV, raising=False)
    monkeypatch.delenv("PORT", raising=False)


def _run_startup(
    database: str,
    signing_key_hex: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, list[tuple[str, list[str], dict[str, str]]]]:
    """Run the startup seam script in-process, capturing child launches."""
    import railway_issuer_startup as startup

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: signing_key_hex,
    }
    if extra_env:
        env.update(extra_env)

    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        try:
            rc = startup.main()
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1

    return rc, captured.calls


def _assert_issuer_argv(argv: list[str]) -> None:
    """Assert the captured argv is the canonical issuer invocation."""
    assert any("hunter_issue_agent_issuer.py" in arg for arg in argv)
    assert "--host" in argv
    assert "--port" in argv
    assert "--provenance-resolver" in argv
    idx = argv.index("--provenance-resolver")
    assert argv[idx + 1] == "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"


# --- Fresh mounted volume --------------------------------------------------


def test_fresh_volume_bootstrap_succeeds_and_issuer_launch_is_allowed(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    rc, calls = _run_startup(database, _signing_key_hex(key))

    assert rc == 0
    assert len(calls) == 1
    _assert_issuer_argv(calls[0][1])

    assert Path(database).exists()
    with sqlite3.connect(database) as conn:
        root = conn.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()
        assert root is not None
        assert root[0] == RULE_GOLDEN
        assert root[1] == _verification_key_sha256(key)

        records = conn.execute("SELECT family, scope FROM source_handling_authority_records").fetchall()
        assert records == [("AUTHORIZATION_RULE", "SOURCE_HANDLING")]


def test_fresh_volume_genesis_record_matches_production_rule(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    rc, _ = _run_startup(database, _signing_key_hex(key))
    assert rc == 0

    production_payload = json.loads(CONFIG_RULE.read_text(encoding="utf-8"))
    expected_id = bootstrap._expected_genesis_record_id(production_payload)

    with sqlite3.connect(database) as conn:
        head = conn.execute(
            "SELECT current_record_id FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = 'SOURCE_HANDLING'"
        ).fetchone()
        assert head is not None
        assert head[0] == expected_id


# --- Existing valid bootstrapped volume: idempotency ----------------------


def test_existing_bootstrapped_volume_is_idempotent(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()

    rc1, _ = _run_startup(database, _signing_key_hex(key))
    assert rc1 == 0

    with sqlite3.connect(database) as conn:
        before = conn.execute(
            "SELECT current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = 'SOURCE_HANDLING'"
        ).fetchone()

    rc2, _ = _run_startup(database, _signing_key_hex(key))
    assert rc2 == 0

    with sqlite3.connect(database) as conn:
        after = conn.execute(
            "SELECT current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = 'SOURCE_HANDLING'"
        ).fetchone()

    assert after == before
    assert after[1] == 1


def test_existing_bootstrapped_volume_deterministic_across_restarts(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()

    for _ in range(3):
        rc, _ = _run_startup(database, _signing_key_hex(key))
        assert rc == 0

    with sqlite3.connect(database) as conn:
        records = conn.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()
        assert records[0] == 1
        root = conn.execute("SELECT COUNT(*) FROM source_handling_operator_root").fetchone()
        assert root[0] == 1


# --- Tampered/mismatched authority: fail closed ---------------------------


def test_tampered_signing_key_fails_closed(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    first_key = _private_key_bytes()
    _run_startup(database, _signing_key_hex(first_key))

    other_key = _private_key_bytes()
    rc, calls = _run_startup(database, _signing_key_hex(other_key))

    assert rc == 1
    assert len(calls) == 0


def test_mismatched_genesis_digest_fails_closed(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _run_startup(database, _signing_key_hex(key))

    foreign_digest = hashlib.sha256(b"foreign-authority-rule").hexdigest()
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE source_handling_operator_root SET genesis_rule_sha256 = ?",
            (foreign_digest,),
        )
        conn.commit()

    rc, calls = _run_startup(database, _signing_key_hex(key))
    assert rc == 1
    assert len(calls) == 0


def test_missing_signing_key_fails_closed_before_issuer(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    import railway_issuer_startup as startup

    with (
        patch.dict(os.environ, {_EVIDENCE_DB_ENV: database}, clear=True),
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        rc = startup.main()
    assert rc == 1
    assert not Path(database).exists()


def test_missing_evidence_db_env_fails_closed(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    with patch.dict(os.environ, {}, clear=True):
        rc = startup.main()
    assert rc == 1


def test_data_directory_not_mounted_fails_closed(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    env = {
        _EVIDENCE_DB_ENV: "/data/nonexistent/evidence.sqlite",
        _SIGNING_KEY_ENV: "ab" * 32,
    }
    with patch.dict(os.environ, env, clear=True):
        rc = startup.main()
    assert rc == 1


def test_evidence_directory_without_a_mounted_volume_fails_closed(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    bootstrap_invoked: list[str] = []
    original_bootstrap = startup._bootstrap

    def _tracking_bootstrap(db: str) -> dict[str, object]:
        bootstrap_invoked.append(db)
        return original_bootstrap(db)

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: "ab" * 32,
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup.os.path, "ismount", return_value=False),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup, "_bootstrap", side_effect=_tracking_bootstrap),
    ):
        rc = startup.main()

    assert rc == 1
    assert bootstrap_invoked == []
    assert len(captured.calls) == 0
    assert not Path(database).exists()


def test_tampered_authority_preserved_after_mismatch_rejection(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _run_startup(database, _signing_key_hex(key))

    with sqlite3.connect(database) as conn:
        original = conn.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()

    other_key = _private_key_bytes()
    _run_startup(database, _signing_key_hex(other_key))

    with sqlite3.connect(database) as conn:
        unchanged = conn.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()

    assert unchanged == original


# --- Signing-key lifecycle -------------------------------------------------


def _launches_by_role(
    launches: list[tuple[str, list[str], dict[str, str]]],
) -> dict[str, tuple[list[str], dict[str, str]]]:
    roles = [role for role, _, _ in launches]
    assert roles == ["provisioner", "issuer", "ingress"]
    return {role: (argv, env) for role, argv, env in launches}


def _flag(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _run_topology(
    tmp_path: Path, *, extra_env: dict[str, str] | None = None
) -> tuple[int, dict[str, tuple[list[str], dict[str, str]]], str]:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    key_hex = _signing_key_hex(_private_key_bytes())
    env = {_EVIDENCE_DB_ENV: database, _SIGNING_KEY_ENV: key_hex, "PATH": "/usr/bin", "OPERATOR_TOKEN": "t0k3n"}
    env.update(extra_env or {})
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        rc = startup.main()
    return rc, _launches_by_role(captured.launches), key_hex


def test_provisioner_child_inherits_key_and_dedicated_port(tmp_path: Path) -> None:
    rc, launches, key_hex = _run_topology(tmp_path, extra_env={"HUNTER_ISSUE_AGENT_PROVISIONER_PORT": "8181"})

    assert rc == 0
    argv, env = launches["provisioner"]
    assert "hunter_issue_agent_provisioner.py" in " ".join(argv)
    assert _flag(argv, "--port") == "8181"
    assert env[_SIGNING_KEY_ENV] == key_hex


# --- Single public ingress topology (Railway 404 regression) ---------------


def test_railway_public_port_is_owned_by_ingress_routing_both_canonical_edges(tmp_path: Path) -> None:
    """Regression: production exposed only $PORT and it was bound by the issuer.

    POST /issue-agent/provision therefore reached the issuer and was answered
    404.  The public port must be owned by the ingress, whose fixed upstreams
    are exactly the internal ports the provisioner and issuer listen on.
    """
    rc, launches, _ = _run_topology(tmp_path, extra_env={"PORT": "9999"})

    assert rc == 0
    ingress_argv, _ = launches["ingress"]
    provisioner_argv, _ = launches["provisioner"]
    issuer_argv, _ = launches["issuer"]
    assert "hunter_issue_agent_ingress.py" in " ".join(ingress_argv)
    assert _flag(ingress_argv, "--port") == "9999"
    assert _flag(ingress_argv, "--host") == "0.0.0.0"
    assert _flag(ingress_argv, "--provisioner-port") == _flag(provisioner_argv, "--port")
    assert _flag(ingress_argv, "--issuer-port") == _flag(issuer_argv, "--port")
    assert _flag(issuer_argv, "--port") != "9999"
    assert _flag(provisioner_argv, "--port") != "9999"


def test_internal_edges_bind_loopback_only(tmp_path: Path) -> None:
    rc, launches, _ = _run_topology(tmp_path)

    assert rc == 0
    assert _flag(launches["provisioner"][0], "--host") == "127.0.0.1"
    assert _flag(launches["issuer"][0], "--host") == "127.0.0.1"
    public = [role for role, (argv, _) in launches.items() if _flag(argv, "--host") != "127.0.0.1"]
    assert public == ["ingress"]


def test_only_the_provisioner_receives_the_signing_key(tmp_path: Path) -> None:
    rc, launches, key_hex = _run_topology(tmp_path)

    assert rc == 0
    assert launches["provisioner"][1][_SIGNING_KEY_ENV] == key_hex
    for role in ("issuer", "ingress"):
        env = launches[role][1]
        assert _SIGNING_KEY_ENV not in env, role
        assert all(key_hex not in value for value in env.values()), role


def test_public_ingress_environment_is_allowlisted_and_secret_free(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    rc, launches, _ = _run_topology(tmp_path)

    assert rc == 0
    env = launches["ingress"][1]
    assert set(env) <= set(startup.INGRESS_ENVIRONMENT_ALLOWLIST)
    assert "OPERATOR_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"
    # The issuer still receives its ordinary operational configuration.
    assert launches["issuer"][1]["OPERATOR_TOKEN"] == "t0k3n"


def test_parent_environment_is_scrubbed_before_issuer_and_ingress_launch(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    parent_has_key: dict[str, bool] = {}

    def _spawn(role: str, argv: list[str], env: dict[str, str]) -> Mock:
        parent_has_key[role] = _SIGNING_KEY_ENV in os.environ
        return Mock(poll=Mock(return_value=None))

    env = {_EVIDENCE_DB_ENV: database, _SIGNING_KEY_ENV: _signing_key_hex(_private_key_bytes())}
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=_spawn),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        assert startup.main() == 0
        assert _SIGNING_KEY_ENV not in os.environ

    assert parent_has_key == {"provisioner": True, "issuer": False, "ingress": False}


@pytest.mark.parametrize(
    "ports",
    [
        {"PORT": "8081"},
        {"PORT": "8082"},
        {"HUNTER_ISSUE_AGENT_PROVISIONER_PORT": "8082"},
        {"PORT": "7000", "HUNTER_ISSUE_AGENT_ISSUER_PORT": "7000"},
        {"PORT": "0"},
        {"PORT": "70000"},
        {"PORT": "80a"},
        {"HUNTER_ISSUE_AGENT_ISSUER_PORT": "-1"},
        {"PORT": "\u0668\u0660\u0668\u0660"},
        {"PORT": ""},
    ],
)
def test_port_collision_or_invalid_port_fails_closed_before_any_launch(tmp_path: Path, ports: dict[str, str]) -> None:
    import railway_issuer_startup as startup

    env = {
        _EVIDENCE_DB_ENV: str(tmp_path / "evidence.sqlite"),
        _SIGNING_KEY_ENV: _signing_key_hex(_private_key_bytes()),
    }
    env.update(ports)
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        assert startup.main() == 1
    assert captured.launches == []


def test_default_port_plan_is_distinct() -> None:
    import railway_issuer_startup as startup

    ports = startup.resolve_runtime_ports({})
    assert (ports.public, ports.provisioner, ports.issuer) == (8080, 8081, 8082)


def test_child_launch_failure_stops_started_children_and_fails_closed(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    provisioner_child = Mock(poll=Mock(return_value=None))

    def _spawn(role: str, argv: list[str], env: dict[str, str]) -> Mock:
        if role == "issuer":
            raise OSError("exec failed")
        return provisioner_child

    env = {
        _EVIDENCE_DB_ENV: str(tmp_path / "evidence.sqlite"),
        _SIGNING_KEY_ENV: _signing_key_hex(_private_key_bytes()),
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=_spawn),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise") as supervise,
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        assert startup.main() == 1
        assert _SIGNING_KEY_ENV not in os.environ
    provisioner_child.terminate.assert_called_once()
    supervise.assert_not_called()


def test_child_exiting_during_startup_fails_closed(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    children = {
        "provisioner": Mock(poll=Mock(return_value=None)),
        "issuer": Mock(poll=Mock(return_value=None)),
        "ingress": Mock(poll=Mock(return_value=2)),
    }
    env = {
        _EVIDENCE_DB_ENV: str(tmp_path / "evidence.sqlite"),
        _SIGNING_KEY_ENV: _signing_key_hex(_private_key_bytes()),
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=lambda role, argv, env: children[role]),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise") as supervise,
        patch.object(startup.os.path, "ismount", return_value=True),
    ):
        assert startup.main() == 1
    children["provisioner"].terminate.assert_called_once()
    children["issuer"].terminate.assert_called_once()
    supervise.assert_not_called()


class _FakeChild:
    def __init__(self, name: str, order: list[str], *, exit_code: int | None = None, stalls: bool = False) -> None:
        self.name = name
        self.order = order
        self.exit_code = exit_code
        self.stalls = stalls
        self.wait_timeouts: list[float | None] = []
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.order.append(self.name)
        self.exit_code = -15

    def wait(self, timeout: float | None = None) -> int | None:
        self.wait_timeouts.append(timeout)
        if timeout is not None and self.stalls:
            raise subprocess.TimeoutExpired(self.name, timeout)
        return self.exit_code

    def kill(self) -> None:
        self.killed = True
        self.stalls = False
        self.exit_code = -9


def test_supervisor_stops_the_topology_when_a_required_child_dies() -> None:
    import threading

    import railway_issuer_startup as startup

    order: list[str] = []
    children = [
        ("provisioner", _FakeChild("provisioner", order, exit_code=1)),
        ("issuer", _FakeChild("issuer", order)),
        ("ingress", _FakeChild("ingress", order)),
    ]
    assert startup.supervise(children, threading.Event(), poll_interval=0.01) == 1
    assert order == ["ingress", "issuer"]


def test_supervisor_stop_signal_stops_ingress_first_and_exits_cleanly() -> None:
    import threading

    import railway_issuer_startup as startup

    order: list[str] = []
    children = [(name, _FakeChild(name, order)) for name in ("provisioner", "issuer", "ingress")]
    stop = threading.Event()
    stop.set()
    assert startup.supervise(children, stop, poll_interval=0.01) == 0
    assert order == ["ingress", "issuer", "provisioner"]


def test_signing_key_scrubbed_from_issuer_environment(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    rc, calls = _run_startup(database, _signing_key_hex(key))

    assert rc == 0
    assert len(calls) == 1
    _, _, env = calls[0]
    assert _SIGNING_KEY_ENV not in env


def test_signing_key_still_present_during_bootstrap(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()

    bootstrap_received_key: list[str] = []

    original_load = bootstrap._load_signing_key

    def _tracking_load(*, environ: Any, signing_key_file: Any) -> bytes:
        bootstrap_received_key.append(environ.get(_SIGNING_KEY_ENV, ""))
        return original_load(environ=environ, signing_key_file=signing_key_file)

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: _signing_key_hex(key),
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
        patch.object(bootstrap, "_load_signing_key", side_effect=_tracking_load),
    ):
        try:
            startup.main()
        except SystemExit:
            pass

    assert len(bootstrap_received_key) == 1
    assert bootstrap_received_key[0] == _signing_key_hex(key)


def test_signing_key_never_appears_in_exec_env(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _, calls = _run_startup(database, _signing_key_hex(key))

    assert len(calls) == 1
    _, _, env = calls[0]
    assert _SIGNING_KEY_ENV not in env
    for value in env.values():
        assert _signing_key_hex(key) not in str(value)


# --- Startup ordering: bootstrap before issuer ----------------------------


def test_issuer_launch_cannot_happen_before_successful_bootstrap(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()

    bootstrap_called = False

    original_bootstrap_fn = startup._bootstrap

    def _tracking_bootstrap(db: str) -> dict[str, object]:
        nonlocal bootstrap_called
        result = original_bootstrap_fn(db)
        bootstrap_called = True
        return result

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: _signing_key_hex(key),
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
        patch.object(startup, "_bootstrap", side_effect=_tracking_bootstrap),
    ):
        try:
            startup.main()
        except SystemExit:
            pass

    assert bootstrap_called
    assert len(captured.calls) == 1


def test_bootstrap_failure_prevents_issuer_launch(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")

    def _failing_bootstrap(db: str) -> dict[str, object]:
        raise SourceHandlingBlockedError("simulated bootstrap failure")

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: "ab" * 32,
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
        patch.object(startup, "_bootstrap", side_effect=_failing_bootstrap),
    ):
        rc = startup.main()

    assert rc == 1
    assert len(captured.calls) == 0


# --- Existing canonical issuer command/provenance resolver -----------------


def test_startup_uses_canonical_provenance_resolver(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _, calls = _run_startup(database, _signing_key_hex(key))

    assert len(calls) == 1
    _, argv, _ = calls[0]
    idx = argv.index("--provenance-resolver")
    assert argv[idx + 1] == "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"


def test_startup_issuer_command_matches_canonical(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _, calls = _run_startup(database, _signing_key_hex(key))

    assert len(calls) == 1
    prog, argv, _ = calls[0]
    assert prog == sys.executable
    assert any("hunter_issue_agent_issuer.py" in arg for arg in argv)
    assert "--host" in argv
    assert "--port" in argv


def test_startup_passes_port_from_environment(tmp_path: Path) -> None:
    rc, launches, _ = _run_topology(tmp_path, extra_env={"PORT": "9999", "HUNTER_ISSUE_AGENT_ISSUER_PORT": "9123"})

    assert rc == 0
    assert _flag(launches["ingress"][0], "--port") == "9999"
    assert _flag(launches["issuer"][0], "--port") == "9123"


# --- Shared public canonical bootstrap contract ----------------------------


def test_evidence_directory_is_accepted_only_as_a_mounted_volume(tmp_path: Path) -> None:
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    rc, calls = _run_startup(database, _signing_key_hex(key))

    assert rc == 0
    assert len(calls) == 1
    assert Path(database).exists()


def test_seam_and_cli_invoke_the_same_public_bootstrap_contract(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    database = str(tmp_path / "evidence.sqlite")
    invoked: list[tuple[str, str | None]] = []

    def _contract(db: str, *, environ: Any, signing_key_file: str | None = None) -> dict[str, object]:
        invoked.append((db, signing_key_file))
        return {
            "status": "bootstrapped",
            "operator_root": "pinned",
            "genesis_record_id": "record",
        }

    env = {
        _EVIDENCE_DB_ENV: database,
        _SIGNING_KEY_ENV: "ab" * 32,
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(bootstrap, "bootstrap_authority", side_effect=_contract),
        patch.object(startup.os.path, "ismount", return_value=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
    ):
        cli_rc = bootstrap.main(["--database", database, "--json"])
        try:
            seam_rc = startup.main()
        except SystemExit as exc:
            seam_rc = exc.code if isinstance(exc.code, int) else 1

    assert cli_rc == 0
    assert seam_rc == 0
    assert invoked == [(database, None), (database, None)]
    assert len(captured.calls) == 1


# --- Railway runtime vendor import path ------------------------------------


def test_runtime_vendor_directory_is_prepended_to_import_paths(tmp_path: Path, monkeypatch) -> None:
    import railway_issuer_startup as startup

    vendor = tmp_path / "app" / "vendor"
    vendor.mkdir(parents=True)
    original_path = list(sys.path)
    original_pythonpath = os.environ.get("PYTHONPATH")
    original_vendor = os.environ.get(startup._RUNTIME_VENDOR_DIR_ENV, startup._DEFAULT_VENDOR_DIR)
    monkeypatch.setenv(startup._RUNTIME_VENDOR_DIR_ENV, str(vendor))
    try:
        startup._ensure_runtime_import_paths()
        assert sys.path[0] == str(vendor)
        assert str(vendor) in os.environ.get("PYTHONPATH", "").split(os.pathsep)
    finally:
        sys.path[:] = original_path
        if original_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original_pythonpath
        os.environ[startup._RUNTIME_VENDOR_DIR_ENV] = original_vendor


def test_runtime_vendor_directory_absent_is_a_noop(tmp_path: Path, monkeypatch) -> None:
    import railway_issuer_startup as startup

    missing = tmp_path / "missing" / "vendor"
    original_path = list(sys.path)
    original_pythonpath = os.environ.get("PYTHONPATH")
    original_vendor = os.environ.get(startup._RUNTIME_VENDOR_DIR_ENV, startup._DEFAULT_VENDOR_DIR)
    monkeypatch.setenv(startup._RUNTIME_VENDOR_DIR_ENV, str(missing))
    try:
        startup._ensure_runtime_import_paths()
        assert sys.path == original_path
        assert os.environ.get("PYTHONPATH") == original_pythonpath
    finally:
        sys.path[:] = original_path
        if original_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original_pythonpath
        os.environ[startup._RUNTIME_VENDOR_DIR_ENV] = original_vendor


def test_vendor_module_is_importable_from_railway_layout(tmp_path: Path, monkeypatch) -> None:
    import importlib

    import railway_issuer_startup as startup

    vendor = tmp_path / "app" / "vendor"
    module_dir = vendor / "railway_runtime_marker"
    module_dir.mkdir(parents=True)
    module_dir.joinpath("__init__.py").write_text("MARKER = 441\n", encoding="utf-8")

    original_path = list(sys.path)
    original_pythonpath = os.environ.get("PYTHONPATH")
    original_vendor = os.environ.get(startup._RUNTIME_VENDOR_DIR_ENV, startup._DEFAULT_VENDOR_DIR)
    monkeypatch.setenv(startup._RUNTIME_VENDOR_DIR_ENV, str(vendor))
    try:
        startup._ensure_runtime_import_paths()
        marker = importlib.import_module("railway_runtime_marker")
        assert marker.MARKER == 441
    finally:
        sys.modules.pop("railway_runtime_marker", None)
        sys.path[:] = original_path
        if original_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original_pythonpath
        os.environ[startup._RUNTIME_VENDOR_DIR_ENV] = original_vendor


# --- Per-authorization workspace root ----------------------------------------


def test_prepare_workspace_root_creates_an_empty_disposable_root_without_cloning(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    import railway_issuer_startup as startup

    disposable_root = tmp_path / "checkouts"
    root = disposable_root / "issue-agent"
    stale = root / ("0" * 64)
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("from a previous process")
    monkeypatch.setattr(startup, "_DISPOSABLE_CHECKOUT_ROOT", disposable_root)
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", "fafa33/Project-Hunter")
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPO_DIR", str(root))
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_EXECUTION_BRANCH", "issue-agent-execution")

    with patch.object(startup.subprocess, "run", side_effect=AssertionError("startup must not clone")):
        with caplog.at_level("WARNING", logger="railway_issuer_startup"):
            startup._prepare_workspace_root()

    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert "HUNTER_ISSUE_AGENT_EXECUTION_BRANCH is retired and ignored" in caplog.text


def test_prepare_workspace_root_rejects_credential_or_url_shaped_repository(tmp_path: Path, monkeypatch) -> None:
    import railway_issuer_startup as startup

    monkeypatch.setattr(startup, "_DISPOSABLE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", "https://token@github.com/fafa33/Project-Hunter")
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPO_DIR", str(tmp_path / "runtime"))
    with pytest.raises(RuntimeError, match="owner/repository slug"):
        startup._prepare_workspace_root()


def test_prepare_workspace_root_requires_complete_configuration(tmp_path: Path, monkeypatch) -> None:
    import railway_issuer_startup as startup

    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", "fafa33/Project-Hunter")
    monkeypatch.delenv("HUNTER_ISSUE_AGENT_REPO_DIR", raising=False)
    with pytest.raises(RuntimeError, match="configuration is incomplete"):
        startup._prepare_workspace_root()


@pytest.mark.parametrize("inside", [False, True], ids=["outside-approved-root", "the-approved-root-itself"])
def test_prepare_workspace_root_never_deletes_outside_the_disposable_root(
    tmp_path: Path, monkeypatch, inside: bool
) -> None:
    import railway_issuer_startup as startup

    disposable_root = tmp_path / "approved"
    protected = disposable_root if inside else tmp_path / "application"
    protected.mkdir()
    sentinel = protected / "keep.txt"
    sentinel.write_text("keep")
    monkeypatch.setattr(startup, "_DISPOSABLE_CHECKOUT_ROOT", disposable_root)
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPOSITORY", "fafa33/Project-Hunter")
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_REPO_DIR", str(protected))
    with pytest.raises(RuntimeError, match="must be contained beneath"):
        startup._prepare_workspace_root()
    assert sentinel.read_text() == "keep"


# --- Governed provider startup self-check --------------------------------------


def test_provider_self_check_runs_with_the_issuer_environment_and_never_the_signing_key(monkeypatch) -> None:
    import railway_issuer_startup as startup

    monkeypatch.setenv(
        "HUNTER_AGENT_OPENCODE_COMMAND", '["python", "-m", "hunter.automation.opencode_provider_runtime"]'
    )
    monkeypatch.setenv(_SIGNING_KEY_ENV, "secret-signing-key")
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs["env"])))
        return Mock(returncode=0)

    with patch.object(startup.subprocess, "run", side_effect=fake_run):
        startup._run_provider_self_check()

    ((argv, env),) = calls
    assert argv[1:] == ("-m", "hunter.automation.opencode_provider_self_check")
    assert _SIGNING_KEY_ENV not in env


def test_a_failed_provider_self_check_fails_startup_closed(monkeypatch) -> None:
    import railway_issuer_startup as startup

    monkeypatch.setenv("HUNTER_AGENT_OPENCODE_COMMAND", '["opencode"]')
    with patch.object(startup.subprocess, "run", return_value=Mock(returncode=1)):
        with pytest.raises(RuntimeError, match="failed its startup self-check"):
            startup._run_provider_self_check()


def test_no_provider_pool_means_no_self_check(monkeypatch) -> None:
    import railway_issuer_startup as startup

    monkeypatch.delenv("HUNTER_AGENT_OPENCODE_COMMAND", raising=False)
    with patch.object(startup.subprocess, "run", side_effect=AssertionError("no provider to check")):
        startup._run_provider_self_check()


def test_startup_launches_nothing_when_the_provider_self_check_fails(tmp_path: Path) -> None:
    import railway_issuer_startup as startup

    env = {
        _EVIDENCE_DB_ENV: str(tmp_path / "evidence.sqlite"),
        _SIGNING_KEY_ENV: _signing_key_hex(_private_key_bytes()),
        "HUNTER_AGENT_OPENCODE_COMMAND": '["opencode"]',
    }
    captured = _ExecCapture()
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(startup, "_spawn", side_effect=captured),
        patch.object(startup, "_install_stop_signals"),
        patch.object(startup, "supervise", return_value=0),
        patch.object(startup.os.path, "ismount", return_value=True),
        patch.object(startup.subprocess, "run", return_value=Mock(returncode=1)),
    ):
        assert startup.main() == 1
    assert captured.launches == []


def test_railway_start_command_launches_the_ingress_owning_seam() -> None:
    """The repository-owned Railway config must start the seam whose ingress owns $PORT."""
    import tomllib

    config = tomllib.loads((_REPO_ROOT / "railway.toml").read_text(encoding="utf-8"))
    assert config["deploy"]["startCommand"] == "python scripts/railway_issuer_startup.py"
    assert config["deploy"]["healthcheckPath"] == "/healthz"


def test_supervisor_never_kills_the_issuer_during_its_execution_drain() -> None:
    """An accepted authorization must reach a durable terminal outcome.

    The issuer drains non-daemon execution workers after SIGTERM; a supervisor
    deadline kill would strand an acknowledged authorization RUNNING.  Only the
    ingress and provisioner, which hold no accepted work, are bounded.
    """
    import threading

    import railway_issuer_startup as startup

    order: list[str] = []
    provisioner = _FakeChild("provisioner", order, stalls=True)
    issuer = _FakeChild("issuer", order, stalls=True)
    ingress = _FakeChild("ingress", order)
    stop = threading.Event()
    stop.set()

    assert startup.supervise([("provisioner", provisioner), ("issuer", issuer), ("ingress", ingress)], stop) == 0

    assert issuer.wait_timeouts == [None]
    assert not issuer.killed
    assert provisioner.killed
    assert provisioner.wait_timeouts[0] == startup._CHILD_STOP_TIMEOUT_SECONDS
