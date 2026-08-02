from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import timedelta

from test_asymmetry_v1 import SCENARIO_IDS, SCENARIO_TYPES
from test_canonical_evidence_assembly import (
    _constituent,
    _contract,
    _ContractAuthority,
    _NativeEvidenceQuery,
    _RegistryAuthority,
    _seed_authorities,
    _SemanticsAuthority,
)
from test_canonical_evidence_assembly import (
    registry as assembly_registry_fixture,
)
from test_comparative_valuation_v1 import (
    NOW,
    RECORDED,
    SIGNING_KEY,
    SIGNING_KEY_ID,
    identity,
    market_fact_identity,
    supply_result,
)
from test_comparative_valuation_v1 import Fixture as ComparativeFixture
from test_mispricing_v1 import methodology_payload

from hunter.asymmetry.repository import AsymmetryRepository
from hunter.asymmetry.service import CanonicalAsymmetryService
from hunter.comparative_valuation.service import CanonicalComparativeValuationService
from hunter.evidence_assembly import AssembledEvidenceRepository, CanonicalEvidenceAssemblyService
from hunter.market_facts.models import MarketFactAcquisitionResult, MarketFactRequest, NormalizedMarketFact
from hunter.market_facts.registry import MarketFactSourceRegistry
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.mispricing.repository import MispricingRepository
from hunter.mispricing.service import CanonicalMispricingService
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationService
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceConfig, ValueCaptureSourceRegistry
from hunter.value_capture.service import SupplyAndValueCaptureService


def _import_targets(tree: ast.AST) -> set[str]:
    targets = {alias.name.lower() for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            targets.add(module)
            targets.update(f"{module}.{alias.name.lower()}" for alias in node.names)
    return targets


def _market_facts(db_path):
    bound = market_fact_identity("target")
    registry = MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "coordinated-spot-source",
                    "provider_id": "coordinated-spot-provider",
                    "endpoint_template": "https://example.org/market/{listing_id}",
                    "allowed_hosts": ["example.org"],
                    "parser_version": "coordinated-spot-v1",
                    "enabled": True,
                    "capabilities": ["spot_price", "circulating_supply"],
                    "quote_currencies": ["usd"],
                    "units": {"spot_price": "usd", "circulating_supply": "native_units"},
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "identity_bindings": [
                        {
                            "entity_id": bound.entity_id,
                            "asset_id": bound.asset_id,
                            "representation_id": bound.representation_id,
                            "chain": bound.chain,
                            "contract_address": bound.contract_address,
                            "provider_listing_id": bound.provider_listing_id,
                        }
                    ],
                    "freshness_seconds": 3600,
                    "observation_confidence": "0.9",
                    "historical_support": "current-only",
                    "limitations": "coordinated integration fixture",
                }
            ]
        }
    )
    request = MarketFactRequest(
        source_id="coordinated-spot-source",
        provider_id="coordinated-spot-provider",
        identity=bound,
        quote_currency="usd",
        requested_fact_types=("spot_price", "circulating_supply"),
        requested_at=NOW,
    )
    result = MarketFactAcquisitionResult(
        source_id=request.source_id,
        provider_id=request.provider_id,
        endpoint=f"https://example.org/market/{bound.provider_listing_id}",
        parser_version="coordinated-spot-v1",
        registry_fingerprint=registry.require(request.source_id).fingerprint,
        provider_source_record_id=bound.provider_listing_id,
        provider_source_record_version="2026-01-01",
        request=request,
        status="success",
        acquired_at=NOW,
        known_at=NOW,
        raw_payload_hash="sha256:" + "c" * 64,
        facts=(
            NormalizedMarketFact(
                fact_type="spot_price",
                value="0.01",
                unit="usd",
                quote_currency="usd",
                effective_at=NOW,
                observed_at=NOW,
                confidence="0.9",
            ),
            NormalizedMarketFact(
                fact_type="circulating_supply",
                value="86000000",
                unit="native_units",
                quote_currency=None,
                effective_at=NOW,
                observed_at=NOW,
                confidence="0.9",
            ),
        ),
    )
    return ObservedMarketFactService(ObservedMarketFactRepository(db_path), registry).ingest(
        request, result, recorded_at=NOW
    )


class CoordinatedFixture:
    """One database, one target identity, and one exact upstream evidence state."""

    def __init__(self, root, *, permuted: bool = False) -> None:
        self.root = root
        self.comparative = ComparativeFixture(root)
        self.db_path = self.comparative.db_path
        self.target_evidence = self.comparative.evidence["target"]
        self.target_supply = self.comparative.supply["target"]
        self.target_rule = self.comparative.rule["target"]
        self.policy = self.comparative.persist_policy()
        universe_order = ("peer-a", "peer-c", "peer-b") if permuted else ("peer-c", "peer-a", "peer-b")
        self.universe = self.comparative.persist_universe(policy=self.policy, entities=universe_order)
        self.comparative_assessment = self.comparative.assess_complete(universe=self.universe)

        self.spot, circulating_fact = _market_facts(self.db_path)
        circulating_source = ValueCaptureSourceConfig(
            source_id="official-cmp-tokenomics",
            authority_tier="official",
            source_type="official_disclosure",
            allowed_hosts=("example.org",),
            endpoint_patterns=("https://example.org/tokenomics/",),
            parser_version="official-cmp-tokenomics-v1",
            capabilities=("supply:circulating_supply", "supply:fully_diluted_supply"),
            enabled=True,
        )
        circulating_provider = RegisteredValueCaptureProvider(
            circulating_source, signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
        )
        circulating_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((circulating_source,)),
            repository=self.comparative.vc_repository,
            verification_keys=ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY}),
        )
        circulating_result = supply_result(
            circulating_provider,
            "target",
            self.target_evidence.record_id,
            circulating_fact,
            acquisition_id="coordinated-circulating-supply",
            supply_basis_type="circulating_supply",
            quantity="86000000",
        )
        self.valuation_supply = circulating_service.ingest_supply(circulating_provider, circulating_result)
        assert self.valuation_supply in self.comparative.vc_repository.supply_records()
        assert self.valuation_supply.conflict_state == "none"
        methodology_repository = ValuationMethodologyRepository(self.db_path)
        methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=methodology_repository, application_root=root
        )
        self.valuation_methodology = methodology_authority.persist_methodology(**methodology_payload())
        self.valuation_repository = CanonicalValuationRepository(self.db_path)
        self.valuation_service = CanonicalValuationService(
            repository=self.valuation_repository,
            methodology_authority=methodology_authority,
            value_capture_repository=self.comparative.vc_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=root,
        )
        self.estimate, self.valuation = self.valuation_service.estimate_fair_value(
            identity=identity("target"),
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )
        self.mispricing_service = CanonicalMispricingService(
            repository=MispricingRepository(self.db_path),
            valuation_repository=self.valuation_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=root,
        )
        self.mispricing_methodology = self.mispricing_service.persist_mispricing_methodology(
            methodology_id="coordinated-mispricing-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="1.0.0",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )
        self.mispricing = self.mispricing_service.assess(
            identity=identity("target"),
            methodology_record_id=self.mispricing_methodology.record_id,
            fair_value_logical_id=self.estimate.logical_id,
            effective_at=NOW,
            recorded_at=RECORDED + timedelta(minutes=1),
            known_at=RECORDED + timedelta(minutes=1),
        )
        self._seed_asymmetry(permuted=permuted)
        self._seed_assembly(permuted=permuted)

    def _seed_asymmetry(self, *, permuted: bool) -> None:
        self.asymmetry_service = CanonicalAsymmetryService(
            repository=AsymmetryRepository(self.db_path),
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=self.root,
        )
        methodology = self.asymmetry_service.persist_asymmetry_methodology(
            methodology_id="coordinated-asymmetry-v1",
            required_quote_currency="usd",
            entity_class_criteria_id="adr-0021-first-entity-class-v1",
            entity_class_criteria_version="1.0.0",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )
        upstream = (self.comparative_assessment.record_id, self.mispricing.record_id)
        if permuted:
            upstream = tuple(reversed(upstream))
        scenario_set = self.asymmetry_service.persist_scenario_set(
            identity=identity("target"),
            methodology_record_id=methodology.record_id,
            scenario_set_id="coordinated-scenarios-v1",
            scenario_ids=SCENARIO_IDS,
            scenario_types=SCENARIO_TYPES,
            baseline_market_fact_record_id=self.spot.record_id,
            quote_currency="usd",
            material_omitted_scenario_policy="material omitted scenarios are declared before payoff evaluation",
            uncertainty="0.05",
            evidence_record_ids=upstream,
            source_versions=(self.comparative_assessment.semantic_version, self.mispricing.semantic_version),
            dependency_pairs=(),
            effective_at=NOW,
            recorded_at=RECORDED + timedelta(minutes=2),
            known_at=RECORDED + timedelta(minutes=2),
        )
        probabilities = ("0.25", "0.35", "0.40")
        payoffs = ("0.30", "0.10", "-0.40")
        self.probabilities = {}
        self.payoffs = {}
        for index, scenario_id in enumerate(SCENARIO_IDS):
            self.probabilities[scenario_id] = self.asymmetry_service.persist_scenario_probability(
                scenario_set_record_id=scenario_set.record_id,
                scenario_id=scenario_id,
                probability=probabilities[index],
                probability_uncertainty="0.05",
                probability_authority="declared-pre-evaluation",
                probability_method="predeclared-scenario-probability-v1",
                effective_at=NOW,
                recorded_at=RECORDED + timedelta(minutes=3),
                known_at=RECORDED + timedelta(minutes=3),
            )
            self.payoffs[scenario_id] = self.asymmetry_service.persist_scenario_payoff(
                scenario_set_record_id=scenario_set.record_id,
                scenario_id=scenario_id,
                terminal_payoff=payoffs[index],
                payoff_uncertainty="0.05",
                evidence_record_ids=upstream,
                source_record_id=f"coordinated-payoff-{scenario_id}",
                source_record_version="1.0.0",
                effective_at=NOW,
                recorded_at=RECORDED + timedelta(minutes=4),
                known_at=RECORDED + timedelta(minutes=4),
            )
        self.scenario_set = scenario_set
        self.asymmetry = self.asymmetry_service.assess(
            methodology_record_id=methodology.record_id,
            scenario_set_logical_id=scenario_set.logical_id,
            identity=identity("target"),
            effective_at=NOW,
            recorded_at=RECORDED + timedelta(minutes=5),
            known_at=RECORDED + timedelta(minutes=5),
        )

    def _seed_assembly(self, *, permuted: bool) -> None:
        registry = assembly_registry_fixture.__wrapped__()
        native = _NativeEvidenceQuery()
        contract_authority = _ContractAuthority()
        contract_authority.contract = replace(
            _contract(),
            accounting_window_start=NOW - timedelta(days=730),
            accounting_window_end=NOW,
            entity_id=self.target_evidence.identity.entity_id,
            representation_id=self.target_evidence.identity.representation_id,
            currency="usd",
            unit="usd",
            effective_at=NOW - timedelta(days=730),
            recorded_at=NOW - timedelta(days=730),
            known_at=NOW - timedelta(days=730),
        )
        semantics = _SemanticsAuthority()
        service = CanonicalEvidenceAssemblyService(
            repository=AssembledEvidenceRepository(self.db_path),
            native_evidence_query=native,
            methodology_contract_authority=contract_authority,
            evidence_shape_registry_authority=_RegistryAuthority(registry),
            evidence_semantics_authority=semantics,
        )
        prior = replace(
            self.target_evidence,
            record_id="coordinated-prior-evidence",
            logical_id="coordinated-prior-evidence-logical",
            accounting_period_start=NOW - timedelta(days=730),
            accounting_period_end=NOW - timedelta(days=365),
            effective_at=NOW - timedelta(days=365),
            recorded_at=NOW - timedelta(days=365),
            known_at=NOW - timedelta(days=365),
            content_hash="d" * 64,
            raw_content_hash="e" * 64,
        )
        constituents = tuple(
            replace(
                _constituent(record),
                currency="usd",
                raw_unit="usd",
                supply_basis_id=self.target_supply.record_id,
                pathway_id=self.target_evidence.attribution_rule_id,
            )
            for record in (self.target_evidence, prior)
        )
        _seed_authorities(service, constituents)
        self.assembly = service.assemble(
            constituents=constituents if permuted else tuple(reversed(constituents)),
            accounting_window_start=NOW - timedelta(days=730),
            accounting_window_end=NOW,
            recorded_at=RECORDED,
            replay_cutoff=RECORDED,
            methodology_contract_id=contract_authority.contract.contract_id,
            methodology_contract_version=contract_authority.contract.contract_version,
            evidence_shape_registry_version=registry.version,
        )


def test_complete_valuation_chain_uses_one_authoritative_state(tmp_path) -> None:
    fixture = CoordinatedFixture(tmp_path)
    permuted_fixture = CoordinatedFixture(tmp_path / "permuted", permuted=True)
    evidence_ids = {
        fixture.target_evidence.record_id,
        fixture.valuation_supply.record_id,
        fixture.target_rule.record_id,
    }
    assert fixture.estimate.evidence_record_id == fixture.target_evidence.record_id
    assert fixture.estimate.supply_record_id == fixture.valuation_supply.record_id
    assert fixture.estimate.rule_record_id == fixture.target_rule.record_id
    assert fixture.target_evidence.record_id in fixture.assembly.constituent_record_ids
    assert fixture.mispricing.fair_value_record_id == fixture.estimate.record_id
    assert fixture.mispricing.market_fact_record_id == fixture.spot.record_id
    assert set(fixture.scenario_set.evidence_record_ids) == {
        fixture.comparative_assessment.record_id,
        fixture.mispricing.record_id,
    }
    assert fixture.valuation.correlation_group == fixture.comparative_assessment.correlation_group
    assert fixture.mispricing.correlation_group == "valuation-mispricing"
    assert fixture.asymmetry.correlation_group == "asymmetry-scenario"
    assert evidence_ids.isdisjoint(set(fixture.scenario_set.evidence_record_ids))
    for original, permuted_record in (
        (fixture.assembly, permuted_fixture.assembly),
        (fixture.estimate, permuted_fixture.estimate),
        (fixture.comparative_assessment, permuted_fixture.comparative_assessment),
        (fixture.mispricing, permuted_fixture.mispricing),
        (fixture.asymmetry, permuted_fixture.asymmetry),
    ):
        assert original.record_id == permuted_record.record_id, type(original).__name__
        assert original.content_hash == permuted_record.content_hash

    replayed = fixture.mispricing_service.strict_known_assessment(
        logical_id=fixture.mispricing.logical_id,
        effective_as_of=NOW,
        known_by=RECORDED + timedelta(minutes=5),
    )
    assert replayed == fixture.mispricing
    permuted = fixture.comparative.persist_universe(policy=fixture.policy, entities=("peer-b", "peer-c", "peer-a"))
    assert permuted.record_id == fixture.universe.record_id
    assert permuted.content_hash == fixture.universe.content_hash

    correction_at = RECORDED + timedelta(minutes=10)
    corrected_estimate, corrected_valuation = fixture.valuation_service.estimate_fair_value(
        identity=identity("target"),
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=correction_at,
        known_at=correction_at,
        supersedes_estimate_record_id=fixture.estimate.record_id,
        supersedes_assessment_record_id=fixture.valuation.record_id,
        correction_reason="coordinated correction replay",
    )
    corrected_mispricing = fixture.mispricing_service.assess(
        identity=identity("target"),
        methodology_record_id=fixture.mispricing_methodology.record_id,
        fair_value_logical_id=fixture.estimate.logical_id,
        effective_at=NOW,
        recorded_at=correction_at + timedelta(minutes=1),
        known_at=correction_at + timedelta(minutes=1),
        supersedes_record_id=fixture.mispricing.record_id,
        correction_reason="propagate exact valuation correction",
    )
    assert corrected_estimate.logical_id == fixture.estimate.logical_id
    assert corrected_valuation.logical_id == fixture.valuation.logical_id
    assert corrected_mispricing.logical_id == fixture.mispricing.logical_id
    assert corrected_mispricing.fair_value_record_id == corrected_estimate.record_id
    assert (
        fixture.mispricing_service.strict_known_assessment(
            logical_id=fixture.mispricing.logical_id,
            effective_as_of=NOW,
            known_by=RECORDED + timedelta(minutes=5),
        ).content_hash
        == fixture.mispricing.content_hash
    )
    assert (
        fixture.mispricing_service.strict_known_assessment(
            logical_id=fixture.mispricing.logical_id,
            effective_as_of=NOW,
            known_by=correction_at + timedelta(minutes=1),
        ).content_hash
        == corrected_mispricing.content_hash
    )

    corrected_payoff = fixture.asymmetry_service.persist_scenario_payoff(
        scenario_set_record_id=fixture.scenario_set.record_id,
        scenario_id=SCENARIO_IDS[0],
        terminal_payoff="0.35",
        payoff_uncertainty="0.05",
        evidence_record_ids=(fixture.comparative_assessment.record_id,),
        source_record_id="coordinated-payoff-correction",
        source_record_version="1.0.1",
        effective_at=NOW,
        recorded_at=correction_at,
        known_at=correction_at,
        supersedes_record_id=fixture.payoffs[SCENARIO_IDS[0]].record_id,
        correction_reason="coordinated scenario correction",
    )
    corrected_asymmetry = fixture.asymmetry_service.assess(
        methodology_record_id=fixture.asymmetry.methodology_record_id,
        scenario_set_logical_id=fixture.scenario_set.logical_id,
        identity=identity("target"),
        effective_at=NOW,
        recorded_at=correction_at + timedelta(minutes=1),
        known_at=correction_at + timedelta(minutes=1),
        supersedes_record_id=fixture.asymmetry.record_id,
        correction_reason="propagate exact scenario correction",
    )
    assert corrected_payoff.record_id in corrected_asymmetry.scenario_payoff_record_ids
    assert fixture.payoffs[SCENARIO_IDS[0]].record_id not in corrected_asymmetry.scenario_payoff_record_ids


def test_service_dependency_graph_has_no_prohibited_layer_edges() -> None:
    service_types = (
        CanonicalValuationService,
        CanonicalComparativeValuationService,
        CanonicalMispricingService,
        CanonicalAsymmetryService,
    )
    prohibited = {"market_validation", "opportunity", "ranking", "portfolio"}
    for service_type in service_types:
        module = inspect.getmodule(service_type)
        assert module is not None
        targets = _import_targets(ast.parse(inspect.getsource(module)))
        components = {component for target in targets for component in target.split(".")}
        assert components.isdisjoint(prohibited)
