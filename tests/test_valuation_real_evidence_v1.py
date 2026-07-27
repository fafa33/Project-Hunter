"""Real-evidence-backed validation for structured valuation outputs (Issue #107).

Every other `valuation`/`valuation_methodology`/`historical.valuation_calibration` test
builds its `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot`/`SupplyBasisSnapshot`
chain through the real, unmodified service/authority boundary
(`SupplyAndValueCaptureService.ingest_evidence`/`ingest_supply`/`ingest_rule`,
`CanonicalValuationMethodologyAuthority`, `CanonicalValuationService`) rather than
hand-constructing dataclasses that bypass validation -- so those tests already carry
genuine, pipeline-validated evidence. What none of them touch is the actual real
production evidence chain already persisted, once and for real, in the tracked
`data/data_ops.sqlite` -- the Sky (`SKY`) pilot entity registered under Issue #107
Milestone 1 (see `docs/IMPLEMENTATION_REPORTS/v3.6.0-milestone-1-entity-registration.md`).

This module reads that real, tracked database (copied read-only into a `tmp_path` --
the tracked file itself is never opened for writing) and validates two things using
only genuine, already-persisted evidence, with no fabricated or placeholder values:

1. The real Sky `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot`/`SupplyBasisSnapshot`
   chain is independently, deterministically strict-known-readable and cross-validates
   exactly as Milestone 1's implementation report claims.
2. `CanonicalValuationService.estimate_fair_value`, run for real against that real
   evidence, deterministically fails closed -- raising `CanonicalValuationAuthorityError`,
   persisting nothing -- rather than fabricating a structured valuation output. This is
   real, disclosed data-completeness state, not a code defect: the real, accepted
   `FundamentalEvidenceRecord.accounting_period_start/end` for Sky spans 30 days, not the
   365-day horizon ADR 0022's permitted methodology fixes (`horizon_days` is a fixed
   constant enforced by `CanonicalValuationService`, never caller-parameterized -- see
   `src/hunter/valuation/service.py` `estimate_fair_value`'s accounting-period-length
   check). Closing that gap requires acquiring new real Sky evidence with a full 365-day
   accounting period through the existing `hunter valuation-evidence acquire` pipeline
   (real external network access this environment does not have) -- it is not something
   this test suite may synthesize without fabricating evidence, which Issue #107's
   completion-audit remediation explicitly prohibits.

No architecture, methodology rule, or production code is changed by this module. It is
read-only against the tracked database and exercises only existing, unmodified, already-
audited service/authority surfaces.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import (
    CANONICAL_DISCOUNT_RATE_POLICY_ID,
    CANONICAL_DISCOUNT_RATE_POLICY_VERSION,
    CANONICAL_SENSITIVITY_POLICY_ID,
    CANONICAL_SENSITIVITY_POLICY_VERSION,
    CANONICAL_SUPPLY_BASIS_SELECTION_RULE,
    CanonicalValuationAuthorityError,
    CanonicalValuationService,
)
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

REAL_TRACKED_DATABASE = Path(__file__).resolve().parents[1] / "data" / "data_ops.sqlite"

# The real, currently-accepted Sky record identity and IDs, as persisted by Issue #107
# Milestone 1 (PR #110) and documented in
# docs/IMPLEMENTATION_REPORTS/v3.6.0-milestone-1-entity-registration.md's "Evidence
# Impact" section. These are not test fixtures -- they are the exact real record IDs
# tracked on `main`.
SKY_IDENTITY = EconomicClaimIdentity(
    entity_id="entity:sky",
    economic_claim_id="economic-claim:sky-smart-burn-engine",
    asset_id="asset:sky",
    representation_id="representation:sky-ethereum",
    token_id="sky",
    chain="ethereum",
    contract_address="0x56072c95faa701256059aa122697b133aded9279",
)
SKY_CURRENT_EVIDENCE_RECORD_ID = "57ddd15569f7348f72b6a0498fd8fff6d0fae0e61d0906f856625cc9ed2e5e47"
SKY_CURRENT_RULE_RECORD_ID = "f774f946b80c11244faddeb69f4d40e01a501226c036df5497bc3dbc00020abd"
SKY_CURRENT_SUPPLY_RECORD_ID = "fe15f8a3cd4c7dd495bca0613b57ac0543fbfedb97be03f8748af43d05a1de0f"

# Far enough past every real record's own known_at that strict-known selection at this
# cutoff sees the complete, current (non-superseded) real chain.
REAL_EVIDENCE_PROBE_AT = datetime(2027, 1, 1, tzinfo=UTC)


def _copy_of_real_database(tmp_path: Path) -> Path:
    """Copy the tracked production database into an isolated tmp_path. The real,
    tracked `data/data_ops.sqlite` is opened read-only (via `shutil.copyfile`'s source)
    and is never written to by this or any other test."""
    assert REAL_TRACKED_DATABASE.exists(), "tracked data/data_ops.sqlite must exist for real-evidence validation"
    copied = tmp_path / "real-sky-evidence-probe.sqlite"
    shutil.copyfile(REAL_TRACKED_DATABASE, copied)
    return copied


def test_real_persisted_sky_evidence_chain_is_strict_known_readable_and_cross_validated(tmp_path: Path) -> None:
    db_path = _copy_of_real_database(tmp_path)
    repository = SupplyAndValueCaptureRepository(db_path)

    evidence = repository.strict_known_evidence(
        entity_id=SKY_IDENTITY.entity_id,
        economic_claim_id=SKY_IDENTITY.economic_claim_id,
        representation_id=SKY_IDENTITY.representation_id,
        effective_as_of=REAL_EVIDENCE_PROBE_AT,
        known_by=REAL_EVIDENCE_PROBE_AT,
        evidence_type="official_disclosure",
    )
    rule = repository.strict_known_rule(
        entity_id=SKY_IDENTITY.entity_id,
        economic_claim_id=SKY_IDENTITY.economic_claim_id,
        representation_id=SKY_IDENTITY.representation_id,
        effective_as_of=REAL_EVIDENCE_PROBE_AT,
        known_by=REAL_EVIDENCE_PROBE_AT,
        rule_type="buyback_and_burn",
    )
    supply = repository.strict_known_supply(
        entity_id=SKY_IDENTITY.entity_id,
        economic_claim_id=SKY_IDENTITY.economic_claim_id,
        representation_id=SKY_IDENTITY.representation_id,
        effective_as_of=REAL_EVIDENCE_PROBE_AT,
        known_by=REAL_EVIDENCE_PROBE_AT,
        supply_basis_type="circulating_supply",
    )

    assert evidence is not None and evidence.record_id == SKY_CURRENT_EVIDENCE_RECORD_ID
    assert rule is not None and rule.record_id == SKY_CURRENT_RULE_RECORD_ID
    assert supply is not None and supply.record_id == SKY_CURRENT_SUPPLY_RECORD_ID
    assert evidence.quality_state == "accepted" and evidence.conflict_state == "none"
    assert rule.quality_state == "accepted" and rule.conflict_state == "none"
    assert supply.quality_state == "accepted" and supply.conflict_state == "none"
    # ADR 0022 Scope condition 4's literal cross-attribution requirement.
    assert evidence.attribution_rule_id == rule.mechanism_policy_id
    assert supply.identity == evidence.identity == rule.identity == SKY_IDENTITY


def test_real_persisted_sky_evidence_replay_is_deterministic_across_repeated_reads(tmp_path: Path) -> None:
    db_path = _copy_of_real_database(tmp_path)
    repository = SupplyAndValueCaptureRepository(db_path)

    def read() -> tuple[str, str, str]:
        evidence = repository.strict_known_evidence(
            entity_id=SKY_IDENTITY.entity_id,
            economic_claim_id=SKY_IDENTITY.economic_claim_id,
            representation_id=SKY_IDENTITY.representation_id,
            effective_as_of=REAL_EVIDENCE_PROBE_AT,
            known_by=REAL_EVIDENCE_PROBE_AT,
            evidence_type="official_disclosure",
        )
        rule = repository.strict_known_rule(
            entity_id=SKY_IDENTITY.entity_id,
            economic_claim_id=SKY_IDENTITY.economic_claim_id,
            representation_id=SKY_IDENTITY.representation_id,
            effective_as_of=REAL_EVIDENCE_PROBE_AT,
            known_by=REAL_EVIDENCE_PROBE_AT,
            rule_type="buyback_and_burn",
        )
        supply = repository.strict_known_supply(
            entity_id=SKY_IDENTITY.entity_id,
            economic_claim_id=SKY_IDENTITY.economic_claim_id,
            representation_id=SKY_IDENTITY.representation_id,
            effective_as_of=REAL_EVIDENCE_PROBE_AT,
            known_by=REAL_EVIDENCE_PROBE_AT,
            supply_basis_type="circulating_supply",
        )
        assert evidence is not None and rule is not None and supply is not None
        return evidence.record_id, rule.record_id, supply.record_id

    first = read()
    second = read()
    assert first == second == (SKY_CURRENT_EVIDENCE_RECORD_ID, SKY_CURRENT_RULE_RECORD_ID, SKY_CURRENT_SUPPLY_RECORD_ID)


def _real_evidence_service(db_path: Path, tmp_path: Path) -> CanonicalValuationService:
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vm_repository = ValuationMethodologyRepository(db_path)
    methodology_authority = CanonicalValuationMethodologyAuthority(repository=vm_repository, application_root=tmp_path)
    methodology_authority.persist_methodology(
        entity_class_criteria_id="adr-0022-first-entity-class-v1",
        entity_class_criteria_version="1.0.0",
        currency="usd",
        discount_rate_policy_id=CANONICAL_DISCOUNT_RATE_POLICY_ID,
        discount_rate_policy_version=CANONICAL_DISCOUNT_RATE_POLICY_VERSION,
        sensitivity_policy_id=CANONICAL_SENSITIVITY_POLICY_ID,
        sensitivity_policy_version=CANONICAL_SENSITIVITY_POLICY_VERSION,
        supply_basis_selection_rule=CANONICAL_SUPPLY_BASIS_SELECTION_RULE,
        effective_at=REAL_EVIDENCE_PROBE_AT,
        recorded_at=REAL_EVIDENCE_PROBE_AT,
        known_at=REAL_EVIDENCE_PROBE_AT,
    )
    return CanonicalValuationService(
        repository=CanonicalValuationRepository(db_path),
        methodology_authority=methodology_authority,
        value_capture_repository=vc_repository,
        market_fact_repository=ObservedMarketFactRepository(db_path),
        application_root=tmp_path,
    )


def test_canonical_valuation_service_fails_closed_on_real_persisted_sky_evidence(tmp_path: Path) -> None:
    """Genuine evidence flowing through the real canonical valuation pipeline: the real,
    tracked Sky evidence chain is fed, unmodified, into the real
    `CanonicalValuationService.estimate_fair_value`. It must fail closed for the real,
    disclosed reason (a 30-day accounting period cannot satisfy the fixed 365-day
    horizon) rather than fabricate a structured valuation output -- proving Milestone 3's
    truthful-missingness guarantee against real, not fixture, evidence."""
    db_path = _copy_of_real_database(tmp_path)
    service = _real_evidence_service(db_path, tmp_path)

    with pytest.raises(CanonicalValuationAuthorityError, match="accounting period length"):
        service.estimate_fair_value(
            identity=SKY_IDENTITY,
            evidence_type="official_disclosure",
            rule_type="buyback_and_burn",
            effective_at=REAL_EVIDENCE_PROBE_AT,
            recorded_at=REAL_EVIDENCE_PROBE_AT,
            known_at=REAL_EVIDENCE_PROBE_AT,
        )

    # No structured valuation output was fabricated as a side effect of the failed attempt.
    assert CanonicalValuationRepository(db_path).count("fair_value_estimate_records") == 0
    assert CanonicalValuationRepository(db_path).count("valuation_assessment_records") == 0


def test_real_evidence_fail_closed_behavior_is_deterministic_across_repeated_attempts(tmp_path: Path) -> None:
    db_path = _copy_of_real_database(tmp_path)
    service = _real_evidence_service(db_path, tmp_path)

    def attempt() -> str:
        with pytest.raises(CanonicalValuationAuthorityError) as excinfo:
            service.estimate_fair_value(
                identity=SKY_IDENTITY,
                evidence_type="official_disclosure",
                rule_type="buyback_and_burn",
                effective_at=REAL_EVIDENCE_PROBE_AT,
                recorded_at=REAL_EVIDENCE_PROBE_AT,
                known_at=REAL_EVIDENCE_PROBE_AT,
            )
        return str(excinfo.value)

    first_message = attempt()
    second_message = attempt()
    assert first_message == second_message
    assert "accounting period length" in first_message
    assert CanonicalValuationRepository(db_path).count("fair_value_estimate_records") == 0
