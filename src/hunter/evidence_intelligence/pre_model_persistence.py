from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.evidence_intelligence.models import EvidenceSpan
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
    EvidencePromptArtifact,
    EvidencePromptPlan,
    EvidencePromptSpecification,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

ReconstructionStatus = Literal["AVAILABLE", "UNAVAILABLE", "NOT_KNOWN_AT_CUTOFF"]


class PreModelPersistenceConflict(RuntimeError):
    """Raised when an existing deterministic build id is reused with different bytes."""


class PreModelPersistenceCorruption(RuntimeError):
    """Raised when persisted payload no longer matches its recorded identity/hash."""


@dataclass(frozen=True)
class PersistedEvidencePreModelBundle:
    recorded_at: datetime
    intent: EvidenceExtractionIntent
    policy: EvidenceContextSelectionPolicy
    specification: EvidencePromptSpecification
    capability: EvidenceCapabilityConstraint
    canonical_inventory: tuple[EvidenceSpan, ...]
    build_result: EvidencePreModelBuildResult

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
    """Append-only durability for ADR 0031 provider-free pre-model build evidence.

    The repository persists a complete immutable build bundle in the existing
    Evidence Intelligence SQLite database. Strict-known reconstruction reads only
    the persisted bundle; it never consults current/latest EvidenceSpan rows.
    """

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
    ) -> PersistedEvidencePreModelBundle:
        _aware("recorded_at", recorded_at)
        bundle = PersistedEvidencePreModelBundle(
            recorded_at=recorded_at.astimezone(UTC),
            intent=intent,
            policy=policy,
            specification=specification,
            capability=capability,
            canonical_inventory=tuple(canonical_inventory),
            build_result=build_result,
        )
        payload = _bundle_payload(bundle)
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
        if build.reconstruction_outcome != "AVAILABLE" or artifact is None or not artifact.content:
            reason = next(
                (code for code in build.reason_codes if code == "EXACT_PROMPT_RETENTION_PROHIBITED"),
                "EXACT_PRE_MODEL_RECONSTRUCTION_UNAVAILABLE",
            )
            return EvidencePreModelReconstruction(
                status="UNAVAILABLE",
                reason_code=reason,
                bundle=bundle,
            )

        return EvidencePreModelReconstruction(
            status="AVAILABLE",
            reason_code="EXACT_PRE_MODEL_RECONSTRUCTION_AVAILABLE",
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
        ),
    )


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
