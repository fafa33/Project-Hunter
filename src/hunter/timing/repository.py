from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.jsonl_contract import JsonlRecord, JsonlWritePlan, envelope, read_records, strict_known
from hunter.timing.models import TimingAssessment, TimingDependencySnapshot, TimingRebuildStatus

TIMING_JSONL_SCHEMA = "hunter-timing-jsonl-v1"


class TimingRepository:
    def __init__(self, root: str | Path = "data/timing") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.assessment_path = self.root / "assessments.jsonl"
        self.run_path = self.root / "runs.jsonl"

    def save(
        self,
        assessments: tuple[TimingAssessment, ...],
        *,
        dependencies: TimingDependencySnapshot | None = None,
        write_plan: JsonlWritePlan | None = None,
    ) -> tuple[TimingAssessment, ...]:
        existing = {item.assessment_id for item in self.assessments()}
        saved = []
        for item in assessments:
            if item.assessment_id in existing:
                continue
            self._append(self.assessment_path, _assessment_payload(item), write_plan=write_plan)
            saved.append(item)
        if assessments:
            generated_at = max(item.generated_at for item in assessments)
            payload: dict[str, Any] = {
                "generated_at": generated_at.isoformat(),
                "assessments": len(assessments),
                "available": sum(1 for item in assessments if item.classification != "INSUFFICIENT_EVIDENCE"),
                "insufficient": sum(1 for item in assessments if item.classification == "INSUFFICIENT_EVIDENCE"),
            }
            if dependencies is not None:
                payload["dependencies"] = _dependency_payload(dependencies)
            self._append(
                self.run_path,
                payload,
                write_plan=write_plan,
            )
        return tuple(saved)

    def assessments(self) -> tuple[TimingAssessment, ...]:
        return tuple(_assessment_from_payload(item) for item in _read_jsonl(self.assessment_path))

    def latest_by_project(self) -> dict[str, TimingAssessment]:
        latest: dict[str, TimingAssessment] = {}
        for item in self.assessments():
            current = latest.get(item.project_id)
            if current is None or item.generated_at > current.generated_at:
                latest[item.project_id] = item
        return latest

    def history(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.run_path)

    def records(self, path: Path) -> tuple[JsonlRecord, ...]:
        return read_records(path, supported_schema=TIMING_JSONL_SCHEMA)

    def strict_known_records(self, path: Path, *, as_of: datetime) -> tuple[JsonlRecord, ...]:
        return strict_known(self.records(path), as_of=as_of)

    def latest_dependencies(self) -> TimingDependencySnapshot | None:
        rows = self.history()
        if not rows:
            return None
        latest = max(rows, key=lambda item: str(item.get("generated_at", "")))
        payload = latest.get("dependencies")
        if not isinstance(payload, dict):
            return None
        return _dependency_from_payload(payload)

    def rebuild_status(self, current: TimingDependencySnapshot) -> TimingRebuildStatus:
        saved = self.latest_dependencies()
        if saved is None:
            return TimingRebuildStatus(
                "STALE_TIMING_REBUILD_REQUIRED",
                ("missing_dependency_metadata",),
                None,
                current.generation_timestamp,
            )
        stale = []
        for dependency, fingerprint in current.dependency_fingerprints.items():
            if saved.dependency_fingerprints.get(dependency) != fingerprint:
                stale.append(dependency)
        for dependency, timestamp in current.dependency_timestamps.items():
            saved_timestamp = saved.dependency_timestamps.get(dependency)
            if saved_timestamp is None or timestamp > saved_timestamp:
                stale.append(dependency)
        if stale:
            return TimingRebuildStatus(
                "STALE_TIMING_REBUILD_REQUIRED",
                tuple(sorted(set(stale))),
                saved.generation_timestamp,
                current.generation_timestamp,
            )
        return TimingRebuildStatus("CURRENT", (), saved.generation_timestamp, current.generation_timestamp)

    def _append(self, path: Path, payload: dict[str, Any], *, write_plan: JsonlWritePlan | None = None) -> None:
        if write_plan is not None:
            payload = envelope(payload, write_plan)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _assessment_payload(item: TimingAssessment) -> dict[str, Any]:
    return {
        "assessment_id": item.assessment_id,
        "project_id": item.project_id,
        "generated_at": item.generated_at.isoformat(),
        "entry_score": item.entry_score,
        "exit_score": item.exit_score,
        "accumulation_score": item.accumulation_score,
        "distribution_score": item.distribution_score,
        "risk_reward_score": item.risk_reward_score,
        "cycle_position": item.cycle_position,
        "market_regime": item.market_regime,
        "timing_confidence": item.timing_confidence,
        "evidence_quality": item.evidence_quality,
        "freshness": item.freshness,
        "classification": item.classification,
        "source_engines": item.source_engines,
        "evidence_ids": item.evidence_ids,
        "repository_ids": item.repository_ids,
        "reasoning_chain": item.reasoning_chain,
        "missing_evidence": item.missing_evidence,
        "stale_evidence": item.stale_evidence,
        "raw_inputs": dict(item.raw_inputs),
        "normalized_factors": dict(item.normalized_factors),
    }


def _assessment_from_payload(payload: dict[str, Any]) -> TimingAssessment:
    return TimingAssessment(
        assessment_id=str(payload["assessment_id"]),
        project_id=str(payload["project_id"]),
        generated_at=datetime.fromisoformat(str(payload["generated_at"])),
        entry_score=float(payload["entry_score"]),
        exit_score=float(payload["exit_score"]),
        accumulation_score=float(payload["accumulation_score"]),
        distribution_score=float(payload["distribution_score"]),
        risk_reward_score=float(payload["risk_reward_score"]),
        cycle_position=str(payload["cycle_position"]),
        market_regime=str(payload["market_regime"]),
        timing_confidence=float(payload["timing_confidence"]),
        evidence_quality=float(payload["evidence_quality"]),
        freshness=float(payload["freshness"]),
        classification=str(payload["classification"]),
        source_engines=tuple(payload.get("source_engines", ())),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        reasoning_chain=tuple(payload.get("reasoning_chain", ())),
        missing_evidence=tuple(payload.get("missing_evidence", ())),
        stale_evidence=tuple(payload.get("stale_evidence", ())),
        raw_inputs={str(k): float(v) for k, v in dict(payload.get("raw_inputs", {})).items()},
        normalized_factors={str(k): float(v) for k, v in dict(payload.get("normalized_factors", {})).items()},
    )


def _dependency_payload(item: TimingDependencySnapshot) -> dict[str, Any]:
    return {
        "generation_timestamp": item.generation_timestamp.isoformat(),
        "dependency_timestamps": {key: value.isoformat() for key, value in item.dependency_timestamps.items()},
        "dependency_fingerprints": dict(item.dependency_fingerprints),
        "protocol_evidence_timestamp": _optional_datetime(item.protocol_evidence_timestamp),
        "narrative_evidence_timestamp": _optional_datetime(item.narrative_evidence_timestamp),
        "developer_evidence_timestamp": _optional_datetime(item.developer_evidence_timestamp),
        "graph_timestamp": _optional_datetime(item.graph_timestamp),
        "macro_timestamp": _optional_datetime(item.macro_timestamp),
        "whale_timestamp": _optional_datetime(item.whale_timestamp),
    }


def _dependency_from_payload(payload: dict[str, Any]) -> TimingDependencySnapshot:
    return TimingDependencySnapshot(
        generation_timestamp=datetime.fromisoformat(str(payload["generation_timestamp"])),
        dependency_timestamps={
            str(key): datetime.fromisoformat(str(value))
            for key, value in dict(payload.get("dependency_timestamps", {})).items()
        },
        dependency_fingerprints={
            str(key): str(value) for key, value in dict(payload.get("dependency_fingerprints", {})).items()
        },
        protocol_evidence_timestamp=_optional_from_payload(payload.get("protocol_evidence_timestamp")),
        narrative_evidence_timestamp=_optional_from_payload(payload.get("narrative_evidence_timestamp")),
        developer_evidence_timestamp=_optional_from_payload(payload.get("developer_evidence_timestamp")),
        graph_timestamp=_optional_from_payload(payload.get("graph_timestamp")),
        macro_timestamp=_optional_from_payload(payload.get("macro_timestamp")),
        whale_timestamp=_optional_from_payload(payload.get("whale_timestamp")),
    )


def _optional_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _optional_from_payload(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value))


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(record.payload for record in read_records(path, supported_schema=TIMING_JSONL_SCHEMA))
