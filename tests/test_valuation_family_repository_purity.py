from __future__ import annotations

import ast
import inspect

import pytest

from hunter.asymmetry.repository import AsymmetryRepository
from hunter.comparative_valuation.repository import ComparativeValuationRepository
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.mispricing.repository import MispricingRepository
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

REPOSITORY_TYPES = (
    CanonicalValuationRepository,
    ValuationMethodologyRepository,
    ComparativeValuationRepository,
    MispricingRepository,
    AsymmetryRepository,
    SupplyAndValueCaptureRepository,
    ObservedMarketFactRepository,
    AssembledEvidenceRepository,
)

PROHIBITED_METHOD_PREFIXES = (
    "strict_known",
    "unresolved",
    "authorize",
    "normalize",
    "select_current",
    "is_superseded",
    "resolve",
    "validate_successor",
    "validate_resolution",
)

PROHIBITED_DOMAIN_ATTRIBUTES = {
    "quality_state",
    "conflict_state",
    "supersedes_record_id",
}


@pytest.mark.parametrize("repository_type", REPOSITORY_TYPES)
def test_valuation_family_repositories_expose_no_domain_authority(repository_type: type[object]) -> None:
    methods = {
        name
        for name, member in inspect.getmembers(repository_type, predicate=inspect.isfunction)
        if not name.startswith("__")
    }
    assert "assess" not in methods
    assert not any(name.startswith(PROHIBITED_METHOD_PREFIXES) for name in methods)

    class_node = next(
        node
        for node in ast.parse(inspect.getsource(repository_type)).body
        if isinstance(node, ast.ClassDef) and node.name == repository_type.__name__
    )
    decision_roots: list[ast.AST] = []
    for node in ast.walk(class_node):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            decision_roots.append(node.test)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            decision_roots.extend(generator for generator in node.generators)
    decision_attributes = {
        node.attr
        for root in decision_roots
        for node in ast.walk(root)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }
    assert decision_attributes.isdisjoint(PROHIBITED_DOMAIN_ATTRIBUTES)
