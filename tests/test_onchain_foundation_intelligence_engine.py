from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.engines.onchain import (
    ONCHAIN_ANALYSIS_TRACE_VERSION,
    ONCHAIN_EVIDENCE_CONTRACT,
    ONCHAIN_FINDING_TYPES,
    OnchainFoundationIntelligenceEngine,
    onchain_engine_definition,
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


def onchain_evidence(
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
        source="onchain",
        collected_at=collected_at,
        reliability=reliability,
        freshness=1.0,
        reference=f"onchain:{evidence_id}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": ONCHAIN_EVIDENCE_CONTRACT},
    )


def full_evidence() -> tuple[Evidence, ...]:
    return (
        onchain_evidence(
            "holder",
            {
                "record_type": "onchain_holder_distribution",
                "holder_id": "holder:uni",
                "holder_count": 1000,
                "top_holder_share": 0.2,
            },
        ),
        onchain_evidence(
            "whale",
            {
                "record_type": "onchain_whale_observation",
                "wallet_address": "0xWhale",
                "large_transfer_count": 3,
                "large_transfer_value": "1000000",
            },
        ),
        onchain_evidence(
            "treasury",
            {
                "record_type": "onchain_treasury_observation",
                "treasury_id": "treasury:uni",
                "treasury_activity": "transfer",
                "treasury_outflow": "1000",
            },
        ),
        onchain_evidence(
            "bridge",
            {
                "record_type": "onchain_bridge_observation",
                "bridge_id": "bridge:base",
                "bridge_inflow": "2000",
                "source_chain": "ethereum",
                "target_chain": "base",
            },
        ),
        onchain_evidence(
            "liquidity",
            {
                "record_type": "onchain_liquidity_observation",
                "pool_id": "pool:uni-eth",
                "liquidity_added": "500",
                "reserve0": "100",
                "reserve1": "200",
            },
        ),
        onchain_evidence(
            "staking",
            {
                "record_type": "onchain_staking_observation",
                "staking_position_id": "stake:1",
                "staked_amount": "300",
                "staked_inflow": "50",
            },
        ),
        onchain_evidence(
            "validator",
            {
                "record_type": "onchain_validator_observation",
                "validator_id": "validator:1",
                "validator_activity": "attestation",
                "staker_count": 400,
            },
        ),
        onchain_evidence(
            "transaction",
            {
                "record_type": "onchain_transaction_pattern",
                "transaction_hash": "0xTx",
                "transaction_count": 10,
                "repeated_pattern_ratio": 0.1,
            },
        ),
        onchain_evidence(
            "contract",
            {
                "record_type": "onchain_contract_interaction",
                "contract_address": "0xContract",
                "interactions": 20,
                "unique_callers": 5,
            },
        ),
    )


def execute_onchain(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        OnchainFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="onchain-config-v1",
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


def test_onchain_engine_definition_uses_builder_contract() -> None:
    definition = onchain_engine_definition()

    assert definition.metadata.id == "onchain-intelligence-foundation"
    assert definition.evidence_contracts == (ONCHAIN_EVIDENCE_CONTRACT,)
    assert set(definition.finding_types) == set(ONCHAIN_FINDING_TYPES)
    assert definition.analysis_trace_version == ONCHAIN_ANALYSIS_TRACE_VERSION
    assert "isolate-onchain-contexts" in definition.analysis_stages


def test_onchain_engine_produces_all_findings_from_sufficient_evidence() -> None:
    batch, repository = execute_onchain(full_evidence())

    assert {finding.finding_type for finding in batch.findings} == set(ONCHAIN_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


@pytest.mark.parametrize(
    ("removed_id", "missing_type"),
    (
        ("holder", "holder_distribution"),
        ("whale", "whale_activity"),
        ("treasury", "treasury_activity"),
        ("bridge", "bridge_activity"),
        ("liquidity", "liquidity_activity"),
        ("staking", "staking_activity"),
        ("validator", "validator_activity"),
        ("transaction", "transaction_pattern"),
        ("contract", "contract_interaction"),
    ),
)
def test_onchain_evidence_sufficiency_suppresses_only_affected_finding(
    removed_id: str,
    missing_type: str,
) -> None:
    full_batch, _repository = execute_onchain(full_evidence())
    reduced_batch, _repository = execute_onchain(without(full_evidence(), removed_id))

    assert findings_of_type(full_batch, missing_type)
    assert not findings_of_type(reduced_batch, missing_type)
    for finding in reduced_batch.findings:
        if finding.finding_type == "onchain_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


@pytest.mark.parametrize(
    "payload",
    (
        {"wallet_address": "0xWallet"},
        {"holder_id": "holder:1"},
        {"treasury_id": "treasury:1"},
        {"bridge_id": "bridge:1"},
        {"validator_id": "validator:1"},
        {"staking_position_id": "stake:1"},
        {"pool_id": "pool:1"},
        {"transaction_hash": "0xTx"},
        {"contract_address": "0xContract"},
        {"token_address": "0xToken"},
    ),
)
def test_onchain_context_identifiers_alone_produce_no_findings(payload: dict[str, object]) -> None:
    batch, _repository = execute_onchain((onchain_evidence("context-only", payload),))

    assert batch.findings == ()


def test_onchain_engine_does_not_fabricate_negative_or_intent_findings() -> None:
    batch, _repository = execute_onchain(
        (
            onchain_evidence(
                "wallet-transfer",
                {
                    "wallet_address": "0xWallet",
                    "large_transfer_count": 2,
                    "large_transfer_value": "1000",
                },
            ),
        )
    )

    forbidden = (
        "accumulation",
        "distribution strategy",
        "intent",
        "manipulation",
        "ownership",
        "profit",
        "quality",
        "risk",
        "strategy",
        "no ",
    )
    assert {finding.finding_type for finding in batch.findings} == {"onchain_observation", "whale_activity"}
    assert all(term not in finding.explanation.lower() for finding in batch.findings for term in forbidden)


def test_onchain_findings_are_independent() -> None:
    full_batch, _repository = execute_onchain(full_evidence())
    reduced_batch, _repository = execute_onchain(without(full_evidence(), "bridge"))

    assert not findings_of_type(reduced_batch, "bridge_activity")
    for finding in reduced_batch.findings:
        if finding.finding_type == "onchain_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_onchain_same_context_observations_are_aggregated_descriptively() -> None:
    evidence = (
        onchain_evidence("wallet-count", {"wallet_address": "0xWhale", "large_transfer_count": 2}),
        onchain_evidence("wallet-value", {"wallet_address": "0xwhale", "large_transfer_value": "1000"}),
    )

    batch, _repository = execute_onchain(evidence)

    finding = finding_by_type(batch, "whale_activity")
    assert finding.supporting_evidence_ids == ("wallet-count", "wallet-value")
    assert "context 0xwhale" in finding.explanation
    assert "descriptively evidenced" in finding.explanation


def test_onchain_cross_context_observations_are_not_aggregated() -> None:
    evidence = (
        onchain_evidence("wallet-a", {"wallet_address": "0xA", "large_transfer_count": 2}),
        onchain_evidence("wallet-b", {"wallet_address": "0xB", "large_transfer_count": 3}),
    )

    batch, _repository = execute_onchain(evidence)

    findings = findings_of_type(batch, "whale_activity")
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_onchain_candidate_id_alone_cannot_merge_contexts() -> None:
    evidence = (
        onchain_evidence("treasury-without-context", {"treasury_activity": "transfer"}),
        onchain_evidence("bridge-without-context", {"bridge_inflow": "100"}),
    )

    batch, _repository = execute_onchain(evidence)

    assert batch.findings == ()


@pytest.mark.parametrize(
    ("finding_type", "evidence"),
    (
        (
            "whale_activity",
            (
                onchain_evidence("wallet-a", {"wallet_address": "0xA", "large_transfer_count": 1}),
                onchain_evidence("wallet-b", {"wallet_address": "0xB", "large_transfer_count": 2}),
            ),
        ),
        (
            "holder_distribution",
            (
                onchain_evidence("holder-a", {"holder_id": "holder:a", "holder_count": 1}),
                onchain_evidence("holder-b", {"holder_id": "holder:b", "holder_count": 2}),
            ),
        ),
        (
            "treasury_activity",
            (
                onchain_evidence("treasury-a", {"treasury_id": "treasury:a", "treasury_inflow": "1"}),
                onchain_evidence("treasury-b", {"treasury_id": "treasury:b", "treasury_inflow": "2"}),
            ),
        ),
        (
            "bridge_activity",
            (
                onchain_evidence("bridge-a", {"bridge_id": "bridge:a", "bridge_inflow": "1"}),
                onchain_evidence("bridge-b", {"bridge_id": "bridge:b", "bridge_inflow": "2"}),
            ),
        ),
        (
            "validator_activity",
            (
                onchain_evidence("validator-a", {"validator_id": "validator:a", "validator_activity": "active"}),
                onchain_evidence("validator-b", {"validator_id": "validator:b", "validator_activity": "active"}),
            ),
        ),
        (
            "staking_activity",
            (
                onchain_evidence("stake-a", {"staking_position_id": "stake:a", "staked_amount": "1"}),
                onchain_evidence("stake-b", {"staking_position_id": "stake:b", "staked_amount": "2"}),
            ),
        ),
        (
            "liquidity_activity",
            (
                onchain_evidence("pool-a", {"pool_id": "pool:a", "liquidity_added": "1"}),
                onchain_evidence("pool-b", {"pool_id": "pool:b", "liquidity_added": "2"}),
            ),
        ),
        (
            "transaction_pattern",
            (
                onchain_evidence("tx-a", {"transaction_hash": "0xA", "transaction_count": 1}),
                onchain_evidence("tx-b", {"transaction_hash": "0xB", "transaction_count": 2}),
            ),
        ),
        (
            "contract_interaction",
            (
                onchain_evidence("contract-a", {"contract_address": "0xA", "interactions": 1}),
                onchain_evidence("contract-b", {"contract_address": "0xB", "interactions": 2}),
            ),
        ),
        (
            "onchain_observation",
            (
                onchain_evidence("token-a", {"token_address": "0xA", "token_balance": "1"}),
                onchain_evidence("token-b", {"token_address": "0xB", "token_balance": "2"}),
            ),
        ),
    ),
)
def test_onchain_context_types_remain_isolated(finding_type: str, evidence: tuple[Evidence, ...]) -> None:
    batch, _repository = execute_onchain(evidence)

    findings = findings_of_type(batch, finding_type)
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_onchain_malformed_context_ids_suppress_affected_findings() -> None:
    evidence = (
        onchain_evidence("wallet-malformed", {"wallet_address": "0x Bad", "large_transfer_count": 1}),
        onchain_evidence("bridge-valid", {"bridge_id": "bridge:1", "bridge_inflow": "100"}),
    )

    batch, _repository = execute_onchain(evidence)

    assert not findings_of_type(batch, "whale_activity")
    assert findings_of_type(batch, "bridge_activity")


def test_onchain_ambiguous_context_ids_suppress_affected_findings() -> None:
    evidence = (
        onchain_evidence("ambiguous-wallet", {"wallet_address": "0xA", "wallet_id": "0xB", "large_transfer_count": 1}),
        onchain_evidence("contract-valid", {"contract_address": "0xContract", "interactions": 10}),
    )

    batch, _repository = execute_onchain(evidence)

    assert not findings_of_type(batch, "whale_activity")
    assert findings_of_type(batch, "contract_interaction")


def test_onchain_balance_or_transfer_evidence_does_not_infer_attribution_or_profitability() -> None:
    evidence = (
        onchain_evidence("token-balance", {"token_address": "0xToken", "token_balance": "1000"}),
        onchain_evidence("wallet-transfer", {"wallet_address": "0xWallet", "transfer_value": "1000"}),
    )

    batch, _repository = execute_onchain(evidence)

    assert "treasury_activity" not in {finding.finding_type for finding in batch.findings}
    assert "liquidity_activity" not in {finding.finding_type for finding in batch.findings}
    forbidden = ("ownership", "intent", "manipulation", "profit", "treasury", "accumulation", "strategy")
    assert all(term not in finding.explanation.lower() for finding in batch.findings for term in forbidden)


def test_onchain_conflicts_are_preserved_without_resolution() -> None:
    evidence = (
        onchain_evidence(
            "conflict-a",
            {
                "wallet_address": "0xWhale",
                "large_transfer_count": 2,
                "conflict_id": "conflict-wallet",
                "conflict_state": "open",
            },
        ),
        onchain_evidence(
            "conflict-b",
            {"wallet_address": "0xwhale", "large_transfer_count": 3, "conflicts": ["transfer-disagreement"]},
        ),
    )

    batch, _repository = execute_onchain(evidence)

    finding = finding_by_type(batch, "whale_activity")
    assert finding.conflicts == ("conflict-wallet", "open", "transfer-disagreement")
    assert "resolved" not in finding.explanation.lower()
    assert "accepted" not in finding.explanation.lower()


def test_onchain_engine_normalizes_reversed_and_shuffled_evidence() -> None:
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

    forward, _repository = execute_onchain(evidence)
    reversed_batch, _repository = execute_onchain(tuple(reversed(evidence)))
    shuffled_batch, _repository = execute_onchain(shuffled)

    assert reversed_batch == forward
    assert shuffled_batch == forward


def test_onchain_repeated_execution_is_deterministic_and_idempotent() -> None:
    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)

    first = service.execute(
        OnchainFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="onchain-config-v1",
    )
    second = service.execute(
        OnchainFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="onchain-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)


def test_onchain_changed_evidence_affects_only_its_own_context() -> None:
    changed = (
        onchain_evidence("whale", {"wallet_address": "0xWhale", "large_transfer_count": 4}),
        *without(full_evidence(), "whale"),
    )

    base, _repository = execute_onchain(full_evidence())
    updated, _repository = execute_onchain(changed)

    assert finding_ids(updated, "whale_activity") != finding_ids(base, "whale_activity")
    base_ids = {item.finding_id for item in base.findings}
    for finding in updated.findings:
        if finding.finding_type == "onchain_observation" and "context 0xwhale" in finding.explanation:
            continue
        if finding.finding_type != "whale_activity":
            assert finding.finding_id in base_ids


def test_onchain_service_excludes_future_evidence_by_as_of() -> None:
    future = onchain_evidence(
        "future-wallet",
        {"wallet_address": "0xFuture", "large_transfer_count": 1},
        collected_at=NOW + timedelta(days=1),
    )

    batch, repository = execute_onchain((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_onchain_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_onchain(full_evidence())
    definition = replace(onchain_engine_definition(), analysis_trace_version=f"{ONCHAIN_ANALYSIS_TRACE_VERSION}-next")
    repository = FindingRepository(full_evidence())

    changed = IntelligenceEngineService(repository).execute(
        OnchainFoundationIntelligenceEngine(definition=definition),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="onchain-config-v1",
    )

    assert {finding.finding_type for finding in changed.findings} == {finding.finding_type for finding in base.findings}
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_onchain_service_rejects_forged_lineage() -> None:
    class ForgingOnchainEngine(OnchainFoundationIntelligenceEngine):
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
            ForgingOnchainEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="onchain-config-v1",
        )


def test_onchain_shared_evidence_contract_alignment() -> None:
    source_only = onchain_evidence(
        "source-only",
        {"wallet_address": "0xWhale", "large_transfer_count": 1},
        metadata={},
    )
    metric_contract = onchain_evidence(
        "metric-contract",
        {"metric": ONCHAIN_EVIDENCE_CONTRACT, "wallet_address": "0xWhale", "large_transfer_count": 1},
        metadata={},
    )

    missing_batch, _repository = execute_onchain((source_only,))
    present_batch, _repository = execute_onchain((metric_contract,))

    assert missing_batch.findings == ()
    assert present_batch.findings
    assert {finding.missing_evidence for finding in present_batch.findings} == {()}


def test_onchain_service_persistence_rollback_and_backward_compatibility() -> None:
    failing_repository = FindingRepository(full_evidence(), fail_on_persist=True)
    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(failing_repository).execute(
            OnchainFoundationIntelligenceEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="onchain-config-v1",
        )
    assert failing_repository.findings == {}

    legacy_type = onchain_evidence(
        "legacy-type",
        {"type": ONCHAIN_EVIDENCE_CONTRACT, "wallet_address": "0xWhale", "large_transfer_count": 1},
        metadata={},
    )
    batch, _repository = execute_onchain((legacy_type,))

    assert findings_of_type(batch, "whale_activity")
