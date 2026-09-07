from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_source_handling_production_runtime import (
    RULE_FIXTURE,
    MutableClock,
    _complete_authority,
    _operator_root,
    _private_key_bytes,
    _public_key_bytes,
)

import hunter.evidence_intelligence.source_handling_provenance as provenance_module
from hunter.evidence_intelligence.source_handling_persistence import (
    SourceHandlingAuthorityService,
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    EVIDENCE_DATABASE_ENV,
    GENESIS_RULE_SHA256_ENV,
    SOURCE_HANDLING_PROVENANCE_HEADS,
    SOURCE_HANDLING_PROVENANCE_RECORDS,
    VERIFICATION_KEY_ENV,
    VERIFICATION_KEY_SHA256_ENV,
    SourceHandlingProvenanceAuthorityRepository,
    SourceHandlingProvenanceView,
)

START = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _keypair() -> tuple[bytes, SourceHandlingOperatorRoot]:
    key = _private_key_bytes()
    return key, _operator_root(key)


def _provisioner(
    db: Path,
    key: bytes,
    root: SourceHandlingOperatorRoot,
    clock: MutableClock,
) -> SourceHandlingProvenanceAuthorityRepository:
    return SourceHandlingProvenanceAuthorityRepository(
        db,
        signing_private_key=key,
        operator_root=root,
        clock=clock,
    )


def _view(db: Path, key: bytes, root: SourceHandlingOperatorRoot) -> SourceHandlingProvenanceView:
    return SourceHandlingProvenanceView(
        db,
        verification_public_key=_public_key_bytes(key),
        operator_root=root,
    )


def _provisioned_database(
    tmp_path: Path, clock: MutableClock | None = None
) -> tuple[Path, bytes, SourceHandlingOperatorRoot, MutableClock]:
    active_clock = clock or MutableClock(START)
    db = tmp_path / "evidence.sqlite"
    key, root = _keypair()
    SourceHandlingAuthorityService(
        db,
        signing_private_key=key,
        operator_root=root,
        provenance_resolver=lambda *_args: None,
        clock=active_clock,
    )
    return db, key, root, active_clock


def _production_resolver(
    db: Path,
    key: bytes,
    root: SourceHandlingOperatorRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    monkeypatch.setenv(EVIDENCE_DATABASE_ENV, str(db))
    monkeypatch.setenv(VERIFICATION_KEY_ENV, _public_key_bytes(key).hex())
    monkeypatch.setenv(VERIFICATION_KEY_SHA256_ENV, root.verification_key_sha256)
    monkeypatch.setenv(GENESIS_RULE_SHA256_ENV, root.genesis_rule_sha256)
    provenance_module._production_view = None
    return provenance_module.production_provenance_resolver


def _evidence_args(*, identity: str, at: datetime, strength: str = "AUTHORITATIVE_SOURCE_EVIDENCE") -> dict[str, Any]:
    return {
        "provenance_id": identity,
        "provenance_kind": "EVIDENCE",
        "authority_identity": "test-operator",
        "effective_from": at,
        "recorded_at": at,
        "known_at": at,
        "evidence_strength": strength,
        "evidence_method": "SOURCE_TERMS_VERIFIED",
    }


def _verifier_args(*, identity: str, at: datetime, verifier_type: str = "SOURCE_VERIFIER") -> dict[str, Any]:
    return {
        "provenance_id": identity,
        "provenance_kind": "VERIFIER",
        "authority_identity": "test-operator",
        "effective_from": at,
        "recorded_at": at,
        "known_at": at,
        "verifier_type": verifier_type,
    }


def test_writer_requires_pinned_operator_root(tmp_path: Path) -> None:
    db = tmp_path / "evidence.sqlite"
    key, root = _keypair()
    provisioner = _provisioner(db, key, root, MutableClock(START))
    with pytest.raises(SourceHandlingBlockedError, match="operator root"):
        provisioner.record_provenance(**_evidence_args(identity="evidence:doc", at=START))


def test_writer_rejects_signing_key_not_matching_pinned_root(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    other_key, _ = _keypair()
    with pytest.raises(SourceHandlingBlockedError, match="pinned operator root"):
        _provisioner(db, other_key, root, clock)


def test_missing_provenance_tables_fail_closed(tmp_path: Path) -> None:
    db, key, root, _clock = _provisioned_database(tmp_path)
    view = _view(db, key, root)
    with pytest.raises(SourceHandlingBlockedError, match="provenance tables are unavailable"):
        view.resolve("evidence:doc", "EVIDENCE", START)


def test_record_resolve_and_unknown_identities(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    first = provisioner.record_provenance(**_evidence_args(identity="evidence:doc", at=START))
    second = provisioner.record_provenance(**_evidence_args(identity="evidence:doc", at=START))
    assert first == second
    view = _view(db, key, root)
    resolved = view.resolve("evidence:doc", "EVIDENCE", START)
    assert resolved is not None
    assert resolved["record_id"] == first
    assert resolved["provenance_id"] == "evidence:doc"
    assert resolved["provenance_kind"] == "EVIDENCE"
    assert resolved["evidence_strength"] == "AUTHORITATIVE_SOURCE_EVIDENCE"
    assert resolved["evidence_method"] == "SOURCE_TERMS_VERIFIED"
    assert view.resolve("evidence:doc", "EVIDENCE", START - timedelta(microseconds=1)) is None
    assert view.resolve("evidence:doc", "VERIFIER", START) is None
    assert view.resolve("evidence:unknown", "EVIDENCE", START) is None


def test_verifier_kind_shape_enforced(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_verifier_args(identity="verifier:doc", at=START))
    with pytest.raises(SourceHandlingBlockedError, match="must not name evidence"):
        provisioner.record_provenance(
            provenance_id="verifier:bad",
            provenance_kind="VERIFIER",
            authority_identity="test-operator",
            effective_from=START,
            recorded_at=START,
            known_at=START,
            verifier_type="SOURCE_VERIFIER",
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        )
    resolved = _view(db, key, root).resolve("verifier:doc", "VERIFIER", START)
    assert resolved is not None
    assert resolved["verifier_type"] == "SOURCE_VERIFIER"


def test_correction_supersedes_and_replays_by_cutoff(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(
        **_evidence_args(identity="evidence:doc", at=START, strength="AUTHORITATIVE_SOURCE_EVIDENCE")
    )
    correction_at = START + timedelta(minutes=5)
    clock.value = correction_at
    provisioner.record_provenance(
        **_evidence_args(identity="evidence:doc", at=correction_at, strength="INDEPENDENT_VERIFIED_EVIDENCE")
    )
    view = _view(db, key, root)
    before = view.resolve("evidence:doc", "EVIDENCE", correction_at - timedelta(microseconds=1))
    assert before is not None
    assert before["evidence_strength"] == "AUTHORITATIVE_SOURCE_EVIDENCE"
    after = view.resolve("evidence:doc", "EVIDENCE", correction_at)
    assert after is not None
    assert after["evidence_strength"] == "INDEPENDENT_VERIFIED_EVIDENCE"


def test_conflicting_same_known_at_rejected_at_write(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(
        **_evidence_args(identity="evidence:doc", at=START, strength="AUTHORITATIVE_SOURCE_EVIDENCE")
    )
    with pytest.raises(SourceHandlingBlockedError, match="strictly later"):
        provisioner.record_provenance(
            **_evidence_args(identity="evidence:doc", at=START, strength="INDEPENDENT_VERIFIED_EVIDENCE")
        )


def test_backdated_known_at_is_not_admitted_until_admission(tmp_path: Path) -> None:
    admission_instant = START + timedelta(minutes=30)
    db, key, root, clock = _provisioned_database(tmp_path, clock=MutableClock(admission_instant))
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_evidence_args(identity="evidence:backdated", at=START))
    view = _view(db, key, root)
    mid_cutoff = START + timedelta(minutes=10)
    assert view.resolve("evidence:backdated", "EVIDENCE", mid_cutoff) is None
    assert view.resolve("evidence:backdated", "EVIDENCE", admission_instant) is not None


def test_schema_is_idempotent_across_repository_instances(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    first = _provisioner(db, key, root, clock)
    first.record_provenance(**_evidence_args(identity="evidence:a", at=START))
    second = _provisioner(db, key, root, clock)
    second.record_provenance(**_evidence_args(identity="evidence:b", at=START))
    view = _view(db, key, root)
    assert view.resolve("evidence:a", "EVIDENCE", START) is not None
    assert view.resolve("evidence:b", "EVIDENCE", START + timedelta(microseconds=1)) is not None


def test_head_tamper_detected(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_evidence_args(identity="evidence:tamper", at=START))
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    connection.execute(
        f"UPDATE {SOURCE_HANDLING_PROVENANCE_HEADS} SET current_record_id = ? "
        "WHERE provenance_id = ? AND provenance_kind = 'EVIDENCE'",
        ("0" * 64, "evidence:tamper"),
    )
    connection.commit()
    connection.close()
    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        _view(db, key, root).resolve("evidence:tamper", "EVIDENCE", START)


def test_rogue_duplicate_genesis_detected(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_evidence_args(identity="evidence:rogue", at=START))
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    original = connection.execute(
        f"SELECT * FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} WHERE provenance_id = 'evidence:rogue'"
    ).fetchone()
    forged = dict(original)
    forged["record_id"] = "b" * 64
    forged["supersedes_record_id"] = None
    forged["known_at"] = START.isoformat()
    forged["integrity_signature"] = "dd" * 64
    connection.execute(
        f"INSERT INTO {SOURCE_HANDLING_PROVENANCE_RECORDS} ("
        "record_id, provenance_id, provenance_kind, evidence_strength, evidence_method, verifier_type, "
        "authority_identity, supersedes_record_id, effective_from, recorded_at, known_at, admission_time, "
        "schema_version, integrity_signature"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            forged["record_id"],
            forged["provenance_id"],
            forged["provenance_kind"],
            forged["evidence_strength"],
            forged["evidence_method"],
            forged["verifier_type"],
            forged["authority_identity"],
            forged["supersedes_record_id"],
            forged["effective_from"],
            forged["recorded_at"],
            forged["known_at"],
            forged["admission_time"],
            forged["schema_version"],
            forged["integrity_signature"],
        ),
    )
    connection.commit()
    connection.close()
    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        _view(db, key, root).resolve("evidence:rogue", "EVIDENCE", START)


def test_view_with_wrong_verification_key_fails_closed(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_evidence_args(identity="evidence:keyed", at=START))
    other_key, other_root = _keypair()
    wrong_view = SourceHandlingProvenanceView(
        db,
        verification_public_key=_public_key_bytes(other_key),
        operator_root=other_root,
    )
    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        wrong_view.resolve("evidence:keyed", "EVIDENCE", START)


def test_production_resolver_resolves_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(**_evidence_args(identity="evidence:prod", at=START))
    resolver = _production_resolver(db, key, root, monkeypatch)
    assert resolver("evidence:prod", "EVIDENCE", START)["provenance_id"] == "evidence:prod"
    assert resolver("evidence:prod", "EVIDENCE", START - timedelta(microseconds=1)) is None
    assert resolver("evidence:missing", "EVIDENCE", START) is None


def test_production_resolver_missing_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EVIDENCE_DATABASE_ENV, raising=False)
    monkeypatch.delenv(VERIFICATION_KEY_ENV, raising=False)
    monkeypatch.delenv(VERIFICATION_KEY_SHA256_ENV, raising=False)
    monkeypatch.delenv(GENESIS_RULE_SHA256_ENV, raising=False)
    provenance_module._production_view = None
    with pytest.raises(SourceHandlingBlockedError, match="operator configuration is incomplete"):
        provenance_module.production_provenance_resolver("evidence:doc", "EVIDENCE", START)


def test_full_authority_publication_with_real_production_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = MutableClock(START)
    key, root = _keypair()
    db = tmp_path / "evidence.sqlite"
    service = SourceHandlingAuthorityService(
        db,
        signing_private_key=key,
        operator_root=root,
        provenance_resolver=_production_resolver(db, key, root, monkeypatch),
        clock=clock,
    )
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    genesis = service.publish_genesis_rule(rule)
    if clock.value <= genesis.admission_time:
        clock.value = genesis.admission_time + timedelta(microseconds=1)
    provisioner = _provisioner(db, key, root, clock)
    for prefix in ("fact", "registry", "policy"):
        provisioner.record_provenance(**_evidence_args(identity=f"evidence:auth:{prefix}:doc-1", at=START))
        provisioner.record_provenance(**_verifier_args(identity=f"verifier:auth:{prefix}:doc-1", at=START))
    clock.value += timedelta(microseconds=60)
    record_ids = _complete_authority(service, clock, genesis.record_id, document_id="doc-1")
    assert set(record_ids) == {"fact", "registry", "policy", "rule"}
    resolved = provenance_module.production_provenance_resolver("evidence:auth:fact:doc-1", "EVIDENCE", clock.value)
    assert resolved is not None
    assert resolved["evidence_strength"] == "AUTHORITATIVE_SOURCE_EVIDENCE"


def test_authorization_fails_closed_without_provisioned_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = MutableClock(START)
    key, root = _keypair()
    db = tmp_path / "evidence.sqlite"
    service = SourceHandlingAuthorityService(
        db,
        signing_private_key=key,
        operator_root=root,
        provenance_resolver=_production_resolver(db, key, root, monkeypatch),
        clock=clock,
    )
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    genesis = service.publish_genesis_rule(rule)
    if clock.value <= genesis.admission_time:
        clock.value = genesis.admission_time + timedelta(microseconds=1)
    with pytest.raises(SourceHandlingBlockedError):
        _complete_authority(service, clock, genesis.record_id, document_id="doc-1")


def test_competing_corrections_from_same_predecessor_cannot_branch(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    provisioner = _provisioner(db, key, root, clock)
    provisioner.record_provenance(
        **_evidence_args(identity="evidence:race", at=START, strength="AUTHORITATIVE_SOURCE_EVIDENCE")
    )
    correction_at = START + timedelta(minutes=5)
    clock.value = correction_at
    worker_a = _provisioner(db, key, root, clock)
    worker_b = _provisioner(db, key, root, clock)

    barrier = threading.Barrier(3)
    outcomes: dict[str, tuple[str, str]] = {}

    def attempt(name: str, worker: SourceHandlingProvenanceAuthorityRepository, strength: str) -> None:
        try:
            barrier.wait(timeout=30)
            record_id = worker.record_provenance(
                **_evidence_args(identity="evidence:race", at=correction_at, strength=strength)
            )
            outcomes[name] = ("recorded", record_id)
        except SourceHandlingBlockedError as error:
            outcomes[name] = ("blocked", str(error))
        except Exception as error:  # noqa: BLE001 - surface unexpected failures so a silent pass is impossible
            outcomes[name] = ("unexpected", f"{type(error).__name__}: {error}")

    writers = [
        threading.Thread(target=attempt, args=("a", worker_a, "INDEPENDENT_VERIFIED_EVIDENCE")),
        threading.Thread(target=attempt, args=("b", worker_b, "REFUTED_BY_AUTHORITY_EVIDENCE")),
    ]
    for writer in writers:
        writer.start()
    barrier.wait(timeout=30)
    for writer in writers:
        writer.join(timeout=60)
        assert not writer.is_alive()

    assert any(outcome[0] == "recorded" for outcome in outcomes.values())

    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"SELECT record_id, supersedes_record_id FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} "
        "WHERE provenance_id = 'evidence:race' ORDER BY admission_time, record_id"
    ).fetchall()
    head = connection.execute(
        f"SELECT current_record_id, revision FROM {SOURCE_HANDLING_PROVENANCE_HEADS} "
        "WHERE provenance_id = 'evidence:race' AND provenance_kind = 'EVIDENCE'"
    ).fetchone()
    connection.close()

    assert len(rows) >= 2
    genesis = rows[0]
    assert genesis["supersedes_record_id"] is None
    direct_successors = [row for row in rows[1:] if row["supersedes_record_id"] == genesis["record_id"]]
    assert len(direct_successors) == 1, "two competing corrections were admitted against the same predecessor"
    for previous, record in zip(rows, rows[1:], strict=False):
        assert record["supersedes_record_id"] == previous["record_id"]
    assert head is not None
    assert int(head["revision"]) == len(rows)
    assert str(head["current_record_id"]) == rows[-1]["record_id"]
    resolved = _view(db, key, root).resolve("evidence:race", "EVIDENCE", correction_at + timedelta(days=1))
    assert resolved is not None
    assert resolved["record_id"] == rows[-1]["record_id"]


def test_write_lock_is_held_for_the_entire_protected_sequence(tmp_path: Path) -> None:
    db, key, root, clock = _provisioned_database(tmp_path)
    worker_a = _provisioner(db, key, root, clock)
    worker_a.record_provenance(**_evidence_args(identity="evidence:lock", at=START))
    correction_at = START + timedelta(minutes=5)
    clock.value = correction_at

    submitted = threading.Event()
    completed = threading.Event()
    outcomes: dict[str, str] = {}

    def competitor() -> None:
        try:
            submitted.set()
            worker_b = _provisioner(db, key, root, clock)
            worker_b.record_provenance(
                **_evidence_args(identity="evidence:lock", at=correction_at, strength="INDEPENDENT_VERIFIED_EVIDENCE")
            )
            outcomes["result"] = "recorded"
        except SourceHandlingBlockedError as error:
            outcomes["result"] = str(error)
        except Exception as error:  # noqa: BLE001 - surface unexpected failures so a silent pass is impossible
            outcomes["result"] = f"unexpected: {type(error).__name__}: {error}"
        finally:
            completed.set()

    with worker_a._transaction() as connection:
        locked = connection.execute(
            "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
        ).fetchone()
        assert locked is not None
        writer = threading.Thread(target=competitor)
        writer.start()
        assert submitted.wait(timeout=5)
        assert not completed.wait(timeout=0.5), "a second writer interleaved while the first transaction was active"
    writer.join(timeout=30)
    assert not writer.is_alive()
    assert completed.is_set()
    assert outcomes["result"] != ""

    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"SELECT record_id, supersedes_record_id FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} "
        "WHERE provenance_id = 'evidence:lock' ORDER BY admission_time, record_id"
    ).fetchall()
    head = connection.execute(
        f"SELECT current_record_id, revision FROM {SOURCE_HANDLING_PROVENANCE_HEADS} "
        "WHERE provenance_id = 'evidence:lock' AND provenance_kind = 'EVIDENCE'"
    ).fetchone()
    connection.close()

    genesis = rows[0]
    assert genesis["supersedes_record_id"] is None
    direct_successors = [row for row in rows[1:] if row["supersedes_record_id"] == genesis["record_id"]]
    assert len(direct_successors) == 1, "two competing corrections were admitted against the same predecessor"
    for previous, record in zip(rows, rows[1:], strict=False):
        assert record["supersedes_record_id"] == previous["record_id"]
    assert head is not None
    assert int(head["revision"]) == len(rows)
    assert str(head["current_record_id"]) == rows[-1]["record_id"]
