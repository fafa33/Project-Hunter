from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.engines.funding import (
    FUNDING_ANALYSIS_TRACE_VERSION,
    FUNDING_EVIDENCE_CONTRACT,
    FUNDING_FINDING_TYPES,
    FundingFoundationIntelligenceEngine,
    funding_engine_definition,
)
from hunter.intelligence.evidence import Evidence

NOW = datetime(2026, 7, 17, tzinfo=UTC)


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


def funding_evidence(
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
        source="funding",
        collected_at=collected_at,
        reliability=reliability,
        freshness=1.0,
        reference=f"funding:{evidence_id}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": FUNDING_EVIDENCE_CONTRACT},
    )


def full_evidence() -> tuple[Evidence, ...]:
    return (
        funding_evidence(
            "round",
            {"funding_round_id": "round:seed", "funding_round": "seed", "round_amount": "1000000"},
        ),
        funding_evidence(
            "investor",
            {"investor_id": "investor:a", "investor_participation": "participated", "participation_amount": "250000"},
        ),
        funding_evidence(
            "lead",
            {"investor_id": "investor:lead", "lead_investor": "true", "lead_role": "lead"},
        ),
        funding_evidence(
            "strategic",
            {"investor_id": "investor:strategic", "strategic_investor": "true", "strategic_status": "strategic"},
        ),
        funding_evidence(
            "treasury",
            {
                "treasury_funding_event_id": "treasury:event",
                "treasury_funding": "approved",
                "treasury_funding_amount": "500000",
            },
        ),
        funding_evidence(
            "grant",
            {"grant_id": "grant:1", "grant_funding": "awarded", "grant_amount": "100000"},
        ),
        funding_evidence(
            "ecosystem",
            {"ecosystem_program_id": "program:1", "ecosystem_funding": "allocated", "program_budget": "2000000"},
        ),
        funding_evidence(
            "fundraising",
            {"fundraising_event_id": "fundraise:1", "fundraising_event": "announced", "fundraising_amount": "1500000"},
        ),
        funding_evidence(
            "capital",
            {"capital_source_id": "source:1", "capital_source": "foundation", "capital_commitment": "750000"},
        ),
    )


def execute_funding(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        FundingFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="funding-config-v1",
    )
    return batch, repository


def findings_of_type(batch: FindingBatch, finding_type: str) -> tuple[Finding, ...]:
    return tuple(finding for finding in batch.findings if finding.finding_type == finding_type)


def finding_by_type(batch: FindingBatch, finding_type: str) -> Finding:
    findings = findings_of_type(batch, finding_type)
    assert len(findings) == 1
    return findings[0]


def without(evidence: tuple[Evidence, ...], evidence_id: str) -> tuple[Evidence, ...]:
    return tuple(item for item in evidence if item.id != evidence_id)


def finding_ids(batch: FindingBatch, finding_type: str) -> tuple[str, ...]:
    return tuple(finding.finding_id for finding in findings_of_type(batch, finding_type))


def test_funding_engine_definition_uses_builder_contract() -> None:
    definition = funding_engine_definition()

    assert definition.metadata.id == "funding-intelligence-foundation"
    assert definition.evidence_contracts == (FUNDING_EVIDENCE_CONTRACT,)
    assert set(definition.finding_types) == set(FUNDING_FINDING_TYPES)
    assert definition.analysis_trace_version == FUNDING_ANALYSIS_TRACE_VERSION
    assert "isolate-funding-contexts" in definition.analysis_stages


def test_funding_engine_produces_all_findings_from_sufficient_evidence() -> None:
    batch, repository = execute_funding(full_evidence())

    assert {finding.finding_type for finding in batch.findings} == set(FUNDING_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


@pytest.mark.parametrize(
    ("removed_id", "missing_type"),
    (
        ("round", "funding_round"),
        ("investor", "investor_participation"),
        ("lead", "lead_investor"),
        ("strategic", "strategic_investor"),
        ("treasury", "treasury_funding"),
        ("grant", "grant_funding"),
        ("ecosystem", "ecosystem_funding"),
        ("fundraising", "fundraising_event"),
        ("capital", "capital_source"),
    ),
)
def test_funding_evidence_sufficiency_suppresses_only_affected_finding(
    removed_id: str,
    missing_type: str,
) -> None:
    full_batch, _repository = execute_funding(full_evidence())
    reduced_batch, _repository = execute_funding(without(full_evidence(), removed_id))

    assert findings_of_type(full_batch, missing_type)
    assert not findings_of_type(reduced_batch, missing_type)
    for finding in reduced_batch.findings:
        if finding.finding_type == "funding_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


@pytest.mark.parametrize(
    "payload",
    (
        {"funding_round_id": "round:1"},
        {"investor_id": "investor:1"},
        {"organization_id": "org:1"},
        {"project_id": "project:1"},
        {"grant_id": "grant:1"},
        {"capital_source_id": "source:1"},
        {"treasury_funding_event_id": "treasury:1"},
        {"fundraising_event_id": "event:1"},
        {"syndicate_id": "syndicate:1"},
        {"ecosystem_program_id": "program:1"},
        {"investor_name": "Example Capital", "investor_alias": "Example"},
    ),
)
def test_funding_context_identifiers_alone_produce_no_findings(payload: dict[str, object]) -> None:
    batch, _repository = execute_funding((funding_evidence("context-only", payload),))

    assert batch.findings == ()


def test_funding_engine_does_not_fabricate_negative_or_quality_findings() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence(
                "participation",
                {"investor_id": "investor:a", "investor_participation": "participated"},
            ),
        )
    )

    forbidden = (
        "no funding",
        "no investor",
        "quality",
        "confidence",
        "conviction",
        "return",
        "valuation",
        "sentiment",
        "intent",
        "strategy",
        "reputation",
    )
    assert {finding.finding_type for finding in batch.findings} == {
        "funding_observation",
        "investor_participation",
    }
    assert all(term not in finding.explanation.lower() for finding in batch.findings for term in forbidden)


def test_funding_findings_are_independent() -> None:
    full_batch, _repository = execute_funding(full_evidence())
    reduced_batch, _repository = execute_funding(without(full_evidence(), "grant"))

    assert not findings_of_type(reduced_batch, "grant_funding")
    for finding in reduced_batch.findings:
        if finding.finding_type == "funding_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_funding_same_context_observations_are_aggregated_descriptively() -> None:
    evidence = (
        funding_evidence("round-amount", {"funding_round_id": "round:seed", "round_amount": "100"}),
        funding_evidence("round-stage", {"round_id": "round:seed", "round_stage": "seed"}),
    )

    batch, _repository = execute_funding(evidence)

    finding = finding_by_type(batch, "funding_round")
    assert finding.supporting_evidence_ids == ("round-amount", "round-stage")
    assert "context round:seed" in finding.explanation
    assert "descriptively evidenced" in finding.explanation


def test_funding_cross_context_observations_are_not_aggregated() -> None:
    evidence = (
        funding_evidence("round-a", {"funding_round_id": "round:a", "round_amount": "100"}),
        funding_evidence("round-b", {"funding_round_id": "round:b", "round_amount": "200"}),
    )

    batch, _repository = execute_funding(evidence)

    findings = findings_of_type(batch, "funding_round")
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_funding_candidate_id_alone_cannot_merge_contexts() -> None:
    evidence = (
        funding_evidence("round-without-context", {"round_amount": "100"}),
        funding_evidence("grant-without-context", {"grant_amount": "200"}),
    )

    batch, _repository = execute_funding(evidence)

    assert batch.findings == ()


@pytest.mark.parametrize(
    ("finding_type", "evidence"),
    (
        (
            "funding_round",
            (
                funding_evidence("round-a", {"funding_round_id": "round:a", "round_amount": "1"}),
                funding_evidence("round-b", {"funding_round_id": "round:b", "round_amount": "2"}),
            ),
        ),
        (
            "investor_participation",
            (
                funding_evidence("investor-a", {"investor_id": "investor:a", "investor_participation": "yes"}),
                funding_evidence("investor-b", {"investor_id": "investor:b", "investor_participation": "yes"}),
            ),
        ),
        (
            "funding_observation",
            (
                funding_evidence("syndicate-a", {"syndicate_id": "syndicate:a", "funding_observation": "reported"}),
                funding_evidence("syndicate-b", {"syndicate_id": "syndicate:b", "funding_observation": "reported"}),
            ),
        ),
        (
            "treasury_funding",
            (
                funding_evidence("treasury-a", {"treasury_funding_event_id": "treasury:a", "treasury_funding": "yes"}),
                funding_evidence("treasury-b", {"treasury_funding_event_id": "treasury:b", "treasury_funding": "yes"}),
            ),
        ),
        (
            "grant_funding",
            (
                funding_evidence("grant-a", {"grant_id": "grant:a", "grant_funding": "yes"}),
                funding_evidence("grant-b", {"grant_id": "grant:b", "grant_funding": "yes"}),
            ),
        ),
        (
            "ecosystem_funding",
            (
                funding_evidence("program-a", {"ecosystem_program_id": "program:a", "ecosystem_funding": "yes"}),
                funding_evidence("program-b", {"ecosystem_program_id": "program:b", "ecosystem_funding": "yes"}),
            ),
        ),
        (
            "fundraising_event",
            (
                funding_evidence("event-a", {"fundraising_event_id": "event:a", "fundraising_event": "yes"}),
                funding_evidence("event-b", {"fundraising_event_id": "event:b", "fundraising_event": "yes"}),
            ),
        ),
        (
            "capital_source",
            (
                funding_evidence("source-a", {"capital_source_id": "source:a", "capital_source": "foundation"}),
                funding_evidence("source-b", {"capital_source_id": "source:b", "capital_source": "venture"}),
            ),
        ),
        (
            "funding_observation",
            (
                funding_evidence("org-a", {"organization_id": "org:a", "organization_funding": "observed"}),
                funding_evidence("org-b", {"organization_id": "org:b", "organization_funding": "observed"}),
            ),
        ),
        (
            "funding_observation",
            (
                funding_evidence("project-a", {"project_id": "project:a", "project_funding": "observed"}),
                funding_evidence("project-b", {"project_id": "project:b", "project_funding": "observed"}),
            ),
        ),
    ),
)
def test_funding_context_types_remain_isolated(finding_type: str, evidence: tuple[Evidence, ...]) -> None:
    batch, _repository = execute_funding(evidence)

    findings = findings_of_type(batch, finding_type)
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_funding_malformed_context_ids_suppress_affected_findings() -> None:
    evidence = (
        funding_evidence("round-malformed", {"funding_round_id": "round bad", "round_amount": "100"}),
        funding_evidence("grant-valid", {"grant_id": "grant:1", "grant_funding": "yes"}),
    )

    batch, _repository = execute_funding(evidence)

    assert not findings_of_type(batch, "funding_round")
    assert findings_of_type(batch, "grant_funding")


def test_funding_ambiguous_context_ids_suppress_affected_findings() -> None:
    evidence = (
        funding_evidence(
            "ambiguous-round", {"funding_round_id": "round:a", "round_id": "round:b", "round_amount": "1"}
        ),
        funding_evidence("capital-valid", {"capital_source_id": "source:1", "capital_source": "foundation"}),
    )

    batch, _repository = execute_funding(evidence)

    assert not findings_of_type(batch, "funding_round")
    assert findings_of_type(batch, "capital_source")


def test_funding_investor_names_and_aliases_do_not_merge_identities() -> None:
    evidence = (
        funding_evidence(
            "investor-a",
            {"investor_id": "investor:a", "investor_name": "Example", "investor_participation": "yes"},
        ),
        funding_evidence(
            "investor-b",
            {"investor_id": "investor:b", "investor_alias": "Example", "investor_participation": "yes"},
        ),
    )

    batch, _repository = execute_funding(evidence)

    findings = findings_of_type(batch, "investor_participation")
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


@pytest.mark.parametrize(
    ("finding_type", "payload"),
    (
        ("lead_investor", {"investor_id": "investor:lead", "investor_participation": "yes"}),
        ("strategic_investor", {"investor_id": "investor:strategic", "investor_participation": "yes"}),
        ("grant_funding", {"grant_id": "grant:1", "funding_observation": "reported"}),
        ("treasury_funding", {"treasury_funding_event_id": "treasury:1", "funding_observation": "reported"}),
    ),
)
def test_funding_attribution_requires_explicit_evidence(finding_type: str, payload: dict[str, object]) -> None:
    batch, _repository = execute_funding((funding_evidence("implicit", payload),))

    assert not findings_of_type(batch, finding_type)


def test_funding_lead_investor_requires_affirmative_value() -> None:
    affirmative, _repository = execute_funding(
        (funding_evidence("lead-true", {"investor_id": "investor:lead", "is_lead_investor": True}),)
    )
    negative, _repository = execute_funding(
        (funding_evidence("lead-false", {"investor_id": "investor:lead", "is_lead_investor": False}),)
    )

    assert findings_of_type(affirmative, "lead_investor")
    assert negative.findings == ()


def test_funding_strategic_investor_requires_affirmative_value() -> None:
    affirmative, _repository = execute_funding(
        (funding_evidence("strategic-true", {"investor_id": "investor:strategic", "strategic_investor": True}),)
    )
    negative, _repository = execute_funding(
        (funding_evidence("strategic-false", {"investor_id": "investor:strategic", "strategic_investor": False}),)
    )

    assert findings_of_type(affirmative, "strategic_investor")
    assert negative.findings == ()


@pytest.mark.parametrize("grant_status", ("approved", "awarded"))
def test_funding_grant_status_affirmative_values_generate_finding(grant_status: str) -> None:
    batch, _repository = execute_funding(
        (funding_evidence("grant", {"grant_id": "grant:1", "grant_status": grant_status}),)
    )

    assert findings_of_type(batch, "grant_funding")


@pytest.mark.parametrize("grant_status", ("rejected", "denied"))
def test_funding_grant_status_negative_values_do_not_generate_finding(grant_status: str) -> None:
    batch, _repository = execute_funding(
        (funding_evidence("grant", {"grant_id": "grant:1", "grant_status": grant_status}),)
    )

    assert batch.findings == ()


def test_funding_treasury_funding_requires_affirmative_value() -> None:
    affirmative, _repository = execute_funding(
        (
            funding_evidence(
                "treasury-approved",
                {"treasury_funding_event_id": "treasury:1", "treasury_funding": "approved"},
            ),
        )
    )
    negative, _repository = execute_funding(
        (
            funding_evidence(
                "treasury-rejected",
                {"treasury_funding_event_id": "treasury:1", "treasury_funding": "rejected"},
            ),
        )
    )

    assert findings_of_type(affirmative, "treasury_funding")
    assert negative.findings == ()


def test_funding_negative_values_suppress_only_affected_finding() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence("lead-false", {"investor_id": "investor:lead", "is_lead_investor": False}),
            funding_evidence("grant-approved", {"grant_id": "grant:1", "grant_status": "approved"}),
        )
    )

    assert not findings_of_type(batch, "lead_investor")
    assert findings_of_type(batch, "grant_funding")


def test_funding_negative_values_do_not_create_negative_explanations() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence("grant-rejected", {"grant_id": "grant:1", "grant_status": "rejected"}),
            funding_evidence("capital-valid", {"capital_source_id": "source:1", "capital_source": "foundation"}),
        )
    )

    assert not findings_of_type(batch, "grant_funding")
    assert all("rejected" not in finding.explanation.lower() for finding in batch.findings)
    assert all("no " not in finding.explanation.lower() for finding in batch.findings)


def test_funding_affirmed_context_preserves_contradictory_grant_record() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence(
                "grant-approved",
                {"grant_id": "grant:1", "grant_status": "approved", "conflict_id": "grant-status-conflict"},
            ),
            funding_evidence(
                "grant-rejected",
                {"grant_id": "grant:1", "grant_status": "rejected", "conflict_id": "grant-rejection"},
            ),
        )
    )

    finding = finding_by_type(batch, "grant_funding")
    assert finding.supporting_evidence_ids == ("grant-approved", "grant-rejected")
    assert finding.evidence_lineage == ("funding:grant-approved", "funding:grant-rejected")
    assert finding.conflicts == ("grant-rejection", "grant-status-conflict")
    assert "grant_status" in finding.explanation
    assert "rejected" not in finding.explanation.lower()


def test_funding_grant_finding_excludes_unrelated_same_context_records() -> None:
    relevant = (
        funding_evidence(
            "grant-approved",
            {"grant_id": "grant:1", "grant_status": "approved", "conflict_id": "grant-status-conflict"},
        ),
        funding_evidence(
            "grant-rejected",
            {"grant_id": "grant:1", "grant_status": "rejected", "conflict_id": "grant-rejection"},
        ),
    )
    with_unrelated = (
        *relevant,
        funding_evidence(
            "grant-investor-note",
            {"grant_id": "grant:1", "investor_participation": "yes"},
            reliability=0.1,
        ),
        funding_evidence(
            "grant-treasury-note",
            {"grant_id": "grant:1", "treasury_funding": "approved"},
            reliability=0.2,
        ),
        funding_evidence(
            "grant-unrelated-observation",
            {"grant_id": "grant:1", "ecosystem_funding": "allocated"},
            reliability=0.3,
        ),
    )

    base, _repository = execute_funding(relevant)
    expanded, _repository = execute_funding(with_unrelated)

    assert finding_by_type(expanded, "grant_funding") == finding_by_type(base, "grant_funding")
    finding = finding_by_type(expanded, "grant_funding")
    assert finding.supporting_evidence_ids == ("grant-approved", "grant-rejected")
    assert finding.evidence_lineage == ("funding:grant-approved", "funding:grant-rejected")
    assert finding.conflicts == ("grant-rejection", "grant-status-conflict")


def test_funding_observation_context_preserves_contradictory_records() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence(
                "treasury-approved",
                {"treasury_funding_event_id": "treasury:1", "treasury_funding": "approved"},
            ),
            funding_evidence(
                "treasury-rejected",
                {
                    "treasury_funding_event_id": "treasury:1",
                    "treasury_funding": "rejected",
                    "conflict_id": "treasury-conflict",
                },
            ),
        )
    )

    finding = finding_by_type(batch, "funding_observation")
    assert finding.supporting_evidence_ids == ("treasury-approved", "treasury-rejected")
    assert finding.evidence_lineage == ("funding:treasury-approved", "funding:treasury-rejected")
    assert finding.conflicts == ("treasury-conflict",)
    assert "treasury_funding" in finding.explanation
    assert "rejected" not in finding.explanation.lower()


def test_funding_observation_context_excludes_unrelated_same_context_records() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence("grant-approved", {"grant_id": "grant:1", "grant_status": "approved"}),
            funding_evidence(
                "grant-rejected",
                {"grant_id": "grant:1", "grant_status": "rejected", "conflict_id": "grant-conflict"},
            ),
            funding_evidence("grant-investor-note", {"grant_id": "grant:1", "investor_participation": "yes"}),
            funding_evidence("grant-treasury-note", {"grant_id": "grant:1", "treasury_funding": "approved"}),
        )
    )

    finding = finding_by_type(batch, "funding_observation")
    assert finding.supporting_evidence_ids == ("grant-approved", "grant-rejected")
    assert finding.evidence_lineage == ("funding:grant-approved", "funding:grant-rejected")
    assert finding.conflicts == ("grant-conflict",)


def test_funding_contradictory_records_do_not_suppress_affirmative_findings() -> None:
    batch, _repository = execute_funding(
        (
            funding_evidence("lead-true", {"investor_id": "investor:lead", "is_lead_investor": True}),
            funding_evidence("lead-false", {"investor_id": "investor:lead", "is_lead_investor": False}),
        )
    )

    finding = finding_by_type(batch, "lead_investor")
    assert finding.supporting_evidence_ids == ("lead-false", "lead-true")
    assert finding.evidence_lineage == ("funding:lead-false", "funding:lead-true")


def test_funding_transfers_or_balances_do_not_authorize_treasury_funding() -> None:
    evidence = (
        funding_evidence("transfer", {"treasury_funding_event_id": "treasury:1", "transfer_value": "100"}),
        funding_evidence("balance", {"treasury_funding_event_id": "treasury:1", "balance": "200"}),
    )

    batch, _repository = execute_funding(evidence)

    assert batch.findings == ()


def test_funding_conflicts_are_preserved_without_resolution() -> None:
    evidence = (
        funding_evidence(
            "conflict-a",
            {"funding_round_id": "round:seed", "round_amount": "100", "conflict_id": "conflict-round"},
        ),
        funding_evidence(
            "conflict-b",
            {"funding_round_id": "round:seed", "round_amount": "200", "conflicts": ["amount-disagreement"]},
        ),
    )

    batch, _repository = execute_funding(evidence)

    finding = finding_by_type(batch, "funding_round")
    assert finding.conflicts == ("amount-disagreement", "conflict-round")
    assert finding.supporting_evidence_ids == ("conflict-a", "conflict-b")
    assert "resolved" not in finding.explanation.lower()
    assert "accepted" not in finding.explanation.lower()


def test_funding_engine_normalizes_reversed_and_shuffled_evidence() -> None:
    evidence = full_evidence()
    shuffled = (
        evidence[3],
        evidence[1],
        evidence[6],
        evidence[0],
        evidence[4],
        evidence[2],
        evidence[8],
        evidence[7],
        evidence[5],
    )

    forward, _repository = execute_funding(evidence)
    reversed_batch, _repository = execute_funding(tuple(reversed(evidence)))
    shuffled_batch, _repository = execute_funding(shuffled)

    assert reversed_batch == forward
    assert shuffled_batch == forward


def test_funding_repeated_execution_is_deterministic_and_idempotent() -> None:
    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)

    first = service.execute(
        FundingFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="funding-config-v1",
    )
    second = service.execute(
        FundingFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="funding-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)


def test_funding_changed_evidence_affects_only_its_own_context() -> None:
    changed = (
        funding_evidence("grant", {"grant_id": "grant:1", "grant_funding": "awarded", "grant_amount": "200000"}),
        *without(full_evidence(), "grant"),
    )

    base, _repository = execute_funding(full_evidence())
    updated, _repository = execute_funding(changed)

    assert finding_ids(updated, "grant_funding") != finding_ids(base, "grant_funding")
    base_ids = {item.finding_id for item in base.findings}
    for finding in updated.findings:
        if finding.finding_type == "funding_observation" and "context grant:1" in finding.explanation:
            continue
        if finding.finding_type != "grant_funding":
            assert finding.finding_id in base_ids


def test_funding_service_excludes_future_evidence_by_as_of() -> None:
    future = funding_evidence(
        "future-round",
        {"funding_round_id": "round:future", "round_amount": "1"},
        collected_at=NOW + timedelta(days=1),
    )

    batch, repository = execute_funding((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_funding_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_funding(full_evidence())
    definition = replace(funding_engine_definition(), analysis_trace_version=f"{FUNDING_ANALYSIS_TRACE_VERSION}-next")
    repository = FindingRepository(full_evidence())

    changed = IntelligenceEngineService(repository).execute(
        FundingFoundationIntelligenceEngine(definition=definition),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="funding-config-v1",
    )

    assert {finding.finding_type for finding in changed.findings} == {finding.finding_type for finding in base.findings}
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_funding_service_rejects_forged_lineage() -> None:
    class ForgingFundingEngine(FundingFoundationIntelligenceEngine):
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
            ForgingFundingEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="funding-config-v1",
        )


def test_funding_shared_evidence_contract_alignment() -> None:
    source_only = funding_evidence(
        "source-only",
        {"funding_round_id": "round:seed", "round_amount": "1"},
        metadata={},
    )
    metric_contract = funding_evidence(
        "metric-contract",
        {"metric": FUNDING_EVIDENCE_CONTRACT, "funding_round_id": "round:seed", "round_amount": "1"},
        metadata={},
    )

    missing_batch, _repository = execute_funding((source_only,))
    present_batch, _repository = execute_funding((metric_contract,))

    assert missing_batch.findings == ()
    assert findings_of_type(present_batch, "funding_round")


def test_funding_repository_rollback_preserves_existing_findings_on_failure() -> None:
    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)
    existing = service.execute(
        FundingFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="funding-config-v1",
    )
    repository.fail_on_persist = True

    with pytest.raises(RuntimeError, match="persistence failed"):
        service.execute(
            FundingFoundationIntelligenceEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW + timedelta(minutes=1),
            engine_configuration_fingerprint="funding-config-v1",
        )

    assert repository.findings == {finding.finding_id: finding for finding in existing.findings}


def test_funding_engine_has_no_provider_repository_registry_file_clock_or_cache_access() -> None:
    definition = funding_engine_definition()
    engine = FundingFoundationIntelligenceEngine()

    assert engine.definition == definition
    assert not any(
        capability in definition.metadata.capabilities for capability in ("provider", "repository", "registry")
    )
