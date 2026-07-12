from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hunter.market_validation.models import EngineValidationSource, MarketValidationRun, ProjectValidationResult

REQUIRED_EVIDENCE_ENGINES: tuple[str, ...] = (
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
    "developer",
    "protocol",
    "news",
    "social",
    "narrative",
    "whale_intelligence",
    "macro_intelligence",
    "future_demand",
    "opportunity_timing",
    "probability",
    "pattern_matching",
    "technology_necessity",
    "capital_rotation",
    "necessity_gap",
    "risk",
    "validation_health",
    "committee",
)


@dataclass(frozen=True)
class EvidenceCoverageStats:
    total_engines: int
    available_engines: int
    missing_engines: int
    stale_engines: int
    contradictory_engines: int

    @property
    def coverage_percent(self) -> float:
        return _percent(self.available_engines, self.total_engines)

    @property
    def available_percent(self) -> float:
        return _percent(self.available_engines, self.total_engines)

    @property
    def missing_percent(self) -> float:
        return _percent(self.missing_engines, self.total_engines)

    @property
    def stale_percent(self) -> float:
        return _percent(self.stale_engines, self.total_engines)

    @property
    def contradictory_percent(self) -> float:
        return _percent(self.contradictory_engines, self.total_engines)


@dataclass(frozen=True)
class EvidenceCoverageReport:
    generated_at: datetime
    project_count: int
    engine_count: int
    sources: tuple[EngineValidationSource, ...]
    stats: EvidenceCoverageStats

    @property
    def missing_sources(self) -> tuple[EngineValidationSource, ...]:
        return tuple(source for source in self.sources if source.status == "UNAVAILABLE")

    @property
    def stale_sources(self) -> tuple[EngineValidationSource, ...]:
        return tuple(source for source in self.sources if source.status == "STALE" or source.freshness < 0.5)

    @property
    def contradictory_sources(self) -> tuple[EngineValidationSource, ...]:
        return tuple(source for source in self.sources if _is_contradictory(source))


class EvidenceCoverageAnalyzer:
    def analyze(self, run: MarketValidationRun) -> EvidenceCoverageReport:
        sources = tuple(
            source
            for result in run.project_results
            for source in ensure_complete_engine_sources(
                result,
                timestamp=run.effective_at,
                required_engines=REQUIRED_EVIDENCE_ENGINES,
            )
        )
        total = len(run.project_results) * len(REQUIRED_EVIDENCE_ENGINES)
        available = sum(1 for source in sources if _is_available(source))
        missing = max(total - available, 0)
        stale = sum(1 for source in sources if _is_available(source) and source.freshness < 0.5)
        contradictory = sum(1 for source in sources if _is_contradictory(source))
        return EvidenceCoverageReport(
            generated_at=run.effective_at.astimezone(UTC),
            project_count=len(run.project_results),
            engine_count=len(REQUIRED_EVIDENCE_ENGINES),
            sources=sources,
            stats=EvidenceCoverageStats(
                total_engines=total,
                available_engines=available,
                missing_engines=missing,
                stale_engines=stale,
                contradictory_engines=contradictory,
            ),
        )


class EvidenceReportRenderer:
    def render_status(self, report: EvidenceCoverageReport) -> str:
        stats = report.stats
        return "\n".join(
            (
                "Real Evidence Coverage",
                f"Coverage: {stats.coverage_percent:.2f}%",
                f"Available: {stats.available_percent:.2f}%",
                f"Missing: {stats.missing_percent:.2f}%",
                f"Stale: {stats.stale_percent:.2f}%",
                f"Contradictory: {stats.contradictory_percent:.2f}%",
            )
        )

    def render_coverage(self, report: EvidenceCoverageReport) -> str:
        stats = report.stats
        lines = [
            "Evidence Completeness",
            f"Projects: {report.project_count}",
            f"Engines per project: {report.engine_count}",
            f"Coverage %: {stats.coverage_percent:.2f}",
            f"Available %: {stats.available_percent:.2f}",
            f"Missing %: {stats.missing_percent:.2f}",
            f"Stale %: {stats.stale_percent:.2f}",
            f"Contradictory %: {stats.contradictory_percent:.2f}",
        ]
        return "\n".join(lines)

    def render_validate(self, report: EvidenceCoverageReport) -> str:
        lines = [
            "Engine Availability",
            "| Engine | Status | Validation | Confidence | Freshness | Source | Collector |",
            "| --- | --- | --- | ---: | ---: | --- | --- |",
        ]
        for source in sorted(report.sources, key=lambda item: (item.engine, item.source)):
            lines.append(
                f"| {source.engine} | {source.status} | {source.validation_status} | "
                f"{source.confidence:.4f} | {source.freshness:.4f} | {source.source} | {source.collector} |"
            )
        return "\n".join(lines)

    def render_sources(self, report: EvidenceCoverageReport) -> str:
        lines = [
            "Repository Trace",
            "| Engine | Evidence IDs | Repository IDs | Raw Metrics | Normalized Metrics |",
            "| --- | --- | --- | --- | --- |",
        ]
        for source in sorted(report.sources, key=lambda item: (item.engine, item.source_record_ids)):
            lines.append(
                f"| {source.engine} | {', '.join(source.evidence_ids) or 'none'} | "
                f"{', '.join(source.repository_ids) or 'none'} | {dict(source.raw_input_metrics)} | "
                f"{dict(source.normalized_inputs)} |"
            )
        return "\n".join(lines)

    def render_missing(self, report: EvidenceCoverageReport) -> str:
        lines = ["Missing Sources"]
        for source in report.missing_sources:
            lines.append(f"- {source.engine}: {', '.join(source.missing_fields) or 'missing persisted evidence'}")
        return "\n".join(lines)

    def render_freshness(self, report: EvidenceCoverageReport) -> str:
        lines = [
            "Evidence Freshness",
            "| Engine | Timestamp | Freshness | Status |",
            "| --- | --- | ---: | --- |",
        ]
        for source in sorted(report.sources, key=lambda item: (item.timestamp, item.engine)):
            lines.append(
                f"| {source.engine} | {source.timestamp.isoformat()} | {source.freshness:.4f} | {source.status} |"
            )
        return "\n".join(lines)


def unavailable_source(engine: str, *, timestamp: datetime, missing_field: str | None = None) -> EngineValidationSource:
    field = missing_field or engine
    return EngineValidationSource(
        engine=engine,
        score=0.0,
        confidence=0.0,
        timestamp=timestamp,
        freshness=0.0,
        source_record_ids=(),
        evidence_ids=(),
        source="persisted-upstream",
        collector="repository",
        validation_status="MISSING",
        status="UNAVAILABLE",
        raw_input_metrics={},
        normalized_inputs={},
        applied_weight=0.0,
        weighted_contribution=0.0,
        missing_fields=(field,),
        warnings=(f"missing:{field}",),
    )


def ensure_complete_engine_sources(
    result: ProjectValidationResult,
    *,
    timestamp: datetime,
    required_engines: tuple[str, ...] = REQUIRED_EVIDENCE_ENGINES,
) -> tuple[EngineValidationSource, ...]:
    by_engine = {source.engine: source for source in result.engine_sources}
    completed = [
        by_engine.get(engine)
        or unavailable_source(
            engine, timestamp=result.engine_sources[0].timestamp if result.engine_sources else timestamp
        )
        for engine in required_engines
    ]
    return tuple(completed)


def _is_available(source: EngineValidationSource) -> bool:
    return source.status == "AVAILABLE" and source.confidence > 0.0 and not source.missing_fields


def _is_contradictory(source: EngineValidationSource) -> bool:
    return source.validation_status == "CONTRADICTORY" or any("contradict" in warning for warning in source.warnings)


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)
