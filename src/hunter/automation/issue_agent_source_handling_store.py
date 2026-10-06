"""Source Handling authority on the GitHub-native anchored ledger (ADR 0038, amending ADR 0036 sections 4 and 9).

ADR 0036's persistence medium moves from Railway SQLite to the forward-only, ruleset-anchored branch
``refs/heads/hunter-state/v1/source-handling``. The authority itself does not move:
``SourceHandlingAuthorityService`` is still the sole publisher, ADR 0036's signed records and history
commitments are unchanged, and the canonical read view still re-verifies everything.

Write path. The control job materializes the verified ledger into an ephemeral database and runs the
canonical provisioning. A post-commit observer then captures **exactly one delta per ADR 0036
transaction**, and each delta becomes **exactly one** K_STATE-signed ledger commit, appended by
compare-and-swap. The provisioning is idempotent and resumes interrupted batches, so a lost CAS halfway
through a batch leaves only complete transactions.

Read path. Every ledger commit is verified before it is replayed:

- signature, domain, sequence, hash chain and trusted-run provenance;
- the delta's digest;
- the per-table mutation discipline;
- the resulting snapshot digest.

After replay, ``SqliteSourceHandlingAuthorityReadView`` re-verifies ADR 0036's own signed history. Any
failure is ``TAMPER_DETECTED``/``BLOCKED``, never a permissive default.

The ledger holds only the ADR 0036 tables' columns, which are classification metadata, decisions,
provenance, identities, digests, signatures and timestamps. Unknown tables or columns, deletions and
out-of-discipline mutations are refused.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_state import (
    POSITIVE,
    RECORDED_BY_VALIDATOR,
    SHA64,
    TIMESTAMP,
    GitLedgerStore,
    LedgerCorruptError,
    LedgerError,
    LedgerSchemaError,
    ProvenanceCheck,
    TrustRoots,
    _exact,
    _int,
    _object,
    _optional,
    canonical_json,
    record_digest,
    sha256_hex,
    sign_record,
    verify_record_signature,
)
from hunter.evidence_intelligence import source_handling_provenance
from hunter.evidence_intelligence.source_handling_commit_observer import observe_commits
from hunter.evidence_intelligence.source_handling_persistence import (
    SourceHandlingAuthorityRepository,
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
)

SOURCE_HANDLING_LEDGER_REF: Final = "refs/heads/hunter-state/v1/source-handling"
SOURCE_HANDLING_LEDGER_DOMAIN: Final = "hunter-source-handling-ledger-v1"
LEDGER_RECORD_SCHEMA_VERSION: Final = "hunter-source-handling-ledger-record-v1"
DELTA_SCHEMA_VERSION: Final = "hunter-source-handling-delta-v1"
SOURCE_HANDLING_WRITER_ROLES: Final = frozenset({"authorize", "source-handling-bootstrap"})
_FILES: Final = frozenset({"record.json", "delta.json"})
_MAX_DELTA_BYTES: Final = 1024 * 1024
_MAX_VALUE_BYTES: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class TableSpec:
    """One ADR 0036 table: exact columns, primary key and the only mutations it may undergo."""

    columns: tuple[str, ...]
    key: tuple[str, ...]
    mode: str  # "insert_only" | "consume_once" | "advance"
    mutable: tuple[str, ...] = ()


TABLES: Final[Mapping[str, TableSpec]] = {
    "source_handling_operator_root": TableSpec(
        ("singleton_id", "genesis_rule_sha256", "verification_key_sha256", "schema_version"),
        ("singleton_id",),
        "insert_only",
    ),
    "source_handling_publication_authorizations": TableSpec(
        ("authorization_id", "claims_json", "issuer_signature", "issued_at", "consumed_at", "consumed_record_id"),
        ("authorization_id",),
        "consume_once",
        ("consumed_at", "consumed_record_id"),
    ),
    "source_handling_authority_records": TableSpec(
        (
            "record_id",
            "family",
            "scope",
            "supersedes_record_id",
            "effective_from",
            "recorded_at",
            "known_at",
            "admission_time",
            "payload_sha256",
            "payload_json",
            "authorization_id",
            "schema_version",
            "integrity_signature",
        ),
        ("record_id",),
        "insert_only",
    ),
    "source_handling_canonical_keys": TableSpec(
        ("family", "scope", "current_record_id", "revision"),
        ("family", "scope"),
        "advance",
        ("current_record_id", "revision"),
    ),
    "source_handling_history_commitments": TableSpec(
        ("sequence", "commitment_sha256", "previous_commitment_sha256", "claims_json", "issuer_signature"),
        ("sequence",),
        "insert_only",
    ),
    "source_handling_provenance_records": TableSpec(
        (
            "record_id",
            "provenance_id",
            "provenance_kind",
            "evidence_strength",
            "evidence_method",
            "verifier_type",
            "authority_identity",
            "supersedes_record_id",
            "effective_from",
            "recorded_at",
            "known_at",
            "admission_time",
            "schema_version",
            "integrity_signature",
        ),
        ("record_id",),
        "insert_only",
    ),
    "source_handling_provenance_heads": TableSpec(
        ("provenance_id", "provenance_kind", "current_record_id", "revision", "integrity_signature"),
        ("provenance_id", "provenance_kind"),
        "advance",
        ("current_record_id", "revision", "integrity_signature"),
    ),
}
_AUTHORITY_FAMILIES: Final = frozenset({"FACT", "POLICY", "FIELD_CATEGORY_REGISTRY", "AUTHORIZATION_RULE"})

Row = dict[str, Any]
Snapshot = dict[str, dict[str, Row]]


class SourceHandlingLedgerError(LedgerCorruptError):
    """The anchored Source Handling ledger does not verify. Source Handling resolves ``BLOCKED``."""


def _row_key(spec: TableSpec, row: Mapping[str, Any]) -> str:
    return canonical_json([row[column] for column in spec.key]).decode()


def _check_row(table: str, row: object) -> Row:
    spec = TABLES[table]
    if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(spec.columns)):
        raise SourceHandlingLedgerError(f"{table}: row columns differ from the closed ADR 0036 schema")
    for column, value in row.items():
        if value is not None and type(value) not in (str, int):
            raise SourceHandlingLedgerError(f"{table}.{column}: only text, integers or null are stored")
        if isinstance(value, str) and len(value.encode("utf-8")) > _MAX_VALUE_BYTES:
            raise SourceHandlingLedgerError(f"{table}.{column}: value exceeds the size bound")
    if table == "source_handling_authority_records" and row["family"] not in _AUTHORITY_FAMILIES:
        raise SourceHandlingLedgerError("authority record family is outside ADR 0036")
    return row


def snapshot(database: Path) -> Snapshot:
    """Every ADR 0036 row, keyed by primary key. Absent tables (not yet created) are empty."""

    result: Snapshot = {table: {} for table in TABLES}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        existing = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, spec in TABLES.items():
            if table not in existing:
                continue
            columns = ", ".join(spec.columns)
            for raw in connection.execute(f"SELECT {columns} FROM {table}"):  # noqa: S608 - closed table names
                row = _check_row(table, dict(raw))
                result[table][_row_key(spec, row)] = row
    finally:
        connection.close()
    return result


def snapshot_digest(state: Snapshot) -> str:
    return sha256_hex(canonical_json({table: [state[table][key] for key in sorted(state[table])] for table in TABLES}))


def history_sequence(state: Snapshot) -> int:
    return max((int(row["sequence"]) for row in state["source_handling_history_commitments"].values()), default=0)


def diff(before: Snapshot, after: Snapshot) -> dict[str, list[Row]]:
    """The rows one transaction inserted or legally mutated. Deletions and other mutations are refused."""

    delta: dict[str, list[Row]] = {}
    for table, spec in TABLES.items():
        old, new = before[table], after[table]
        if set(old) - set(new):
            raise SourceHandlingLedgerError(f"{table}: rows were deleted")
        changed: list[Row] = []
        for key in sorted(new):
            row = new[key]
            previous = old.get(key)
            if previous is None:
                changed.append(row)
                continue
            if previous == row:
                continue
            _check_mutation(table, spec, previous, row)
            changed.append(row)
        if changed:
            delta[table] = changed
    return delta


def _check_mutation(table: str, spec: TableSpec, previous: Row, row: Row) -> None:
    frozen = [column for column in spec.columns if column not in spec.mutable]
    if spec.mode == "insert_only" or any(previous[column] != row[column] for column in frozen):
        raise SourceHandlingLedgerError(f"{table}: an append-only or frozen column changed")
    if spec.mode == "consume_once" and (previous["consumed_at"] is not None or row["consumed_at"] is None):
        raise SourceHandlingLedgerError(f"{table}: an authorization was consumed twice or un-consumed")
    if spec.mode == "advance" and row["revision"] != previous["revision"] + 1:
        raise SourceHandlingLedgerError(f"{table}: a head did not advance by exactly one revision")


def _encode_delta(delta: Mapping[str, list[Row]]) -> bytes:
    document = {"schema_version": DELTA_SCHEMA_VERSION, "tables": {table: delta[table] for table in sorted(delta)}}
    encoded = canonical_json(document)
    if len(encoded) > _MAX_DELTA_BYTES:
        raise SourceHandlingLedgerError("transaction delta exceeds the size bound")
    return encoded


def _decode_delta(encoded: bytes) -> dict[str, list[Row]]:
    try:
        document = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SourceHandlingLedgerError("delta is not canonical JSON") from None
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "tables"}
        or document["schema_version"] != DELTA_SCHEMA_VERSION
        or not isinstance(document["tables"], dict)
        or not document["tables"]
    ):
        raise SourceHandlingLedgerError("delta differs from the closed schema")
    if canonical_json(document) != encoded:
        raise SourceHandlingLedgerError("delta is not canonically encoded")
    tables: dict[str, list[Row]] = {}
    for table, rows in document["tables"].items():
        if table not in TABLES or not isinstance(rows, list) or not rows:
            raise SourceHandlingLedgerError("delta names an unknown table")
        tables[table] = [_check_row(table, row) for row in rows]
    return tables


_LEDGER_RECORD = _object(
    {
        "schema_version": _exact(LEDGER_RECORD_SCHEMA_VERSION),
        "record_seq": _int(0),
        "prev_record_sha256": _optional(SHA64),
        "recorded_at": TIMESTAMP,
        "recorded_by": RECORDED_BY_VALIDATOR,
        "delta_sha256": SHA64,
        "snapshot_sha256": SHA64,
        "history_sequence": _int(0),
        "repository_id": POSITIVE,
        "signature": lambda _value, _where: None,
    }
)


def _validate_ledger_record(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SourceHandlingLedgerError("ledger record must be an object")
    try:
        _LEDGER_RECORD(record, "source-handling record")
    except LedgerSchemaError as error:
        raise SourceHandlingLedgerError(f"schema: {error}") from None
    return record


@dataclass(slots=True)
class LedgerPosition:
    head: str | None = None
    next_seq: int = 0
    prev_digest: str | None = None
    snapshot_sha256: str | None = None


def _create_schema(
    database: Path, *, verification_public_key: bytes, operator_root: SourceHandlingOperatorRoot
) -> None:
    def never_sign(_payload: bytes) -> bytes:
        raise SourceHandlingBlockedError("a Source Handling read view never signs")

    def never_resolve(*_args: Any) -> Any:
        raise SourceHandlingBlockedError("a Source Handling replay never resolves provenance")

    SourceHandlingAuthorityRepository(
        database,
        verification_public_key=verification_public_key,
        operator_root=operator_root,
        record_integrity_signer=never_sign,
        provenance_resolver=never_resolve,
    )
    connection = sqlite3.connect(database)
    try:
        connection.executescript(source_handling_provenance._SCHEMA)
    finally:
        connection.close()


def _apply(database: Path, current: Snapshot, delta: Mapping[str, list[Row]]) -> Snapshot:
    """Replay one verified transaction delta under the per-table mutation discipline."""

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table, spec in TABLES.items():
            for row in delta.get(table, []):
                key = _row_key(spec, row)
                previous = current[table].get(key)
                if previous == row:
                    continue
                if previous is None:
                    columns = ", ".join(spec.columns)
                    marks = ", ".join("?" for _ in spec.columns)
                    connection.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({marks})",  # noqa: S608 - closed names
                        [row[column] for column in spec.columns],
                    )
                else:
                    _check_mutation(table, spec, previous, row)
                    assignments = ", ".join(f"{column} = ?" for column in spec.mutable)
                    predicate = " AND ".join(f"{column} = ?" for column in spec.key)
                    connection.execute(
                        f"UPDATE {table} SET {assignments} WHERE {predicate}",  # noqa: S608 - closed names
                        [row[column] for column in spec.mutable] + [row[column] for column in spec.key],
                    )
        connection.commit()
    except sqlite3.OperationalError:
        # A lock, full disk, or momentarily unopenable store is an operational/local
        # replay failure, never ledger corruption. Re-raise unmodified so the lifecycle
        # reports its sanitized re-dispatch refusal and no raw sqlite payload crosses a
        # bounded-refusal boundary.
        connection.rollback()
        raise
    except sqlite3.DatabaseError:
        connection.rollback()
        raise SourceHandlingLedgerError("delta does not replay onto the verified history") from None
    finally:
        connection.close()
    return snapshot(database)


def materialize(
    store: GitLedgerStore,
    database: Path,
    *,
    trust: TrustRoots,
    provenance: ProvenanceCheck,
    verification_public_key: bytes,
    operator_root: SourceHandlingOperatorRoot,
) -> LedgerPosition:
    """Replay the verified anchored ledger into an empty database. Any failure is ``TAMPER_DETECTED``."""

    if database.exists():
        raise SourceHandlingLedgerError("materialization requires an empty, ephemeral database")
    head, entries = store.read_files(SOURCE_HANDLING_LEDGER_REF, _FILES)
    if not entries:
        raise SourceHandlingBlockedError("the anchored Source Handling ledger is empty (not bootstrapped)")
    _create_schema(database, verification_public_key=verification_public_key, operator_root=operator_root)
    current = snapshot(database)
    position = LedgerPosition()
    for commit, files in entries:
        try:
            record = _validate_ledger_record(json.loads(files["record.json"]))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SourceHandlingLedgerError("ledger record is not JSON") from None
        verify_record_signature(record, trust.state_keys, domain=SOURCE_HANDLING_LEDGER_DOMAIN)
        if record["record_seq"] != position.next_seq or record["prev_record_sha256"] != position.prev_digest:
            raise SourceHandlingLedgerError("Source Handling ledger chain is broken")
        if record["repository_id"] != trust.repository_id:
            raise SourceHandlingLedgerError("Source Handling ledger record binds a foreign repository")
        if record["recorded_by"]["role"] not in SOURCE_HANDLING_WRITER_ROLES or not provenance(
            record["recorded_by"], record
        ):
            raise SourceHandlingLedgerError("Source Handling ledger record was not written by a trusted run")
        if sha256_hex(files["delta.json"]) != record["delta_sha256"]:
            raise SourceHandlingLedgerError("delta digest does not match its signed record")
        current = _apply(database, current, _decode_delta(files["delta.json"]))
        if (
            snapshot_digest(current) != record["snapshot_sha256"]
            or history_sequence(current) != record["history_sequence"]
        ):
            raise SourceHandlingLedgerError("replayed state differs from the signed snapshot")
        position = LedgerPosition(commit, position.next_seq + 1, record_digest(record), record["snapshot_sha256"])
    position.head = head
    return position


@dataclass(slots=True)
class CapturedTransaction:
    delta: dict[str, list[Row]]
    snapshot_sha256: str
    history_sequence: int


@dataclass(slots=True)
class TransactionCapture:
    """Post-commit observer: exactly one captured delta per committed ADR 0036 transaction."""

    database: Path
    before: Snapshot = field(init=False)
    transactions: list[CapturedTransaction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.before = snapshot(self.database) if self.database.exists() else {table: {} for table in TABLES}

    def __call__(self, path: Path) -> None:
        if path.resolve() != self.database.resolve():
            raise SourceHandlingLedgerError("a commit to an unexpected Source Handling database was observed")
        after = snapshot(path)
        delta = diff(self.before, after)
        if delta:
            self.transactions.append(CapturedTransaction(delta, snapshot_digest(after), history_sequence(after)))
        self.before = after


def capture(database: Path, operation: Callable[[], Any]) -> tuple[Any, list[CapturedTransaction]]:
    """Run a canonical Source Handling operation and capture each committed transaction."""

    observer = TransactionCapture(database)
    with observe_commits(observer):
        result = operation()
    return result, observer.transactions


def publish(
    store: GitLedgerStore,
    position: LedgerPosition,
    transactions: list[CapturedTransaction],
    *,
    signing_key: Ed25519PrivateKey,
    recorded_by: Mapping[str, Any],
    recorded_at: str,
    repository_id: int,
) -> LedgerPosition:
    """Append each captured transaction as exactly one signed ledger commit, by compare-and-swap."""

    for transaction in transactions:
        delta = _encode_delta(transaction.delta)
        record = sign_record(
            {
                "schema_version": LEDGER_RECORD_SCHEMA_VERSION,
                "record_seq": position.next_seq,
                "prev_record_sha256": position.prev_digest,
                "recorded_at": recorded_at,
                "recorded_by": dict(recorded_by),
                "delta_sha256": sha256_hex(delta),
                "snapshot_sha256": transaction.snapshot_sha256,
                "history_sequence": transaction.history_sequence,
                "repository_id": repository_id,
            },
            signing_key,
            domain=SOURCE_HANDLING_LEDGER_DOMAIN,
        )
        head = store.append_files(
            SOURCE_HANDLING_LEDGER_REF,
            position.head,
            {"record.json": canonical_json(record), "delta.json": delta},
            message=f"source-handling {record['record_seq']} history {transaction.history_sequence}",
            timestamp=recorded_at,
        )
        position = LedgerPosition(head, position.next_seq + 1, record_digest(record), transaction.snapshot_sha256)
    return position


def iter_rows(state: Snapshot) -> Iterator[tuple[str, Row]]:
    for table in TABLES:
        for key in sorted(state[table]):
            yield table, state[table][key]


__all__ = [
    "LedgerError",
    "SOURCE_HANDLING_LEDGER_REF",
    "SourceHandlingLedgerError",
    "TABLES",
    "capture",
    "diff",
    "materialize",
    "publish",
    "snapshot",
    "snapshot_digest",
]
