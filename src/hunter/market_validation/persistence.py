from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from hunter.execution.hashing import stable_fingerprint, stable_identifier
from hunter.market_validation.configuration import MarketValidationConfig
from hunter.market_validation.models import MarketValidationRun, ProjectValidationResult
from hunter.market_validation.renderer import MarketValidationRenderer
from hunter.market_validation.repositories import result_to_record, run_to_record
from hunter.persistence.records import MarketValidationProjectResultRecord, MarketValidationRunRecord

MARKET_VALIDATION_SCHEMA_VERSION = "canonical-market-validation-v1"


@dataclass(frozen=True, slots=True)
class MarketValidationPersistenceContext:
    recorded_at: datetime
    known_at: datetime | None
    known_time_limitation: str | None
    model_version: str
    methodology_fingerprint: str
    source_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))
        if self.known_at is not None:
            if self.known_at.tzinfo is None:
                raise ValueError("known_at must be timezone-aware")
            object.__setattr__(self, "known_at", self.known_at.astimezone(UTC))
        if self.known_at is None and not self.known_time_limitation:
            raise ValueError("known_time_limitation is required when known_at is unknown")
        if self.known_at is not None and self.known_time_limitation is not None:
            raise ValueError("known_time_limitation must be absent when known_at is known")
        for name in ("model_version", "methodology_fingerprint"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        versions = {str(key): str(value) for key, value in self.source_versions.items()}
        if any(not key.strip() or not value.strip() for key, value in versions.items()):
            raise ValueError("source version identities and values cannot be blank")
        object.__setattr__(self, "source_versions", versions)


@dataclass(frozen=True, slots=True)
class AuthorizedMarketValidationWrite:
    run_record: MarketValidationRunRecord
    project_records: tuple[MarketValidationProjectResultRecord, ...]
    operation: Literal["create", "correct"]

    def __post_init__(self) -> None:
        if self.run_record.status != "complete":
            raise ValueError("only a complete service-authorized run may be committed")
        if tuple(record.id for record in self.project_records) != self.run_record.project_result_ids:
            raise ValueError("run project identities must exactly match the authorized project records")
        if any(record.validation_run_id != self.run_record.validation_run_id for record in self.project_records):
            raise ValueError("all project records must belong to the authorized validation run")
        if self.operation == "create" and (
            self.run_record.supersedes_id is not None
            or any(record.supersedes_id is not None for record in self.project_records)
        ):
            raise ValueError("create cannot supersede canonical records")
        if self.operation == "correct" and self.run_record.supersedes_id is None:
            raise ValueError("correction requires a run predecessor")


class MarketValidationPersistenceAuthorizationService:
    """Converts already-computed canonical output into a complete write plan."""

    def authorize(
        self,
        run: MarketValidationRun,
        config: MarketValidationConfig,
        context: MarketValidationPersistenceContext,
        *,
        predecessor_run: MarketValidationRunRecord | None = None,
        predecessor_projects: Mapping[str, MarketValidationProjectResultRecord] | None = None,
        correction_reason: str | None = None,
    ) -> AuthorizedMarketValidationWrite:
        if run.run_id != config.run_id or run.effective_at != config.effective_at:
            raise ValueError("run identity/effective time must match its authorizing configuration")
        correcting = predecessor_run is not None
        if correcting and (not correction_reason or not correction_reason.strip()):
            raise ValueError("correction_reason is required")
        if not correcting and (predecessor_projects or correction_reason is not None):
            raise ValueError("predecessors and correction reason require an explicit run predecessor")
        if predecessor_run is not None and predecessor_run.validation_run_id != run.run_id:
            raise ValueError("correction must preserve canonical validation_run_id")
        if correcting and set(predecessor_projects or {}) != {result.project_id for result in run.project_results}:
            raise ValueError("correction requires one explicit predecessor for every project record")

        config_fingerprint = stable_fingerprint(
            "market-validation-configuration",
            _plain(config),
            schema_version=MARKET_VALIDATION_SCHEMA_VERSION,
        )
        source_ids = tuple(
            sorted(
                {
                    source_id
                    for result in run.project_results
                    for source in result.engine_sources
                    for source_id in source.source_record_ids
                }
            )
        )
        source_versions = self._versions(source_ids, context)
        report_hashes = _report_hashes(run)
        native_run = _plain(run)
        run_id = stable_identifier(
            "canonical-market-validation-run",
            {
                "validation_run_id": run.run_id,
                "recorded_at": context.recorded_at,
                "payload": native_run,
                "supersedes_id": predecessor_run.id if predecessor_run else None,
            },
            schema_version=MARKET_VALIDATION_SCHEMA_VERSION,
        )

        project_records = tuple(
            self._project_record(
                result,
                run,
                context,
                config_fingerprint,
                (predecessor_projects or {}).get(result.project_id),
                correction_reason,
            )
            for result in run.project_results
        )
        base_run = run_to_record(run)
        run_record = replace(
            base_run,
            id=run_id,
            schema_version=MARKET_VALIDATION_SCHEMA_VERSION,
            created_at=context.recorded_at,
            project_result_ids=tuple(record.id for record in project_records),
            status="complete",
            known_at=context.known_at,
            known_time_limitation=context.known_time_limitation,
            model_version=context.model_version,
            configuration_fingerprint=config_fingerprint,
            methodology_fingerprint=context.methodology_fingerprint,
            source_record_ids=source_ids,
            source_versions=source_versions,
            report_artifact_hashes=report_hashes,
            supersedes_id=predecessor_run.id if predecessor_run else None,
            correction_reason=correction_reason,
            authorized_payload={"authority_classification": "production", "native_run": native_run},
        )
        return AuthorizedMarketValidationWrite(
            run_record,
            project_records,
            "correct" if correcting else "create",
        )

    def _project_record(
        self,
        result: ProjectValidationResult,
        run: MarketValidationRun,
        context: MarketValidationPersistenceContext,
        config_fingerprint: str,
        predecessor: MarketValidationProjectResultRecord | None,
        correction_reason: str | None,
    ) -> MarketValidationProjectResultRecord:
        if predecessor is not None and (
            predecessor.validation_run_id != run.run_id or predecessor.project_id != result.project_id
        ):
            raise ValueError("project correction must preserve run and project identity")
        source_ids = tuple(
            sorted({source_id for source in result.engine_sources for source_id in source.source_record_ids})
        )
        evidence_ids = tuple(
            sorted({evidence_id for source in result.engine_sources for evidence_id in source.evidence_ids})
        )
        source_versions = self._versions(source_ids, context)
        native_result = _plain(result)
        record_id = stable_identifier(
            "canonical-market-validation-project",
            {
                "validation_run_id": run.run_id,
                "project_id": result.project_id,
                "native_result_id": result.result_id,
                "recorded_at": context.recorded_at,
                "payload": native_result,
                "supersedes_id": predecessor.id if predecessor else None,
            },
            schema_version=MARKET_VALIDATION_SCHEMA_VERSION,
        )
        return replace(
            result_to_record(result, effective_at=run.effective_at),
            id=record_id,
            schema_version=MARKET_VALIDATION_SCHEMA_VERSION,
            created_at=context.recorded_at,
            known_at=context.known_at,
            known_time_limitation=context.known_time_limitation,
            model_version=context.model_version,
            configuration_fingerprint=config_fingerprint,
            methodology_fingerprint=context.methodology_fingerprint,
            source_record_ids=source_ids,
            source_versions=source_versions,
            evidence_references=evidence_ids,
            supersedes_id=predecessor.id if predecessor else None,
            correction_reason=correction_reason if predecessor else None,
            authorized_payload={
                "authority_classification": "production",
                "native_project_result": native_result,
            },
        )

    @staticmethod
    def _versions(source_ids: tuple[str, ...], context: MarketValidationPersistenceContext) -> tuple[str, ...]:
        missing = tuple(source_id for source_id in source_ids if source_id not in context.source_versions)
        if missing:
            raise ValueError(f"source versions are required for: {', '.join(missing)}")
        return tuple(context.source_versions[source_id] for source_id in source_ids)


def _report_hashes(run: MarketValidationRun) -> tuple[str, ...]:
    renderer = MarketValidationRenderer()
    artifacts = {
        "csv": renderer.render_csv(run),
        "json": renderer.render_json(run),
        "markdown": renderer.render_markdown(run),
    }
    return tuple(f"{name}:sha256:{sha256(content.encode()).hexdigest()}" for name, content in sorted(artifacts.items()))


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported Market Validation payload value: {type(value).__name__}")
