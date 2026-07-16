from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.engines.governance import (
    GOVERNANCE_ANALYSIS_TRACE_VERSION,
    GOVERNANCE_FINDING_TYPES,
    GovernanceFoundationIntelligenceEngine,
    governance_engine_definition,
)
from hunter.intelligence.evidence import Evidence

NOW = datetime(2026, 7, 10, tzinfo=UTC)


class FindingRepository:
    def __init__(self, evidence: tuple[Evidence, ...], *, fail_on_persist: bool = False) -> None:
        self.evidence = evidence
        self.fail_on_persist = fail_on_persist
        self.findings: dict[str, Finding] = {}

    def load_engine_evidence(self, candidate_id: str) -> tuple[Evidence, ...]:
        assert candidate_id == "uniswap"
        return self.evidence

    def persist_authorized_findings(self, batch: FindingBatch) -> None:
        if self.fail_on_persist:
            raise RuntimeError("persistence failed")
        staged = dict(self.findings)
        for finding in batch.findings:
            staged[finding.finding_id] = finding
        self.findings = staged


def governance_evidence(
    evidence_id: str,
    raw_data: dict[str, object],
    *,
    collected_at: datetime = NOW,
    metadata: dict[str, object] | None = None,
    reliability: float = 0.9,
) -> Evidence:
    payload = {"asset_id": "asset:uniswap", **raw_data}
    return Evidence(
        id=evidence_id,
        source="governance",
        collected_at=collected_at,
        reliability=reliability,
        freshness=1.0,
        reference=f"governance:{evidence_id}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": "governance_evidence"},
    )


def full_evidence() -> tuple[Evidence, ...]:
    return (
        governance_evidence(
            "space",
            {
                "record_type": "governance_space_record",
                "governance_space_id": "space:uniswap",
                "space_name": "Uniswap Governance",
            },
        ),
        governance_evidence(
            "proposal",
            {
                "record_type": "governance_proposal_record",
                "proposal_id": "proposal:1",
                "proposal_state": "active",
                "proposal_created_at": "2026-07-01T00:00:00Z",
                "proposal_end_at": "2026-07-08T00:00:00Z",
            },
        ),
        governance_evidence(
            "vote",
            {
                "record_type": "governance_vote_record",
                "proposal_id": "proposal:1",
                "vote_count": 250,
                "voter_count": 120,
                "participation_rate": 0.42,
            },
        ),
        governance_evidence(
            "quorum",
            {
                "record_type": "governance_quorum_record",
                "proposal_id": "proposal:1",
                "quorum_required": "40000000",
                "quorum_reached": True,
            },
        ),
        governance_evidence(
            "delegate",
            {
                "record_type": "governance_delegate_record",
                "delegate_id": "delegate:alice",
                "delegated_voting_power": "1000000",
            },
        ),
        governance_evidence(
            "parameter",
            {
                "record_type": "governance_parameter_record",
                "parameter_id": "parameter:voting-period",
                "parameter_name": "voting_period",
                "parameter_value": "7d",
            },
        ),
        governance_evidence(
            "execution",
            {
                "record_type": "governance_execution_record",
                "execution_id": "execution:1",
                "execution_state": "queued",
                "execution_timestamp": "2026-07-09T00:00:00Z",
            },
        ),
    )


def execute_governance(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        GovernanceFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="governance-config-v1",
    )
    return batch, repository


def findings_of_type(batch: FindingBatch, finding_type: str) -> tuple[Finding, ...]:
    return tuple(finding for finding in batch.findings if finding.finding_type == finding_type)


def finding_by_type(batch: FindingBatch, finding_type: str) -> Finding:
    findings = findings_of_type(batch, finding_type)
    assert len(findings) == 1
    return findings[0]


def finding_ids(batch: FindingBatch, finding_type: str) -> tuple[str, ...]:
    return tuple(finding.finding_id for finding in findings_of_type(batch, finding_type))


def without(evidence: tuple[Evidence, ...], evidence_id: str) -> tuple[Evidence, ...]:
    return tuple(item for item in evidence if item.id != evidence_id)


def test_governance_engine_definition_uses_builder_contract() -> None:
    definition = governance_engine_definition()

    assert definition.metadata.id == "governance-intelligence-foundation"
    assert definition.evidence_contracts == ("governance_evidence",)
    assert set(definition.finding_types) == set(GOVERNANCE_FINDING_TYPES)
    assert definition.analysis_trace_version == GOVERNANCE_ANALYSIS_TRACE_VERSION


def test_governance_engine_produces_all_findings_from_sufficient_evidence() -> None:
    batch, repository = execute_governance(full_evidence())

    assert {finding.finding_type for finding in batch.findings} == set(GOVERNANCE_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


@pytest.mark.parametrize(
    ("removed_id", "missing_type"),
    (
        ("space", "governance_activity"),
        ("proposal", "proposal_lifecycle"),
        ("vote", "voting_participation"),
        ("quorum", "quorum_observation"),
        ("delegate", "delegate_distribution"),
        ("parameter", "governance_parameter_observation"),
        ("execution", "governance_execution_observation"),
    ),
)
def test_governance_evidence_sufficiency_suppresses_only_affected_finding(
    removed_id: str,
    missing_type: str,
) -> None:
    full_batch, _repository = execute_governance(full_evidence())
    reduced_batch, _repository = execute_governance(without(full_evidence(), removed_id))

    assert findings_of_type(full_batch, missing_type)
    assert not findings_of_type(reduced_batch, missing_type)
    for finding in reduced_batch.findings:
        if finding.finding_type == "governance_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_governance_engine_does_not_fabricate_negative_findings_from_absent_evidence() -> None:
    batch, _repository = execute_governance(
        (
            governance_evidence(
                "space-only",
                {
                    "record_type": "governance_space_record",
                    "governance_space_id": "space:uniswap",
                    "space_name": "Uniswap Governance",
                },
            ),
        )
    )

    assert {finding.finding_type for finding in batch.findings} == {"governance_activity", "governance_observation"}
    assert all("no " not in finding.explanation.lower() for finding in batch.findings)
    assert all("risk" not in finding.explanation.lower() for finding in batch.findings)


def test_governance_findings_are_independent() -> None:
    full_batch, _repository = execute_governance(full_evidence())
    reduced_batch, _repository = execute_governance(without(full_evidence(), "vote"))

    assert not findings_of_type(reduced_batch, "voting_participation")
    for finding in reduced_batch.findings:
        if finding.finding_type == "governance_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_governance_two_proposals_under_one_candidate_remain_isolated() -> None:
    evidence = (
        governance_evidence("proposal-1", {"proposal_id": "proposal:1", "proposal_state": "active"}),
        governance_evidence("proposal-2", {"proposal_id": "proposal:2", "proposal_state": "closed"}),
    )

    batch, _repository = execute_governance(evidence)

    proposal_findings = findings_of_type(batch, "proposal_lifecycle")
    assert len(proposal_findings) == 2
    assert any("proposal:1" in finding.explanation for finding in proposal_findings)
    assert any("proposal:2" in finding.explanation for finding in proposal_findings)
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in proposal_findings)


def test_governance_votes_from_different_proposals_are_never_aggregated() -> None:
    evidence = (
        governance_evidence("vote-1", {"proposal_id": "proposal:1", "vote_count": 10}),
        governance_evidence("vote-2", {"proposal_id": "proposal:2", "vote_count": 20}),
    )

    batch, _repository = execute_governance(evidence)

    vote_findings = findings_of_type(batch, "voting_participation")
    assert len(vote_findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in vote_findings)
    assert any("proposal:1" in finding.explanation for finding in vote_findings)
    assert any("proposal:2" in finding.explanation for finding in vote_findings)


def test_governance_execution_records_remain_isolated() -> None:
    evidence = (
        governance_evidence("execution-1", {"execution_id": "execution:1", "execution_state": "queued"}),
        governance_evidence("execution-2", {"execution_id": "execution:2", "execution_state": "executed"}),
    )

    batch, _repository = execute_governance(evidence)

    execution_findings = findings_of_type(batch, "governance_execution_observation")
    assert len(execution_findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in execution_findings)


def test_governance_spaces_remain_isolated() -> None:
    evidence = (
        governance_evidence("space-1", {"governance_space_id": "space:uniswap", "space_name": "Uniswap"}),
        governance_evidence("space-2", {"governance_space_id": "space:compound", "space_name": "Compound"}),
    )

    batch, _repository = execute_governance(evidence)

    activity_findings = findings_of_type(batch, "governance_activity")
    assert len(activity_findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in activity_findings)


def test_governance_candidate_id_alone_cannot_merge_contexts() -> None:
    evidence = (
        governance_evidence("proposal-without-id", {"proposal_state": "active"}),
        governance_evidence("vote-without-id", {"vote_count": 100}),
    )

    batch, _repository = execute_governance(evidence)

    assert batch.findings == ()


def test_governance_missing_context_identifiers_suppress_affected_findings() -> None:
    evidence = (
        governance_evidence("proposal-without-id", {"proposal_state": "active"}),
        governance_evidence("execution-without-id", {"execution_state": "queued"}),
        governance_evidence("parameter-without-id", {"parameter_value": "7d"}),
    )

    batch, _repository = execute_governance(evidence)

    assert batch.findings == ()


def test_governance_ambiguous_context_identifiers_suppress_affected_findings() -> None:
    evidence = (
        governance_evidence(
            "ambiguous-space",
            {
                "governance_space_id": "space:uniswap",
                "space_id": "space:compound",
                "space_name": "Ambiguous Governance",
            },
        ),
        governance_evidence(
            "ambiguous-execution",
            {
                "execution_id": "execution:1",
                "execution_record_id": "execution:2",
                "execution_state": "queued",
            },
        ),
    )

    batch, _repository = execute_governance(evidence)

    assert batch.findings == ()


def test_governance_engine_normalizes_reversed_and_shuffled_multi_context_evidence() -> None:
    evidence = full_evidence()
    shuffled = (evidence[3], evidence[1], evidence[6], evidence[0], evidence[4], evidence[2], evidence[5])

    forward, _repository = execute_governance(evidence)
    reversed_batch, _repository = execute_governance(tuple(reversed(evidence)))
    shuffled_batch, _repository = execute_governance(shuffled)

    assert reversed_batch == forward
    assert shuffled_batch == forward


def test_governance_changed_evidence_affects_only_its_own_context() -> None:
    changed = (
        governance_evidence("proposal", {"proposal_id": "proposal:1", "proposal_state": "closed"}),
        *without(full_evidence(), "proposal"),
    )

    base, _repository = execute_governance(full_evidence())
    updated, _repository = execute_governance(changed)

    assert finding_ids(updated, "proposal_lifecycle") != finding_ids(base, "proposal_lifecycle")
    for finding in updated.findings:
        if finding.finding_type == "governance_observation" and "proposal:1" in finding.explanation:
            continue
        if finding.finding_type != "proposal_lifecycle":
            assert finding.finding_id in {item.finding_id for item in base.findings}


def test_governance_engine_preserves_conflicts_without_resolving_them() -> None:
    evidence = (
        governance_evidence(
            "conflicted-proposal",
            {
                "proposal_id": "proposal:1",
                "proposal_state": "active",
                "conflict_id": "conflict-proposal-1",
                "conflict_state": "open",
            },
        ),
    )

    batch, _repository = execute_governance(evidence)

    finding = finding_by_type(batch, "proposal_lifecycle")
    assert finding.conflicts == ("conflict-proposal-1", "open")
    assert "resolved" not in finding.explanation.lower()


def test_governance_service_excludes_future_evidence_by_as_of() -> None:
    future = governance_evidence(
        "future-proposal",
        {"proposal_id": "proposal:future", "proposal_state": "active"},
        collected_at=NOW + timedelta(days=1),
    )

    batch, repository = execute_governance((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_governance_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_governance(full_evidence())
    definition = replace(
        governance_engine_definition(),
        analysis_trace_version=f"{GOVERNANCE_ANALYSIS_TRACE_VERSION}-next",
    )
    repository = FindingRepository(full_evidence())

    changed = IntelligenceEngineService(repository).execute(
        GovernanceFoundationIntelligenceEngine(definition=definition),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="governance-config-v1",
    )

    assert {finding.finding_type for finding in changed.findings} == {finding.finding_type for finding in base.findings}
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_governance_service_rejects_forged_lineage() -> None:
    class ForgingGovernanceEngine(GovernanceFoundationIntelligenceEngine):
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
            ForgingGovernanceEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="governance-config-v1",
        )


def test_governance_shared_evidence_contract_alignment() -> None:
    source_only = governance_evidence(
        "source-only",
        {"proposal_id": "proposal:1", "proposal_state": "active"},
        metadata={},
    )
    metric_contract = governance_evidence(
        "metric-contract",
        {"metric": "governance_evidence", "proposal_id": "proposal:1", "proposal_state": "active"},
        metadata={},
    )

    missing_batch, _repository = execute_governance((source_only,))
    present_batch, _repository = execute_governance((metric_contract,))

    assert missing_batch.findings == ()
    assert present_batch.findings
    assert {finding.missing_evidence for finding in present_batch.findings} == {()}


def test_governance_service_persistence_rollback_and_idempotency() -> None:
    failing_repository = FindingRepository(full_evidence(), fail_on_persist=True)
    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(failing_repository).execute(
            GovernanceFoundationIntelligenceEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="governance-config-v1",
        )
    assert failing_repository.findings == {}

    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)
    first = service.execute(
        GovernanceFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="governance-config-v1",
    )
    second = service.execute(
        GovernanceFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="governance-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)
