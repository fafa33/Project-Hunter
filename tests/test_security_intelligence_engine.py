from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.engines.security import (
    SECURITY_ANALYSIS_TRACE_VERSION,
    SECURITY_EVIDENCE_CONTRACT,
    SECURITY_FINDING_TYPES,
    SecurityFoundationIntelligenceEngine,
    security_engine_definition,
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


def security_evidence(
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
        source="security",
        collected_at=collected_at,
        reliability=reliability,
        freshness=1.0,
        reference=f"security:{evidence_id}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": SECURITY_EVIDENCE_CONTRACT},
    )


def full_evidence() -> tuple[Evidence, ...]:
    return (
        security_evidence(
            "contract",
            {
                "record_type": "security_contract_observation",
                "contract_address": "0xABC",
                "contract_verified": True,
                "security_provider": "goplus",
            },
        ),
        security_evidence(
            "ownership",
            {
                "record_type": "security_ownership_observation",
                "ownership_id": "ownership:0xabc",
                "ownership_status": "renounced",
                "owner_type": "timelock",
            },
        ),
        security_evidence(
            "proxy",
            {
                "record_type": "security_proxy_observation",
                "proxy_address": "0xProxy",
                "proxy_status": "proxy",
                "implementation_address": "0xImplementation",
                "upgradeability_metadata": {"kind": "transparent"},
            },
        ),
        security_evidence(
            "privilege",
            {
                "record_type": "security_privilege_observation",
                "role_id": "role:minter",
                "role_holder": "0xAdmin",
                "permission": "mint",
            },
        ),
        security_evidence(
            "token-control",
            {
                "record_type": "security_token_control_observation",
                "token_address": "0xToken",
                "mint_capability": True,
                "pause_capability": True,
            },
        ),
        security_evidence(
            "audit",
            {
                "record_type": "security_audit_reference",
                "audit_id": "audit:trailofbits:1",
                "auditor": "Trail of Bits",
                "audit_url": "https://example.invalid/audit.pdf",
            },
        ),
        security_evidence(
            "exploit",
            {
                "record_type": "security_exploit_observation",
                "exploit_id": "exploit:1",
                "exploit_reference": "postmortem:1",
                "exploit_status": "reported",
            },
        ),
        security_evidence(
            "vulnerability",
            {
                "record_type": "security_vulnerability_observation",
                "vulnerability_id": "vuln:1",
                "vulnerability_status": "reported",
                "vulnerability_type": "access-control",
            },
        ),
    )


def execute_security(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        SecurityFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="security-config-v1",
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


def test_security_engine_definition_uses_builder_contract() -> None:
    definition = security_engine_definition()

    assert definition.metadata.id == "security-intelligence-foundation"
    assert definition.evidence_contracts == (SECURITY_EVIDENCE_CONTRACT,)
    assert set(definition.finding_types) == set(SECURITY_FINDING_TYPES)
    assert definition.analysis_trace_version == SECURITY_ANALYSIS_TRACE_VERSION
    assert "isolate-security-contexts" in definition.analysis_stages


def test_security_engine_produces_all_findings_from_sufficient_evidence() -> None:
    batch, repository = execute_security(full_evidence())

    assert {finding.finding_type for finding in batch.findings} == set(SECURITY_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


@pytest.mark.parametrize(
    ("removed_id", "missing_type"),
    (
        ("contract", "contract_security"),
        ("ownership", "ownership_model"),
        ("proxy", "proxy_configuration"),
        ("privilege", "privileged_permissions"),
        ("token-control", "token_control_features"),
        ("audit", "audit_observation"),
        ("exploit", "exploit_history"),
        ("vulnerability", "vulnerability_observation"),
    ),
)
def test_security_evidence_sufficiency_suppresses_only_affected_finding(
    removed_id: str,
    missing_type: str,
) -> None:
    full_batch, _repository = execute_security(full_evidence())
    reduced_batch, _repository = execute_security(without(full_evidence(), removed_id))

    assert findings_of_type(full_batch, missing_type)
    assert not findings_of_type(reduced_batch, missing_type)
    for finding in reduced_batch.findings:
        if finding.finding_type == "security_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_security_engine_does_not_fabricate_negative_or_safe_unsafe_findings() -> None:
    batch, _repository = execute_security(
        (
            security_evidence(
                "contract-only",
                {
                    "record_type": "security_contract_observation",
                    "contract_address": "0xABC",
                    "contract_verified": True,
                },
            ),
        )
    )

    assert {finding.finding_type for finding in batch.findings} == {"contract_security", "security_observation"}
    forbidden = ("safe", "unsafe", "risk", "trust", "recommend", "no ")
    assert all(term not in finding.explanation.lower() for finding in batch.findings for term in forbidden)


@pytest.mark.parametrize("identifier", ("contract_address", "contract_id"))
def test_security_contract_context_identifier_only_produces_no_findings(identifier: str) -> None:
    batch, _repository = execute_security((security_evidence("contract-identifier-only", {identifier: "0xABC"}),))

    assert batch.findings == ()


def test_security_explicit_contract_evidence_still_produces_contract_findings() -> None:
    batch, _repository = execute_security(
        (
            security_evidence(
                "contract-explicit",
                {"contract_address": "0xABC", "contract_verified": True},
            ),
        )
    )

    assert findings_of_type(batch, "contract_security")
    assert findings_of_type(batch, "security_observation")


def test_security_contract_context_identifiers_isolate_without_satisfying_sufficiency() -> None:
    evidence = (
        security_evidence("contract-a-context-only", {"contract_address": "0xA"}),
        security_evidence("contract-b-explicit", {"contract_address": "0xB", "verification_status": "verified"}),
    )

    batch, _repository = execute_security(evidence)

    contract_findings = findings_of_type(batch, "contract_security")
    observation_findings = findings_of_type(batch, "security_observation")
    assert len(contract_findings) == 1
    assert len(observation_findings) == 1
    assert contract_findings[0].supporting_evidence_ids == ("contract-b-explicit",)
    assert observation_findings[0].supporting_evidence_ids == ("contract-b-explicit",)
    assert "context 0xb" in contract_findings[0].explanation
    assert "context 0xa" not in contract_findings[0].explanation


def test_security_findings_are_independent() -> None:
    full_batch, _repository = execute_security(full_evidence())
    reduced_batch, _repository = execute_security(without(full_evidence(), "proxy"))

    assert not findings_of_type(reduced_batch, "proxy_configuration")
    for finding in reduced_batch.findings:
        if finding.finding_type == "security_observation":
            continue
        assert finding.finding_id in {item.finding_id for item in full_batch.findings}


def test_security_same_context_observations_are_aggregated_descriptively() -> None:
    evidence = (
        security_evidence("contract-verification", {"contract_address": "0xABC", "contract_verified": True}),
        security_evidence("contract-provider", {"contract_address": "0xabc", "security_provider": "goplus"}),
    )

    batch, _repository = execute_security(evidence)

    finding = finding_by_type(batch, "contract_security")
    assert finding.supporting_evidence_ids == ("contract-provider", "contract-verification")
    assert "context 0xabc" in finding.explanation
    assert "descriptively evidenced" in finding.explanation


def test_security_cross_context_observations_are_not_aggregated() -> None:
    evidence = (
        security_evidence("contract-a", {"contract_address": "0xA", "contract_verified": True}),
        security_evidence("contract-b", {"contract_address": "0xB", "contract_verified": True}),
    )

    batch, _repository = execute_security(evidence)

    findings = findings_of_type(batch, "contract_security")
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_security_candidate_id_alone_cannot_aggregate_contexts() -> None:
    evidence = (
        security_evidence("ownership-without-context", {"ownership_status": "renounced"}),
        security_evidence("proxy-without-context", {"proxy_status": "proxy"}),
    )

    batch, _repository = execute_security(evidence)

    assert batch.findings == ()


@pytest.mark.parametrize(
    ("finding_type", "evidence"),
    (
        (
            "contract_security",
            (
                security_evidence("contract-a", {"contract_address": "0xA", "contract_verified": True}),
                security_evidence("contract-b", {"contract_address": "0xB", "contract_verified": True}),
            ),
        ),
        (
            "proxy_configuration",
            (
                security_evidence("proxy-a", {"proxy_address": "0xProxyA", "proxy_status": "proxy"}),
                security_evidence("proxy-b", {"proxy_address": "0xProxyB", "proxy_status": "proxy"}),
            ),
        ),
        (
            "ownership_model",
            (
                security_evidence("ownership-a", {"ownership_id": "own:a", "ownership_status": "renounced"}),
                security_evidence("ownership-b", {"ownership_id": "own:b", "ownership_status": "active"}),
            ),
        ),
        (
            "privileged_permissions",
            (
                security_evidence("privilege-a", {"role_id": "role:a", "permission": "mint"}),
                security_evidence("privilege-b", {"role_id": "role:b", "permission": "pause"}),
            ),
        ),
        (
            "audit_observation",
            (
                security_evidence("audit-a", {"audit_id": "audit:a", "auditor": "A"}),
                security_evidence("audit-b", {"audit_id": "audit:b", "auditor": "B"}),
            ),
        ),
        (
            "exploit_history",
            (
                security_evidence("exploit-a", {"exploit_id": "exploit:a", "exploit_status": "reported"}),
                security_evidence("exploit-b", {"exploit_id": "exploit:b", "exploit_status": "reported"}),
            ),
        ),
        (
            "vulnerability_observation",
            (
                security_evidence("vuln-a", {"vulnerability_id": "vuln:a", "vulnerability_status": "reported"}),
                security_evidence("vuln-b", {"vulnerability_id": "vuln:b", "vulnerability_status": "reported"}),
            ),
        ),
    ),
)
def test_security_context_types_remain_isolated(finding_type: str, evidence: tuple[Evidence, ...]) -> None:
    batch, _repository = execute_security(evidence)

    findings = findings_of_type(batch, finding_type)
    assert len(findings) == 2
    assert all(len(finding.supporting_evidence_ids) == 1 for finding in findings)


def test_security_malformed_context_ids_suppress_affected_findings() -> None:
    evidence = (
        security_evidence("contract-malformed", {"contract_address": "0x ABC", "contract_verified": True}),
        security_evidence("audit-valid", {"audit_id": "audit:1", "auditor": "Trail of Bits"}),
    )

    batch, _repository = execute_security(evidence)

    assert not findings_of_type(batch, "contract_security")
    assert findings_of_type(batch, "audit_observation")


def test_security_ambiguous_context_ids_suppress_affected_findings() -> None:
    evidence = (
        security_evidence(
            "ambiguous-contract",
            {"contract_address": "0xABC", "contract_id": "0xDEF", "contract_verified": True},
        ),
        security_evidence("exploit-valid", {"exploit_id": "exploit:1", "exploit_status": "reported"}),
    )

    batch, _repository = execute_security(evidence)

    assert not findings_of_type(batch, "contract_security")
    assert findings_of_type(batch, "exploit_history")


def test_security_same_context_conflicts_are_preserved_without_resolution() -> None:
    evidence = (
        security_evidence(
            "conflict-a",
            {
                "contract_address": "0xABC",
                "contract_verified": True,
                "conflict_id": "conflict-contract",
                "conflict_state": "open",
            },
        ),
        security_evidence(
            "conflict-b",
            {"contract_address": "0xabc", "contract_verified": False, "conflicts": ["verification-disagreement"]},
        ),
    )

    batch, _repository = execute_security(evidence)

    finding = finding_by_type(batch, "contract_security")
    assert finding.conflicts == ("conflict-contract", "open", "verification-disagreement")
    assert "resolved" not in finding.explanation.lower()
    assert "accepted" not in finding.explanation.lower()


def test_security_engine_normalizes_reversed_and_shuffled_evidence() -> None:
    evidence = full_evidence()
    shuffled = (evidence[3], evidence[1], evidence[6], evidence[0], evidence[4], evidence[2], evidence[7], evidence[5])

    forward, _repository = execute_security(evidence)
    reversed_batch, _repository = execute_security(tuple(reversed(evidence)))
    shuffled_batch, _repository = execute_security(shuffled)

    assert reversed_batch == forward
    assert shuffled_batch == forward


def test_security_repeated_execution_is_deterministic_and_idempotent() -> None:
    repository = FindingRepository(full_evidence())
    service = IntelligenceEngineService(repository)

    first = service.execute(
        SecurityFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="security-config-v1",
    )
    second = service.execute(
        SecurityFoundationIntelligenceEngine(),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="security-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)


def test_security_changed_evidence_affects_only_its_own_context() -> None:
    changed = (
        security_evidence("contract", {"contract_address": "0xABC", "contract_verified": False}),
        *without(full_evidence(), "contract"),
    )

    base, _repository = execute_security(full_evidence())
    updated, _repository = execute_security(changed)

    assert finding_ids(updated, "contract_security") != finding_ids(base, "contract_security")
    base_ids = {item.finding_id for item in base.findings}
    for finding in updated.findings:
        if finding.finding_type == "security_observation" and "context 0xabc" in finding.explanation:
            continue
        if finding.finding_type != "contract_security":
            assert finding.finding_id in base_ids


def test_security_service_excludes_future_evidence_by_as_of() -> None:
    future = security_evidence(
        "future-contract",
        {"contract_address": "0xFuture", "contract_verified": True},
        collected_at=NOW + timedelta(days=1),
    )

    batch, repository = execute_security((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_security_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_security(full_evidence())
    definition = replace(security_engine_definition(), analysis_trace_version=f"{SECURITY_ANALYSIS_TRACE_VERSION}-next")
    repository = FindingRepository(full_evidence())

    changed = IntelligenceEngineService(repository).execute(
        SecurityFoundationIntelligenceEngine(definition=definition),
        candidate_id="uniswap",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="security-config-v1",
    )

    assert {finding.finding_type for finding in changed.findings} == {finding.finding_type for finding in base.findings}
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_security_service_rejects_forged_lineage() -> None:
    class ForgingSecurityEngine(SecurityFoundationIntelligenceEngine):
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
            ForgingSecurityEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="security-config-v1",
        )


def test_security_shared_evidence_contract_alignment() -> None:
    source_only = security_evidence(
        "source-only",
        {"contract_address": "0xABC", "contract_verified": True},
        metadata={},
    )
    metric_contract = security_evidence(
        "metric-contract",
        {"metric": SECURITY_EVIDENCE_CONTRACT, "contract_address": "0xABC", "contract_verified": True},
        metadata={},
    )

    missing_batch, _repository = execute_security((source_only,))
    present_batch, _repository = execute_security((metric_contract,))

    assert missing_batch.findings == ()
    assert present_batch.findings
    assert {finding.missing_evidence for finding in present_batch.findings} == {()}


def test_security_service_persistence_rollback_and_backward_compatibility() -> None:
    failing_repository = FindingRepository(full_evidence(), fail_on_persist=True)
    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(failing_repository).execute(
            SecurityFoundationIntelligenceEngine(),
            candidate_id="uniswap",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="security-config-v1",
        )
    assert failing_repository.findings == {}

    legacy_type = security_evidence(
        "legacy-type",
        {"type": SECURITY_EVIDENCE_CONTRACT, "contract_address": "0xABC", "contract_verified": True},
        metadata={},
    )
    batch, _repository = execute_security((legacy_type,))

    assert findings_of_type(batch, "contract_security")
