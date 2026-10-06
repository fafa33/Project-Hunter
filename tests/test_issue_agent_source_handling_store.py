"""ADR 0038: Source Handling authority on the anchored ledger (one ADR 0036 transaction = one commit)."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bootstrap_source_handling_authority as bootstrap
import hunter_issue_agent_provisioner as provisioner
import hunter_issue_agent_trigger as trigger
import provision_source_handling_issue_authority as provisioning
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from issue_agent_wire import issue_body_with_scope

from hunter.automation import issue_agent_source_handling_store as sh
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    ISSUE_AGENT_VERIFYING_KEY_ENV,
    OWNER_LOGIN_ENV,
    REPOSITORY_ENV,
    SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV,
    SignedIssueAgentAuthorization,
)
from hunter.evidence_intelligence import source_handling_provenance
from hunter.evidence_intelligence.source_handling_persistence import (
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
    SqliteSourceHandlingAuthorityReadView,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    GENESIS_RULE_SHA256_ENV as PROV_GENESIS_RULE_SHA256_ENV,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    VERIFICATION_KEY_ENV as PROV_VERIFICATION_KEY_ENV,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    VERIFICATION_KEY_SHA256_ENV as PROV_VERIFICATION_KEY_SHA256_ENV,
)

REPOSITORY = "fafa33/Project-Hunter"
OWNER = "fafa33"
STATE_KEY = Ed25519PrivateKey.generate()
TRUST = state.TrustRoots({state.public_key_id(STATE_KEY.public_key()): STATE_KEY.public_key()}, repository_id=1)
ISSUER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("33" * 32))
NOW = "2026-10-04T12:00:00Z"
UPDATED_AT = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def by(role: str, run_id: int = 100) -> dict[str, Any]:
    return {
        "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
        "job": role,
        "role": role,
        "run_id": run_id,
        "run_attempt": 1,
        "head_sha": "c" * 40,
    }


def trusted(recorded_by: Any, _record: Any) -> bool:
    return recorded_by["run_id"] in {100, 101, 102} and recorded_by["run_attempt"] == 1


@pytest.fixture(autouse=True)
def _isolated_provenance_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setattr(source_handling_provenance, "_production_view", None)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return build_world(tmp_path, monkeypatch)


def build_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A bare remote plus a real ADR 0036 operator environment (shared with the authorize tests)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    private_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    rule = bootstrap._load_production_rule()
    verification_hex, verification_sha256, genesis_sha256 = bootstrap._derived_digests(private_key, rule)
    issuer_hex = ISSUER_KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    for name, value in {
        provisioner._SOURCE_HANDLING_SIGNING_KEY_ENV: private_key.hex(),
        PROV_VERIFICATION_KEY_ENV: verification_hex,
        PROV_VERIFICATION_KEY_SHA256_ENV: verification_sha256,
        PROV_GENESIS_RULE_SHA256_ENV: genesis_sha256,
        REPOSITORY_ENV: REPOSITORY,
        OWNER_LOGIN_ENV: OWNER,
        ISSUE_AGENT_VERIFYING_KEY_ENV: issuer_hex,
        SOURCE_HANDLING_VERIFICATION_KEY_ENV: verification_hex,
        SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV: verification_sha256,
        SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV: genesis_sha256,
    }.items():
        monkeypatch.setenv(name, value)
    return {
        "tmp": tmp_path,
        "remote": str(remote),
        "private_key": private_key,
        "verification_key": bytes.fromhex(verification_hex),
        "operator_root": SourceHandlingOperatorRoot(
            genesis_rule_sha256=genesis_sha256, verification_key_sha256=verification_sha256
        ),
    }


def store(world: dict[str, Any], name: str) -> state.GitLedgerStore:
    return state.GitLedgerStore(world["remote"], workdir=world["tmp"] / f"git-{name}")


def bootstrap_ledger(world: dict[str, Any]) -> tuple[sh.LedgerPosition, int]:
    database = world["tmp"] / "bootstrap.sqlite"
    saved = os.environ.get(provisioning.SIGNING_KEY_ENV)
    os.environ[provisioning.SIGNING_KEY_ENV] = world["private_key"].hex()
    try:
        _, transactions = sh.capture(database, lambda: bootstrap.main(["--database", str(database), "--json"]))
    finally:
        if saved is None:
            os.environ.pop(provisioning.SIGNING_KEY_ENV, None)
        else:
            os.environ[provisioning.SIGNING_KEY_ENV] = saved
    position = sh.publish(
        store(world, "boot"),
        sh.LedgerPosition(),
        transactions,
        signing_key=STATE_KEY,
        recorded_by=by("source-handling-bootstrap"),
        recorded_at=NOW,
        repository_id=1,
    )
    return position, len(transactions)


def materialize(world: dict[str, Any], name: str, **overrides: Any) -> tuple[sh.LedgerPosition, Path]:
    database = world["tmp"] / f"{name}.sqlite"
    options = dict(
        trust=TRUST,
        provenance=trusted,
        verification_public_key=world["verification_key"],
        operator_root=world["operator_root"],
    )
    options.update(overrides)
    position = sh.materialize(store(world, name), database, **options)
    return position, database


def _replay_fault_connect(real_connect: Any, *, exception_type: type[sqlite3.Error], marker: str) -> Any:
    """Wrap `sqlite3.connect` so the very first `_apply` replay INSERT raises.

    `_apply` opens a fresh connection and, before any delta mutation, executes
    `PRAGMA foreign_keys = ON`, then `BEGIN IMMEDIATE`, then the INSERTs for rows
    that are not already present. Faulting the first connection that reaches that
    exact sequence deterministically targets replay in the materialize-first call
    order: `_create_schema` only runs the schema script and the authority writes in
    `source_handling_persistence` run after materialize, never inside it.
    """

    class _ReplayFault:
        def __init__(self, real: sqlite3.Connection) -> None:
            object.__setattr__(self, "_real", real)
            object.__setattr__(self, "_locked", False)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def __setattr__(self, name: str, value: Any) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
            else:
                setattr(self._real, name, value)

        def execute(self, sql: str, parameters: Any = (), *args: Any, **kwargs: Any) -> Any:
            statement = str(sql).lstrip()
            if statement.upper().startswith("BEGIN IMMEDIATE"):
                object.__setattr__(self, "_locked", True)
            elif self._locked and statement.upper().startswith("INSERT"):
                raise exception_type(marker)
            return self._real.execute(sql, parameters, *args, **kwargs)

    def faulted_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        return _ReplayFault(real_connect(database, *args, **kwargs))

    return faulted_connect


def test_a_replay_sqlite_operational_failure_is_operational_not_corruption(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `sqlite3.OperationalError` while replaying a verified delta (for example a
    database lock or a full filesystem) must propagate as the operational error the
    lifecycle turns into its sanitized re-dispatch refusal -- never be bagged as
    `TAMPER_DETECTED` ledger corruption carrying the raw sqlite payload."""
    bootstrap_ledger(world)
    marker = "hunter-replay-lock-marker"
    monkeypatch.setattr(
        sh.sqlite3,
        "connect",
        _replay_fault_connect(sh.sqlite3.connect, exception_type=sqlite3.OperationalError, marker=marker),
    )
    with pytest.raises(sqlite3.OperationalError) as operational:
        materialize(world, "victim")
    assert str(operational.value) == marker
    assert not isinstance(operational.value, state.LedgerCorruptError)


def test_a_replay_database_failure_stays_bounded_corruption_without_the_payload(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-operational replay `sqlite3.DatabaseError` is genuine ledger corruption and
    keeps the `SourceHandlingLedgerError` semantics, but the fixed message must not
    carry the raw sqlite payload across the bounded-corruption boundary."""
    bootstrap_ledger(world)
    marker = "hunter-integrity-marker"
    monkeypatch.setattr(
        sh.sqlite3,
        "connect",
        _replay_fault_connect(sh.sqlite3.connect, exception_type=sqlite3.IntegrityError, marker=marker),
    )
    with pytest.raises(sh.SourceHandlingLedgerError) as corruption:
        materialize(world, "victim")
    assert str(corruption.value) == "delta does not replay onto the verified history"
    assert marker not in str(corruption.value)


def signed_authorization(number: int = 497, body: str = "Provision authority automatically.") -> Any:
    event = {
        "action": "labeled",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "label": {"name": ISSUE_AGENT_AUTHORIZATION_LABEL},
        "issue": {
            "number": number,
            "state": "open",
            "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
            "title": "Canary",
            "body": issue_body_with_scope(body),
            "updated_at": UPDATED_AT,
        },
    }
    authorization = trigger.authorize_event(
        event, expected_repository=REPOSITORY, owner_login=OWNER, authorization_label=ISSUE_AGENT_AUTHORIZATION_LABEL
    )
    return SignedIssueAgentAuthorization.from_json(
        trigger.sign_authorization(authorization, signing_key=ISSUER_KEY).to_json()
    )


def provision_on(database: Path, monkeypatch: pytest.MonkeyPatch, signed: Any) -> list[sh.CapturedTransaction]:
    monkeypatch.setenv(EVIDENCE_DATABASE_ENV, str(database))
    monkeypatch.setattr(source_handling_provenance, "_production_view", None)
    configuration = provisioner.ProvisionerConfiguration.from_environment()
    _, transactions = sh.capture(database, lambda: provisioner.provision_issue_authority(configuration, signed))
    return transactions


def read_view(world: dict[str, Any], database: Path) -> SqliteSourceHandlingAuthorityReadView:
    return SqliteSourceHandlingAuthorityReadView(
        database,
        verification_public_key=world["verification_key"],
        operator_root=world["operator_root"],
        provenance_resolver=lambda *_args: None,
    )


# --- round trip and granularity ---------------------------------------------------------------------------


def test_bootstrap_and_provisioning_replicate_one_commit_per_transaction(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    position, bootstrap_transactions = bootstrap_ledger(world)
    assert bootstrap_transactions >= 1 and position.next_seq == bootstrap_transactions

    materialized, database = materialize(world, "writer")
    transactions = provision_on(database, monkeypatch, signed_authorization())
    assert len(transactions) >= 3, "FACT, registry, policy and provenance are separate ADR 0036 transactions"
    assert all(t.delta for t in transactions)
    final = sh.publish(
        store(world, "writer"),
        materialized,
        transactions,
        signing_key=STATE_KEY,
        recorded_by=by("authorize", 101),
        recorded_at=NOW,
        repository_id=1,
    )
    _, entries = store(world, "count").read_files(
        sh.SOURCE_HANDLING_LEDGER_REF, frozenset({"record.json", "delta.json"})
    )
    assert len(entries) == bootstrap_transactions + len(transactions) == final.next_seq

    replayed, replica = materialize(world, "reader")
    assert sh.snapshot_digest(sh.snapshot(replica)) == sh.snapshot_digest(sh.snapshot(database))
    assert replayed.snapshot_sha256 == final.snapshot_sha256
    read_view(world, replica)  # ADR 0036's own signed-history verification accepts the replica


def test_idempotent_reprovisioning_writes_no_transaction(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    position, database = materialize(world, "first")
    signed = signed_authorization()
    sh.publish(
        store(world, "first"),
        position,
        provision_on(database, monkeypatch, signed),
        signing_key=STATE_KEY,
        recorded_by=by("authorize"),
        recorded_at=NOW,
        repository_id=1,
    )
    _, again = materialize(world, "second")
    assert provision_on(again, monkeypatch, signed) == []


def test_an_empty_ledger_is_blocked(world: dict[str, Any]) -> None:
    with pytest.raises(SourceHandlingBlockedError, match="not bootstrapped"):
        materialize(world, "empty")


# --- adversarial ledger writers -------------------------------------------------------------------------


def _append_crafted(world: dict[str, Any], record: dict[str, Any], delta: bytes) -> None:
    writer = store(world, "attacker")
    writer.append_files(
        sh.SOURCE_HANDLING_LEDGER_REF,
        writer.ref_head(sh.SOURCE_HANDLING_LEDGER_REF),
        {"record.json": state.canonical_json(record), "delta.json": delta},
        message="crafted",
        timestamp=NOW,
    )


def _next_record(
    world: dict[str, Any], delta: bytes, snapshot_sha: str, history: int, **overrides: Any
) -> dict[str, Any]:
    position, _ = materialize(world, "probe")
    unsigned = {
        "schema_version": sh.LEDGER_RECORD_SCHEMA_VERSION,
        "record_seq": position.next_seq,
        "prev_record_sha256": position.prev_digest,
        "recorded_at": NOW,
        "recorded_by": by("authorize", 101),
        "delta_sha256": state.sha256_hex(delta),
        "snapshot_sha256": snapshot_sha,
        "history_sequence": history,
        "repository_id": 1,
    }
    key = overrides.pop("key", STATE_KEY)
    domain = overrides.pop("domain", sh.SOURCE_HANDLING_LEDGER_DOMAIN)
    unsigned.update(overrides)
    return state.sign_record(unsigned, key, domain=domain)


def _a_real_delta(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, str, int, Path]:
    bootstrap_ledger(world)
    _, database = materialize(world, "victim")
    transactions = provision_on(database, monkeypatch, signed_authorization())
    first = transactions[0]
    return sh._encode_delta(first.delta), first.snapshot_sha256, first.history_sequence, database


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"key": Ed25519PrivateKey.generate()}, "not pinned"),
        ({"domain": state.STATE_SIGNATURE_DOMAIN}, "another ledger domain"),
        ({"recorded_by": by("authorize", 999)}, "trusted run"),
        ({"recorded_by": by("bind", 101)}, "trusted run"),
        ({"snapshot_sha256": "0" * 64}, "signed snapshot"),
        ({"history_sequence": 999}, "signed snapshot"),
        ({"repository_id": 2}, "foreign repository"),
        ({"record_seq": 99}, "chain"),
    ],
)
def test_untrusted_or_inconsistent_ledger_records_are_tamper_detected(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], message: str
) -> None:
    delta, snapshot_sha, history, _ = _a_real_delta(world, monkeypatch)
    _append_crafted(world, _next_record(world, delta, snapshot_sha, history, **overrides), delta)
    with pytest.raises((sh.SourceHandlingLedgerError, state.LedgerCorruptError), match=message):
        materialize(world, "reader")


def test_a_delta_swapped_after_signing_is_detected(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    delta, snapshot_sha, history, _ = _a_real_delta(world, monkeypatch)
    record = _next_record(world, delta, snapshot_sha, history)
    tampered = json.loads(delta)
    first_table = next(iter(tampered["tables"]))
    tampered["tables"][first_table][0] = copy.deepcopy(tampered["tables"][first_table][0])
    _append_crafted(world, record, state.canonical_json(tampered) + b" ")
    with pytest.raises(sh.SourceHandlingLedgerError, match="digest"):
        materialize(world, "reader")


def _crafted_delta(table: str, rows: list[dict[str, Any]]) -> bytes:
    return state.canonical_json({"schema_version": sh.DELTA_SCHEMA_VERSION, "tables": {table: rows}})


def test_mutating_an_append_only_row_is_refused(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_ledger(world)
    _, database = materialize(world, "probe0")
    commitment = next(iter(sh.snapshot(database)["source_handling_history_commitments"].values()))
    forged = _crafted_delta("source_handling_history_commitments", [{**commitment, "claims_json": "{}"}])
    _append_crafted(world, _next_record(world, forged, "0" * 64, 1), forged)
    with pytest.raises(sh.SourceHandlingLedgerError, match="append-only or frozen"):
        materialize(world, "reader")


def test_unknown_tables_columns_and_families_are_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    for delta in (
        _crafted_delta("evidence_documents", [{"x": 1}]),
        state.canonical_json(
            {
                "schema_version": sh.DELTA_SCHEMA_VERSION,
                "tables": {"source_handling_operator_root": [{"singleton_id": "SOURCE_HANDLING", "note": "x"}]},
            }
        ),
    ):
        with pytest.raises(sh.SourceHandlingLedgerError):
            sh._decode_delta(delta)
    _, database = materialize(world, "probe1")
    record = next(iter(sh.snapshot(database)["source_handling_authority_records"].values()))
    with pytest.raises(sh.SourceHandlingLedgerError, match="family"):
        sh._decode_delta(_crafted_delta("source_handling_authority_records", [{**record, "family": "ISSUE_BODY"}]))


def test_deletions_and_double_consumption_are_refused() -> None:
    spec_rows = {table: {} for table in sh.TABLES}
    before = copy.deepcopy(spec_rows)
    before["source_handling_history_commitments"]["[1]"] = {
        "sequence": 1,
        "commitment_sha256": "a",
        "previous_commitment_sha256": None,
        "claims_json": "{}",
        "issuer_signature": "s",
    }
    with pytest.raises(sh.SourceHandlingLedgerError, match="deleted"):
        sh.diff(before, copy.deepcopy(spec_rows))
    auth = {
        "authorization_id": "x",
        "claims_json": "{}",
        "issuer_signature": "s",
        "issued_at": NOW,
        "consumed_at": NOW,
        "consumed_record_id": "r1",
    }
    consumed = copy.deepcopy(spec_rows)
    consumed["source_handling_publication_authorizations"]['["x"]'] = auth
    twice = copy.deepcopy(consumed)
    twice["source_handling_publication_authorizations"]['["x"]'] = {**auth, "consumed_record_id": "r2"}
    with pytest.raises(sh.SourceHandlingLedgerError, match="consumed twice"):
        sh.diff(consumed, twice)


def test_a_compromised_state_key_still_cannot_forge_authority(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: ledger signatures are K_STATE; ADR 0036 records stay K_SH-signed and re-verified."""

    bootstrap_ledger(world)
    position, database = materialize(world, "victim")
    transactions = provision_on(database, monkeypatch, signed_authorization())
    table = "source_handling_authority_records"
    index = next(i for i, t in enumerate(transactions) if table in t.delta)
    position = sh.publish(  # honest prefix up to the first authority-record transaction
        store(world, "honest"),
        position,
        transactions[:index],
        signing_key=STATE_KEY,
        recorded_by=by("authorize", 101),
        recorded_at=NOW,
        repository_id=1,
    )
    forged_delta = copy.deepcopy(transactions[index].delta)
    forged_delta[table][0] = {**forged_delta[table][0], "payload_json": json.dumps({"forged": True})}
    forged = sh._encode_delta(forged_delta)
    _, replica = materialize(world, "pre")
    replayed = sh._apply(replica, sh.snapshot(replica), sh._decode_delta(forged))
    record = _next_record(world, forged, sh.snapshot_digest(replayed), sh.history_sequence(replayed))
    _append_crafted(world, record, forged)
    _, attacked = materialize(world, "attacked")  # the K_STATE ledger layer cannot tell ...
    with pytest.raises(SourceHandlingBlockedError):  # ... but ADR 0036's K_SH-signed history must
        read_view(world, attacked)


def test_concurrent_publishers_at_one_position_have_exactly_one_winner(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    position, database = materialize(world, "a")
    transactions = provision_on(database, monkeypatch, signed_authorization(497))
    _, database_b = materialize(world, "b")
    transactions_b = provision_on(database_b, monkeypatch, signed_authorization(498, "Another Issue."))
    sh.publish(
        store(world, "a"),
        position,
        transactions,
        signing_key=STATE_KEY,
        recorded_by=by("authorize"),
        recorded_at=NOW,
        repository_id=1,
    )
    with pytest.raises(state.LedgerConflictError):
        sh.publish(
            store(world, "b"),
            position,
            transactions_b,
            signing_key=STATE_KEY,
            recorded_by=by("authorize", 101),
            recorded_at=NOW,
            repository_id=1,
        )


def test_a_head_must_advance_by_exactly_one_revision() -> None:
    empty: dict[str, dict[str, Any]] = {table: {} for table in sh.TABLES}
    head = {"family": "FACT", "scope": "s", "current_record_id": "r1", "revision": 1}
    before = copy.deepcopy(empty)
    before["source_handling_canonical_keys"]['["FACT","s"]'] = head
    skipped = copy.deepcopy(before)
    skipped["source_handling_canonical_keys"]['["FACT","s"]'] = {**head, "current_record_id": "r3", "revision": 3}
    with pytest.raises(sh.SourceHandlingLedgerError, match="exactly one revision"):
        sh.diff(before, skipped)
    advanced = copy.deepcopy(before)
    advanced["source_handling_canonical_keys"]['["FACT","s"]'] = {**head, "current_record_id": "r2", "revision": 2}
    assert sh.diff(before, advanced) == {
        "source_handling_canonical_keys": [advanced["source_handling_canonical_keys"]['["FACT","s"]']]
    }
