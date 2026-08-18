from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.evidence_intelligence.models import EvidenceSpan, evidence_text_digest
from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidenceContextAllocationResult,
    EvidenceContextDecision,
    EvidenceContextPackage,
    EvidenceContextSelectionLedger,
    EvidenceContextSelectionPolicy,
    EvidenceExtractionIntent,
    EvidencePreModelBuildRecord,
    EvidencePreModelBuildResult,
    EvidencePreModelSourceHandlingAuthority,
    EvidencePromptArtifact,
    EvidencePromptPlan,
    EvidencePromptSpecification,
    _render_prompt,
    resolve_pre_model_source_handling,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import (
    SourceHandlingBlockedError,
    validate_durable_payload,
)

ReconstructionStatus = Literal["AVAILABLE", "UNAVAILABLE", "NOT_KNOWN_AT_CUTOFF"]

RETENTION_PROHIBITED_REASON_CODE = "EXACT_PROMPT_RETENTION_PROHIBITED"
REDACTED_SOURCE_EXCERPT = "[REDACTED:EXACT_PROMPT_RETENTION_PROHIBITED]"


class PreModelPersistenceConflict(RuntimeError):
    """Raised when an existing deterministic build id is reused with different bytes."""


class PreModelPersistenceCorruption(RuntimeError):
    """Raised when persisted payload no longer matches its recorded identity/hash."""


class PreModelPersistenceLineageError(RuntimeError):
    """Raised when a supplied bundle is not internally consistent with its build result."""


@dataclass(frozen=True)
class PersistedEvidencePreModelBundle:
    recorded_at: datetime
    intent: EvidenceExtractionIntent
    policy: EvidenceContextSelectionPolicy
    specification: EvidencePromptSpecification
    capability: EvidenceCapabilityConstraint
    canonical_inventory: tuple[EvidenceSpan, ...]
    build_result: EvidencePreModelBuildResult
    exact_source_bytes_retained: bool = True

    @property
    def build_record_id(self) -> str:
        return self.build_result.build_record.build_record_id


@dataclass(frozen=True)
class EvidencePreModelReconstruction:
    status: ReconstructionStatus
    reason_code: str
    bundle: PersistedEvidencePreModelBundle | None

    @property
    def exact_prompt(self) -> str | None:
        if self.status != "AVAILABLE" or self.bundle is None:
            return None
        artifact = self.bundle.build_result.prompt_artifact
        if artifact is None or not artifact.content:
            return None
        return artifact.content


class EvidencePreModelPersistenceRepository:
    """Append-only durability for ADR 0031 provider-free pre-model build evidence."""

    def __init__(self, evidence_repository: EvidenceIntelligenceRepository) -> None:
        self.path = evidence_repository.path
        self._initialize()

    def save(
        self,
        *,
        intent: EvidenceExtractionIntent,
        policy: EvidenceContextSelectionPolicy,
        specification: EvidencePromptSpecification,
        capability: EvidenceCapabilityConstraint,
        canonical_inventory: tuple[EvidenceSpan, ...],
        build_result: EvidencePreModelBuildResult,
        recorded_at: datetime,
        source_handling_authority: EvidencePreModelSourceHandlingAuthority | None = None,
    ) -> PersistedEvidencePreModelBundle:
        """Persist one immutable pre-model build bundle after independent authority re-resolution."""
        _aware("recorded_at", recorded_at)
        inventory = tuple(canonical_inventory)
        _validate_bundle_lineage(
            intent=intent,
            policy=policy,
            specification=specification,
            capability=capability,
            canonical_inventory=inventory,
            build_result=build_result,
        )
        _validate_known_at_lower_bound(recorded_at.astimezone(UTC), inventory)

        if source_handling_authority is None:
            raise PreModelPersistenceLineageError("source handling authority is required at persistence")
        try:
            resolved = resolve_pre_model_source_handling(source_handling_authority)
        except SourceHandlingBlockedError as error:
            raise PreModelPersistenceLineageError(f"source handling authority cannot be resolved: {error}") from error
        persistence_decision = resolved.decision
        build_decision = build_result.source_handling_decision
        if build_decision is None:
            raise PreModelPersistenceLineageError("build lacks source handling authority decision")
        if _canonical_json(_jsonable(build_decision)) != _canonical_json(_jsonable(persistence_decision)):
            raise PreModelPersistenceLineageError(
                "persistence-time source handling decision does not match the build-time authority decision"
            )

        retain_exact_source_bytes = (
            persistence_decision.get("retention_decision") == "ALLOW"
            and persistence_decision.get("reconstruction_decision") == "ALLOW"
        )
        if not retain_exact_source_bytes:
            artifact = build_result.prompt_artifact
            if artifact is not None and artifact.content:
                raise PreModelPersistenceLineageError(
                    "source handling authority prohibits exact source retention but exact prompt bytes remain"
                )
            inventory = tuple(_redacted_span(span) for span in inventory)

        bundle = PersistedEvidencePreModelBundle(
            recorded_at=recorded_at.astimezone(UTC),
            intent=intent,
            policy=policy,
            specification=specification,
            capability=capability,
            canonical_inventory=inventory,
            build_result=build_result,
            exact_source_bytes_retained=retain_exact_source_bytes,
        )
        payload = _bundle_payload(bundle)
        fact = resolved.fact_record.get("fact")
        secret_presence = (
            {str(value) for value in (fact.get("secret_presence") or ())} if isinstance(fact, dict) else set()
        )
        try:
            validate_durable_payload(
                decision=persistence_decision,
                registry=resolved.registry_record,
                payload={"pre_model_bundle": payload},
                secret_presence=secret_presence,
            )
        except SourceHandlingBlockedError as error:
            raise PreModelPersistenceLineageError(
                f"durable payload rejected by source handling authority: {error}"
            ) from error
        payload_json = _canonical_json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        build_record_id = bundle.build_record_id

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT recorded_at, payload_hash, payload_json
                FROM evidence_pre_model_build_bundles
                WHERE build_record_id = ?
                """,
                (build_record_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash or str(existing["payload_json"]) != payload_json:
                    raise PreModelPersistenceConflict(
                        f"conflicting payload for existing pre-model build {build_record_id}"
                    )
                return _bundle_from_payload(
                    json.loads(str(existing["payload_json"])),
                    recorded_at=_parse_time(str(existing["recorded_at"])),
                )

            connection.execute(
                """
                INSERT INTO evidence_pre_model_build_bundles (
                    build_record_id,
                    recorded_at,
                    payload_hash,
                    payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    build_record_id,
                    bundle.recorded_at.isoformat(),
                    payload_hash,
                    payload_json,
                ),
            )
        return bundle

    def strict_known_bundle(
        self,
        build_record_id: str,
        cutoff: datetime,
    ) -> PersistedEvidencePreModelBundle | None:
        _aware("cutoff", cutoff)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT recorded_at, payload_hash, payload_json
                FROM evidence_pre_model_build_bundles
                WHERE build_record_id = ? AND recorded_at <= ?
                LIMIT 1
                """,
                (build_record_id, cutoff.astimezone(UTC).isoformat()),
            ).fetchone()
        if row is None:
            return None

        payload_json = str(row["payload_json"])
        expected_hash = str(row["payload_hash"])
        actual_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise PreModelPersistenceCorruption("persisted pre-model bundle hash mismatch")

        bundle = _bundle_from_payload(
            json.loads(payload_json),
            recorded_at=_parse_time(str(row["recorded_at"])),
        )
        if bundle.build_record_id != build_record_id:
            raise PreModelPersistenceCorruption("persisted pre-model build identity mismatch")
        _validate_prompt_artifact(bundle.build_result.prompt_artifact)
        return bundle

    def strict_known_reconstruction(
        self,
        build_record_id: str,
        cutoff: datetime,
    ) -> EvidencePreModelReconstruction:
        bundle = self.strict_known_bundle(build_record_id, cutoff)
        if bundle is None:
            return EvidencePreModelReconstruction(
                status="NOT_KNOWN_AT_CUTOFF",
                reason_code="PRE_MODEL_BUILD_NOT_KNOWN_AT_CUTOFF",
                bundle=None,
            )

        build = bundle.build_result.build_record
        artifact = bundle.build_result.prompt_artifact
        if build.reconstruction_outcome == "AVAILABLE":
            if artifact is None or not artifact.content:
                raise PreModelPersistenceCorruption(
                    "persisted build claims AVAILABLE reconstruction without retained prompt bytes"
                )
            return EvidencePreModelReconstruction(
                status="AVAILABLE",
                reason_code="EXACT_PRE_MODEL_RECONSTRUCTION_AVAILABLE",
                bundle=bundle,
            )

        reason = next(
            (code for code in build.reason_codes if code == RETENTION_PROHIBITED_REASON_CODE),
            "EXACT_PRE_MODEL_RECONSTRUCTION_UNAVAILABLE",
        )
        return EvidencePreModelReconstruction(
            status="UNAVAILABLE",
            reason_code=reason,
            bundle=bundle,
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_pre_model_build_bundles (
                    build_record_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_pre_model_build_recorded_at_idx
                    ON evidence_pre_model_build_bundles(recorded_at, build_record_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _bundle_payload(bundle: PersistedEvidencePreModelBundle) -> dict[str, Any]:
    return {
        "intent": _jsonable(asdict(bundle.intent)),
        "policy": _jsonable(asdict(bundle.policy)),
        "specification": _jsonable(asdict(bundle.specification)),
        "capability": _jsonable(asdict(bundle.capability)),
        "source_retention": {
            "exact_source_bytes_retained": bundle.exact_source_bytes_retained,
            "reason_code": (None if bundle.exact_source_bytes_retained else RETENTION_PROHIBITED_REASON_CODE),
        },
        "canonical_inventory": [_jsonable(asdict(span)) for span in bundle.canonical_inventory],
        "build_result": {
            "ledger": _jsonable(asdict(bundle.build_result.ledger)),
            "allocation": _jsonable(asdict(bundle.build_result.allocation)),
            "package": (_jsonable(asdict(bundle.build_result.package)) if bundle.build_result.package else None),
            "prompt_plan": (
                _jsonable(asdict(bundle.build_result.prompt_plan)) if bundle.build_result.prompt_plan else None
            ),
            "prompt_artifact": (
                _jsonable(asdict(bundle.build_result.prompt_artifact)) if bundle.build_result.prompt_artifact else None
            ),
            "build_record": _jsonable(asdict(bundle.build_result.build_record)),
            "source_handling_decision": _jsonable(bundle.build_result.source_handling_decision),
        },
    }


def _bundle_from_payload(payload: dict[str, Any], *, recorded_at: datetime) -> PersistedEvidencePreModelBundle:
    intent_payload = dict(payload["intent"])
    cutoff = intent_payload.get("historical_cutoff")
    intent_payload["historical_cutoff"] = _parse_time(cutoff) if cutoff else None
    intent = EvidenceExtractionIntent(**intent_payload)

    policy_payload = dict(payload["policy"])
    policy_payload["required_span_ids"] = tuple(policy_payload["required_span_ids"])
    policy_payload["optional_span_ids"] = tuple(policy_payload["optional_span_ids"])
    policy = EvidenceContextSelectionPolicy(**policy_payload)

    specification = EvidencePromptSpecification(**dict(payload["specification"]))
    capability = EvidenceCapabilityConstraint(**dict(payload["capability"]))
    inventory = tuple(_span_from_payload(item) for item in payload["canonical_inventory"])

    result_payload = dict(payload["build_result"])
    ledger_payload = dict(result_payload["ledger"])
    ledger_payload["decisions"] = tuple(EvidenceContextDecision(**item) for item in ledger_payload["decisions"])
    ledger = EvidenceContextSelectionLedger(**ledger_payload)

    allocation_payload = dict(result_payload["allocation"])
    allocation_payload["included_span_ids"] = tuple(allocation_payload["included_span_ids"])
    allocation_payload["budget_excluded_span_ids"] = tuple(allocation_payload["budget_excluded_span_ids"])
    allocation_payload["reason_codes"] = tuple(allocation_payload["reason_codes"])
    allocation = EvidenceContextAllocationResult(**allocation_payload)

    package_payload = result_payload.get("package")
    package = None
    if package_payload is not None:
        package_dict = dict(package_payload)
        package_dict["ordered_span_ids"] = tuple(package_dict["ordered_span_ids"])
        package_dict["ordered_content_hashes"] = tuple(package_dict["ordered_content_hashes"])
        package = EvidenceContextPackage(**package_dict)

    plan_payload = result_payload.get("prompt_plan")
    prompt_plan = None
    if plan_payload is not None:
        plan_dict = dict(plan_payload)
        plan_dict["missingness_reason_codes"] = tuple(plan_dict["missingness_reason_codes"])
        prompt_plan = EvidencePromptPlan(**plan_dict)

    artifact_payload = result_payload.get("prompt_artifact")
    prompt_artifact = EvidencePromptArtifact(**dict(artifact_payload)) if artifact_payload is not None else None

    build_payload = dict(result_payload["build_record"])
    build_payload["reason_codes"] = tuple(build_payload["reason_codes"])
    build_record = EvidencePreModelBuildRecord(**build_payload)
    source_handling_decision = result_payload.get("source_handling_decision")

    retention = payload.get("source_retention") or {}
    retained = bool(retention.get("exact_source_bytes_retained", True))

    return PersistedEvidencePreModelBundle(
        recorded_at=recorded_at,
        intent=intent,
        policy=policy,
        specification=specification,
        capability=capability,
        canonical_inventory=inventory,
        build_result=EvidencePreModelBuildResult(
            ledger=ledger,
            allocation=allocation,
            package=package,
            prompt_plan=prompt_plan,
            prompt_artifact=prompt_artifact,
            build_record=build_record,
            source_handling_decision=(
                dict(source_handling_decision) if isinstance(source_handling_decision, dict) else None
            ),
        ),
        exact_source_bytes_retained=retained,
    )


def _redacted_span(span: EvidenceSpan) -> EvidenceSpan:
    return replace(span, excerpt=REDACTED_SOURCE_EXCERPT, section_title="")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreModelPersistenceLineageError(message)


def _validate_known_at_lower_bound(recorded_at: datetime, canonical_inventory: tuple[EvidenceSpan, ...]) -> None:
    bounds: list[datetime] = []
    for span in canonical_inventory:
        bounds.append(span.created_at.astimezone(UTC))
        bounds.append(span.validated_at.astimezone(UTC))
    if not bounds:
        return
    earliest_known_at = max(bounds)
    if recorded_at < earliest_known_at:
        raise PreModelPersistenceLineageError(
            f"recorded_at {recorded_at.isoformat()} predates the evidence it contains "
            f"(earliest valid known-at coordinate is {earliest_known_at.isoformat()})"
        )


def _validate_bundle_lineage(
    *,
    intent: EvidenceExtractionIntent,
    policy: EvidenceContextSelectionPolicy,
    specification: EvidencePromptSpecification,
    capability: EvidenceCapabilityConstraint,
    canonical_inventory: tuple[EvidenceSpan, ...],
    build_result: EvidencePreModelBuildResult,
) -> None:
    ledger = build_result.ledger
    allocation = build_result.allocation
    package = build_result.package
    prompt_plan = build_result.prompt_plan
    artifact = build_result.prompt_artifact
    build = build_result.build_record

    _require(ledger.intent_id == intent.intent_id, "supplied intent does not match the build's selection ledger")
    _require(
        ledger.policy_identity == policy.policy_identity,
        "supplied policy does not match the build's selection ledger",
    )
    _require(allocation.ledger_id == ledger.ledger_id, "allocation does not belong to the build's selection ledger")
    _require(
        allocation.capability_identity == capability.constraint_identity,
        "supplied capability constraint does not match the build's allocation",
    )
    _require(
        allocation.prompt_specification_identity == specification.specification_identity,
        "supplied prompt specification does not match the build's allocation",
    )
    _require(
        allocation.available_input_bytes == capability.available_input_bytes,
        "supplied capability budget does not match the build's allocation",
    )

    spans_by_id = {span.span_id: span for span in canonical_inventory}
    _require(len(spans_by_id) == len(canonical_inventory), "canonical inventory contains duplicate span ids")
    _require(
        set(policy.required_span_ids).union(policy.optional_span_ids) == set(spans_by_id),
        "supplied policy coverage does not match the supplied canonical inventory",
    )
    _require(
        {decision.span_id for decision in ledger.decisions} == set(spans_by_id),
        "supplied canonical inventory does not match the build's ledger decisions",
    )
    for span in canonical_inventory:
        _require(
            evidence_text_digest(span.excerpt) == span.text_hash,
            f"supplied span {span.span_id} excerpt bytes do not match its claimed text_hash",
        )

    for decision in ledger.decisions:
        span = spans_by_id[decision.span_id]
        _require(
            decision.content_hash == span.text_hash
            and decision.start_offset == span.start_offset
            and decision.end_offset == span.end_offset,
            f"supplied span {decision.span_id} does not match the content recorded in the build's ledger",
        )

    if package is None:
        _require(build.package_id is None, "build references a package that was not supplied")
    else:
        _require(package.allocation_id == allocation.allocation_id, "package does not belong to the build's allocation")
        _require(
            package.ordered_span_ids == allocation.included_span_ids,
            "package ordering does not match the build's allocation",
        )
        _require(
            len(package.ordered_span_ids) == len(package.ordered_content_hashes),
            "package span ordering and content hashes have different lengths",
        )
        for span_id, content_hash in zip(package.ordered_span_ids, package.ordered_content_hashes, strict=True):
            _require(span_id in spans_by_id, f"package references span {span_id} outside the supplied inventory")
            _require(
                spans_by_id[span_id].text_hash == content_hash,
                f"supplied span {span_id} does not match the content hash recorded in the build's package",
            )
        _require(build.package_id == package.package_id, "build record does not reference the supplied package")

    if prompt_plan is None:
        _require(build.prompt_plan_id is None, "build references a prompt plan that was not supplied")
    else:
        _require(prompt_plan.intent_id == intent.intent_id, "prompt plan does not belong to the supplied intent")
        _require(
            prompt_plan.prompt_specification_identity == specification.specification_identity,
            "prompt plan does not belong to the supplied prompt specification",
        )
        _require(package is not None, "prompt plan was supplied without its context package")
        assert package is not None
        _require(prompt_plan.package_id == package.package_id, "prompt plan does not belong to the supplied package")
        _require(build.prompt_plan_id == prompt_plan.plan_id, "build record does not reference the supplied plan")

    if artifact is None:
        _require(build.prompt_artifact_id is None, "build references a prompt artifact that was not supplied")
    else:
        _require(prompt_plan is not None, "prompt artifact was supplied without its prompt plan")
        assert prompt_plan is not None
        assert package is not None
        _require(artifact.plan_id == prompt_plan.plan_id, "prompt artifact does not belong to the supplied plan")
        _require(artifact.ledger_id == ledger.ledger_id, "prompt artifact does not belong to the supplied ledger")
        _require(
            artifact.allocation_id == allocation.allocation_id,
            "prompt artifact does not belong to the supplied allocation",
        )
        _require(artifact.package_id == package.package_id, "prompt artifact does not belong to the supplied package")
        _require(
            artifact.prompt_specification_identity == specification.specification_identity
            and artifact.compiler_version == specification.compiler_version,
            "prompt artifact does not belong to the supplied prompt specification",
        )
        _require(
            build.prompt_artifact_id == artifact.artifact_id,
            "build record does not reference the supplied prompt artifact",
        )
        _validate_prompt_artifact(artifact)
        rendered = _render_prompt(
            intent=intent,
            specification=specification,
            spans=tuple(spans_by_id[span_id] for span_id in package.ordered_span_ids),
            missingness_reason_codes=prompt_plan.missingness_reason_codes,
        )
        encoded = rendered.encode(artifact.encoding)
        _require(
            hashlib.sha256(encoded).hexdigest() == artifact.content_hash,
            "supplied inventory does not re-render the build's exact prompt artifact",
        )
        _require(
            len(encoded) == artifact.measured_size_bytes,
            "supplied inventory does not re-render the build's exact prompt size",
        )

    _require(build.intent_id == intent.intent_id, "build record does not belong to the supplied intent")
    _require(build.ledger_id == ledger.ledger_id, "build record does not belong to the supplied ledger")
    _require(build.allocation_id == allocation.allocation_id, "build record does not belong to the supplied allocation")


def _span_from_payload(payload: dict[str, Any]) -> EvidenceSpan:
    item = dict(payload)
    item["created_at"] = _parse_time(str(item["created_at"]))
    item["validated_at"] = _parse_time(str(item["validated_at"]))
    return EvidenceSpan(**item)  # type: ignore[arg-type]


def _validate_prompt_artifact(artifact: EvidencePromptArtifact | None) -> None:
    if artifact is None or not artifact.content:
        return
    encoded = artifact.content.encode(artifact.encoding)
    if len(encoded) != artifact.measured_size_bytes:
        raise PreModelPersistenceCorruption("persisted prompt size mismatch")
    if hashlib.sha256(encoded).hexdigest() != artifact.content_hash:
        raise PreModelPersistenceCorruption("persisted prompt hash mismatch")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
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
