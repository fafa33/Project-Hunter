from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    """Captures ``os.execvpe`` calls instead of replacing the process."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, program: str, argv: list[str], env: dict[str, str]) -> None:
        self.calls.append((program, list(argv), dict(env)))
        raise SystemExit(0)


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
    """Run the startup seam script in-process, capturing execvpe calls."""
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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
    database = str(tmp_path / "evidence.sqlite")
    key = _private_key_bytes()
    _, calls = _run_startup(database, _signing_key_hex(key), extra_env={"PORT": "9999"})

    assert len(calls) == 1
    _, argv, _ = calls[0]
    port_idx = argv.index("--port")
    assert argv[port_idx + 1] == "9999"


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
        patch.object(startup.os, "execvpe", side_effect=captured),
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
