from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hunter.execution import (
    FixedClock,
    PipelineRun,
    SystemClock,
    canonicalize,
    fingerprint,
    stable_identifier,
)
from hunter.execution.exceptions import CanonicalizationError
from hunter.execution.identity import IntelligenceIdentityFactory
from hunter.intelligence import Confidence, Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.macro import MacroDataPoint, MacroIntelligenceEngine
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import DEFAULT_EFFECTIVE_AT, PipelineContext

FIXED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_canonicalization_stability_mapping_sets_lists_datetimes_and_numbers() -> None:
    first = {
        "mapping": {"b": 2, "a": 1},
        "unordered": {"z", "a", "m"},
        "ordered": ["first", "second"],
        "time": datetime(2026, 1, 2, 4, 4, 5, tzinfo=UTC),
        "decimal": Decimal("1.2300"),
        "float": 1.23,
        "none": None,
    }
    second = {
        "none": None,
        "float": 1.23,
        "decimal": Decimal("1.23"),
        "time": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).replace(hour=4),
        "ordered": ["first", "second"],
        "unordered": {"m", "z", "a"},
        "mapping": {"a": 1, "b": 2},
    }

    assert canonicalize(first) == canonicalize(second)
    assert canonicalize(["a", "b"]) != canonicalize(["b", "a"])
    assert canonicalize({"value": None}) != canonicalize({})


def test_canonicalization_rejects_unsupported_and_unstable_values() -> None:
    class Unsupported:
        pass

    with pytest.raises(CanonicalizationError):
        canonicalize(Unsupported())
    with pytest.raises(CanonicalizationError):
        canonicalize(float("nan"))
    with pytest.raises(CanonicalizationError):
        canonicalize(datetime(2026, 1, 1))


def test_hashing_is_stable_namespaced_and_schema_versioned() -> None:
    payload = {"a": 1, "b": 2}

    assert stable_identifier("signal", payload, schema_version="v1") == stable_identifier(
        "signal",
        {"b": 2, "a": 1},
        schema_version="v1",
    )
    assert stable_identifier("signal", payload, schema_version="v1") != stable_identifier(
        "evidence",
        payload,
        schema_version="v1",
    )
    assert stable_identifier("signal", payload, schema_version="v1") != stable_identifier(
        "signal",
        payload,
        schema_version="v2",
    )


def test_pipeline_run_identity_is_deterministic_and_fingerprint_sensitive() -> None:
    base = _run(input_fingerprint=fingerprint("input", {"value": 1}))
    same = _run(input_fingerprint=fingerprint("input", {"value": 1}))
    different_input = _run(input_fingerprint=fingerprint("input", {"value": 2}))
    different_config = _run(configuration_fingerprint=fingerprint("config", {"threshold": 2}))

    assert base.run_id == same.run_id
    assert base.run_id != different_input.run_id
    assert base.run_id != different_config.run_id


def test_pipeline_run_identity_ignores_metadata_and_operational_uniqueness_flag() -> None:
    base = _run(metadata={"attempt": "one"})
    changed_metadata = _run(metadata={"attempt": "two"})
    unique_operational = _run(
        metadata={"attempt": "three"},
        requested_at=datetime(2026, 1, 2, 4, 4, 5, tzinfo=UTC),
        unique_operational_run=True,
    )

    assert base.run_id == changed_metadata.run_id
    assert base.run_id == unique_operational.run_id


def test_replay_identity_semantics_include_replay_source() -> None:
    original = _run(run_type="live")
    replay = _run(run_type="replay", replay_of_run_id=original.run_id)

    assert replay.run_id != original.run_id
    assert replay.replay_of_run_id == original.run_id


def test_clock_contracts() -> None:
    assert FixedClock(FIXED_AT).now() == FIXED_AT
    assert SystemClock().now().tzinfo is not None


def test_identity_factory_generates_deterministic_intelligence_graph_ids() -> None:
    run = _run()
    intelligence = _sample_intelligence()
    factory = IntelligenceIdentityFactory()

    first = factory.stabilize(intelligence, run, engine_version="1.0.0")
    second = factory.stabilize(_sample_intelligence(), run, engine_version="1.0.0")
    changed = factory.stabilize(_sample_intelligence(raw_value=2), run, engine_version="1.0.0")

    assert first.id == second.id
    assert first.generated_at == FIXED_AT
    assert first.signals[0].id == second.signals[0].id
    assert first.evidence[0].id == second.evidence[0].id
    assert first.observations[0].id == second.observations[0].id
    assert first.insights[0].id == second.insights[0].id
    assert first.id != changed.id
    assert first.evidence[0].id != changed.evidence[0].id


def test_explicit_model_ids_remain_supported() -> None:
    evidence = Evidence(
        id="explicit-evidence-id",
        source="source",
        collected_at=FIXED_AT,
        reliability=1.0,
        freshness=1.0,
        reference="reference",
        raw_data={"value": 1},
    )

    assert evidence.id == "explicit-evidence-id"


def test_pipeline_context_shares_one_run() -> None:
    context = PipelineContext(clock=FixedClock(FIXED_AT), values={"b": 2, "a": 1})

    first = context.ensure_run(engine_manifest={"engine": "macro"})
    second = context.ensure_run(engine_manifest={"engine": "changed"})

    assert first is second


def test_pipeline_context_default_run_uses_stable_effective_time() -> None:
    first = PipelineContext(values={"a": 1}).ensure_run(engine_manifest={"engine": "macro"})
    second = PipelineContext(values={"a": 1}).ensure_run(engine_manifest={"engine": "macro"})

    assert first.run_id == second.run_id
    assert first.effective_at == DEFAULT_EFFECTIVE_AT


def test_engine_runner_emits_intelligence_associated_with_canonical_run() -> None:
    context = PipelineContext(clock=FixedClock(FIXED_AT), values={"macro_data": [_macro_point(0.7)]})
    engine = MacroIntelligenceEngine()

    emitted = engine.generate_intelligence(context, engine.analyze(context, engine.collect(context)))
    stabilized = IntelligenceIdentityFactory().stabilize(emitted, context.ensure_run(), engine_version=engine.version)

    assert stabilized.generated_at == FIXED_AT
    assert context.run is not None
    assert stabilized.metadata.get("pipeline_run_id") == context.run.run_id


def test_end_to_end_fixed_clock_engine_execution_is_reproducible_and_input_sensitive() -> None:
    first = _run_macro_once(0.7)
    second = _run_macro_once(0.7)
    changed = _run_macro_once(0.2)

    assert first.id == second.id
    assert first.generated_at == FIXED_AT
    assert second.generated_at == FIXED_AT
    assert [item.id for item in first.evidence] == [item.id for item in second.evidence]
    assert first.id != changed.id


def _run(**overrides) -> PipelineRun:
    defaults = {
        "run_type": "test",
        "target_id": "bitcoin",
        "target_type": "project",
        "configuration_fingerprint": fingerprint("config", {"threshold": 1}),
        "input_fingerprint": fingerprint("input", {"value": 1}),
        "engine_manifest_fingerprint": fingerprint("manifest", {"engine": "macro", "version": "1.0.0"}),
        "requested_at": FIXED_AT,
        "effective_at": FIXED_AT,
        "clock": FixedClock(FIXED_AT),
    }
    defaults.update(overrides)
    return PipelineRun.create(**defaults)


def _sample_intelligence(raw_value: int = 1) -> Intelligence:
    evidence = Evidence(
        id="legacy-evidence",
        source="source",
        collected_at=FIXED_AT,
        reliability=0.9,
        freshness=0.8,
        reference="reference",
        raw_data={"value": raw_value},
    )
    observation = Observation(
        id="legacy-observation",
        engine="sample-engine",
        project="bitcoin",
        description="Observed evidence.",
        evidence=(evidence,),
        importance=0.7,
    )
    insight = Insight(
        id="legacy-insight",
        title="Insight",
        explanation="Evidence supports the insight.",
        supporting_observations=(observation,),
        confidence=0.8,
        priority=0.6,
    )
    return Intelligence(
        id="legacy-intelligence",
        project="bitcoin",
        engine="sample-engine",
        signals=(
            Signal(
                id="legacy-signal",
                source="sample-engine",
                timestamp=FIXED_AT,
                category="sample",
                strength=0.7,
                confidence=0.8,
                severity=0.2,
            ),
        ),
        evidence=(evidence,),
        observations=(observation,),
        insights=(insight,),
        confidence=Confidence.calculate(
            completeness=0.9,
            evidence_quality=0.8,
            freshness=0.7,
            uncertainty=0.2,
        ),
        generated_at=FIXED_AT,
    )


def _macro_point(value: float) -> MacroDataPoint:
    return MacroDataPoint(
        domain="global_liquidity",
        value=value,
        previous_value=0.4,
        timestamp=FIXED_AT,
        source="fixture",
        reference="macro://fixture/global-liquidity",
        reliability=0.9,
        raw_data={"value": value},
    )


def _run_macro_once(value: float) -> Intelligence:
    context = PipelineContext(clock=FixedClock(FIXED_AT), values={"macro_data": [_macro_point(value)]})
    context.ensure_run()
    return EngineRunner().run([MacroIntelligenceEngine()], context)[0]
