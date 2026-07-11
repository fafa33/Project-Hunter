from __future__ import annotations

from dataclasses import replace
from typing import Any

from hunter.execution.hashing import stable_fingerprint, stable_identifier
from hunter.execution.run import PipelineRun
from hunter.intelligence.evidence import Evidence
from hunter.intelligence.insight import Insight
from hunter.intelligence.intelligence import Intelligence
from hunter.intelligence.observation import Observation
from hunter.intelligence.signal import Signal

IDENTITY_SCHEMA_VERSION = "analytical-identity-v1"


def fingerprint(namespace: str, payload: Any) -> str:
    return stable_fingerprint(namespace, payload, schema_version=IDENTITY_SCHEMA_VERSION)


def identity(namespace: str, payload: Any) -> str:
    return stable_identifier(namespace, payload, schema_version=IDENTITY_SCHEMA_VERSION)


class IntelligenceIdentityFactory:
    def stabilize(self, intelligence: Intelligence, run: PipelineRun, *, engine_version: str) -> Intelligence:
        evidence = tuple(
            self.evidence(item, run, intelligence.engine, engine_version) for item in intelligence.evidence
        )
        evidence_by_previous_id = {old.id: new for old, new in zip(intelligence.evidence, evidence, strict=True)}
        observations = tuple(
            self.observation(item, run, intelligence.engine, engine_version, evidence_by_previous_id)
            for item in intelligence.observations
        )
        observations_by_previous_id = {
            old.id: new for old, new in zip(intelligence.observations, observations, strict=True)
        }
        insights = tuple(
            self.insight(item, run, intelligence.engine, engine_version, observations_by_previous_id)
            for item in intelligence.insights
        )
        signals = tuple(
            self.signal(item, run, intelligence.project, intelligence.engine, engine_version)
            for item in intelligence.signals
        )
        stable = replace(
            intelligence,
            id="",
            signals=signals,
            evidence=evidence,
            observations=observations,
            insights=insights,
            generated_at=run.effective_at,
            metadata={
                **intelligence.metadata.as_dict(),
                "pipeline_run_id": run.run_id,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )
        return replace(stable, id=self.intelligence_id(stable, run, engine_version))

    def evidence(self, evidence: Evidence, run: PipelineRun, engine_id: str, engine_version: str) -> Evidence:
        return replace(
            evidence,
            id=identity(
                "evidence",
                {
                    "run": _run_identity(run),
                    "previous_id": evidence.id,
                    "engine_id": engine_id,
                    "engine_version": engine_version,
                    "source": evidence.source,
                    "reference": evidence.reference,
                    "collected_at": evidence.collected_at,
                    "raw_data": evidence.raw_data,
                    "metadata": evidence.metadata.as_dict(),
                },
            ),
        )

    def signal(self, signal: Signal, run: PipelineRun, target_id: str, engine_id: str, engine_version: str) -> Signal:
        return replace(
            signal,
            id=identity(
                "signal",
                {
                    "run": _run_identity(run),
                    "previous_id": signal.id,
                    "target_id": target_id,
                    "engine_id": engine_id,
                    "engine_version": engine_version,
                    "category": signal.category,
                    "source": signal.source,
                    "timestamp": run.effective_at,
                    "strength": signal.strength,
                    "confidence": signal.confidence,
                    "severity": signal.severity,
                    "metadata": signal.metadata.as_dict(),
                },
            ),
            timestamp=run.effective_at,
        )

    def observation(
        self,
        observation: Observation,
        run: PipelineRun,
        engine_id: str,
        engine_version: str,
        evidence_by_previous_id: dict[str, Evidence],
    ) -> Observation:
        evidence = tuple(evidence_by_previous_id.get(item.id, item) for item in observation.evidence)
        return replace(
            observation,
            evidence=evidence,
            id=identity(
                "observation",
                {
                    "run": _run_identity(run),
                    "previous_id": observation.id,
                    "engine_id": engine_id,
                    "engine_version": engine_version,
                    "project": observation.project,
                    "description": observation.description,
                    "importance": observation.importance,
                    "evidence_ids": {item.id for item in evidence},
                    "metadata": observation.metadata.as_dict(),
                },
            ),
        )

    def insight(
        self,
        insight: Insight,
        run: PipelineRun,
        engine_id: str,
        engine_version: str,
        observations_by_previous_id: dict[str, Observation],
    ) -> Insight:
        observations = tuple(observations_by_previous_id.get(item.id, item) for item in insight.supporting_observations)
        return replace(
            insight,
            supporting_observations=observations,
            id=identity(
                "insight",
                {
                    "run": _run_identity(run),
                    "previous_id": insight.id,
                    "engine_id": engine_id,
                    "engine_version": engine_version,
                    "title": insight.title,
                    "explanation": insight.explanation,
                    "confidence": insight.confidence,
                    "priority": insight.priority,
                    "observation_ids": {item.id for item in observations},
                },
            ),
        )

    def intelligence_id(self, intelligence: Intelligence, run: PipelineRun, engine_version: str) -> str:
        return identity(
            "intelligence",
            {
                "run": _run_identity(run),
                "project": intelligence.project,
                "engine": intelligence.engine,
                "engine_version": engine_version,
                "generated_at": run.effective_at,
                "signal_ids": [item.id for item in intelligence.signals],
                "evidence_ids": [item.id for item in intelligence.evidence],
                "observation_ids": [item.id for item in intelligence.observations],
                "insight_ids": [item.id for item in intelligence.insights],
                "confidence": intelligence.confidence,
                "metadata": intelligence.metadata.as_dict(),
            },
        )


def _run_identity(run: PipelineRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "run_type": run.run_type,
        "target_id": run.target_id,
        "target_type": run.target_type,
        "configuration_fingerprint": run.configuration_fingerprint,
        "input_fingerprint": run.input_fingerprint,
        "engine_manifest_fingerprint": run.engine_manifest_fingerprint,
        "effective_at": run.effective_at,
    }
