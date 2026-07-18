from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from hunter.execution.canonicalization import canonicalize
from hunter.execution.hashing import stable_identifier
from hunter.persistence import AnalyticalRecord, AuthorizedAnalyticalWrite
from hunter.persistence.sql.exceptions import AnalyticalCorrectionConflictError, AnalyticalWriteAuthorizationError
from hunter.prediction_evaluation.models import (
    AggregateRequest,
    EvaluationContext,
    EvaluationPolicy,
    LifecycleState,
    OutcomeObservation,
    PredictionPublication,
)
from hunter.prediction_evaluation.store import (
    ACCURACY_TYPE,
    CALIBRATION_TYPE,
    EVALUATION_TYPE,
    POLICY_TYPE,
    PUBLICATION_TYPE,
    RECORD_PREFIX,
    SCHEMA_VERSION,
    PredictionEvaluationRepository,
    PredictionEvaluationStore,
)

ALLOWED_TRANSITIONS: Mapping[LifecycleState, frozenset[LifecycleState]] = {
    "pending": frozenset({"due", "invalidated", "superseded"}),
    "due": frozenset({"awaiting-data", "evaluable", "unevaluable", "invalidated", "superseded"}),
    "awaiting-data": frozenset({"evaluable", "unevaluable", "invalidated", "superseded"}),
    "evaluable": frozenset({"evaluated-correct", "evaluated-incorrect", "invalidated", "superseded"}),
    "evaluated-correct": frozenset({"superseded"}),
    "evaluated-incorrect": frozenset({"superseded"}),
    "unevaluable": frozenset({"superseded"}),
    "invalidated": frozenset({"superseded"}),
    "superseded": frozenset(),
}


class PredictionEvaluationService:
    """Sole authorization boundary for canonical prediction evaluation."""

    def persist_policy(
        self,
        policy: EvaluationPolicy,
        store: PredictionEvaluationStore,
        context: EvaluationContext,
    ) -> AnalyticalRecord:
        payload = self._base_payload(
            target_identity=_policy_target(policy.policy_id, policy.policy_version),
            policy=_plain(policy),
        )
        record = _record(
            semantic_type=POLICY_TYPE,
            target_identity=_policy_target(policy.policy_id, policy.policy_version),
            identity_key={"policy_id": policy.policy_id, "policy_version": policy.policy_version},
            effective_at=context.known_by,
            context=context,
            payload=payload,
            model_version=None,
            methodology=policy.methodology_version,
        )
        with store.repository() as repository:
            return repository.persist(AuthorizedAnalyticalWrite(record, "create"))

    def publish(
        self,
        publication: PredictionPublication,
        store: PredictionEvaluationStore,
        context: EvaluationContext,
        *,
        predecessor_publication: AnalyticalRecord | None = None,
        correction_reason: str | None = None,
    ) -> tuple[AnalyticalRecord, AnalyticalRecord]:
        if publication.recorded_at != context.recorded_at or publication.known_at != context.known_by:
            raise ValueError("publication clocks must equal the service authorization context")
        with store.repository() as repository:
            policy_record = self._load_policy(repository, publication.policy_id, publication.policy_version)
            policy = _policy_from_record(policy_record)
            if (
                policy_record.recorded_at > publication.published_at
                or policy_record.known_at is None
                or policy_record.known_at > publication.known_at
            ):
                raise ValueError("bound policy was not strict-known before prediction publication")
            self._validate_publication(publication, policy)
            current = repository.current(PUBLICATION_TYPE, publication.prediction_id)
            correcting = predecessor_publication is not None
            if current is not None and not correcting:
                predecessor_publication = None
            if correcting:
                if current != predecessor_publication:
                    raise AnalyticalCorrectionConflictError("publication correction must use the current predecessor")
                if not correction_reason or not correction_reason.strip():
                    raise ValueError("publication correction requires a reason")
                if context.recorded_at >= publication.due_at:
                    raise ValueError("prediction contract cannot be corrected at or after its due time")
            elif correction_reason is not None:
                raise ValueError("correction_reason requires a predecessor")

            publication_record = self._publication_record(
                publication,
                policy_record,
                context,
                predecessor=predecessor_publication,
                correction_reason=correction_reason,
            )
            initial_predecessor = repository.current(EVALUATION_TYPE, publication.prediction_id) if correcting else None
            initial = self._evaluation_record(
                publication_record,
                policy_record,
                "pending",
                context,
                predecessor=initial_predecessor,
                reason="canonical publication accepted",
                outcome=None,
                correctness=None,
                correction_reason=correction_reason if initial_predecessor else None,
            )
            stored = repository.persist_many(
                (
                    AuthorizedAnalyticalWrite(publication_record, "correct" if correcting else "create"),
                    AuthorizedAnalyticalWrite(initial, "correct" if initial_predecessor else "create"),
                )
            )
            return stored[0], stored[1]

    def transition(
        self,
        prediction_id: str,
        target_state: LifecycleState,
        store: PredictionEvaluationStore,
        context: EvaluationContext,
        *,
        reason: str,
    ) -> AnalyticalRecord:
        if target_state in {"evaluable", "evaluated-correct", "evaluated-incorrect"}:
            raise ValueError("outcome-dependent states require evaluate()")
        with store.repository() as repository:
            publication, policy, current = self._current_bundle(repository, prediction_id)
            if current.payload.get("state") == target_state:
                return current
            self._require_transition(current, target_state)
            if target_state == "due" and context.recorded_at < publication.effective_at:
                raise ValueError("transition context cannot precede publication")
            if target_state == "due" and context.recorded_at < _payload_time(publication, "due_at"):
                raise ValueError("prediction is not due")
            successor = self._evaluation_record(
                publication,
                policy,
                target_state,
                context,
                predecessor=current,
                reason=reason,
                outcome=None,
                correctness=None,
                correction_reason=f"lifecycle transition: {current.payload['state']} -> {target_state}",
            )
            return repository.persist(AuthorizedAnalyticalWrite(successor, "correct"))

    def evaluate(
        self,
        prediction_id: str,
        outcome: OutcomeObservation | None,
        store: PredictionEvaluationStore,
        context: EvaluationContext,
    ) -> AnalyticalRecord:
        with store.repository() as repository:
            publication, policy_record, current = self._current_bundle(repository, prediction_id)
            current_state = str(current.payload["state"])
            if current_state in {"evaluated-correct", "evaluated-incorrect"}:
                return current
            if current_state not in {"due", "awaiting-data"}:
                raise ValueError("prediction must be due or awaiting-data before evaluation")
            policy = _policy_from_record(policy_record)
            due_at = _payload_time(publication, "due_at")
            deadline = due_at + timedelta(seconds=policy.outcome_data_deadline_seconds)
            target_state, reason = self._outcome_eligibility(publication, policy, outcome, context, deadline)
            if target_state != "evaluable":
                if current_state == target_state:
                    return current
                self._require_transition(current, target_state)
                successor = self._evaluation_record(
                    publication,
                    policy_record,
                    target_state,
                    context,
                    predecessor=current,
                    reason=reason,
                    outcome=outcome,
                    correctness=None,
                    correction_reason=f"evaluation eligibility: {reason}",
                )
                return repository.persist(AuthorizedAnalyticalWrite(successor, "correct"))

            evaluable = self._evaluation_record(
                publication,
                policy_record,
                "evaluable",
                context,
                predecessor=current,
                reason="compatible strict-known outcome selected",
                outcome=outcome,
                correctness=None,
                correction_reason="authorized outcome selection",
            )
            correctness, calculation = _correctness(publication.payload["publication"], policy, outcome)
            terminal_state: LifecycleState = "evaluated-correct" if correctness else "evaluated-incorrect"
            terminal = self._evaluation_record(
                publication,
                policy_record,
                terminal_state,
                context,
                predecessor=evaluable,
                reason=calculation,
                outcome=outcome,
                correctness=correctness,
                correction_reason="deterministic policy evaluation",
            )
            _, stored = repository.persist_many(
                (
                    AuthorizedAnalyticalWrite(evaluable, "correct"),
                    AuthorizedAnalyticalWrite(terminal, "correct"),
                )
            )
            return stored

    def aggregate(
        self,
        request: AggregateRequest,
        store: PredictionEvaluationStore,
        context: EvaluationContext,
    ) -> tuple[AnalyticalRecord, AnalyticalRecord]:
        with store.repository() as repository:
            policy_record = self._load_policy(repository, request.policy_id, request.policy_version)
            policy = _policy_from_record(policy_record)
            evaluations = tuple(
                self._load_current_evaluation(repository, identity) for identity in request.evaluation_ids
            )
            self._validate_aggregate_compatibility(request, evaluations)
            eligible_ids = {
                record.id
                for record in repository.by_semantic_type(EVALUATION_TYPE)
                if repository.current(EVALUATION_TYPE, str(record.payload["target_identity"])) == record
                and request.window_start <= record.effective_at <= request.window_end
                and (not request.target_ids or record.payload["target_identity"] in request.target_ids)
                and record.payload.get("policy_id") == request.policy_id
                and record.payload.get("policy_version") == request.policy_version
                and record.payload.get("model_version") == request.model_version
                and record.payload.get("methodology_version") == request.methodology_version
                and record.payload.get("configuration_version") == request.configuration_version
            }
            if eligible_ids != set(request.evaluation_ids):
                raise ValueError("aggregate source IDs must equal the complete declared cohort")
            states = Counter(str(record.payload["state"]) for record in evaluations)
            included = tuple(
                record
                for record in evaluations
                if record.payload["state"] in {"evaluated-correct", "evaluated-incorrect"}
            )
            numerator = len([record for record in included if record.payload["state"] == "evaluated-correct"])
            denominator = len(included)
            sufficient = denominator >= policy.minimum_sample_size
            accuracy = round(numerator / denominator, 10) if sufficient and denominator else None
            interval = _wilson(numerator, denominator) if sufficient else None
            fingerprint = sha256(canonicalize(tuple(record.id for record in evaluations))).hexdigest()
            common = {
                "aggregate_id": request.aggregate_id,
                "cohort": request.cohort,
                "filter_definition": request.filter_definition,
                "target_ids": list(request.target_ids),
                "window_start": request.window_start.isoformat(),
                "window_end": request.window_end.isoformat(),
                "policy_id": request.policy_id,
                "policy_version": request.policy_version,
                "model_version": request.model_version,
                "methodology_version": request.methodology_version,
                "configuration_version": request.configuration_version,
                "source_evaluation_ids": [record.id for record in evaluations],
                "source_record_fingerprint": fingerprint,
                "numerator": numerator,
                "denominator": denominator,
                "exclusions": dict(
                    sorted((key, value) for key, value in states.items() if not key.startswith("evaluated-"))
                ),
                "status": "available" if sufficient else "insufficient-sample",
            }
            accuracy_payload = self._base_payload(
                target_identity=request.aggregate_id,
                aggregate={**common, "accuracy": accuracy, "confidence_interval_95_wilson": interval},
            )
            probabilities = [record.payload.get("forecast_probability") for record in included]
            calibration_sufficient = sufficient and all(isinstance(value, int | float) for value in probabilities)
            brier = None
            bins: list[dict[str, object]] = []
            if calibration_sufficient:
                outcomes = [1.0 if record.payload["state"] == "evaluated-correct" else 0.0 for record in included]
                brier = round(
                    math.fsum(
                        (float(probability) - actual) ** 2
                        for probability, actual in zip(probabilities, outcomes, strict=True)
                    )
                    / denominator,
                    10,
                )
                bins = _calibration_bins(probabilities, outcomes, policy.minimum_calibration_bin_size)
            calibration_payload = self._base_payload(
                target_identity=request.aggregate_id,
                aggregate={
                    **common,
                    "status": "available" if calibration_sufficient else "insufficient-sample",
                    "brier_score": brier,
                    "reliability_bins": bins,
                },
            )
            predecessor_accuracy = repository.current(ACCURACY_TYPE, request.aggregate_id)
            predecessor_calibration = repository.current(CALIBRATION_TYPE, request.aggregate_id)
            if (
                predecessor_accuracy is not None
                and predecessor_calibration is not None
                and predecessor_accuracy.payload == accuracy_payload
                and predecessor_calibration.payload == calibration_payload
            ):
                return predecessor_accuracy, predecessor_calibration
            accuracy_record = _record(
                semantic_type=ACCURACY_TYPE,
                target_identity=request.aggregate_id,
                identity_key={"aggregate_id": request.aggregate_id, "fingerprint": fingerprint},
                effective_at=request.window_end,
                context=context,
                payload=accuracy_payload,
                model_version=request.model_version,
                methodology=request.methodology_version,
                source_ids=tuple(record.id for record in evaluations),
                source_versions=tuple(record.schema_version for record in evaluations),
                predecessor=predecessor_accuracy,
                correction_reason="aggregate source set correction" if predecessor_accuracy else None,
            )
            calibration_record = _record(
                semantic_type=CALIBRATION_TYPE,
                target_identity=request.aggregate_id,
                identity_key={"aggregate_id": request.aggregate_id, "fingerprint": fingerprint},
                effective_at=request.window_end,
                context=context,
                payload=calibration_payload,
                model_version=request.model_version,
                methodology=request.methodology_version,
                source_ids=tuple(record.id for record in evaluations),
                source_versions=tuple(record.schema_version for record in evaluations),
                predecessor=predecessor_calibration,
                correction_reason="aggregate source set correction" if predecessor_calibration else None,
            )
            stored = repository.persist_many(
                (
                    AuthorizedAnalyticalWrite(accuracy_record, "correct" if predecessor_accuracy else "create"),
                    AuthorizedAnalyticalWrite(calibration_record, "correct" if predecessor_calibration else "create"),
                )
            )
            return stored[0], stored[1]

    @staticmethod
    def classify_legacy_operational_prediction(payload: Mapping[str, object]) -> dict[str, object]:
        return {
            "prediction_id": payload.get("prediction_id"),
            "classification": "legacy-unevaluable",
            "reason": "operational record lacks the complete pre-outcome canonical publication contract",
        }

    @staticmethod
    def _base_payload(*, target_identity: str, **native: object) -> dict[str, object]:
        return {
            "authority_classification": "canonical-evaluation",
            "target_identity": target_identity,
            **native,
        }

    @staticmethod
    def _load_policy(repository: PredictionEvaluationRepository, policy_id: str, version: str) -> AnalyticalRecord:
        record = repository.current(POLICY_TYPE, _policy_target(policy_id, version))
        if record is None:
            raise AnalyticalWriteAuthorizationError("bound immutable evaluation policy does not exist")
        return record

    @staticmethod
    def _validate_publication(publication: PredictionPublication, policy: EvaluationPolicy) -> None:
        if (
            publication.policy_id != policy.policy_id
            or publication.policy_version != policy.policy_version
            or publication.claim_type != policy.claim_type
            or publication.entity_type != policy.entity_type
            or publication.operator != policy.allowed_operator
            or publication.measurement_unit != policy.measurement_unit
        ):
            raise ValueError("publication is incompatible with its bound policy")
        if policy.comparison_mode == "benchmark-relative" and publication.benchmark_id is None:
            raise ValueError("benchmark-relative publication requires benchmark_id")

    def _publication_record(
        self,
        publication: PredictionPublication,
        policy_record: AnalyticalRecord,
        context: EvaluationContext,
        *,
        predecessor: AnalyticalRecord | None,
        correction_reason: str | None,
    ) -> AnalyticalRecord:
        payload = self._base_payload(
            target_identity=publication.prediction_id,
            publication=_plain(publication),
            policy_record_id=policy_record.id,
            policy_canonical_hash=sha256(canonicalize(policy_record.payload)).hexdigest(),
        )
        return _record(
            semantic_type=PUBLICATION_TYPE,
            target_identity=publication.prediction_id,
            identity_key={
                "prediction_id": publication.prediction_id,
                "predecessor": predecessor.id if predecessor else None,
            },
            effective_at=publication.effective_at,
            context=context,
            payload=payload,
            model_version=publication.model_version,
            methodology=publication.methodology_version,
            source_ids=(*publication.source_record_ids, policy_record.id),
            source_versions=(*publication.source_versions, policy_record.schema_version),
            evidence=tuple(sorted(set(publication.evidence_references + publication.baseline_evidence_references))),
            predecessor=predecessor,
            correction_reason=correction_reason,
        )

    def _evaluation_record(
        self,
        publication: AnalyticalRecord,
        policy: AnalyticalRecord,
        state: LifecycleState,
        context: EvaluationContext,
        *,
        predecessor: AnalyticalRecord | None,
        reason: str,
        outcome: OutcomeObservation | None,
        correctness: bool | None,
        correction_reason: str | None,
    ) -> AnalyticalRecord:
        target = str(publication.payload["target_identity"])
        payload = self._base_payload(
            target_identity=target,
            state=state,
            reason=reason,
            correctness=correctness,
            publication_record_id=publication.id,
            policy_record_id=policy.id,
            policy_id=publication.payload["publication"]["policy_id"],
            policy_version=publication.payload["publication"]["policy_version"],
            model_version=publication.model_version,
            methodology_version=publication.payload["publication"]["methodology_version"],
            configuration_version=publication.payload["publication"]["configuration_version"],
            forecast_probability=publication.payload["publication"].get("forecast_probability"),
            outcome=_plain(outcome) if outcome else None,
        )
        source_ids = (publication.id, policy.id, *((outcome.observation_id,) if outcome else ()))
        source_versions = (
            publication.schema_version,
            policy.schema_version,
            *((outcome.source_version,) if outcome else ()),
        )
        return _record(
            semantic_type=EVALUATION_TYPE,
            target_identity=target,
            identity_key={
                "prediction_id": target,
                "state": state,
                "predecessor": predecessor.id if predecessor else None,
            },
            effective_at=outcome.effective_at if outcome else publication.effective_at,
            context=context,
            payload=payload,
            model_version=publication.model_version,
            methodology=publication.methodology_fingerprint,
            source_ids=source_ids,
            source_versions=source_versions,
            evidence=outcome.evidence_references if outcome else publication.evidence_references,
            predecessor=predecessor,
            correction_reason=correction_reason,
        )

    @staticmethod
    def _current_bundle(
        repository: PredictionEvaluationRepository, prediction_id: str
    ) -> tuple[AnalyticalRecord, AnalyticalRecord, AnalyticalRecord]:
        publication = repository.current(PUBLICATION_TYPE, prediction_id)
        evaluation = repository.current(EVALUATION_TYPE, prediction_id)
        if publication is None or evaluation is None:
            raise LookupError("canonical prediction publication/lifecycle does not exist")
        policy = PredictionEvaluationService._load_policy(
            repository,
            str(publication.payload["publication"]["policy_id"]),
            str(publication.payload["publication"]["policy_version"]),
        )
        return publication, policy, evaluation

    @staticmethod
    def _require_transition(current: AnalyticalRecord, target: LifecycleState) -> None:
        state = str(current.payload["state"])
        if target not in ALLOWED_TRANSITIONS[state]:  # type: ignore[index]
            raise ValueError(f"invalid prediction-evaluation transition: {state} -> {target}")

    @staticmethod
    def _outcome_eligibility(
        publication: AnalyticalRecord,
        policy: EvaluationPolicy,
        outcome: OutcomeObservation | None,
        context: EvaluationContext,
        deadline: datetime,
    ) -> tuple[LifecycleState, str]:
        if outcome is None:
            return (
                ("unevaluable", "outcome data deadline passed")
                if context.recorded_at > deadline
                else ("awaiting-data", "authorized outcome is not yet available")
            )
        native = publication.payload["publication"]
        if (
            outcome.target_id != native["target_id"]
            or outcome.entity_type != native["entity_type"]
            or outcome.source_type != policy.outcome_source_type
            or outcome.source_version != policy.outcome_source_version
            or outcome.measurement_unit != policy.measurement_unit
        ):
            return "invalidated", "outcome target, source, version, entity, or units violate the bound policy"
        if outcome.known_at is None:
            return "unevaluable", "outcome has unknown known-time provenance"
        if outcome.recorded_at > context.known_by or outcome.known_at > context.known_by:
            return (
                ("unevaluable", "outcome was unavailable by the declared data deadline")
                if context.recorded_at > deadline
                else ("awaiting-data", "outcome is post-cutoff")
            )
        due = _payload_time(publication, "due_at")
        window = timedelta(seconds=policy.observation_window_seconds)
        if not due - window <= outcome.effective_at <= due + window:
            return "unevaluable", "outcome observation is outside the policy window"
        if policy.comparison_mode == "benchmark-relative" and (
            outcome.benchmark_id != native.get("benchmark_id") or outcome.benchmark_value is None
        ):
            return "unevaluable", "compatible benchmark observation is unavailable"
        return "evaluable", "compatible strict-known outcome selected"

    @staticmethod
    def _load_current_evaluation(repository: PredictionEvaluationRepository, identity: str) -> AnalyticalRecord:
        record = repository.load(identity)
        if record is None or record.semantic_type != EVALUATION_TYPE:
            raise ValueError("aggregate source must be an exact canonical evaluation record")
        current = repository.current(EVALUATION_TYPE, str(record.payload["target_identity"]))
        if current != record:
            raise ValueError("aggregate source evaluation is superseded")
        return record

    @staticmethod
    def _validate_aggregate_compatibility(request: AggregateRequest, evaluations: tuple[AnalyticalRecord, ...]) -> None:
        for record in evaluations:
            payload = record.payload
            if (
                payload.get("policy_id") != request.policy_id
                or payload.get("policy_version") != request.policy_version
                or payload.get("model_version") != request.model_version
                or payload.get("methodology_version") != request.methodology_version
                or payload.get("configuration_version") != request.configuration_version
                or not request.window_start <= record.effective_at <= request.window_end
            ):
                raise ValueError("aggregate cannot mix incompatible evaluations")


def _record(
    *,
    semantic_type: str,
    target_identity: str,
    identity_key: object,
    effective_at: datetime,
    context: EvaluationContext,
    payload: dict[str, object],
    model_version: str | None,
    methodology: str | None,
    source_ids: tuple[str, ...] = (),
    source_versions: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    predecessor: AnalyticalRecord | None = None,
    correction_reason: str | None = None,
) -> AnalyticalRecord:
    identity = stable_identifier(
        RECORD_PREFIX, {"semantic_type": semantic_type, "identity": identity_key}, schema_version=SCHEMA_VERSION
    )
    return AnalyticalRecord(
        id=identity,
        schema_version=SCHEMA_VERSION,
        created_at=context.recorded_at,
        effective_at=effective_at,
        logical_identity=f"{semantic_type}:{target_identity}",
        semantic_type=semantic_type,
        known_at=context.known_by,
        known_time_limitation=None,
        model_version=model_version,
        methodology_fingerprint=methodology,
        source_record_ids=source_ids,
        source_versions=source_versions,
        evidence_references=evidence,
        confidence=None,
        missing_evidence=(),
        supersedes_id=predecessor.id if predecessor else None,
        correction_reason=correction_reason,
        payload=payload,
    )


def _policy_target(policy_id: str, version: str) -> str:
    return f"{policy_id}:{version}"


def _policy_from_record(record: AnalyticalRecord) -> EvaluationPolicy:
    return EvaluationPolicy(**record.payload["policy"])


def _payload_time(record: AnalyticalRecord, name: str) -> datetime:
    return datetime.fromisoformat(str(record.payload["publication"][name])).astimezone(UTC)


def _correctness(
    publication: Mapping[str, object], policy: EvaluationPolicy, outcome: OutcomeObservation | None
) -> tuple[bool, str]:
    if outcome is None:
        raise ValueError("correctness requires an outcome")
    if policy.comparison_mode == "absolute-threshold":
        measured = outcome.value
    elif policy.comparison_mode == "change-from-baseline":
        measured = outcome.value - float(publication["baseline_value"])
    else:
        if outcome.benchmark_value is None:
            raise ValueError("benchmark-relative correctness requires benchmark value")
        measured = outcome.value - outcome.benchmark_value
    measured = round(measured, policy.required_precision)
    threshold = round(float(publication["threshold"]), policy.required_precision)
    tolerance = policy.tolerance
    operator = str(publication["operator"])
    result = {
        "gt": measured > threshold + tolerance,
        "gte": measured >= threshold - tolerance,
        "lt": measured < threshold - tolerance,
        "lte": measured <= threshold + tolerance,
        "eq": abs(measured - threshold) <= tolerance,
    }[operator]
    return result, f"{measured} {operator} {threshold} with tolerance {tolerance} -> {result}"


def _wilson(numerator: int, denominator: int) -> dict[str, float]:
    z = 1.959963984540054
    proportion = numerator / denominator
    center = (proportion + z * z / (2 * denominator)) / (1 + z * z / denominator)
    margin = (
        z
        * math.sqrt((proportion * (1 - proportion) + z * z / (4 * denominator)) / denominator)
        / (1 + z * z / denominator)
    )
    return {"lower": round(max(0.0, center - margin), 10), "upper": round(min(1.0, center + margin), 10)}


def _calibration_bins(probabilities: list[object], outcomes: list[float], minimum: int) -> list[dict[str, object]]:
    bins: list[dict[str, object]] = []
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = [
            (float(probability), outcome)
            for probability, outcome in zip(probabilities, outcomes, strict=True)
            if (lower <= float(probability) <= upper if index == 9 else lower <= float(probability) < upper)
        ]
        sufficient = len(members) >= minimum
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "status": "available" if sufficient else "insufficient-sample",
                "mean_forecast": round(sum(item[0] for item in members) / len(members), 10) if sufficient else None,
                "observed_rate": round(sum(item[1] for item in members) / len(members), 10) if sufficient else None,
            }
        )
    return bins


def _plain(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported prediction-evaluation value: {type(value).__name__}")
