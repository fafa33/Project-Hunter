from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bootstrap_source_handling_authority as bootstrap
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_execution import ISSUE_AGENT_VERIFYING_KEY_ENV
from hunter.evidence_intelligence.source_handling import (
    SourceHandlingBlockedError,
    resolve_canonical_head,
)
from hunter.evidence_intelligence.source_handling_persistence import (
    SOURCE_HANDLING_RULE_SCOPE,
    SourceHandlingAuthorityService,
    SourceHandlingOperatorRoot,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    SourceHandlingProvenanceAuthorityRepository,
    SourceHandlingProvenanceView,
)

CONFIG_RULE = Path("config/source_handling/authorization_rule_v1.json")
FIXTURE_RULE = Path("tests/fixtures/source_handling/authorization_rule_v1.json")
RULE_GOLDEN = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"

START = datetime.now(UTC) + timedelta(days=1)


class MutableClock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


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


def _operator_root(private_key: bytes) -> SourceHandlingOperatorRoot:
    return SourceHandlingOperatorRoot(
        genesis_rule_sha256=RULE_GOLDEN,
        verification_key_sha256=_verification_key_sha256(private_key),
    )


def _signing_key_hex(private_key: bytes) -> str:
    return private_key.hex()


def _provenance(provenance_id: str, provenance_kind: str, cutoff: datetime) -> dict[str, Any] | None:
    known = cutoff - timedelta(days=1)
    base = {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "effective_from": known,
        "recorded_at": known,
        "known_at": known,
    }
    if provenance_kind == "EVIDENCE" and provenance_id.startswith("evidence:"):
        return {
            **base,
            "evidence_strength": "AUTHORITATIVE_SOURCE_EVIDENCE",
            "evidence_method": "SOURCE_TERMS_VERIFIED",
        }
    if provenance_kind == "VERIFIER" and provenance_id.startswith("verifier:"):
        return {**base, "verifier_type": "SOURCE_VERIFIER"}
    return None


def _bootstrap(
    database: Path,
    private_key: bytes,
) -> None:
    saved = os.environ.get(bootstrap.SIGNING_KEY_ENV)
    os.environ[bootstrap.SIGNING_KEY_ENV] = _signing_key_hex(private_key)
    try:
        bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(bootstrap.SIGNING_KEY_ENV, None)
        else:
            os.environ[bootstrap.SIGNING_KEY_ENV] = saved


def _advance_rule_chain(database: Path, private_key: bytes) -> str:
    """Publish a genuine successor AUTHORIZATION_RULE so the genesis is no longer the head."""
    clock = MutableClock(START + timedelta(days=1))
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=private_key,
        operator_root=_operator_root(private_key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    genesis_id = service.resolver()("advance", clock.now()).store.current_canonical_head_id(
        "AUTHORIZATION_RULE",
        SOURCE_HANDLING_RULE_SCOPE,
    )
    assert genesis_id is not None
    rule = json.loads(CONFIG_RULE.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        **rule,
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": SOURCE_HANDLING_RULE_SCOPE,
        "supersedes_authorization_rule_id": genesis_id,
        "effective_from": clock.now(),
        "recorded_at": clock.now(),
        "known_at": clock.now(),
    }
    authorization = service.issue_authorization(
        publication_kind="AUTHORIZATION_RULE",
        governed_subject_scope=SOURCE_HANDLING_RULE_SCOPE,
        payload=payload,
        authorization_rule_id=genesis_id,
        expected_current_head_id=genesis_id,
        evidence_ids=("evidence:rule:AUTHORIZATION_RULE_V2",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=("verifier:rule:AUTHORIZATION_RULE_V2",),
        verifier_type="SOURCE_VERIFIER",
        effective_from=clock.now(),
        recorded_at=clock.now(),
        known_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        authorization_id="auth:rule:AUTHORIZATION_RULE_V2",
    )
    service.publish(
        family="AUTHORIZATION_RULE",
        scope=SOURCE_HANDLING_RULE_SCOPE,
        expected_current_head_id=genesis_id,
        payload=payload,
        authorization=authorization,
    )
    new_head = service.resolver()("advance-check", clock.now()).store.current_canonical_head_id(
        "AUTHORIZATION_RULE",
        SOURCE_HANDLING_RULE_SCOPE,
    )
    assert new_head is not None and new_head != genesis_id
    return genesis_id


# --- production rule identity -------------------------------------------------


def test_production_rule_canonical_digest_matches_the_pinned_golden() -> None:
    payload = json.loads(CONFIG_RULE.read_text(encoding="utf-8"))
    assert _canonical_sha256(payload) == RULE_GOLDEN
    assert RULE_GOLDEN == bootstrap.PINNED_PRODUCTION_RULE_SHA256
    assert payload["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"


def test_production_rule_matches_the_test_fixture_content_and_lives_outside_tests() -> None:
    fixture = json.loads(FIXTURE_RULE.read_text(encoding="utf-8"))
    production = json.loads(CONFIG_RULE.read_text(encoding="utf-8"))
    assert _canonical_sha256(production) == _canonical_sha256(fixture)
    assert "config/source_handling/authorization_rule_v1.json" in str(bootstrap._DEFAULT_RULE)
    assert "tests/" not in str(bootstrap._DEFAULT_RULE)


def test_production_cli_accepts_no_rule_override(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main(["--database", str(database), "--rule", str(FIXTURE_RULE), "--json"])
    assert excinfo.value.code == 2
    assert not database.exists()


def test_missing_production_rule_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    monkeypatch.setattr(bootstrap, "_DEFAULT_RULE", tmp_path / "missing_rule.json")
    saved = os.environ.get(bootstrap.SIGNING_KEY_ENV)
    os.environ[bootstrap.SIGNING_KEY_ENV] = _signing_key_hex(key)
    try:
        with pytest.raises(SystemExit) as excinfo:
            bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(bootstrap.SIGNING_KEY_ENV, None)
        else:
            os.environ[bootstrap.SIGNING_KEY_ENV] = saved
    assert excinfo.value.code == 2
    assert not database.exists()


def test_malformed_production_rule_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    bad_rule = tmp_path / "malformed_rule.json"
    bad_rule.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_DEFAULT_RULE", bad_rule)
    saved = os.environ.get(bootstrap.SIGNING_KEY_ENV)
    os.environ[bootstrap.SIGNING_KEY_ENV] = _signing_key_hex(key)
    try:
        with pytest.raises(SystemExit) as excinfo:
            bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(bootstrap.SIGNING_KEY_ENV, None)
        else:
            os.environ[bootstrap.SIGNING_KEY_ENV] = saved
    assert excinfo.value.code == 2
    assert not database.exists()


def test_tampered_production_rule_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    tampered_rule = tmp_path / "tampered_rule.json"
    payload = json.loads(CONFIG_RULE.read_text(encoding="utf-8"))
    payload["authorization_rule_id"] = "AUTHORIZATION_RULE_TAMPERED"
    tampered_rule.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "_DEFAULT_RULE", tampered_rule)
    saved = os.environ.get(bootstrap.SIGNING_KEY_ENV)
    os.environ[bootstrap.SIGNING_KEY_ENV] = _signing_key_hex(key)
    try:
        with pytest.raises(SystemExit) as excinfo:
            bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(bootstrap.SIGNING_KEY_ENV, None)
        else:
            os.environ[bootstrap.SIGNING_KEY_ENV] = saved
    assert excinfo.value.code == 2
    assert "does not match the pinned canonical digest" in capsys.readouterr().err
    assert not database.exists()


# --- fresh database bootstrap -------------------------------------------------


def test_fresh_database_bootstrap_pins_root_and_publishes_genesis(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)

    with sqlite3.connect(database) as connection:
        root = connection.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()
        assert root is not None
        assert root[0] == RULE_GOLDEN
        assert root[1] == _verification_key_sha256(key)
        records = connection.execute("SELECT family, scope FROM source_handling_authority_records").fetchall()
        assert records == [("AUTHORIZATION_RULE", SOURCE_HANDLING_RULE_SCOPE)]

    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=lambda _id, _kind, _cutoff: None,
    )
    genesis = resolve_canonical_head(
        service.resolver()("doc-1", START).store,
        family="AUTHORIZATION_RULE",
        scope=SOURCE_HANDLING_RULE_SCOPE,
        cutoff=START,
    )
    production_payload = json.loads(bootstrap._DEFAULT_RULE.read_text(encoding="utf-8"))
    assert genesis["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"
    assert genesis["id"] == bootstrap._expected_genesis_record_id(production_payload)


def test_fresh_database_bootstrap_reports_derived_non_secret_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "bootstrapped"
    assert payload["operator_root"] == "pinned"
    assert payload[bootstrap.VERIFICATION_KEY_ENV] == _public_key_bytes(key).hex()
    assert payload[bootstrap.VERIFICATION_KEY_SHA256_ENV] == _verification_key_sha256(key)
    assert payload[bootstrap.GENESIS_RULE_SHA256_ENV] == RULE_GOLDEN
    assert payload["genesis_record_id"]


def test_signing_key_file_variant_bootstraps(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    key_file = tmp_path / "source-handling-signing-key.hex"
    key_file.write_text(_signing_key_hex(key), encoding="utf-8")
    bootstrap.main(["--database", str(database), "--signing-key-file", str(key_file), "--json"])
    with sqlite3.connect(database) as connection:
        root = connection.execute("SELECT verification_key_sha256 FROM source_handling_operator_root").fetchone()
        assert root is not None
        assert root[0] == _verification_key_sha256(key)


# --- idempotency --------------------------------------------------------------


def test_exact_rerun_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = ?",
            (SOURCE_HANDLING_RULE_SCOPE,),
        ).fetchone()

    outcome = bootstrap._run(
        str(database),
        key,
        json.loads(bootstrap._DEFAULT_RULE.read_text(encoding="utf-8")),
    )
    assert outcome["status"] == "already-provisioned"
    assert outcome["operator_root"] == "verified"
    assert outcome["genesis_record_id"] == before[0]

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT current_record_id, revision FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = ?",
            (SOURCE_HANDLING_RULE_SCOPE,),
        ).fetchone()
    assert after == before
    assert after[1] == 1


# --- fail-closed mismatches ---------------------------------------------------


def test_mismatched_signing_key_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    first = _private_key_bytes()
    _bootstrap(database, first)
    with sqlite3.connect(database) as connection:
        pinned = connection.execute(
            "SELECT verification_key_sha256, genesis_rule_sha256 FROM source_handling_operator_root"
        ).fetchone()
    other = _private_key_bytes()
    with pytest.raises(SystemExit) as excinfo:
        _bootstrap(database, other)
    assert excinfo.value.code == 2
    with sqlite3.connect(database) as connection:
        still = connection.execute(
            "SELECT verification_key_sha256, genesis_rule_sha256 FROM source_handling_operator_root"
        ).fetchone()
    assert still == pinned


def test_existing_foreign_root_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    with sqlite3.connect(database) as connection:
        original = connection.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()

    other_key = _private_key_bytes()
    with pytest.raises(SystemExit) as excinfo:
        _bootstrap(database, other_key)
    assert excinfo.value.code == 2
    with sqlite3.connect(database) as connection:
        unchanged = connection.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()
    assert unchanged == original


def test_mismatched_genesis_digest_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    foreign_digest = hashlib.sha256(b"foreign-authority-rule").hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_handling_operator_root SET genesis_rule_sha256 = ?",
            (foreign_digest,),
        )
        connection.commit()
        tampered = connection.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()

    with pytest.raises(SystemExit) as excinfo:
        _bootstrap(database, key)
    assert excinfo.value.code == 2
    with sqlite3.connect(database) as connection:
        unchanged = connection.execute(
            "SELECT genesis_rule_sha256, verification_key_sha256 FROM source_handling_operator_root"
        ).fetchone()
    assert unchanged == tampered


def test_bootstrap_refuses_to_replace_a_genesis_that_is_no_longer_the_head(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    genesis_id = _advance_rule_chain(database, key)

    with sqlite3.connect(database) as connection:
        head = connection.execute(
            "SELECT current_record_id FROM source_handling_canonical_keys "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = ?",
            (SOURCE_HANDLING_RULE_SCOPE,),
        ).fetchone()
    assert head is not None and head[0] != genesis_id

    with pytest.raises(SystemExit) as excinfo:
        _bootstrap(database, key)
    assert excinfo.value.code == 2


def test_missing_signing_key_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    saved = os.environ.get(bootstrap.SIGNING_KEY_ENV)
    os.environ.pop(bootstrap.SIGNING_KEY_ENV, None)
    try:
        with pytest.raises(SystemExit):
            bootstrap.main(["--database", str(database)])
    finally:
        if saved is not None:
            os.environ[bootstrap.SIGNING_KEY_ENV] = saved
    assert not database.exists()


# --- security boundary --------------------------------------------------------


def test_signing_key_never_appears_in_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)
    captured = capsys.readouterr()
    assert _signing_key_hex(key) not in captured.out
    assert _signing_key_hex(key) not in captured.err


def test_runtime_read_path_requires_no_signing_key_after_bootstrap(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite"
    key = _private_key_bytes()
    _bootstrap(database, key)

    provenance_service = SourceHandlingProvenanceAuthorityRepository(
        database,
        signing_private_key=key,
        operator_root=_operator_root(key),
        clock=MutableClock(START),
    )
    provenance_service.record_provenance(
        provenance_id="evidence:operation:1",
        provenance_kind="EVIDENCE",
        authority_identity="EVIDENCE_INTELLIGENCE_SOURCE_HANDLING_AUTHORITY",
        effective_from=START,
        recorded_at=START,
        known_at=START,
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
    )

    verification_public_key = _public_key_bytes(key)
    view = SourceHandlingProvenanceView(
        database,
        verification_public_key=verification_public_key,
        operator_root=SourceHandlingOperatorRoot(
            genesis_rule_sha256=RULE_GOLDEN,
            verification_key_sha256=hashlib.sha256(verification_public_key).hexdigest(),
        ),
    )
    cutoff = START + timedelta(days=30)
    record = view.resolve("evidence:operation:1", "EVIDENCE", cutoff)
    assert record is not None
    assert record["provenance_id"] == "evidence:operation:1"
    assert view.resolve("evidence:unprovisioned", "EVIDENCE", cutoff) is None

    fresh = tmp_path / "fresh.sqlite"
    fresh_key = _private_key_bytes()
    _bootstrap(fresh, fresh_key)
    bare = SourceHandlingProvenanceView(
        fresh,
        verification_public_key=_public_key_bytes(fresh_key),
        operator_root=_operator_root(fresh_key),
    )
    with pytest.raises(SourceHandlingBlockedError, match="provenance tables are unavailable"):
        bare.resolve("evidence:operation:1", "EVIDENCE", cutoff)


def test_issuer_runtime_environment_has_no_source_handling_signing_key() -> None:
    import hunter_issue_agent_issuer as issuer

    assert bootstrap.SIGNING_KEY_ENV not in issuer._REQUIRED_ENV
    assert ISSUE_AGENT_VERIFYING_KEY_ENV in issuer._REQUIRED_ENV
