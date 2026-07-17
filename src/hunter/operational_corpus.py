from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.execution.canonicalization import normalize
from hunter.execution.identity import identity
from hunter.execution.run import PipelineRun
from hunter.persistence.records import OperationalAttemptRecord
from hunter.plugins.contracts import PipelineContext


class OperationalCorpusRecorder:
    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    def record(
        self,
        context: PipelineContext,
        *,
        run: PipelineRun,
        attempt: OperationalAttemptRecord,
        artifact_ids: tuple[str, ...],
    ) -> None:
        if self.settings is not None and not bool(getattr(self.settings, "enabled", True)):
            return
        root = Path(getattr(self.settings, "root", Path("data/operational_corpus")))
        filename = str(getattr(self.settings, "filename", "executions.jsonl"))
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _payload(context, run=run, attempt=attempt, artifact_ids=artifact_ids)
        existing = _entry_ids(path)
        if payload["corpus_entry_id"] in existing:
            return
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        _record_prediction(root, self.settings, payload)
        _record_opportunities(root, self.settings, payload)
        _record_outcomes(root, self.settings, payload)
        monitor_due_predictions(self.settings, at=datetime.now(tz=UTC))
        _write_readiness(root, self.settings)


def monitor_due_predictions(settings: Any | None = None, *, at: datetime | None = None) -> None:
    if settings is not None and not bool(getattr(settings, "enabled", True)):
        return
    root = Path(getattr(settings, "root", Path("data/operational_corpus")))
    now = at or datetime.now(tz=UTC)
    predictions = _read_jsonl(root / str(getattr(settings, "prediction_filename", "predictions.jsonl")))
    executions = _read_jsonl(root / str(getattr(settings, "filename", "executions.jsonl")))
    sample_path = root / str(getattr(settings, "validation_sample_filename", "validation_samples.jsonl"))
    closed_ids = {
        str(sample["prediction_id"])
        for sample in _read_jsonl(sample_path)
        if isinstance(sample.get("prediction_id"), str)
    }
    outcomes = []
    samples = []
    closures = []
    for prediction in predictions:
        prediction_id = str(prediction.get("prediction_id", ""))
        if not prediction_id or prediction_id in closed_ids:
            continue
        horizon = _parse_dt(prediction.get("evaluation_horizon_at"))
        if horizon is None or horizon > now:
            continue
        completion = _completion_payload(prediction, executions, now=now)
        if completion is None:
            continue
        outcome_records = _outcome_records(completion, prediction, _plain_sequence(completion.get("realized_outcomes")))
        validation_samples = _validation_samples(completion, prediction, outcome_records)
        outcomes.extend(outcome_records)
        samples.extend(validation_samples)
        closures.append(_prediction_closure(prediction, outcome_records, completion))
        closed_ids.add(prediction_id)
    _append_unique(root / str(getattr(settings, "outcome_filename", "outcomes.jsonl")), tuple(outcomes), "outcome_id")
    _append_unique(sample_path, tuple(samples), "validation_sample_id")
    closure_path = root / str(getattr(settings, "prediction_closure_filename", "prediction_closures.jsonl"))
    _append_unique(closure_path, tuple(closures), "prediction_closure_id")
    _write_prediction_state(root, settings)
    _write_readiness(root, settings)


def _payload(
    context: PipelineContext,
    *,
    run: PipelineRun,
    attempt: OperationalAttemptRecord,
    artifact_ids: tuple[str, ...],
) -> dict[str, object]:
    started = attempt.started_at
    finished = attempt.finished_at
    duration_seconds = (
        round((finished - started).total_seconds(), 6) if started is not None and finished is not None else None
    )
    payload = {
        "execution_identity": run.run_id,
        "pipeline_run_id": run.run_id,
        "attempt_id": attempt.attempt_id,
        "attempt_number": attempt.attempt_number,
        "run_type": run.run_type,
        "target_id": run.target_id,
        "target_type": run.target_type,
        "requested_at": _dt(run.requested_at),
        "effective_at": _dt(run.effective_at),
        "started_at": _dt(started),
        "finished_at": _dt(finished),
        "execution_status": attempt.status,
        "execution_duration_seconds": duration_seconds,
        "discovered_opportunities": _discovered_opportunities(context),
        "observations": _observations(context),
        "evidence": _evidence(context),
        "intelligence": _intelligence(context),
        "rankings": _sequence(context.get("rankings", ())),
        "confidence_values": _confidence_values(context),
        "final_recommendations": _sequence(context.get("final_recommendations", context.get("recommendations", ()))),
        "realized_outcomes": _sequence(context.get("realized_outcomes", ())),
        "benchmark_values": _sequence(context.get("benchmark_values", ())),
        "benchmark_outcomes": _sequence(context.get("benchmark_outcomes", ())),
        "evaluation_horizon_at": _optional_datetime(context.get("evaluation_horizon_at")),
        "market_cycle_id": context.get("market_cycle_id"),
        "observation_window_days": context.get("observation_window_days"),
        "artifact_ids": tuple(sorted(artifact_ids)),
        "failure_summary": attempt.error_summary,
        "warning_summary": attempt.warning_summary,
        "failures": _sequence(context.persistence_errors),
        "retries": attempt.attempt_number - 1,
        "recovery_information": _recovery_information(context),
        "persistence_events": _persistence_events(context),
    }
    normalized = _jsonable(payload)
    if not isinstance(normalized, dict):
        msg = "operational corpus payload must normalize to a mapping"
        raise TypeError(msg)
    normalized["corpus_entry_id"] = identity(
        "operational-corpus-entry",
        {
            "pipeline_run_id": run.run_id,
            "attempt_id": attempt.attempt_id,
            "status": attempt.status,
            "artifact_ids": tuple(sorted(artifact_ids)),
        },
    )
    normalized["recorded_at"] = _dt(datetime.now(tz=UTC))
    return normalized


def _intelligence(context: PipelineContext) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "id": item.id,
            "engine": item.engine,
            "project": item.project,
            "generated_at": _dt(item.generated_at),
            "confidence": _confidence(item.confidence),
            "evidence_ids": tuple(sorted(evidence.id for evidence in item.evidence)),
            "observation_ids": tuple(sorted(observation.id for observation in item.observations)),
            "insight_ids": tuple(sorted(insight.id for insight in item.insights)),
            "signal_ids": tuple(sorted(signal.id for signal in item.signals)),
        }
        for item in sorted(context.intelligence, key=lambda item: item.id)
    )


def _evidence(context: PipelineContext) -> tuple[dict[str, object], ...]:
    rows: dict[str, dict[str, object]] = {}
    for intelligence in context.intelligence:
        for evidence in intelligence.evidence:
            rows[evidence.id] = {
                "id": evidence.id,
                "source": evidence.source,
                "reference": evidence.reference,
                "collected_at": _dt(evidence.collected_at),
                "reliability": evidence.reliability,
                "freshness": evidence.freshness,
            }
    return tuple(rows[key] for key in sorted(rows))


def _observations(context: PipelineContext) -> tuple[dict[str, object], ...]:
    rows: dict[str, dict[str, object]] = {}
    for intelligence in context.intelligence:
        for observation in intelligence.observations:
            rows[observation.id] = {
                "id": observation.id,
                "engine": observation.engine,
                "project": observation.project,
                "description": observation.description,
                "importance": observation.importance,
                "evidence_ids": tuple(sorted(evidence.id for evidence in observation.evidence)),
            }
    return tuple(rows[key] for key in sorted(rows))


def _confidence_values(context: PipelineContext) -> tuple[dict[str, object], ...]:
    return tuple(
        {"intelligence_id": item.id, "engine": item.engine, "confidence": _confidence(item.confidence)}
        for item in sorted(context.intelligence, key=lambda item: item.id)
    )


def _discovered_opportunities(context: PipelineContext) -> tuple[object, ...]:
    rows = []
    for item in context.opportunity_timing:
        rows.append(
            _jsonable(
                {
                    "assessment_id": getattr(item, "assessment_id", getattr(item, "id", "")),
                    "project_id": getattr(item, "project_id", ""),
                    "timing_state": getattr(item, "timing_state", ""),
                    "confidence": getattr(item, "confidence", None),
                }
            )
        )
    return tuple(sorted(rows, key=lambda item: json.dumps(item, sort_keys=True)))


def _recovery_information(context: PipelineContext) -> tuple[dict[str, object], ...]:
    rows = []
    for event in context.persistence_events:
        detail = str(getattr(event, "detail", ""))
        event_type = str(getattr(event, "event_type", ""))
        lowered = detail.lower()
        if (
            "recover" in lowered
            or "failed" in lowered
            or "rollback" in event_type.lower()
            or "failed" in event_type.lower()
        ):
            rows.append({"event_type": event_type, "detail": detail, "record_id": getattr(event, "record_id", None)})
    return tuple(rows)


def _persistence_events(context: PipelineContext) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "event_type": str(getattr(event, "event_type", "")),
            "pipeline_run_id": str(getattr(event, "pipeline_run_id", "")),
            "detail": str(getattr(event, "detail", "")),
            "record_id": getattr(event, "record_id", None),
            "at": _dt(getattr(event, "at", None)),
        }
        for event in context.persistence_events
    )


def _record_opportunities(root: Path, settings: Any | None, payload: dict[str, object]) -> None:
    events_path = root / str(getattr(settings, "opportunity_events_filename", "opportunity_events.jsonl"))
    state_path = root / str(getattr(settings, "opportunity_state_filename", "opportunities.json"))
    events = _opportunity_events(payload)
    existing = _entry_ids(events_path)
    with events_path.open("a", encoding="utf-8") as handle:
        for event in events:
            if event["opportunity_event_id"] in existing:
                continue
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
    state = _opportunity_state(events_path)
    _write_json(state_path, state)


def _record_prediction(root: Path, settings: Any | None, payload: dict[str, object]) -> None:
    prediction_path = root / str(getattr(settings, "prediction_filename", "predictions.jsonl"))
    prediction = _prediction(payload)
    existing = _entry_ids(prediction_path)
    if prediction["prediction_id"] in existing:
        return
    with prediction_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(prediction, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
    _write_prediction_state(root, settings)


def _prediction(payload: dict[str, object]) -> dict[str, object]:
    prediction_id = identity(
        "real-market-prediction",
        {
            "pipeline_run_id": payload.get("pipeline_run_id"),
            "corpus_entry_id": payload.get("corpus_entry_id"),
            "target_id": payload.get("target_id"),
            "effective_at": payload.get("effective_at"),
        },
    )
    return {
        "prediction_id": prediction_id,
        "published_at": payload.get("finished_at") or payload.get("effective_at"),
        "effective_at": payload.get("effective_at"),
        "evaluation_horizon_at": payload.get("evaluation_horizon_at"),
        "pipeline_run_id": payload.get("pipeline_run_id"),
        "execution_identity": payload.get("execution_identity"),
        "corpus_entry_id": payload.get("corpus_entry_id"),
        "target_id": payload.get("target_id"),
        "target_type": payload.get("target_type"),
        "evidence": payload.get("evidence", []),
        "observations": payload.get("observations", []),
        "intelligence": payload.get("intelligence", []),
        "rankings": payload.get("rankings", []),
        "recommendations": payload.get("final_recommendations", []),
        "confidence_values": payload.get("confidence_values", []),
        "benchmark_values": payload.get("benchmark_values", []),
        "artifact_ids": payload.get("artifact_ids", []),
        "status": "open",
    }


def _record_outcomes(root: Path, settings: Any | None, payload: dict[str, object]) -> None:
    realized = _plain_sequence(payload.get("realized_outcomes"))
    if not realized:
        return
    outcome_path = root / str(getattr(settings, "outcome_filename", "outcomes.jsonl"))
    sample_path = root / str(getattr(settings, "validation_sample_filename", "validation_samples.jsonl"))
    prediction = _prediction(payload)
    outcomes = _outcome_records(payload, prediction, realized)
    samples = _validation_samples(payload, prediction, outcomes)
    _append_unique(outcome_path, outcomes, "outcome_id")
    _append_unique(sample_path, samples, "validation_sample_id")
    _append_unique(
        root / str(getattr(settings, "prediction_closure_filename", "prediction_closures.jsonl")),
        tuple(_prediction_closure(prediction, (outcome,), payload) for outcome in outcomes),
        "prediction_closure_id",
    )
    _write_prediction_state(root, settings)


def _outcome_records(
    payload: dict[str, object],
    prediction: dict[str, object],
    realized: tuple[object, ...],
) -> tuple[dict[str, object], ...]:
    records = []
    for item in realized:
        outcome = item if isinstance(item, dict) else {"outcome": item}
        outcome_id = identity(
            "real-market-outcome",
            {
                "prediction_id": prediction["prediction_id"],
                "outcome": outcome,
            },
        )
        records.append(
            {
                "outcome_id": outcome_id,
                "prediction_id": prediction["prediction_id"],
                "pipeline_run_id": payload.get("pipeline_run_id"),
                "target_id": payload.get("target_id"),
                "recorded_at": payload.get("finished_at") or payload.get("effective_at"),
                "realized_outcome": outcome,
                "benchmark_outcomes": payload.get("benchmark_outcomes", []),
                "benchmark_values": payload.get("benchmark_values", []),
            }
        )
    return tuple(records)


def _validation_samples(
    payload: dict[str, object],
    prediction: dict[str, object],
    outcomes: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "validation_sample_id": identity(
                "real-market-validation-sample",
                {
                    "prediction_id": prediction["prediction_id"],
                    "outcome_id": outcome["outcome_id"],
                },
            ),
            "prediction_id": prediction["prediction_id"],
            "outcome_id": outcome["outcome_id"],
            "pipeline_run_id": payload.get("pipeline_run_id"),
            "corpus_entry_id": payload.get("corpus_entry_id"),
            "target_id": payload.get("target_id"),
            "published_at": prediction.get("published_at"),
            "evaluation_horizon_at": prediction.get("evaluation_horizon_at"),
            "evidence_ids": _ids(payload.get("evidence")),
            "intelligence_ids": _ids(payload.get("intelligence")),
            "rankings": payload.get("rankings", []),
            "recommendations": payload.get("final_recommendations", []),
            "confidence_values": payload.get("confidence_values", []),
            "realized_outcome": outcome.get("realized_outcome"),
            "benchmark_outcomes": outcome.get("benchmark_outcomes", []),
        }
        for outcome in outcomes
    )


def _completion_payload(
    prediction: dict[str, object],
    executions: tuple[dict[str, object], ...],
    *,
    now: datetime,
) -> dict[str, object] | None:
    target_id = prediction.get("target_id")
    published_at = _parse_dt(prediction.get("published_at")) or datetime.min.replace(tzinfo=UTC)
    candidates = [
        item
        for item in executions
        if item.get("target_id") == target_id
        and (_parse_dt(item.get("finished_at")) or datetime.min.replace(tzinfo=UTC)) >= published_at
        and item.get("benchmark_values")
    ]
    if not candidates:
        return None
    latest = sorted(candidates, key=lambda item: str(item.get("finished_at") or item.get("effective_at") or ""))[-1]
    benchmark_outcomes = _benchmark_outcomes(prediction, latest)
    if not benchmark_outcomes:
        return None
    target_outcome = next(
        (
            item
            for item in benchmark_outcomes
            if isinstance(item, dict) and item.get("benchmark_id") in {target_id, "target"}
        ),
        benchmark_outcomes[0],
    )
    return {
        **latest,
        "pipeline_run_id": prediction.get("pipeline_run_id"),
        "corpus_entry_id": prediction.get("corpus_entry_id"),
        "target_id": target_id,
        "finished_at": _dt(now),
        "realized_outcomes": (
            {
                "project_id": target_id,
                "outcome": "EVALUATION_COMPLETE",
                "return": target_outcome.get("return") if isinstance(target_outcome, dict) else None,
            },
        ),
        "benchmark_outcomes": tuple(benchmark_outcomes),
    }


def _benchmark_outcomes(prediction: dict[str, object], latest: dict[str, object]) -> tuple[dict[str, object], ...]:
    start = _values_by_id(prediction.get("benchmark_values"))
    end = _values_by_id(latest.get("benchmark_values"))
    rows = []
    for benchmark_id in sorted(set(start) & set(end)):
        start_value = start[benchmark_id]
        end_value = end[benchmark_id]
        rows.append(
            {
                "benchmark_id": benchmark_id,
                "start_value": start_value,
                "end_value": end_value,
                "return": round((end_value / start_value) - 1.0, 10) if start_value else None,
            }
        )
    return tuple(rows)


def _values_by_id(value: object) -> dict[str, float]:
    rows = {}
    for item in _plain_sequence(value):
        if not isinstance(item, dict):
            continue
        benchmark_id = item.get("benchmark_id")
        raw_value = item.get("value")
        if isinstance(benchmark_id, str) and isinstance(raw_value, int | float):
            rows[benchmark_id] = float(raw_value)
    return rows


def _prediction_closure(
    prediction: dict[str, object],
    outcomes: tuple[dict[str, object], ...],
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "prediction_closure_id": identity(
            "real-market-prediction-closure",
            {
                "prediction_id": prediction["prediction_id"],
                "outcome_ids": tuple(outcome["outcome_id"] for outcome in outcomes),
            },
        ),
        "prediction_id": prediction["prediction_id"],
        "status": "closed",
        "closed_at": payload.get("finished_at") or _dt(datetime.now(tz=UTC)),
        "outcome_ids": tuple(outcome["outcome_id"] for outcome in outcomes),
        "benchmark_outcomes": payload.get("benchmark_outcomes", []),
    }


def _write_prediction_state(root: Path, settings: Any | None) -> None:
    prediction_path = root / str(getattr(settings, "prediction_filename", "predictions.jsonl"))
    closure_path = root / str(getattr(settings, "prediction_closure_filename", "prediction_closures.jsonl"))
    state_path = root / str(getattr(settings, "prediction_state_filename", "prediction_state.json"))
    closures = {str(item.get("prediction_id")): item for item in _read_jsonl(closure_path)}
    rows = []
    for prediction in _read_jsonl(prediction_path):
        prediction_id = str(prediction.get("prediction_id", ""))
        closure = closures.get(prediction_id)
        rows.append(
            {
                "prediction_id": prediction_id,
                "status": "closed" if closure else "open",
                "published_at": prediction.get("published_at"),
                "evaluation_horizon_at": prediction.get("evaluation_horizon_at"),
                "closed_at": closure.get("closed_at") if closure else None,
                "outcome_ids": closure.get("outcome_ids", []) if closure else [],
            }
        )
    _write_json(state_path, {"predictions": rows})


def _opportunity_events(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    rows = _opportunity_rows(payload)
    realized = _plain_sequence(payload.get("realized_outcomes"))
    benchmarks = _plain_sequence(payload.get("benchmark_outcomes"))
    event_payloads = []
    for row in rows:
        opportunity_key = str(row.get("project_id") or row.get("target_id") or row.get("opportunity_key"))
        opportunity_id = identity("operational-opportunity", {"opportunity_key": opportunity_key})
        status = "closed" if realized else "open"
        event = {
            "opportunity_event_id": identity(
                "operational-opportunity-event",
                {
                    "corpus_entry_id": payload["corpus_entry_id"],
                    "opportunity_id": opportunity_id,
                    "status": status,
                },
            ),
            "opportunity_id": opportunity_id,
            "opportunity_key": opportunity_key,
            "status": status,
            "first_seen_at": payload.get("effective_at"),
            "last_seen_at": payload.get("finished_at") or payload.get("effective_at"),
            "closed_at": payload.get("finished_at") if status == "closed" else None,
            "pipeline_run_id": payload.get("pipeline_run_id"),
            "execution_identity": payload.get("execution_identity"),
            "corpus_entry_id": payload.get("corpus_entry_id"),
            "rankings": payload.get("rankings", []),
            "recommendations": payload.get("final_recommendations", []),
            "realized_outcomes": realized,
            "benchmark_outcomes": benchmarks,
            "market_cycle_id": payload.get("market_cycle_id"),
            "observation_window_days": payload.get("observation_window_days"),
            "evidence_ids": _ids(payload.get("evidence")),
            "intelligence_ids": _ids(payload.get("intelligence")),
            "artifact_ids": payload.get("artifact_ids", []),
        }
        event_payloads.append(event)
    return tuple(event_payloads)


def _opportunity_rows(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    discovered = payload.get("discovered_opportunities")
    rows = [item for item in discovered if isinstance(item, dict)] if isinstance(discovered, list | tuple) else []
    if rows:
        return tuple(rows)
    return (
        {
            "target_id": str(payload.get("target_id", "")),
            "target_type": str(payload.get("target_type", "")),
            "opportunity_key": str(payload.get("target_id", "")),
        },
    )


def _opportunity_state(events_path: Path) -> dict[str, object]:
    events = _read_jsonl(events_path)
    state: dict[str, dict[str, object]] = {}
    for event in events:
        opportunity_id = str(event["opportunity_id"])
        current = state.get(opportunity_id, {})
        first_seen = min(
            [item for item in (current.get("first_seen_at"), event.get("first_seen_at")) if isinstance(item, str)],
            default=None,
        )
        status = "closed" if current.get("status") == "closed" or event.get("status") == "closed" else "open"
        current.update(event)
        current["status"] = status
        current["first_seen_at"] = first_seen
        current["last_seen_at"] = event.get("last_seen_at")
        if status == "closed" and event.get("closed_at") is not None:
            current["closed_at"] = event.get("closed_at")
        state[opportunity_id] = current
    return {"opportunities": [state[key] for key in sorted(state)]}


def _write_readiness(root: Path, settings: Any | None) -> None:
    events_path = root / str(getattr(settings, "opportunity_events_filename", "opportunity_events.jsonl"))
    state_path = root / str(getattr(settings, "opportunity_state_filename", "opportunities.json"))
    readiness_path = root / str(getattr(settings, "readiness_filename", "readiness.json"))
    state = _read_json(state_path)
    opportunities = [item for item in state.get("opportunities", []) if isinstance(item, dict)]
    events = _read_jsonl(events_path)
    market_cycles = {str(event["market_cycle_id"]) for event in events if event.get("market_cycle_id")}
    benchmarks = {
        str(item["benchmark_id"])
        for event in events
        for item in _plain_sequence(event.get("benchmark_outcomes"))
        if isinstance(item, dict) and item.get("benchmark_id")
    }
    windows = []
    for event in events:
        window = event.get("observation_window_days")
        if isinstance(window, int | float):
            windows.append(int(window))
    progress = {
        "historical_opportunities": len(opportunities),
        "completed_outcomes": sum(1 for item in opportunities if item.get("status") == "closed"),
        "independent_market_cycles": len(market_cycles),
        "benchmark_assets": len(benchmarks),
        "observation_window_days": max(windows, default=0),
    }
    targets = {
        "historical_opportunities": 30,
        "completed_outcomes": 30,
        "independent_market_cycles": 2,
        "benchmark_assets": 5,
        "observation_window_days": 730,
    }
    _write_json(
        readiness_path,
        {
            "targets": targets,
            "progress": progress,
            "corpus_ready": all(progress[key] >= target for key, target in targets.items()),
        },
    )


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple | list):
        return tuple(_jsonable(item) for item in value)
    return (_jsonable(value),)


def _plain_sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple | list):
        return tuple(value)
    return (value,)


def _ids(value: object) -> tuple[str, ...]:
    rows = _plain_sequence(value)
    return tuple(sorted(str(item["id"]) for item in rows if isinstance(item, dict) and item.get("id")))


def _confidence(value: object) -> object:
    score = getattr(value, "score", None)
    if score is not None:
        return score
    return normalize(value)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _optional_datetime(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _dt(value)
    return str(value)


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, datetime):
        return _dt(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return normalize(value)


def _entry_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        entry_id = (
            payload.get("corpus_entry_id")
            or payload.get("opportunity_event_id")
            or payload.get("prediction_id")
            or payload.get("outcome_id")
            or payload.get("validation_sample_id")
        )
        if isinstance(entry_id, str):
            ids.add(entry_id)
    return ids


def _append_unique(path: Path, rows: tuple[dict[str, object], ...], key: str) -> None:
    existing = _entry_ids(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            row_id = row.get(key)
            if not isinstance(row_id, str) or row_id in existing:
                continue
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            existing.add(row_id)


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)
