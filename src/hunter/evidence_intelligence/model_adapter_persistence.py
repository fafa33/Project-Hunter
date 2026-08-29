"""ADR 0034 Phases A and B — mechanical durability for Model Adapter records.

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

Phase B adds two more storage guarantees of the same kind:

* an outcome and its governed response artifact commit in one transaction, so a
  durable response can never exist without the outcome that attributes it; and
* the outcome family is insert-only. There is no `UPDATE` statement against it
  anywhere in this module, so a correction has to be a new superseding record
  rather than a rewrite, and the pre-send attempt is never touched again.

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
    DISPATCH_CAPABILITY_CATEGORY,
    ModelAttemptOutcomeRecord,
    ModelAttemptRecord,
    ModelHandoffRecord,
    ProviderRequestEvidence,
    ProviderResponseArtifact,
    category_persist_allowed,
    disposition_identity,
    permitted_request_evidence_state,
    permitted_response_evidence_state,
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

    # -- append-only outcome and governed response capture -------------------

    def append_outcome(
        self,
        *,
        outcome: ModelAttemptOutcomeRecord,
        response_artifact: ProviderResponseArtifact | None,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
    ) -> None:
        """Append one outcome, and its governed response artifact, in one transaction.

        Insert-only. This method contains no `UPDATE`, so an existing outcome
        cannot be rewritten through it, and the pre-send attempt row is never
        touched. A second outcome for an attempt is admitted only when it
        explicitly supersedes an existing one, which is how ADR 0034 requires a
        correction to be expressed.

        Like `persist_attempt_and_handoff`, the authority is re-resolved here, so a
        caller who bypasses `ModelAdapterService` still cannot persist an outcome
        or a response artifact whose governed state disagrees with the authority
        that actually applies at the attempt cutoff.
        """
        if response_artifact is not None:
            if outcome.attempt_id is None or response_artifact.attempt_id != outcome.attempt_id:
                raise ModelAdapterPersistenceError("response artifact does not belong to the supplied outcome")
            if outcome.handoff_id is not None and response_artifact.handoff_id != outcome.handoff_id:
                raise ModelAdapterPersistenceError("response artifact handoff lineage does not match the outcome")
            if response_artifact.execution_profile_identity != outcome.execution_profile_identity:
                raise ModelAdapterPersistenceError("response artifact profile lineage does not match the outcome")
            # The artifact row is keyed by its *own* computed identity, so without
            # this an outcome could name one response artifact while a different
            # object was stored under that attempt -- an outcome pointing at
            # evidence that was never written.
            if (
                outcome.response_artifact_identity is not None
                and outcome.response_artifact_identity != response_artifact.response_artifact_identity
            ):
                raise ModelAdapterPersistenceError(
                    "outcome response-artifact identity does not match the supplied response artifact"
                )
        elif outcome.response_artifact_identity is not None:
            raise ModelAdapterPersistenceError(
                "outcome claims a response-artifact identity but no response artifact was supplied"
            )

        outcome_payload = _canonical_json(_jsonable(asdict(outcome)))
        outcome_hash = _sha256(outcome_payload)
        response_payload = (
            _canonical_json(_jsonable(asdict(response_artifact))) if response_artifact is not None else None
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_outcome_against_rederived_authority(
                outcome=outcome,
                response_artifact=response_artifact,
                attempt_authority=attempt_authority,
            )
            if outcome.attempt_id is not None:
                attempt_row = connection.execute(
                    "SELECT 1 FROM model_attempt_records WHERE attempt_id = ?",
                    (outcome.attempt_id,),
                ).fetchone()
                if attempt_row is None:
                    raise ModelAdapterPersistenceError(
                        f"outcome references attempt {outcome.attempt_id}, which is not durably recorded"
                    )

            existing = connection.execute(
                "SELECT payload_hash FROM model_attempt_outcome_records WHERE outcome_id = ?",
                (outcome.outcome_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != outcome_hash:
                    raise ModelAdapterPersistenceConflict(
                        f"conflicting payload for existing outcome {outcome.outcome_id}"
                    )
                return

            if outcome.attempt_id is not None:
                prior = connection.execute(
                    "SELECT outcome_id FROM model_attempt_outcome_records WHERE attempt_id = ?",
                    (outcome.attempt_id,),
                ).fetchall()
                if prior and outcome.supersedes_outcome_id is None:
                    raise ModelAdapterPersistenceConflict(
                        f"attempt {outcome.attempt_id} already has a recorded outcome; "
                        "a correction must supersede it explicitly rather than append silently"
                    )
                if outcome.supersedes_outcome_id is not None:
                    known = {str(row["outcome_id"]) for row in prior}
                    if outcome.supersedes_outcome_id not in known:
                        raise ModelAdapterPersistenceError(
                            "a superseding outcome must reference an existing outcome for the same attempt"
                        )

            connection.execute(
                """
                INSERT INTO model_attempt_outcome_records (
                    outcome_id, attempt_id, handoff_id, recorded_at, attempt_cutoff,
                    supersedes_outcome_id, payload_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.outcome_id,
                    outcome.attempt_id,
                    outcome.handoff_id,
                    outcome.recorded_at.astimezone(UTC).isoformat(),
                    outcome.attempt_cutoff.astimezone(UTC).isoformat(),
                    outcome.supersedes_outcome_id,
                    outcome_hash,
                    outcome_payload,
                ),
            )

            if response_artifact is not None and response_payload is not None:
                identity = response_artifact.response_artifact_identity
                existing_response = connection.execute(
                    "SELECT payload_hash FROM provider_response_artifacts WHERE response_artifact_id = ?",
                    (identity,),
                ).fetchone()
                response_hash = _sha256(response_payload)
                if existing_response is None:
                    connection.execute(
                        """
                        INSERT INTO provider_response_artifacts (
                            response_artifact_id, attempt_id, outcome_id, recorded_at,
                            evidence_state, payload_hash, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identity,
                            response_artifact.attempt_id,
                            outcome.outcome_id,
                            response_artifact.recorded_at.astimezone(UTC).isoformat(),
                            response_artifact.state,
                            response_hash,
                            response_payload,
                        ),
                    )
                elif str(existing_response["payload_hash"]) != response_hash:
                    raise ModelAdapterPersistenceConflict(
                        f"conflicting payload for existing response artifact {identity}"
                    )

    def _verify_outcome_against_rederived_authority(
        self,
        *,
        outcome: ModelAttemptOutcomeRecord,
        response_artifact: ProviderResponseArtifact | None,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
    ) -> None:
        """Rederive the governing decision and reject any durable state it forbids."""
        if outcome.attempt_cutoff != attempt_authority.cutoff.astimezone(UTC):
            raise ModelAdapterAuthorityMismatch("outcome cutoff does not match the supplied attempt authority")
        try:
            resolved = resolve_pre_model_source_handling(attempt_authority)
        except SourceHandlingBlockedError as error:
            raise ModelAdapterAuthorityMismatch(
                f"attempt authority cannot be independently resolved at persistence: {error}"
            ) from error
        decision = resolved.decision

        # Correlation and idempotency identifiers are operational metadata. They
        # persist only where that category is authorized; a record carrying them
        # under a denying authority is rejected rather than quietly stripped,
        # because silently dropping a field would hide the disagreement.
        metadata_permitted = category_persist_allowed(decision, DISPATCH_CAPABILITY_CATEGORY)
        if not metadata_permitted and (
            outcome.correlation_identity is not None or outcome.idempotency_identity is not None
        ):
            raise ModelAdapterAuthorityMismatch(
                "outcome carries provider correlation or idempotency metadata the rederived authority prohibits"
            )

        if response_artifact is None:
            return

        permitted = permitted_response_evidence_state(decision)
        if response_artifact.state == "RESPONSE_EVIDENCE_DURABLE" and permitted != "RESPONSE_EVIDENCE_DURABLE":
            raise ModelAdapterAuthorityMismatch(
                "durable response evidence is not authorized by independently rederived authority"
            )
        if response_artifact.state != "RESPONSE_EVIDENCE_DURABLE" and any(
            value is not None
            for value in (
                response_artifact.content,
                response_artifact.content_hash,
                response_artifact.measured_size_bytes,
                response_artifact.content_derived_identity,
            )
        ):
            raise ModelAdapterAuthorityMismatch(
                "response artifact carries content-derived material its own recorded state prohibits"
            )
        if outcome.response_evidence_state != response_artifact.state:
            raise ModelAdapterAuthorityMismatch("outcome response-evidence state does not match the response artifact")

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

    def strict_known_handoff(self, handoff_id: str, cutoff: datetime) -> ModelHandoffRecord | None:
        """Read the exact canonical handoff only when known at ``cutoff``."""
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_hash, payload_json
                FROM model_handoff_records
                WHERE handoff_id = ? AND recorded_at <= ?
                LIMIT 1
                """,
                (handoff_id, cutoff.astimezone(UTC).isoformat()),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if _sha256(payload_json) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted handoff hash mismatch")
        return _handoff_from_payload(json.loads(payload_json))

    def handoff_consumed_at(self, handoff_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT consumed_at FROM model_handoff_records WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if row is None or row["consumed_at"] is None:
            return None
        return _parse_time(str(row["consumed_at"]))

    def terminal_outcome_exists(self, attempt_id: str) -> bool:
        """Whether this attempt already has any recorded outcome.

        An attempt with an outcome is over, including an uncertain one. ADR 0034
        requires a further try to be a *new* attempt, so this is the check that
        stops one attempt being dispatched again after its outcome was recorded.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM model_attempt_outcome_records WHERE attempt_id = ? LIMIT 1",
                (attempt_id,),
            ).fetchone()
        return row is not None

    def attempts_without_outcome(self, *, cutoff: datetime) -> tuple[str, ...]:
        """Durable attempts recorded at `cutoff` that carry no outcome.

        Crash-recovery evidence, read strict-known. These are uncertain by
        construction: ADR 0034 forbids rewriting them into success or failure, so
        this reports identities and nothing more.
        """
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.attempt_id FROM model_attempt_records AS a
                WHERE a.recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM model_attempt_outcome_records AS o
                      WHERE o.attempt_id = a.attempt_id AND o.recorded_at <= ?
                  )
                ORDER BY a.recorded_at, a.attempt_id
                """,
                (cutoff.astimezone(UTC).isoformat(), cutoff.astimezone(UTC).isoformat()),
            ).fetchall()
        return tuple(str(row["attempt_id"]) for row in rows)

    def strict_known_outcomes(self, attempt_id: str, cutoff: datetime) -> tuple[ModelAttemptOutcomeRecord, ...]:
        """Every outcome for an attempt that was already recorded at the cutoff."""
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_hash, payload_json FROM model_attempt_outcome_records
                WHERE attempt_id = ? AND recorded_at <= ?
                ORDER BY recorded_at, outcome_id
                """,
                (attempt_id, cutoff.astimezone(UTC).isoformat()),
            ).fetchall()
        outcomes: list[ModelAttemptOutcomeRecord] = []
        for row in rows:
            payload_json = str(row["payload_json"])
            if _sha256(payload_json) != str(row["payload_hash"]):
                raise ModelAdapterPersistenceCorruption("persisted outcome hash mismatch")
            outcomes.append(_outcome_from_payload(json.loads(payload_json)))
        return tuple(outcomes)

    def authoritative_outcome(self, attempt_id: str, cutoff: datetime) -> ModelAttemptOutcomeRecord | None:
        """The outcome that currently governs this attempt, as known at the cutoff.

        `append_outcome` admits a correction as a new record carrying
        `supersedes_outcome_id`, so an attempt can hold a chain of outcomes. The
        governing one is the head of that chain: the outcome that no other outcome
        *known at this cutoff* supersedes. Reading the earliest row instead would
        make a correction invisible forever, and reading the latest row blindly
        would promote a superseded record the moment ordering wobbled.

        Strict-known throughout: a correction recorded after the cutoff does not
        retroactively govern an earlier read.
        """
        _aware("cutoff", cutoff)
        moment = cutoff.astimezone(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT o.payload_hash, o.payload_json FROM model_attempt_outcome_records AS o
                WHERE o.attempt_id = ? AND o.recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM model_attempt_outcome_records AS s
                      WHERE s.attempt_id = o.attempt_id
                        AND s.recorded_at <= ?
                        AND s.supersedes_outcome_id = o.outcome_id
                  )
                ORDER BY o.recorded_at DESC, o.outcome_id DESC
                LIMIT 1
                """,
                (attempt_id, moment, moment),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if _sha256(payload_json) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted outcome hash mismatch")
        return _outcome_from_payload(json.loads(payload_json))

    def strict_known_response_artifact(self, attempt_id: str, cutoff: datetime) -> ProviderResponseArtifact | None:
        """Return exactly the response evidence durably authorized at the cutoff.

        Where response retention was prohibited, or the capture gate refused the
        content, this returns the governed unavailability state that was recorded
        then. It never reconstructs bytes, hash, size, or a derived identity from
        current policy, and it never re-invokes the provider to fill the gap.

        Follows supersession: the artifact returned is the one attached to the
        outcome that governs the attempt at this cutoff, so a correction that
        attaches revised response evidence is what a later read sees. Where the
        governing outcome carries no artifact of its own, the most recent earlier
        artifact known at the cutoff stands, because a correction that records no
        new response evidence does not erase the evidence already captured.
        """
        _aware("cutoff", cutoff)
        moment = cutoff.astimezone(UTC).isoformat()
        governing = self.authoritative_outcome(attempt_id, cutoff)
        with self._connect() as connection:
            row = None
            if governing is not None:
                row = connection.execute(
                    """
                    SELECT payload_hash, payload_json FROM provider_response_artifacts
                    WHERE attempt_id = ? AND outcome_id = ? AND recorded_at <= ?
                    ORDER BY recorded_at DESC, response_artifact_id DESC
                    LIMIT 1
                    """,
                    (attempt_id, governing.outcome_id, moment),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT payload_hash, payload_json FROM provider_response_artifacts
                    WHERE attempt_id = ? AND recorded_at <= ?
                    ORDER BY recorded_at DESC, response_artifact_id DESC
                    LIMIT 1
                    """,
                    (attempt_id, moment),
                ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if _sha256(payload_json) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted response artifact hash mismatch")
        return _response_artifact_from_payload(json.loads(payload_json))

    def strict_known_response_capture(
        self,
        response_capture_identity: str,
        cutoff: datetime,
    ) -> tuple[ProviderResponseArtifact, ModelAttemptOutcomeRecord] | None:
        """Resolve one exact capture and the exact authoritative outcome that owns it.

        ADR 0035 allocates a validation event against a response-capture identity,
        not against a caller-selected attempt.  This lookup starts from that exact
        identity and rejects a capture whose linked outcome is no longer the
        strict-known authoritative head at the validation cutoff.
        """
        _aware("cutoff", cutoff)
        moment = cutoff.astimezone(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT response_artifact_id, attempt_id, outcome_id,
                       payload_hash, payload_json
                FROM provider_response_artifacts
                WHERE response_artifact_id = ? AND recorded_at <= ?
                LIMIT 1
                """,
                (response_capture_identity, moment),
            ).fetchone()
            if row is None:
                return None
            outcome_row = connection.execute(
                """
                SELECT payload_hash, payload_json
                FROM model_attempt_outcome_records
                WHERE outcome_id = ? AND recorded_at <= ?
                LIMIT 1
                """,
                (str(row["outcome_id"]), moment),
            ).fetchone()
        if outcome_row is None:
            raise ModelAdapterPersistenceCorruption("response capture outcome is unavailable at cutoff")

        response_payload = str(row["payload_json"])
        if _sha256(response_payload) != str(row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted response artifact hash mismatch")
        response = _response_artifact_from_payload(json.loads(response_payload))
        if response.response_artifact_identity != str(row["response_artifact_id"]):
            raise ModelAdapterPersistenceCorruption("persisted response capture identity mismatch")

        outcome_payload = str(outcome_row["payload_json"])
        if _sha256(outcome_payload) != str(outcome_row["payload_hash"]):
            raise ModelAdapterPersistenceCorruption("persisted outcome hash mismatch")
        outcome = _outcome_from_payload(json.loads(outcome_payload))
        if outcome.outcome_id != str(row["outcome_id"]):
            raise ModelAdapterPersistenceCorruption("response capture outcome identity mismatch")
        if response.attempt_id != str(row["attempt_id"]) or outcome.attempt_id != response.attempt_id:
            raise ModelAdapterPersistenceCorruption("response capture attempt lineage mismatch")

        governing = self.authoritative_outcome(response.attempt_id, cutoff)
        if governing is None or governing.outcome_id != outcome.outcome_id:
            raise ModelAdapterPersistenceCorruption(
                "response capture is not attached to the strict-known authoritative attempt outcome"
            )
        return response, outcome

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
            connection.executescript("""
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
                -- Append-only. `attempt_id` and `handoff_id` are nullable because a
                -- pre-dispatch refusal is decided before either record exists, and
                -- ADR 0034 requires such an outcome to carry exactly the lineage
                -- that actually existed rather than a fabricated placeholder.
                CREATE TABLE IF NOT EXISTS model_attempt_outcome_records (
                    outcome_id TEXT PRIMARY KEY,
                    attempt_id TEXT,
                    handoff_id TEXT,
                    recorded_at TEXT NOT NULL,
                    attempt_cutoff TEXT NOT NULL,
                    supersedes_outcome_id TEXT,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (attempt_id) REFERENCES model_attempt_records(attempt_id)
                );
                CREATE INDEX IF NOT EXISTS model_attempt_outcome_attempt_idx
                    ON model_attempt_outcome_records(attempt_id, recorded_at);
                CREATE TABLE IF NOT EXISTS provider_response_artifacts (
                    response_artifact_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    outcome_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    evidence_state TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (attempt_id) REFERENCES model_attempt_records(attempt_id),
                    FOREIGN KEY (outcome_id) REFERENCES model_attempt_outcome_records(outcome_id)
                );
                CREATE INDEX IF NOT EXISTS provider_response_attempt_idx
                    ON provider_response_artifacts(attempt_id, recorded_at);
                """)

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


def _outcome_from_payload(payload: Mapping[str, Any]) -> ModelAttemptOutcomeRecord:
    item = dict(payload)
    for name in ("attempt_cutoff", "recorded_at"):
        item[name] = _parse_time(str(item[name]))
    if item.get("dispatched_at") is not None:
        item["dispatched_at"] = _parse_time(str(item["dispatched_at"]))
    return ModelAttemptOutcomeRecord(**item)


def _response_artifact_from_payload(payload: Mapping[str, Any]) -> ProviderResponseArtifact:
    item = dict(payload)
    item["recorded_at"] = _parse_time(str(item["recorded_at"]))
    metadata = item.get("provider_status_metadata") or ()
    item["provider_status_metadata"] = tuple(tuple(entry) for entry in metadata)
    return ProviderResponseArtifact(**item)


def _handoff_from_payload(payload: Mapping[str, Any]) -> ModelHandoffRecord:
    item = dict(payload.get("handoff") or {})
    item["attempt_cutoff"] = _parse_time(str(item["attempt_cutoff"]))
    if item.get("expires_at") is not None:
        item["expires_at"] = _parse_time(str(item["expires_at"]))
    return ModelHandoffRecord(**item)


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
