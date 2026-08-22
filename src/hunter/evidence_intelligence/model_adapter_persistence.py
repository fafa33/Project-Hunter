"""ADR 0034 Phase A — mechanical durability for Model Adapter pre-dispatch records.

ADR 0009 separation is the point of this module: it stores and reads, and it
decides nothing. It does not resolve Source Handling, does not judge whether
processing is authorized, does not assess capability compatibility, does not
decide request-evidence eligibility, and does not authorize retries. Those are
`ModelAdapterService` responsibilities.

The two guarantees this layer *does* own are storage guarantees:

* an attempt and its handoff commit in one transaction, so a dispatch-capable
  handoff can never exist without its durable attempt; and
* a handoff is consumable at most once, enforced by a conditional compare-and-set
  inside an immediate transaction rather than by a read-then-write race.

Histories are append-only. Records are never updated into a different state; the
sole mutation in the schema is the one-way `consumed_at` claim that implements
single-use consumption.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from hunter.evidence_intelligence.model_adapter import (
    ModelAttemptRecord,
    ModelHandoffRecord,
    ProviderRequestEvidence,
    disposition_identity,
    permitted_request_evidence_state,
)
from hunter.evidence_intelligence.pre_model import (
    EvidencePreModelSourceHandlingAuthority,
    resolve_pre_model_source_handling,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError


class ModelAdapterPersistenceError(RuntimeError):
    """Base class for Model Adapter persistence failures."""


class ModelAdapterPersistenceConflict(ModelAdapterPersistenceError):
    """Raised when a deterministic identity is reused with different bytes."""


class ModelAdapterPersistenceCorruption(ModelAdapterPersistenceError):
    """Raised when a persisted payload no longer matches its recorded hash."""


class ModelAdapterDirectWriteForbidden(ModelAdapterPersistenceError):
    """Raised when a caller tries to write an authoritative record directly.

    Mirrors `AuthorityStore.direct_write`: the repository is not a public bypass
    around Model Adapter authority.
    """


class ModelAdapterAuthorityMismatch(ModelAdapterPersistenceError):
    """Raised when a supplied record does not match independently rederived authority.

    ADR 0033 requires persistence to resolve the authoritative historical facts and
    policy itself, rederive every relevant handling decision, verify the durable
    payload against those decisions, and reject mismatches. That is verification,
    not decision-making: this layer never chooses an outcome, it recomputes the
    one the authority already produced and refuses anything that disagrees.
    """


class ModelAdapterPersistenceRepository:
    """Append-only durability for Phase A Model Adapter pre-dispatch evidence."""

    def __init__(self, evidence_repository: EvidenceIntelligenceRepository) -> None:
        self.path = evidence_repository.path
        self._initialize()

    # -- authority boundary -------------------------------------------------

    def direct_write(self, *, table: str, record: Mapping[str, Any]) -> None:
        """Refuse authoritative writes that did not pass through the adapter."""
        del table, record
        raise ModelAdapterDirectWriteForbidden(
            "direct Model Adapter record writes are forbidden; use ModelAdapterService"
        )

    # -- durable-before-send ------------------------------------------------

    def persist_attempt_and_handoff(
        self,
        *,
        attempt: ModelAttemptRecord,
        handoff: ModelHandoffRecord,
        request_evidence: ProviderRequestEvidence,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
    ) -> None:
        """Commit the attempt and its handoff atomically, attempt first.

        The authority is re-resolved here and the handoff's bound identities are
        verified against it, so this method cannot be used as a public bypass to
        fabricate an authoritative record: forged Source Handling identities are
        rejected even when the caller never went through `ModelAdapterService`.

        Re-persisting identical bytes is idempotent; re-using an identity with
        different bytes is a conflict rather than a silent overwrite.
        """
        if handoff.attempt_id != attempt.attempt_id:
            raise ModelAdapterPersistenceError("handoff does not belong to the supplied attempt")
        if handoff.request_evidence_identity != attempt.request_evidence_identity:
            raise ModelAdapterPersistenceError("handoff request-evidence lineage does not match the attempt")
        if handoff.execution_profile_identity != attempt.execution_profile_identity:
            raise ModelAdapterPersistenceError("handoff profile lineage does not match the attempt")
        if request_evidence.request_evidence_identity != attempt.request_evidence_identity:
            raise ModelAdapterPersistenceError("request evidence does not match the attempt's recorded identity")

        attempt_payload = _canonical_json(_jsonable(asdict(attempt)))
        handoff_payload = _canonical_json(
            _jsonable(
                {
                    "handoff": asdict(handoff),
                    "request_evidence": asdict(request_evidence),
                }
            )
        )
        attempt_hash = _sha256(attempt_payload)
        handoff_hash = _sha256(handoff_payload)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Re-resolved *inside* the write transaction, so the window between
            # verifying authority and committing the handoff is the transaction
            # itself rather than an unbounded gap. See the residual-window note on
            # `_verify_against_rederived_authority`.
            self._verify_against_rederived_authority(
                attempt=attempt,
                handoff=handoff,
                request_evidence=request_evidence,
                attempt_authority=attempt_authority,
            )
            existing_attempt = connection.execute(
                "SELECT payload_hash FROM model_attempt_records WHERE attempt_id = ?",
                (attempt.attempt_id,),
            ).fetchone()
            if existing_attempt is None:
                connection.execute(
                    """
                    INSERT INTO model_attempt_records (
                        attempt_id, recorded_at, attempt_cutoff, payload_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.attempt_id,
                        attempt.recorded_at.astimezone(UTC).isoformat(),
                        attempt.attempt_cutoff.astimezone(UTC).isoformat(),
                        attempt_hash,
                        attempt_payload,
                    ),
                )
            elif str(existing_attempt["payload_hash"]) != attempt_hash:
                raise ModelAdapterPersistenceConflict(f"conflicting payload for existing attempt {attempt.attempt_id}")

            existing_for_attempt = connection.execute(
                "SELECT handoff_id, payload_hash FROM model_handoff_records WHERE attempt_id = ?",
                (attempt.attempt_id,),
            ).fetchone()
            if existing_for_attempt is not None and str(existing_for_attempt["handoff_id"]) != handoff.handoff_id:
                raise ModelAdapterPersistenceConflict(
                    f"attempt {attempt.attempt_id} already has dispatch authorization "
                    f"{existing_for_attempt['handoff_id']}; an attempt may authorize at most one dispatch"
                )

            existing_handoff = connection.execute(
                "SELECT payload_hash FROM model_handoff_records WHERE handoff_id = ?",
                (handoff.handoff_id,),
            ).fetchone()
            if existing_handoff is None:
                # The attempt row is written above in this same transaction, so a
                # committed handoff always has a committed attempt behind it.
                connection.execute(
                    """
                    INSERT INTO model_handoff_records (
                        handoff_id, attempt_id, recorded_at, attempt_cutoff,
                        payload_hash, payload_json, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        handoff.handoff_id,
                        handoff.attempt_id,
                        attempt.recorded_at.astimezone(UTC).isoformat(),
                        handoff.attempt_cutoff.astimezone(UTC).isoformat(),
                        handoff_hash,
                        handoff_payload,
                    ),
                )
            elif str(existing_handoff["payload_hash"]) != handoff_hash:
                raise ModelAdapterPersistenceConflict(f"conflicting payload for existing handoff {handoff.handoff_id}")

    def _verify_against_rederived_authority(
        self,
        *,
        attempt: ModelAttemptRecord,
        handoff: ModelHandoffRecord,
        request_evidence: ProviderRequestEvidence,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
    ) -> None:
        """Independently rederive the decision and reject any disagreement.

        Residual window, stated honestly: the Source Handling authority store and
        this evidence database are separate substrates, so `BEGIN IMMEDIATE` cannot
        also lock the authority store. Re-resolving here narrows the exposure to
        the write transaction, but a restrictive successor backdated to at or
        before the attempt cutoff and published inside that window would not be
        observed. ADR 0034 makes an equivalent atomic snapshot-to-handoff guarantee
        a precondition for provider *activation*, which Phase A does not perform;
        closing it fully requires a governed cross-substrate commit primitive that
        does not exist yet and is not invented here.
        """
        if handoff.attempt_cutoff != attempt_authority.cutoff.astimezone(UTC):
            raise ModelAdapterAuthorityMismatch("handoff cutoff does not match the supplied attempt authority")
        try:
            resolved = resolve_pre_model_source_handling(attempt_authority)
        except SourceHandlingBlockedError as error:
            raise ModelAdapterAuthorityMismatch(
                f"attempt authority cannot be independently resolved at persistence: {error}"
            ) from error

        decision = resolved.decision
        if decision.get("processing_decision") != "ALLOW":
            raise ModelAdapterAuthorityMismatch("rederived attempt authority does not permit model-facing processing")
        expected = {
            "fact_record_id": str(decision.get("fact_record_id") or ""),
            "policy_record_id": str(decision.get("policy_record_id") or ""),
            "field_category_registry_id": str(decision.get("field_category_registry_id") or ""),
            "authorization_rule_id": str(decision.get("authorization_rule_id") or ""),
        }
        for name, value in expected.items():
            if getattr(handoff, name) != value:
                raise ModelAdapterAuthorityMismatch(f"handoff {name} does not match independently rederived authority")
        if handoff.durable_disposition_identity != disposition_identity(decision):
            raise ModelAdapterAuthorityMismatch(
                "handoff durable-disposition identity does not match independently rederived authority"
            )

        # Independently derive what request evidence this authority permits, rather
        # than accepting a supplied object merely because its identities are
        # internally consistent.
        permitted = permitted_request_evidence_state(decision)
        if request_evidence.state != permitted:
            raise ModelAdapterAuthorityMismatch(
                f"request evidence state {request_evidence.state} is not the state "
                f"{permitted} authorized by independently rederived authority"
            )
        if attempt.request_evidence_state != permitted:
            raise ModelAdapterAuthorityMismatch(
                "attempt request-evidence state does not match independently rederived authority"
            )
        if permitted == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY" and any(
            value is not None
            for value in (
                request_evidence.content_hash,
                request_evidence.measured_size_bytes,
                request_evidence.content_derived_identity,
            )
        ):
            raise ModelAdapterAuthorityMismatch(
                "request evidence carries content-derived material the rederived authority prohibits"
            )

    # -- single-use consumption ---------------------------------------------

    def consume_handoff_once(self, *, handoff_id: str, consumed_at: datetime) -> None:
        """Claim a handoff exactly once via compare-and-set.

        The conditional `consumed_at IS NULL` predicate is evaluated and applied
        inside one immediate transaction, so two concurrent consumers of the same
        handoff produce exactly one winner rather than a read-then-write race.
        """
        if consumed_at.tzinfo is None:
            raise ModelAdapterPersistenceError("consumed_at must be timezone-aware")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT consumed_at FROM model_handoff_records WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise ModelAdapterPersistenceError(f"handoff {handoff_id} is unknown; no dispatch authorization exists")
            cursor = connection.execute(
                """
                UPDATE model_handoff_records
                SET consumed_at = ?
                WHERE handoff_id = ? AND consumed_at IS NULL
                """,
                (consumed_at.astimezone(UTC).isoformat(), handoff_id),
            )
            if cursor.rowcount != 1:
                raise ModelAdapterPersistenceError(f"handoff {handoff_id} has already been consumed")

    # -- strict-known historical reads ---------------------------------------

    def strict_known_attempt(self, attempt_id: str, cutoff: datetime) -> ModelAttemptRecord | None:
        """Read an attempt only if it was already recorded at the historical cutoff."""
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_hash, payload_json
                FROM model_attempt_records
                WHERE attempt_id = ? AND recorded_at <= ?
                LIMIT 1
                """,
                (attempt_id, cutoff.astimezone(UTC).isoformat()),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if _sha256(payload_json) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted attempt hash mismatch")
        return _attempt_from_payload(json.loads(payload_json))

    def strict_known_request_evidence(self, attempt_id: str, cutoff: datetime) -> ProviderRequestEvidence | None:
        """Return exactly the request evidence durably authorized at the cutoff.

        When retention was prohibited, this returns the governed unavailability
        state that was recorded then. It never reconstructs bytes, hash, size, or
        a content-derived identity from the current prompt or current policy.
        """
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_hash, payload_json
                FROM model_handoff_records
                WHERE attempt_id = ? AND recorded_at <= ?
                ORDER BY handoff_id
                LIMIT 1
                """,
                (attempt_id, cutoff.astimezone(UTC).isoformat()),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if _sha256(payload_json) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted handoff hash mismatch")
        evidence_payload = dict(json.loads(payload_json)["request_evidence"])
        return ProviderRequestEvidence(**evidence_payload)

    def handoff_consumed_at(self, handoff_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT consumed_at FROM model_handoff_records WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if row is None or row["consumed_at"] is None:
            return None
        return _parse_time(str(row["consumed_at"]))

    def attempt_exists(self, attempt_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM model_attempt_records WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return row is not None

    # -- schema --------------------------------------------------------------

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_attempt_records (
                    attempt_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    attempt_cutoff TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS model_attempt_recorded_at_idx
                    ON model_attempt_records(recorded_at, attempt_id);
                CREATE TABLE IF NOT EXISTS model_handoff_records (
                    handoff_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    attempt_cutoff TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY (attempt_id) REFERENCES model_attempt_records(attempt_id)
                );
                -- ADR 0034 requires one dispatch opportunity per attempt, not merely
                -- that each handoff is single-use. Without this uniqueness, two
                -- handoffs differing only in expiry could bind to the same attempt
                -- and each be consumed once, dispatching one attempt twice. Declared
                -- as a unique index rather than a table constraint so it also applies
                -- to a database created before this invariant existed.
                CREATE UNIQUE INDEX IF NOT EXISTS model_handoff_attempt_unique_idx
                    ON model_handoff_records(attempt_id);
                """
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection that is committed/rolled back *and* closed.

        `sqlite3.Connection.__exit__` only ends the transaction; it does not close
        the connection, so a bare `with sqlite3.connect(...)` leaks handles and can
        hold locks until garbage collection.
        """
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _attempt_from_payload(payload: Mapping[str, Any]) -> ModelAttemptRecord:
    item = dict(payload)
    for name in ("build_cutoff", "attempt_cutoff", "recorded_at"):
        item[name] = _parse_time(str(item[name]))
    return ModelAttemptRecord(**item)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("persisted timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
