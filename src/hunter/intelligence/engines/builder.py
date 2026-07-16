from __future__ import annotations

from dataclasses import replace

from hunter.intelligence.engines.contracts import EngineDefinition, EngineMetadata
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError


class HunterIntelligenceEngineBuilder:
    """Definition-only builder for immutable intelligence engine contracts."""

    def __init__(self, metadata: EngineMetadata) -> None:
        self._metadata = metadata
        self._evidence_contracts: tuple[str, ...] = ()
        self._supported_evidence_types: tuple[str, ...] = ()
        self._analysis_stages: tuple[str, ...] = ()
        self._finding_types: tuple[str, ...] = ()
        self._output_schema_version = "intelligence-finding-v1"
        self._analysis_trace_version = "analysis-trace-v1"
        self._deterministic_execution_contract = "same evidence bundle and engine context produce identical findings"

    def with_capabilities(self, *capabilities: str) -> HunterIntelligenceEngineBuilder:
        metadata = replace(self._metadata, capabilities=_normalized(capabilities, "capability"))
        clone = self._clone()
        clone._metadata = metadata
        return clone

    def with_evidence_contracts(self, *contracts: str) -> HunterIntelligenceEngineBuilder:
        clone = self._clone()
        clone._evidence_contracts = _normalized(contracts, "evidence contract")
        return clone

    def with_supported_evidence_types(self, *evidence_types: str) -> HunterIntelligenceEngineBuilder:
        clone = self._clone()
        clone._supported_evidence_types = _normalized(evidence_types, "evidence type")
        return clone

    def with_analysis_stages(self, *stages: str) -> HunterIntelligenceEngineBuilder:
        clone = self._clone()
        clone._analysis_stages = _ordered(stages, "analysis stage")
        return clone

    def with_finding_types(self, *finding_types: str) -> HunterIntelligenceEngineBuilder:
        clone = self._clone()
        clone._finding_types = _normalized(finding_types, "finding type")
        return clone

    def with_output_schema(
        self, *, schema_version: str, analysis_trace_version: str
    ) -> HunterIntelligenceEngineBuilder:
        if not schema_version.strip() or not analysis_trace_version.strip():
            msg = "output schema and analysis trace versions are required"
            raise IntelligenceEngineValidationError(msg)
        clone = self._clone()
        clone._output_schema_version = schema_version
        clone._analysis_trace_version = analysis_trace_version
        return clone

    def build(self) -> EngineDefinition:
        return EngineDefinition(
            metadata=self._metadata,
            evidence_contracts=self._evidence_contracts,
            supported_evidence_types=self._supported_evidence_types,
            analysis_stages=self._analysis_stages,
            finding_types=self._finding_types,
            output_schema_version=self._output_schema_version,
            analysis_trace_version=self._analysis_trace_version,
            deterministic_execution_contract=self._deterministic_execution_contract,
        )

    def _clone(self) -> HunterIntelligenceEngineBuilder:
        clone = HunterIntelligenceEngineBuilder(self._metadata)
        clone._evidence_contracts = self._evidence_contracts
        clone._supported_evidence_types = self._supported_evidence_types
        clone._analysis_stages = self._analysis_stages
        clone._finding_types = self._finding_types
        clone._output_schema_version = self._output_schema_version
        clone._analysis_trace_version = self._analysis_trace_version
        clone._deterministic_execution_contract = self._deterministic_execution_contract
        return clone


def _normalized(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted({value.strip() for value in values if value.strip()}))
    if not normalized:
        msg = f"at least one {label} is required"
        raise IntelligenceEngineValidationError(msg)
    return normalized


def _ordered(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    ordered = tuple(value.strip() for value in values if value.strip())
    if not ordered:
        msg = f"at least one {label} is required"
        raise IntelligenceEngineValidationError(msg)
    return ordered
