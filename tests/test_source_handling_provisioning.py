from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bootstrap_source_handling_authority as bootstrap
import provision_source_handling_issue_authority as provisioning
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import hunter.evidence_intelligence.source_handling_provenance as provenance_module
from hunter.automation.issue_agent_execution import IssueAgentAuthorization, issue_agent_document_id
from hunter.evidence_intelligence.pre_model import resolve_pre_model_source_handling
from hunter.evidence_intelligence.source_handling_persistence import (
    SourceHandlingAuthorityService,
    SourceHandlingOperatorRoot,
)

RULE_GOLDEN = bootstrap.PINNED_PRODUCTION_RULE_SHA256
REPOSITORY = "fafa33/Project-Hunter"


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


def _operator_root(private_key: bytes) -> SourceHandlingOperatorRoot:
    return SourceHandlingOperatorRoot(
        genesis_rule_sha256=RULE_GOLDEN,
        verification_key_sha256=hashlib.sha256(_public_key_bytes(private_key)).hexdigest(),
    )


def _bootstrap(database: Path, private_key: bytes) -> None:
    saved = os.environ.get(provisioning.SIGNING_KEY_ENV)
    os.environ[provisioning.SIGNING_KEY_ENV] = private_key.hex()
    try:
        bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(provisioning.SIGNING_KEY_ENV, None)
        else:
            os.environ[provisioning.SIGNING_KEY_ENV] = saved


def _export_operator_environment(monkeypatch: pytest.MonkeyPatch, database: Path, private_key: bytes) -> None:
    rule = bootstrap._load_production_rule()
    verification_key_hex, verification_key_sha256, genesis_rule_sha256 = bootstrap._derived_digests(private_key, rule)
    monkeypatch.setenv(provenance_module.EVIDENCE_DATABASE_ENV, str(database))
    monkeypatch.setenv(provenance_module.VERIFICATION_KEY_ENV, verification_key_hex)
    monkeypatch.setenv(provenance_module.VERIFICATION_KEY_SHA256_ENV, verification_key_sha256)
    monkeypatch.setenv(provenance_module.GENESIS_RULE_SHA256_ENV, genesis_rule_sha256)


def _issue_updated_at_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _issue_updated_at(updated_at_text: str) -> datetime:
    return datetime.fromisoformat(updated_at_text.replace("Z", "+00:00"))


def _authorization(
    issue_number: int, updated_at: str, authorization_id: str = "auth-test-provisioning"
) -> IssueAgentAuthorization:
    return IssueAgentAuthorization(
        repository=REPOSITORY,
        issue_number=issue_number,
        issue_url=f"https://github.com/{REPOSITORY}/issues/{issue_number}",
        issue_title="Provisioning test Issue",
        issue_body="A body that fixes a typo in the docs.",
        authorized_by="fafa33",
        authorization_label="hunter-agent-execute",
        issue_updated_at=updated_at,
        authorization_id=authorization_id,
    )


def _arguments(
    database: Path,
    updated_at: str,
    *,
    issue_number: int = 430,
    authorization_id: str = "auth-test-provisioning",
    sensitivity: str = "PUBLIC",
    as_of: str | None = None,
) -> list[str]:
    authorization = _authorization(issue_number, updated_at, authorization_id)
    arguments = [
        "--database",
        str(database),
        "--repository",
        REPOSITORY,
        "--issue-number",
        str(authorization.issue_number),
        "--issue-url",
        authorization.issue_url,
        "--issue-title",
        authorization.issue_title,
        "--issue-body",
        authorization.issue_body,
        "--authorized-by",
        authorization.authorized_by,
        "--issue-updated-at",
        updated_at,
        "--authorization-id",
        authorization_id,
        "--sensitivity",
        sensitivity,
        "--json",
    ]
    if as_of is not None:
        arguments += ["--as-of", as_of]
    return arguments


def _run_provisioning(database: Path, private_key: bytes, arguments: list[str]) -> None:
    """Invoke the provisioning CLI exactly as an operator would, via the env signing key."""
    saved = os.environ.get(provisioning.SIGNING_KEY_ENV)
    os.environ[provisioning.SIGNING_KEY_ENV] = private_key.hex()
    try:
        provisioning.main(arguments)
    finally:
        if saved is None:
            os.environ.pop(provisioning.SIGNING_KEY_ENV, None)
        else:
            os.environ[provisioning.SIGNING_KEY_ENV] = saved


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, bytes, str]:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    _export_operator_environment(monkeypatch, database, key)
    monkeypatch.setattr(provenance_module, "_production_view", None)
    return database, key, _issue_updated_at_text()


# --- happy path --------------------------------------------------------------


def test_provisioning_publishes_exact_authority_records_for_one_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database, key, updated_at = _prepare(tmp_path, monkeypatch)
    capsys.readouterr()
    _run_provisioning(database, key, _arguments(database, updated_at))
    outcome = json.loads(capsys.readouterr().out)

    authorization = _authorization(430, updated_at)
    document_id = issue_agent_document_id(authorization)
    assert outcome["status"] == "provisioned"
    assert outcome["document_id"] == document_id
    assert provisioning._parse_time(outcome["as_of"]) >= _issue_updated_at(updated_at)
    assert outcome["records"]["FACT"]["status"] == "provisioned"
    assert outcome["records"]["FIELD_CATEGORY_REGISTRY"]["status"] == "provisioned"
    assert outcome["records"]["POLICY"]["status"] == "provisioned"

    with sqlite3.connect(database) as connection:
        records = connection.execute(
            "SELECT family, scope, revision FROM source_handling_canonical_keys ORDER BY family"
        ).fetchall()
        assert records == [
            ("AUTHORIZATION_RULE", "SOURCE_HANDLING", 1),
            ("FACT", document_id, 1),
            ("FIELD_CATEGORY_REGISTRY", f"registry:{document_id}:v1", 1),
            ("POLICY", f"policy:{document_id}:v1", 1),
        ]
        assert connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0] == 4
        issued = connection.execute("SELECT COUNT(*) FROM source_handling_publication_authorizations").fetchone()[0]
        consumed = connection.execute(
            "SELECT COUNT(*) FROM source_handling_publication_authorizations WHERE consumed_at IS NOT NULL"
        ).fetchone()[0]
        assert issued == 3
        assert consumed == 3
        assert connection.execute("SELECT COUNT(*) FROM source_handling_provenance_records").fetchone()[0] == 6

    cutoff = _issue_updated_at(updated_at) + timedelta(days=1)
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=provenance_module.production_provenance_resolver,
    )
    resolved = resolve_pre_model_source_handling(service.resolver()(document_id, cutoff))
    assert resolved.fact_record["id"] == outcome["records"]["FACT"]["record_id"]
    assert resolved.registry_record["id"] == outcome["records"]["FIELD_CATEGORY_REGISTRY"]["record_id"]
    assert resolved.decision["retention_decision"] == "ALLOW"
    assert resolved.decision["field_category_registry_id"] == f"registry:{document_id}:v1"
    expected_rule_id = bootstrap._expected_genesis_record_id(bootstrap._load_production_rule())
    assert resolved.authorization_rule["id"] == expected_rule_id


# --- idempotency -------------------------------------------------------------


def test_provisioning_exact_rerun_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database, key, updated_at = _prepare(tmp_path, monkeypatch)
    capsys.readouterr()
    _run_provisioning(database, key, _arguments(database, updated_at))
    first = json.loads(capsys.readouterr().out)
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()
        before_records = connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0]
        before_provenance = connection.execute("SELECT COUNT(*) FROM source_handling_provenance_records").fetchone()[0]

    capsys.readouterr()
    _run_provisioning(database, key, _arguments(database, updated_at, as_of=first["as_of"]))
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["status"] == "already-provisioned"
    assert rerun["as_of"] == first["as_of"]
    assert all(entry["status"] == "already-provisioned" for entry in rerun["records"].values())

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()
        after_records = connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0]
        after_provenance = connection.execute("SELECT COUNT(*) FROM source_handling_provenance_records").fetchone()[0]
    assert after == before
    assert all(row[3] == 1 for row in after)
    assert after_records == before_records == 4
    assert after_provenance == before_provenance == 6


# --- fail-closed mismatches --------------------------------------------------


def test_provisioning_mismatched_authority_content_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database, key, updated_at = _prepare(tmp_path, monkeypatch)
    capsys.readouterr()
    _run_provisioning(database, key, _arguments(database, updated_at))
    first = json.loads(capsys.readouterr().out)
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()

    with pytest.raises(SystemExit) as excinfo:
        _run_provisioning(
            database,
            key,
            _arguments(database, updated_at, sensitivity="RESTRICTED", as_of=first["as_of"]),
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "refusing to replace provisioned authority state" in err

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()
    assert after == before
    assert all(row[3] == 1 for row in after)


def test_provisioning_mismatched_authority_head_fails_before_any_provenance_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database, key, updated_at = _prepare(tmp_path, monkeypatch)

    capsys.readouterr()
    _run_provisioning(database, key, _arguments(database, updated_at))
    baseline = json.loads(capsys.readouterr().out)
    as_of = baseline["as_of"]

    # Model "existing mismatched authority head, no per-Issue provenance yet":
    # drop the per-Issue provenance records (append-only guards lifted only to
    # build this fixture; the next repository initialization re-creates them) so
    # the re-run sees existing authority state with zero provenance antecedents.
    with sqlite3.connect(database) as connection:
        for trigger in (
            "source_handling_provenance_no_delete",
            "source_handling_provenance_no_update",
            "source_handling_provenance_head_no_delete",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DELETE FROM source_handling_provenance_records")
        connection.execute("DELETE FROM source_handling_provenance_heads")
        before_keys = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()
        before_authority_records = connection.execute(
            "SELECT COUNT(*) FROM source_handling_authority_records"
        ).fetchone()[0]
        before_authorizations = connection.execute(
            "SELECT COUNT(*) FROM source_handling_publication_authorizations"
        ).fetchone()[0]

    with pytest.raises(SystemExit) as excinfo:
        _run_provisioning(
            database,
            key,
            _arguments(database, updated_at, sensitivity="RESTRICTED", as_of=as_of),
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "refusing to replace provisioned authority state" in err

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_handling_provenance_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_handling_provenance_heads").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0] == (
            before_authority_records
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM source_handling_publication_authorizations").fetchone()[0]
            == before_authorizations
        )
        after_keys = connection.execute(
            "SELECT family, scope, current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family != 'AUTHORIZATION_RULE' ORDER BY family"
        ).fetchall()
    assert after_keys == before_keys
    assert all(row[3] == 1 for row in after_keys)


def test_provisioning_refuses_as_of_predating_genesis_rule_or_issue_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database, key, _updated_at = _prepare(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        _run_provisioning(
            database,
            key,
            _arguments(database, "2026-08-13T12:00:00Z", as_of="2026-08-13T23:59:59Z"),
        )
    assert excinfo.value.code == 2
    assert "predates the genesis authorization rule" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        _run_provisioning(
            database,
            key,
            _arguments(database, "2026-08-14T06:00:00Z", as_of="2026-08-14T00:30:00Z"),
        )
    assert excinfo.value.code == 2
    assert "predates the Issue's updated_at" in capsys.readouterr().err

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0] == 1
        fact_rows = connection.execute(
            "SELECT COUNT(*) FROM source_handling_canonical_keys WHERE family != 'AUTHORIZATION_RULE'"
        ).fetchone()[0]
        assert fact_rows == 0
        provenance_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("source_handling_provenance_records",),
        ).fetchone()
        if provenance_table is not None:
            assert connection.execute("SELECT COUNT(*) FROM source_handling_provenance_records").fetchone()[0] == 0


def test_provisioning_requires_operator_environment_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    monkeypatch.delenv(provenance_module.EVIDENCE_DATABASE_ENV, raising=False)
    monkeypatch.delenv(provenance_module.VERIFICATION_KEY_ENV, raising=False)
    monkeypatch.delenv(provenance_module.VERIFICATION_KEY_SHA256_ENV, raising=False)
    monkeypatch.delenv(provenance_module.GENESIS_RULE_SHA256_ENV, raising=False)
    monkeypatch.setattr(provenance_module, "_production_view", None)

    with pytest.raises(SystemExit) as excinfo:
        _run_provisioning(database, key, _arguments(database, "2026-09-07T12:00:00Z"))
    assert excinfo.value.code == 2
    assert "must resolve to the exact provisioning database" in capsys.readouterr().err
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0] == 1


def test_provisioning_signing_key_sources_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    key_file = tmp_path / "source-handling-signing-key.hex"
    key_file.write_text(key.hex(), encoding="utf-8")
    saved = os.environ.get(provisioning.SIGNING_KEY_ENV)
    os.environ[provisioning.SIGNING_KEY_ENV] = key.hex()
    try:
        with pytest.raises(SystemExit) as excinfo:
            provisioning.main(_arguments(database, "2026-09-07T12:00:00Z") + ["--signing-key-file", str(key_file)])
        assert excinfo.value.code == 2
    finally:
        if saved is None:
            os.environ.pop(provisioning.SIGNING_KEY_ENV, None)
        else:
            os.environ[provisioning.SIGNING_KEY_ENV] = saved
    assert not database.exists()
