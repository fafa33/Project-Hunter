from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hunter.intelligence.engines.builder import HunterIntelligenceEngineBuilder
from hunter.intelligence.engines.contracts import (
    EngineContext,
    EngineDefinition,
    EngineMetadata,
    EvidenceBundle,
    Finding,
    FindingBatch,
    finding_identity,
)
from hunter.intelligence.engines.evidence_contracts import evidence_satisfies_contract
from hunter.intelligence.evidence import Evidence

DEVELOPER_ANALYSIS_TRACE_VERSION = "developer-analysis-trace-v1"
DEVELOPER_FINDING_TYPES = (
    "archival_state",
    "contributor_diversity",
    "development_continuity",
    "maintenance_state",
    "release_cadence",
    "repository_activity",
    "repository_health_observation",
    "repository_migration",
)


@dataclass(frozen=True)
class DeveloperRepositoryEvidence:
    evidence: Evidence
    repository: str
    payload: dict[str, Any]


class DeveloperFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or developer_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _developer_records(evidence)
        generators = (
            _repository_activity,
            _release_cadence,
            _contributor_diversity,
            _maintenance_state,
            _archival_state,
            _development_continuity,
            _repository_migration,
            _repository_health_observation,
        )
        findings = tuple(
            finding
            for generator in generators
            for finding in (generator(self.definition, evidence, context, records),)
            if finding is not None
        )
        return FindingBatch(
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            candidate_id=evidence.candidate_id,
            as_of=context.as_of,
            evaluated_at=context.evaluated_at,
            findings=findings,
        )


def developer_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="developer-intelligence-foundation",
        name="Developer Intelligence Foundation",
        category="developer",
        version="1.0.0",
        priority=10,
        required_inputs=("github_repository_profile",),
        produced_outputs=("developer_findings",),
        capabilities=("analyze", "developer-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts("github_repository_profile")
        .with_supported_evidence_types("github_repository_profile", "repository")
        .with_analysis_stages("normalize-evidence", "derive-findings", "explain-findings")
        .with_finding_types(*DEVELOPER_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=DEVELOPER_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _developer_records(bundle: EvidenceBundle) -> tuple[DeveloperRepositoryEvidence, ...]:
    rows = []
    for evidence in bundle.evidence:
        payload = _payload(evidence)
        repository = _repository_identity(evidence, payload)
        if not repository:
            continue
        if not evidence_satisfies_contract(evidence, "github_repository_profile"):
            continue
        rows.append(DeveloperRepositoryEvidence(evidence=evidence, repository=repository, payload=payload))
    return tuple(sorted(rows, key=lambda item: (item.repository, item.evidence.id)))


def _repository_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _present(record.payload.get("pushed_at"))
        or _present(record.payload.get("updated_at"))
        or _number(record.payload.get("commit_count")) is not None
        or _number(record.payload.get("commits_365d")) is not None
    )
    if not relevant:
        return None
    commits = sum(
        _number(record.payload.get("commit_count") or record.payload.get("commits_365d")) or 0 for record in relevant
    )
    repositories = ", ".join(record.repository for record in relevant)
    explanation = f"Repository activity is evidenced for {repositories}; observed commit records: {commits}."
    return _finding(
        definition,
        bundle,
        context,
        "repository_activity",
        explanation,
        relevant,
        confidence_basis="repository activity is supported by persisted repository timestamps or commit counters",
    )


def _release_cadence(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if (_number(record.payload.get("releases")) or 0) > 0
        or _present(record.payload.get("latest_release"))
        or bool(_sequence(record.payload.get("tags")))
    )
    if not relevant:
        return None
    releases = sum(_number(record.payload.get("releases")) or 0 for record in relevant)
    tag_count = sum(len(_sequence(record.payload.get("tags"))) for record in relevant)
    explanation = f"Release cadence is evidenced by {releases} release records and {tag_count} tag records."
    return _finding(
        definition,
        bundle,
        context,
        "release_cadence",
        explanation,
        relevant,
        confidence_basis="release cadence is supported by persisted release or tag evidence",
    )


def _contributor_diversity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _number(record.payload.get("contributors_count")) is not None
        or _number(record.payload.get("active_contributors")) is not None
    )
    if not relevant:
        return None
    contributors = sum(_number(record.payload.get("contributors_count")) or 0 for record in relevant)
    active = sum(_number(record.payload.get("active_contributors")) or 0 for record in relevant)
    explanation = f"Contributor diversity is evidenced by {contributors} contributors and {active} active contributors."
    return _finding(
        definition,
        bundle,
        context,
        "contributor_diversity",
        explanation,
        relevant,
        confidence_basis="contributor diversity is supported by persisted contributor counters",
    )


def _maintenance_state(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _present(record.payload.get("updated_at"))
        or _present(record.payload.get("pushed_at"))
        or _number(record.payload.get("open_issues")) is not None
        or _number(record.payload.get("closed_issues")) is not None
        or record.payload.get("archived") is not None
        or record.payload.get("disabled") is not None
    )
    if not relevant:
        return None
    open_issues = sum(_number(record.payload.get("open_issues")) or 0 for record in relevant)
    closed_issues = sum(_number(record.payload.get("closed_issues")) or 0 for record in relevant)
    explanation = f"Maintenance state is evidenced by repository metadata with {open_issues} open and {closed_issues} closed issues."
    return _finding(
        definition,
        bundle,
        context,
        "maintenance_state",
        explanation,
        relevant,
        confidence_basis="maintenance state is supported by persisted repository maintenance metadata",
    )


def _archival_state(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if record.payload.get("archived") is not None or record.payload.get("disabled") is not None
    )
    if not relevant:
        return None
    archived = tuple(record.repository for record in relevant if _bool(record.payload.get("archived")))
    disabled = tuple(record.repository for record in relevant if _bool(record.payload.get("disabled")))
    explanation = (
        "Archival state is explicitly evidenced; "
        f"archived repositories: {', '.join(archived) or 'none'}; "
        f"disabled repositories: {', '.join(disabled) or 'none'}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "archival_state",
        explanation,
        relevant,
        confidence_basis="archival state is supported by explicit archived or disabled repository metadata",
    )


def _development_continuity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _number(record.payload.get("commits_30d")) is not None
        or _number(record.payload.get("commits_90d")) is not None
        or _number(record.payload.get("commits_365d")) is not None
        or _present(record.payload.get("last_commit_timestamp"))
    )
    if not relevant:
        return None
    commits_30d = sum(_number(record.payload.get("commits_30d")) or 0 for record in relevant)
    commits_90d = sum(_number(record.payload.get("commits_90d")) or 0 for record in relevant)
    commits_365d = sum(_number(record.payload.get("commits_365d")) or 0 for record in relevant)
    explanation = (
        "Development continuity is evidenced by commit windows: "
        f"30d={commits_30d}, 90d={commits_90d}, 365d={commits_365d}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "development_continuity",
        explanation,
        relevant,
        confidence_basis="development continuity is supported by persisted commit-window evidence",
    )


def _repository_migration(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    if len({record.repository for record in records}) < 2:
        return None
    repositories = ", ".join(record.repository for record in records)
    explanation = f"Repository migration evidence is present through multiple repository identities: {repositories}."
    return _finding(
        definition,
        bundle,
        context,
        "repository_migration",
        explanation,
        records,
        confidence_basis="repository migration is supported by multiple persisted repository identities for the candidate",
    )


def _repository_health_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[DeveloperRepositoryEvidence, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _present(record.payload.get("default_branch"))
        or _number(record.payload.get("stars")) is not None
        or _number(record.payload.get("stargazers_count")) is not None
        or _number(record.payload.get("forks")) is not None
        or _number(record.payload.get("forks_count")) is not None
        or _present(record.payload.get("license"))
    )
    if not relevant:
        return None
    branches = tuple(
        sorted(
            {
                str(record.payload.get("default_branch"))
                for record in relevant
                if _present(record.payload.get("default_branch"))
            }
        )
    )
    licenses = tuple(
        sorted({str(record.payload.get("license")) for record in relevant if _present(record.payload.get("license"))})
    )
    explanation = (
        "Repository health observation is evidenced by repository metadata; "
        f"default branches: {', '.join(branches) or 'unknown'}; licenses: {', '.join(licenses) or 'unknown'}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "repository_health_observation",
        explanation,
        relevant,
        confidence_basis="repository health observation is supported by persisted repository metadata",
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[DeveloperRepositoryEvidence, ...],
    *,
    confidence_basis: str,
) -> Finding:
    supporting = tuple(sorted({record.evidence.id for record in records}))
    lineage = tuple(sorted({lineage for record in records for lineage in _lineage(record)}))
    confidence = _confidence(records)
    finding_id = finding_identity(
        candidate_id=bundle.candidate_id,
        engine_id=definition.metadata.id,
        engine_version=definition.metadata.version,
        finding_type=finding_type,
        explanation=explanation,
        supporting_evidence_ids=supporting,
        evidence_lineage=lineage,
        deterministic_confidence=confidence,
        confidence_basis=confidence_basis,
        evaluated_at=context.evaluated_at,
        as_of=context.as_of,
        analysis_trace_version=definition.analysis_trace_version,
        missing_evidence=bundle.missing_evidence,
        schema_version=definition.output_schema_version,
    )
    return Finding(
        finding_id=finding_id,
        candidate_id=bundle.candidate_id,
        engine_id=definition.metadata.id,
        engine_version=definition.metadata.version,
        finding_type=finding_type,
        explanation=explanation,
        supporting_evidence_ids=supporting,
        evidence_lineage=lineage,
        deterministic_confidence=confidence,
        confidence_basis=confidence_basis,
        evaluated_at=context.evaluated_at,
        as_of=context.as_of,
        analysis_trace_version=definition.analysis_trace_version,
        missing_evidence=bundle.missing_evidence,
        schema_version=definition.output_schema_version,
    )


def _payload(evidence: Evidence) -> dict[str, Any]:
    raw = evidence.raw_data
    if not isinstance(raw, dict):
        return {}
    return {str(key): raw[key] for key in sorted(raw)}


def _repository_identity(evidence: Evidence, payload: dict[str, Any]) -> str:
    candidates = (
        payload.get("full_name"),
        payload.get("github_repository"),
        payload.get("repository"),
        payload.get("repository_name"),
        (
            evidence.reference.removeprefix("https://github.com/")
            if evidence.reference.startswith("https://github.com/")
            else ""
        ),
        getattr(evidence, "value", ""),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return ""


def _lineage(record: DeveloperRepositoryEvidence) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _confidence(records: tuple[DeveloperRepositoryEvidence, ...]) -> float:
    if not records:
        return 0.0
    total = sum(min(max(record.evidence.reliability, 0.0), 1.0) for record in records)
    return round(total / len(records), 4)


def _number(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, tuple | list):
        return tuple(value)
    return ()


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)
