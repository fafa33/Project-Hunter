from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.valuation_methodology.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_AUTHORITY_ID,
    PERMITTED_MODEL_IDENTIFIER,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_HORIZON_DAYS,
    VALUATION_METHODOLOGY_SCHEMA_VERSION,
    ValuationMethodologySnapshot,
)
from hunter.valuation_methodology.repository import (
    ValuationMethodologyIntegrityError,
    ValuationMethodologyRepository,
    methodology_snapshot,
)

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"


class CanonicalValuationMethodologyAuthorityError(ValueError):
    """Raised when an unattested or otherwise unauthorized methodology write is attempted."""


class CanonicalValuationMethodologyAuthority:
    """Service-owned authority boundary for ValuationMethodologySnapshot.

    Authority model (see PR body / architecture note for the full pre-implementation
    analysis): this reuses the operator-attested, HUNTER_APPLICATION_ROOT-gated pattern
    already accepted for hunter.committee.command and hunter.valuation_evidence.command,
    rather than hunter.value_capture's provider-signing-key model. A
    ValuationMethodologySnapshot is a governance-authored configuration object fixed by
    an accepted ADR, not a fact acquired from an external, untrusted-by-default source --
    there is no endpoint, host, or provider to authenticate against, so a signing-key
    scheme would falsely model it as provider-acquired evidence. Instead:

    - construction requires a validated, operator-approved application root (mirroring
      the identical `_application_root()` check already used, unmodified, in both
      existing CLIs) -- this is the sole "is this caller attested" gate;
    - every ADR-fixed parameter (permitted_model_identifier, horizon_days,
      correlation_group, normalization_policy_id, authorizing_adr_reference,
      authorized_by) is injected here as a fixed constant and is never accepted as a
      caller-supplied argument to persist_methodology -- there is no code path through
      this service's public API that can override them, exactly mirroring how
      ObservedMarketFactService.semantic_version and market_validation/runner.py's
      four-input hard gate are non-parameterized.

    The repository remains purely mechanical; all validation lives here, exactly as in
    hunter.value_capture.service.SupplyAndValueCaptureService.
    """

    def __init__(
        self,
        *,
        repository: ValuationMethodologyRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self._application_root = _authorized_application_root(application_root)

    def persist_methodology(
        self,
        *,
        entity_class_criteria_id: str,
        entity_class_criteria_version: str,
        currency: str,
        discount_rate_policy_id: str,
        discount_rate_policy_version: str,
        sensitivity_policy_id: str,
        sensitivity_policy_version: str,
        supply_basis_selection_rule: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ValuationMethodologySnapshot:
        record = ValuationMethodologySnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUATION_METHODOLOGY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            entity_class_criteria_id=entity_class_criteria_id,
            entity_class_criteria_version=entity_class_criteria_version,
            permitted_model_identifier=PERMITTED_MODEL_IDENTIFIER,
            horizon_days=REQUIRED_HORIZON_DAYS,
            currency=currency,
            discount_rate_policy_id=discount_rate_policy_id,
            discount_rate_policy_version=discount_rate_policy_version,
            sensitivity_policy_id=sensitivity_policy_id,
            sensitivity_policy_version=sensitivity_policy_version,
            supply_basis_selection_rule=supply_basis_selection_rule,
            normalization_policy_id=None,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_AUTHORITY_ID,
            accepts_assembled_evidence=False,
            accepted_evidence_shape_ids=(),
            accepted_native_evidence_families=("fundamental-evidence",),
            accepted_assembly_rule_versions=(),
            assembled_evidence_granularity_override=None,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize(record)
        return self._persist_record(record)

    def persist_evidence_assembly_methodology(
        self,
        *,
        entity_class_criteria_id: str,
        entity_class_criteria_version: str,
        currency: str,
        discount_rate_policy_id: str,
        discount_rate_policy_version: str,
        sensitivity_policy_id: str,
        sensitivity_policy_version: str,
        supply_basis_selection_rule: str,
        accepted_evidence_shape_ids: tuple[str, ...],
        accepted_native_evidence_families: tuple[str, ...],
        accepted_assembly_rule_versions: tuple[str, ...],
        assembled_evidence_granularity_override: str | None,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ValuationMethodologySnapshot:
        """Persist the ADR-0025-governed opt-in contract through the sole methodology authority."""
        record = ValuationMethodologySnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUATION_METHODOLOGY_SCHEMA_VERSION,
            semantic_version="2.0.0",
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            entity_class_criteria_id=entity_class_criteria_id,
            entity_class_criteria_version=entity_class_criteria_version,
            permitted_model_identifier=PERMITTED_MODEL_IDENTIFIER,
            horizon_days=REQUIRED_HORIZON_DAYS,
            currency=currency,
            discount_rate_policy_id=discount_rate_policy_id,
            discount_rate_policy_version=discount_rate_policy_version,
            sensitivity_policy_id=sensitivity_policy_id,
            sensitivity_policy_version=sensitivity_policy_version,
            supply_basis_selection_rule=supply_basis_selection_rule,
            normalization_policy_id=None,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_AUTHORITY_ID,
            accepts_assembled_evidence=True,
            accepted_evidence_shape_ids=accepted_evidence_shape_ids,
            accepted_native_evidence_families=accepted_native_evidence_families,
            accepted_assembly_rule_versions=accepted_assembly_rule_versions,
            assembled_evidence_granularity_override=assembled_evidence_granularity_override,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        return self._persist_record(_normalize(record))

    def _persist_record(self, record: ValuationMethodologySnapshot) -> ValuationMethodologySnapshot:
        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            # BEGIN IMMEDIATE before validating, exactly the hardened transaction pattern
            # from audit finding F1 (hunter.market_facts.repository.apply /
            # hunter.value_capture.service.persist_capability): two concurrent corrections
            # of the same predecessor cannot both observe "no existing successor".
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_correction(snapshots, record)
            snapshots.save(methodology_snapshot(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise ValuationMethodologyIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return record

    def get(self, record_id: str) -> ValuationMethodologySnapshot | None:
        return self.repository.get(record_id)

    def methodology_history(self, logical_id: str) -> tuple[ValuationMethodologySnapshot, ...]:
        return self.repository.methodology_history(logical_id)

    def strict_known_methodology(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> ValuationMethodologySnapshot | None:
        return self.repository.strict_known_methodology(effective_as_of=effective_as_of, known_by=known_by)

    def unresolved_conflicts(self) -> tuple[ValuationMethodologySnapshot, ...]:
        return self.repository.unresolved_conflicts()


def _authorize_correction(snapshots: Any, record: ValuationMethodologySnapshot) -> None:
    predecessor_id = record.supersedes_record_id
    if predecessor_id is None:
        # A second, independent "root" record for the same logical_id would bypass the
        # correction mechanism entirely (branching without ever setting
        # supersedes_record_id). ADR 0022 authorizes exactly one methodology for the
        # first supported entity class, so unlike hunter.value_capture's provider-fact
        # conflict-flagging (appropriate for genuinely divergent external observations),
        # a second root record here is rejected outright rather than merely flagged --
        # any amendment must go through supersedes_record_id.
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == "valuation-methodology-snapshot"
                and item.payload.get("logical_id") == record.logical_id
                and item.id != record.record_id
            ),
            None,
        )
        if existing_root is not None:
            raise ValuationMethodologyIntegrityError(
                "a root methodology record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(predecessor_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != "valuation-methodology-snapshot":
        raise ValuationMethodologyIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise ValuationMethodologyIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise ValuationMethodologyIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise ValuationMethodologyIntegrityError("correction known_at must follow predecessor")
    # A retried, byte-identical correction (same content-addressed record_id) is an
    # idempotent no-op, not a branch -- excluded here and handled by the ordinary
    # save()-time canonical-hash upsert below. Only a *different* record claiming the
    # same predecessor is a genuine branching correction.
    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == "valuation-methodology-snapshot"
            and item.payload.get("supersedes_record_id") == predecessor_id
            and item.id != record.record_id
        ),
        None,
    )
    if competing_successor is not None:
        raise ValuationMethodologyIntegrityError("branching correction lineage is prohibited")


def _normalize(record: ValuationMethodologySnapshot) -> ValuationMethodologySnapshot:
    logical_id = _logical_id(record)
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _logical_id(record: ValuationMethodologySnapshot) -> str:
    # A correction's logical identity is the (model, entity-class-criteria) pair it
    # governs, not the specific parameter values -- a discount-rate-policy revision is a
    # correction of the *same* methodology; a different model or entity-class-criteria
    # id is a materially different methodology family (a new logical_id), never a
    # correction chain of this one.
    raw = "|".join((record.permitted_model_identifier, record.entity_class_criteria_id))
    return hashlib.sha256(raw.encode()).hexdigest()


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "logical_id", "content_hash"})


def _content_hash(record: ValuationMethodologySnapshot, *, logical_id: str) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    payload["logical_id"] = logical_id
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def canonical_methodology_content_hash(record: ValuationMethodologySnapshot) -> str:
    """Return the canonical payload hash for a materialized methodology snapshot."""
    return _content_hash(record, logical_id=record.logical_id)


def verify_methodology_content_hash(record: ValuationMethodologySnapshot) -> bool:
    """Verify canonical methodology payload integrity without trusting its embedded hash."""
    return record.content_hash == canonical_methodology_content_hash(record)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _authorized_application_root(application_root: Path | None) -> Path:
    """The sole authorization boundary for this service: mirrors, unmodified in
    substance, the identical `_application_root()` check already accepted and running in
    hunter.committee.command and hunter.valuation_evidence.command. Enforced here at
    service-construction time (rather than only at a CLI entry point) so that
    unauthorized/unattested construction fails closed regardless of caller."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalValuationMethodologyAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalValuationMethodologyAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
