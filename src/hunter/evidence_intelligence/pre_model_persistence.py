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
    EvidencePromptArtifact,
    EvidencePromptPlan,
    EvidencePromptSpecification,
    _render_prompt,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.retention import (
    EvidenceRetentionDecision,
    legacy_retention_decision,
)

ReconstructionStatus = Literal["AVAILABLE", "UNAVAILABLE", "NOT_KNOWN_AT_CUTOFF"]

RETENTION_PROHIBITED_REASON_CODE = "EXACT_PROMPT_RETENTION_PROHIBITED"

# ADR 0031 "Data handling and retention" permits durably retaining source
# identities, revisions, ranges, and hashes, but requires artifact retention to
# be "at least as restrictive as the governing source classification and
# policy". Every span the governed policy marks non-retainable has its text
# replaced by this tombstone. Because prompt retention is prohibited whenever any
# *included* span is non-retainable, and that span is itself redacted here, a
# prohibited prompt can never be regenerated from the persisted bundle. The
# tombstone is never presented as, and must never be mistaken for, original
# content.
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
    retention: EvidenceRetentionDecision

    @property
    def build_record_id(self) -> str:
        return self.build_result.build_record.build_record_id

    @property
    def exact_source_bytes_retained(self) -> bool:
        """Derived from the governed retention decision, never stored as a flag."""

        return self.retention.retain_source_bytes


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
        """Persist one immutable pre-model build bundle, append-only.

        The supplied bundle is validated for internal lineage consistency before
        any INSERT: second-write conflict detection cannot protect the very
        first write, so a mismatched combination would otherwise be recorded
        permanently under a build identity it did not produce.

        Retention is taken from the build's governed ``EvidenceRetentionDecision``.
        This method accepts no retention parameter by design: a caller-supplied
        value would override governed policy at the durability boundary, which is
        precisely the authority ADR 0031 reserves to source policy.
        """
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

        retention = build_result.retention
        if retention.policy_identity != build_result.build_record.retention_policy_identity:
            raise PreModelPersistenceLineageError(
                "build record retention policy identity does not match its retention decision"
            )

        artifact = build_result.prompt_artifact
        if not retention.retain_prompt_bytes and artifact is not None and artifact.content:
            raise PreModelPersistenceLineageError(
                "exact prompt retention is prohibited but the build still carries exact prompt content"
            )

        non_retainable = set(retention.non_retainable_span_ids)
        if non_retainable:
            # Redaction is per span: a non-retainable span that never entered the
            # prompt suppresses only its own excerpt. Blanket redaction would
            # discard bytes the governing policy actually permits retaining.
            inventory = tuple(_redacted_span(span) if span.span_id in non_retainable else span for span in inventory)

        bundle = PersistedEvidencePreModelBundle(
            recorded_at=recorded_at.astimezone(UTC),
            intent=intent,
            policy=policy,
            specification=specification,
            capability=capability,
            canonical_inventory=inventory,
            build_result=build_result,
            retention=retention,
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
        if build.reconstruction_outcome == "AVAILABLE":
            # build_evidence_pre_model only records AVAILABLE together with a
            # retained, non-empty prompt artifact. Reaching this state without
            # those bytes therefore means the persisted bundle no longer matches
            # the build it claims to be, which is corruption -- not the ordinary
            # "exact prompt was never retainable" outcome. Downgrading it to
            # UNAVAILABLE would silently hide a broken durability guarantee.
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
            "decision": _jsonable(asdict(bundle.retention)),
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

    retention_payload = payload.get("source_retention") or {}
    decision_payload = retention_payload.get("decision")
    if decision_payload is None:
        # Pre-correction payloads recorded only a boolean. It is preserved as the
        # historical fact it was, under an explicit legacy policy identity, so the
        # absence of a governed policy stays visible instead of being backfilled
        # with a policy that never governed this bundle.
        #
        # The stored boolean is cross-checked against physical evidence rather
        # than trusted: a payload carrying redaction tombstones demonstrably did
        # not retain its source bytes, whatever the flag says. Reading the flag
        # alone would let a missing or wrong value claim retention that the
        # persisted bytes contradict.
        redacted_present = any(span.excerpt == REDACTED_SOURCE_EXCERPT for span in inventory)
        retention = legacy_retention_decision(
            exact_source_bytes_retained=(
                bool(retention_payload.get("exact_source_bytes_retained", True)) and not redacted_present
            )
        )
    else:
        decision_dict = dict(decision_payload)
        decision_dict["non_retainable_span_ids"] = tuple(decision_dict["non_retainable_span_ids"])
        decision_dict["span_classifications"] = tuple(
            (str(item[0]), str(item[1])) for item in decision_dict["span_classifications"]
        )
        decision_dict["reason_codes"] = tuple(decision_dict["reason_codes"])
        retention = EvidenceRetentionDecision(**decision_dict)

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
            retention=retention,
        ),
        retention=retention,
    )


def _redacted_span(span: EvidenceSpan) -> EvidenceSpan:
    """Strip raw source text while preserving identities, ranges, and hashes.

    ``excerpt`` is the exact text the prompt is rendered from, and
    ``section_title`` is likewise source-derived free text. Everything retained
    here (identities, offsets, versions, coordinates, content hashes, status,
    timestamps) is explicitly listed by ADR 0031 as minimum persistable
    provenance. ``excerpt`` cannot be emptied because EvidenceSpan requires it
    to be non-empty, so it carries an unambiguous tombstone instead.
    """
    return replace(span, excerpt=REDACTED_SOURCE_EXCERPT, section_title="")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreModelPersistenceLineageError(message)


def _validate_known_at_lower_bound(recorded_at: datetime, canonical_inventory: tuple[EvidenceSpan, ...]) -> None:
    """Reject a known-at coordinate that predates the evidence it contains.

    ``strict_known_bundle`` selects on ``recorded_at <= cutoff``, so a backdated
    coordinate would let a cutoff query return a bundle built from spans that
    did not yet exist at that cutoff -- a false historical-knowledge claim of
    exactly the kind ADR 0031 forbids. The bundle's true lower bound is the
    latest creation/validation time among its own persisted spans.
    """
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
    """Fail closed unless every supplied input actually produced this build.

    Each deterministic identity in the build result is recomputed from the
    supplied inputs and compared. Without this, the first save() of a bundle
    that mixes one build's result with another build's intent/policy/inventory
    would be accepted and become permanent, silently corrupting reconstruction
    provenance for a record that can never be rewritten.
    """
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
    _require(
        len(spans_by_id) == len(canonical_inventory),
        "canonical inventory contains duplicate span ids",
    )
    _require(
        set(policy.required_span_ids).union(policy.optional_span_ids) == set(spans_by_id),
        "supplied policy coverage does not match the supplied canonical inventory",
    )
    _require(
        {decision.span_id for decision in ledger.decisions} == set(spans_by_id),
        "supplied canonical inventory does not match the build's ledger decisions",
    )
    for span in canonical_inventory:
        # `text_hash` is a claim about the excerpt bytes. Evidence Intelligence
        # has exactly one convention for that claim (models.evidence_text_digest,
        # used by intake, the only original producer of EvidenceSpan), so it can
        # be recomputed rather than trusted. Without this, an excerpt altered
        # while its stale hash and offsets were left intact would be persisted
        # as provenance for text it does not contain.
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

        # `text_hash` is caller-supplied metadata with no defined relationship
        # to `excerpt` anywhere in this codebase, so comparing hashes cannot
        # detect an excerpt that was altered while its hash was left intact.
        # Re-rendering is the only available proof that these exact span bytes
        # are the ones that produced this artifact. This works even when
        # retention is prohibited, because `content_hash` is always the digest
        # of the full rendered prompt regardless of whether it was retained.
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
    _require(
        build.allocation_id == allocation.allocation_id,
        "build record does not belong to the supplied allocation",
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
