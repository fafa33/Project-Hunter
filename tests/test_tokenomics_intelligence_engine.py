from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.engines.tokenomics import (
    TOKENOMICS_ANALYSIS_TRACE_VERSION,
    TOKENOMICS_FINDING_TYPES,
    TokenomicsFoundationIntelligenceEngine,
    tokenomics_engine_definition,
)
from hunter.intelligence.evidence import Evidence

NOW = datetime(2026, 7, 10, tzinfo=UTC)


class FindingRepository:
    def __init__(self, evidence: tuple[Evidence, ...], *, fail_on_persist: bool = False) -> None:
        self.evidence = evidence
        self.fail_on_persist = fail_on_persist
        self.findings: dict[str, Finding] = {}

    def load_engine_evidence(self, candidate_id: str) -> tuple[Evidence, ...]:
        assert candidate_id == "bitcoin"
        return self.evidence

    def persist_authorized_findings(self, batch: FindingBatch) -> None:
        if self.fail_on_persist:
            raise RuntimeError("persistence failed")
        staged = dict(self.findings)
        for finding in batch.findings:
            staged[finding.finding_id] = finding
        self.findings = staged


def tokenomics_evidence(
    evidence_id: str,
    raw_data: dict[str, object],
    *,
    collected_at: datetime = NOW,
    metadata: dict[str, object] | None = None,
    reliability: float = 0.9,
) -> Evidence:
    payload = {"asset_id": "asset:bitcoin", **raw_data}
    return Evidence(
        id=evidence_id,
        source="tokenomics",
        collected_at=collected_at,
        reliability=reliability,
        freshness=1.0,
        reference=f"tokenomics:{evidence_id}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": "tokenomics_evidence"},
    )


def full_evidence() -> tuple[Evidence, ...]:
    return (
        tokenomics_evidence(
            "supply",
            {
                "record_type": "tokenomics_supply_observation",
                "supply_metric": "total_supply",
                "total_supply": "21000000",
                "circulating_supply": "19500000",
                "max_supply": "21000000",
            },
        ),
        tokenomics_evidence(
            "issuance",
            {
                "record_type": "tokenomics_supply_definition",
                "issuance_schedule": "halving",
                "inflation_rate": "0.015",
            },
        ),
        tokenomics_evidence(
            "unlock",
            {
                "record_type": "tokenomics_unlock_event",
                "unlock_event_id": "unlock-1",
                "unlock_at": "2026-08-01T00:00:00Z",
                "unlock_state": "scheduled",
            },
        ),
        tokenomics_evidence(
            "vesting",
            {
                "record_type": "tokenomics_vesting_schedule",
                "vesting_schedule": "linear",
                "vesting_start": "2026-01-01T00:00:00Z",
                "vesting_end": "2027-01-01T00:00:00Z",
            },
        ),
        tokenomics_evidence(
            "allocation",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_id": "allocation-community",
                "allocation_category": "community",
                "percentage": 0.4,
            },
        ),
        tokenomics_evidence(
            "treasury",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_category": "treasury",
                "treasury_allocation": "0.1",
            },
        ),
        tokenomics_evidence(
            "emission",
            {
                "record_type": "tokenomics_emission_observation",
                "emissions": "1200",
                "staking_emissions": "800",
                "reward_emissions": "400",
            },
        ),
        tokenomics_evidence(
            "burn",
            {
                "record_type": "tokenomics_burn_event",
                "burn_event": "burn-1",
                "burned_supply": "100",
            },
        ),
        tokenomics_evidence(
            "protocol",
            {
                "record_type": "tokenomics_protocol_metric",
                "protocol_fees": "1000",
                "protocol_revenue": "250",
                "tvl": "50000",
            },
        ),
    )


def execute_tokenomics(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        TokenomicsFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="tokenomics-config-v1",
    )
    return batch, repository


def by_type(batch: FindingBatch) -> dict[str, Finding]:
    return {finding.finding_type: finding for finding in batch.findings}


def without(evidence: tuple[Evidence, ...], evidence_id: str) -> tuple[Evidence, ...]:
    return tuple(item for item in evidence if item.id != evidence_id)


def test_tokenomics_engine_definition_uses_builder_contract() -> None:
    definition = tokenomics_engine_definition()

    assert definition.metadata.id == "tokenomics-intelligence-foundation"
    assert definition.evidence_contracts == ("tokenomics_evidence",)
    assert set(definition.finding_types) == set(TOKENOMICS_FINDING_TYPES)
    assert definition.analysis_trace_version == TOKENOMICS_ANALYSIS_TRACE_VERSION


def test_tokenomics_engine_produces_all_findings_from_sufficient_evidence() -> None:
    batch, repository = execute_tokenomics(full_evidence())

    assert set(by_type(batch)) == set(TOKENOMICS_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


@pytest.mark.parametrize(
    ("removed_id", "missing_type"),
    (
        ("supply", "supply_structure"),
        ("issuance", "issuance_schedule"),
        ("unlock", "unlock_schedule"),
        ("vesting", "vesting_schedule"),
        ("allocation", "allocation_structure"),
        ("treasury", "treasury_distribution"),
        ("emission", "emission_profile"),
        ("burn", "burn_activity"),
        ("protocol", "protocol_distribution"),
    ),
)
def test_tokenomics_evidence_sufficiency_suppresses_only_affected_finding(removed_id: str, missing_type: str) -> None:
    full_batch, _repository = execute_tokenomics(full_evidence())
    reduced_batch, _repository = execute_tokenomics(without(full_evidence(), removed_id))

    assert missing_type in by_type(full_batch)
    assert missing_type not in by_type(reduced_batch)
    for finding_type, finding in by_type(reduced_batch).items():
        if finding_type == "tokenomics_observation":
            continue
        assert finding == by_type(full_batch)[finding_type]


def test_tokenomics_engine_does_not_fabricate_negative_findings_from_absent_evidence() -> None:
    evidence = (
        tokenomics_evidence(
            "fully-unlocked-supply",
            {
                "record_type": "tokenomics_supply_observation",
                "supply_metric": "unlocked_supply",
                "unlocked_supply": "21000000",
                "total_supply": "21000000",
                "fully_unlocked": True,
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert set(by_type(batch)) == {"supply_structure", "tokenomics_observation"}
    assert "unlock_schedule" not in by_type(batch)
    assert all("risk" not in finding.explanation.lower() for finding in batch.findings)


def test_tokenomics_engine_preserves_conflicts_without_resolving_them() -> None:
    evidence = (
        tokenomics_evidence(
            "conflicted-supply",
            {
                "record_type": "tokenomics_supply_observation",
                "supply_metric": "total_supply",
                "total_supply": "1000",
                "conflict_id": "conflict-supply-1",
                "conflict_state": "open",
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    finding = by_type(batch)["supply_structure"]
    assert finding.conflicts == ("conflict-supply-1", "open")
    assert "accepted" not in finding.explanation.lower()
    assert "resolved" not in finding.explanation.lower()


def test_tokenomics_engine_rejects_balance_only_ownership_attribution() -> None:
    evidence = (
        tokenomics_evidence(
            "balance-only-team",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_category": "team",
                "percentage": 0.2,
                "attribution_basis": "balance_only",
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert "allocation_structure" not in by_type(batch)
    assert "treasury_distribution" not in by_type(batch)
    assert set(by_type(batch)) == {"tokenomics_observation"}


def test_tokenomics_engine_rejects_balance_only_treasury_without_category() -> None:
    evidence = (
        tokenomics_evidence(
            "balance-only-treasury",
            {
                "record_type": "tokenomics_allocation_definition",
                "treasury_allocation": "0.1",
                "attribution_basis": "balance_only",
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert "treasury_distribution" not in by_type(batch)
    assert set(by_type(batch)) == {"tokenomics_observation"}


def test_tokenomics_engine_rejects_balance_only_market_maker_category_variant() -> None:
    evidence = (
        tokenomics_evidence(
            "balance-only-market-maker",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_category": "market-maker",
                "percentage": 0.15,
                "attribution_basis": "balance_only",
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert "allocation_structure" not in by_type(batch)
    assert set(by_type(batch)) == {"tokenomics_observation"}


@pytest.mark.parametrize(
    ("evidence_id", "payload", "blocked_finding"),
    (
        (
            "balance-only-team-field",
            {"team_allocation": "0.2", "percentage": 0.2, "attribution_basis": "balance_only"},
            "allocation_structure",
        ),
        (
            "balance-only-investor-field",
            {"investor_allocation": "0.2", "percentage": 0.2, "attribution_basis": "balance_only"},
            "allocation_structure",
        ),
        (
            "balance-only-exchange-field",
            {"exchange_allocation": "0.2", "percentage": 0.2, "attribution_basis": "balance_only"},
            "allocation_structure",
        ),
        (
            "balance-only-treasury-field",
            {"treasury_allocation": "0.2", "attribution_basis": "balance_only"},
            "treasury_distribution",
        ),
        (
            "balance-only-market-maker-field",
            {"market_maker_allocation": "0.2", "percentage": 0.2, "attribution_basis": "balance_only"},
            "allocation_structure",
        ),
    ),
)
def test_tokenomics_engine_rejects_balance_only_ownership_sensitive_fields(
    evidence_id: str,
    payload: dict[str, object],
    blocked_finding: str,
) -> None:
    evidence = (
        tokenomics_evidence(
            evidence_id,
            {"record_type": "tokenomics_allocation_definition", **payload},
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert blocked_finding not in by_type(batch)
    assert set(by_type(batch)) == {"tokenomics_observation"}


def test_tokenomics_engine_preserves_valid_non_balance_treasury_and_allocation_findings() -> None:
    evidence = (
        tokenomics_evidence(
            "valid-treasury",
            {
                "record_type": "tokenomics_allocation_definition",
                "treasury_allocation": "0.1",
                "attribution_basis": "foundation_disclosure",
            },
        ),
        tokenomics_evidence(
            "valid-market-maker",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_category": "market-maker",
                "percentage": 0.15,
                "attribution_basis": "foundation_disclosure",
            },
        ),
    )

    batch, _repository = execute_tokenomics(evidence)

    assert "treasury_distribution" in by_type(batch)
    assert "allocation_structure" in by_type(batch)


def test_tokenomics_findings_are_independent() -> None:
    full_batch, _repository = execute_tokenomics(full_evidence())
    reduced_batch, _repository = execute_tokenomics(without(full_evidence(), "unlock"))

    assert "unlock_schedule" not in by_type(reduced_batch)
    for finding_type, finding in by_type(reduced_batch).items():
        if finding_type == "tokenomics_observation":
            continue
        assert finding == by_type(full_batch)[finding_type]


def test_tokenomics_engine_normalizes_reversed_and_shuffled_evidence_order() -> None:
    evidence = full_evidence()
    shuffled = (
        evidence[3],
        evidence[1],
        evidence[8],
        evidence[0],
        evidence[4],
        evidence[7],
        evidence[2],
        evidence[6],
        evidence[5],
    )

    forward, _repository = execute_tokenomics(evidence)
    reversed_batch, _repository = execute_tokenomics(tuple(reversed(evidence)))
    shuffled_batch, _repository = execute_tokenomics(shuffled)

    assert reversed_batch == forward
    assert shuffled_batch == forward


def test_tokenomics_engine_repeated_execution_is_identical() -> None:
    first, _repository = execute_tokenomics(full_evidence())
    second, _repository = execute_tokenomics(full_evidence())

    assert second == first


def test_tokenomics_supply_change_affects_only_supply_finding() -> None:
    changed = (
        tokenomics_evidence(
            "supply",
            {
                "record_type": "tokenomics_supply_observation",
                "supply_metric": "circulating_supply",
                "circulating_supply": "19600000",
            },
        ),
        *without(full_evidence(), "supply"),
    )

    base, _repository = execute_tokenomics(full_evidence())
    updated, _repository = execute_tokenomics(changed)

    assert by_type(updated)["supply_structure"] != by_type(base)["supply_structure"]
    for finding_type, finding in by_type(updated).items():
        if finding_type not in {"supply_structure", "tokenomics_observation"}:
            assert finding == by_type(base)[finding_type]


def test_tokenomics_unlock_change_affects_only_unlock_finding() -> None:
    changed = (
        tokenomics_evidence(
            "unlock",
            {
                "record_type": "tokenomics_unlock_event",
                "unlock_event_id": "unlock-2",
                "unlock_at": "2026-09-01T00:00:00Z",
                "unlock_state": "delayed",
            },
        ),
        *without(full_evidence(), "unlock"),
    )

    base, _repository = execute_tokenomics(full_evidence())
    updated, _repository = execute_tokenomics(changed)

    assert by_type(updated)["unlock_schedule"] != by_type(base)["unlock_schedule"]
    for finding_type, finding in by_type(updated).items():
        if finding_type not in {"unlock_schedule", "tokenomics_observation"}:
            assert finding == by_type(base)[finding_type]


def test_tokenomics_allocation_change_affects_only_allocation_finding() -> None:
    changed = (
        tokenomics_evidence(
            "allocation",
            {
                "record_type": "tokenomics_allocation_definition",
                "allocation_id": "allocation-ecosystem",
                "allocation_category": "ecosystem",
                "percentage": 0.35,
            },
        ),
        *without(full_evidence(), "allocation"),
    )

    base, _repository = execute_tokenomics(full_evidence())
    updated, _repository = execute_tokenomics(changed)

    assert by_type(updated)["allocation_structure"] != by_type(base)["allocation_structure"]
    for finding_type, finding in by_type(updated).items():
        if finding_type not in {"allocation_structure", "tokenomics_observation"}:
            assert finding == by_type(base)[finding_type]


def test_tokenomics_service_excludes_future_evidence_by_as_of() -> None:
    future = tokenomics_evidence(
        "future-supply",
        {"record_type": "tokenomics_supply_observation", "supply_metric": "total_supply", "total_supply": "1"},
        collected_at=NOW + timedelta(days=1),
    )

    batch, repository = execute_tokenomics((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_tokenomics_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_tokenomics(full_evidence())
    definition = replace(
        tokenomics_engine_definition(),
        analysis_trace_version=f"{TOKENOMICS_ANALYSIS_TRACE_VERSION}-next",
    )
    repository = FindingRepository(full_evidence())

    changed = IntelligenceEngineService(repository).execute(
        TokenomicsFoundationIntelligenceEngine(definition=definition),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="tokenomics-config-v1",
    )

    assert set(by_type(changed)) == set(by_type(base))
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_tokenomics_service_rejects_forged_lineage() -> None:
    class ForgingTokenomicsEngine(TokenomicsFoundationIntelligenceEngine):
        def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
            batch = super().analyze(evidence, context)
            first = batch.findings[0]
            forged = replace(first, evidence_lineage=("forged-lineage",))
            forged = replace(
                forged,
                finding_id=finding_identity(
                    candidate_id=forged.candidate_id,
                    engine_id=forged.engine_id,
                    engine_version=forged.engine_version,
                    finding_type=forged.finding_type,
                    explanation=forged.explanation,
                    supporting_evidence_ids=forged.supporting_evidence_ids,
                    evidence_lineage=forged.evidence_lineage,
                    deterministic_confidence=forged.deterministic_confidence,
                    confidence_basis=forged.confidence_basis,
                    evaluated_at=forged.evaluated_at,
                    as_of=forged.as_of,
                    analysis_trace_version=forged.analysis_trace_version,
                    missing_evidence=forged.missing_evidence,
                    conflicts=forged.conflicts,
                    schema_version=forged.schema_version,
                ),
            )
            return replace(batch, findings=(forged, *batch.findings[1:]))

    with pytest.raises(IntelligenceEngineValidationError, match="evidence lineage"):
        IntelligenceEngineService(FindingRepository(full_evidence())).execute(
            ForgingTokenomicsEngine(),
            candidate_id="bitcoin",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="tokenomics-config-v1",
        )


def test_tokenomics_shared_evidence_contract_alignment() -> None:
    source_only = tokenomics_evidence(
        "source-only",
        {"record_type": "tokenomics_supply_observation", "supply_metric": "total_supply", "total_supply": "1"},
        metadata={},
    )
    metric_contract = tokenomics_evidence(
        "metric-contract",
        {"metric": "tokenomics_evidence", "supply_metric": "total_supply", "total_supply": "1"},
        metadata={},
    )

    missing_batch, _repository = execute_tokenomics((source_only,))
    present_batch, _repository = execute_tokenomics((metric_contract,))

    assert missing_batch.findings == ()
    assert present_batch.findings
    assert {finding.missing_evidence for finding in present_batch.findings} == {()}


def test_tokenomics_service_persistence_rollback_and_idempotency() -> None:
    failing_repository = FindingRepository(full_evidence(), fail_on_persist=True)
    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(failing_repository).execute(
            TokenomicsFoundationIntelligenceEngine(),
            candidate_id="bitcoin",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="tokenomics-config-v1",
        )
    assert failing_repository.findings == {}

    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)
    first = service.execute(
        TokenomicsFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="tokenomics-config-v1",
    )
    second = service.execute(
        TokenomicsFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="tokenomics-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)
