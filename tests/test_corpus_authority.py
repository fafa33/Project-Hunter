from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hunter.operational_corpus as operational_corpus
from hunter.corpus_authority import (
    CORPUS_OBSERVATION_SCHEMA_VERSION,
    AuthorityReference,
    ResolvedAuthorityRecord,
    append_corpus_observation,
    authorize_corpus_observation,
    classify_legacy_corpus_record,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


class Resolver:
    def __init__(self, resolved: ResolvedAuthorityRecord | None, *, failure: bool = False) -> None:
        self.resolved = resolved
        self.failure = failure
        self.calls = 0
        self.writes = 0

    def resolve(self, reference: AuthorityReference) -> ResolvedAuthorityRecord | None:
        self.calls += 1
        if self.failure:
            raise OSError("store unavailable")
        return self.resolved


def test_legacy_record_is_readable_and_labeled_without_mutation() -> None:
    legacy = {"prediction_id": "legacy-1", "status": "closed", "rankings": [{"rank": 1}]}
    original = dict(legacy)
    projection = classify_legacy_corpus_record(legacy)
    assert legacy == original
    assert projection["status"] == "legacy-unverified"
    assert projection["authority_classification"] == "unverified"
    assert projection["observation_payload"] == legacy
    assert projection["ownership_statement"] == "downstream operational observation; not analytical authority"


def test_non_analytical_operational_observation_requires_no_authority_reference(tmp_path: Path) -> None:
    envelope = authorize_corpus_observation(
        observation_category="runtime-health",
        target_id="scheduler",
        entity_type="process",
        payload={"status": "running", "attempts": 2},
        recorded_at=NOW,
    )
    assert envelope.status == "authority_not_required"
    assert envelope.authority_references == ()
    path = tmp_path / "corpus" / "authority_observations.jsonl"
    append_corpus_observation(path, envelope)
    append_corpus_observation(path, envelope)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["schema_version"] == CORPUS_OBSERVATION_SCHEMA_VERSION


def test_existing_prediction_write_contract_is_additively_unverified() -> None:
    prediction = operational_corpus._prediction(  # type: ignore[attr-defined]
        {
            "pipeline_run_id": "run-1",
            "corpus_entry_id": "entry-1",
            "target_id": "bitcoin",
            "target_type": "project",
            "effective_at": NOW.isoformat(),
            "rankings": [{"rank": 1}],
            "final_recommendations": [{"decision": "watch"}],
        }
    )
    assert prediction["corpus_schema_version"] == CORPUS_OBSERVATION_SCHEMA_VERSION
    assert prediction["payload_status"] == "unverified"
    assert prediction["authority_references"] == []
    assert prediction["ownership_statement"].endswith("not analytical authority")


@pytest.mark.parametrize(
    ("category", "payload"),
    (
        ("score", {"hunter_score": 0.84}),
        ("ranking", {"rankings": [{"rank": 1}]}),
        ("recommendation", {"recommendation": "hold"}),
        ("correctness", {"correctness": True}),
        ("accuracy", {"accuracy": 0.75}),
        ("assessment", {"assessment": "qualified"}),
    ),
)
def test_valid_canonical_reference_preserves_exact_reference_and_downstream_status(
    category: str, payload: dict[str, object]
) -> None:
    reference = _canonical_reference()
    resolver = Resolver(_resolved(reference))
    envelope = authorize_corpus_observation(
        observation_category=category,
        target_id="bitcoin",
        entity_type="project",
        payload=payload,
        recorded_at=NOW,
        authority_references=(reference,),
        resolver=resolver,
    )
    assert envelope.status == "authority_referenced"
    assert envelope.authority_classification == "production"
    assert envelope.authority_references == (reference,)
    assert envelope.observation_payload == payload
    assert envelope.ownership_statement.endswith("not analytical authority")
    assert resolver.calls == 1
    assert resolver.writes == 0


def test_experimental_reference_can_never_be_represented_as_production() -> None:
    experimental = _experimental_reference()
    resolver = Resolver(_resolved(experimental))
    valid = authorize_corpus_observation(
        observation_category="assessment",
        target_id="bitcoin",
        entity_type="project",
        payload={"score": 0.5},
        recorded_at=NOW,
        authority_references=(experimental,),
        resolver=resolver,
    )
    assert valid.status == "authority_referenced"
    assert valid.authority_classification == "experimental"

    false_production = replace(experimental, authority_classification="production")
    invalid = authorize_corpus_observation(
        observation_category="assessment",
        target_id="bitcoin",
        entity_type="project",
        payload={"score": 0.5},
        recorded_at=NOW,
        authority_references=(false_production,),
        resolver=resolver,
    )
    assert invalid.status == "unverified"
    assert invalid.authority_classification == "experimental"


@pytest.mark.parametrize("case", ("missing", "unknown", "target", "unresolved", "mismatch"))
def test_invalid_or_unresolved_analytical_references_are_deterministically_unverified(case: str) -> None:
    reference = _canonical_reference()
    references = (reference,)
    resolver: Resolver | None = Resolver(_resolved(reference))
    target = "bitcoin"
    if case == "missing":
        references = ()
        resolver = None
    elif case == "unknown":
        references = (replace(reference, semantic_type="unknown.score"),)
    elif case == "target":
        target = "ethereum"
    elif case == "unresolved":
        resolver = None
    elif case == "mismatch":
        resolver = Resolver(replace(_resolved(reference), record_version="wrong"))
    envelope = authorize_corpus_observation(
        observation_category="score",
        target_id=target,
        entity_type="project",
        payload={"score": 0.8},
        recorded_at=NOW,
        authority_references=references,
        resolver=resolver,
    )
    assert envelope.status == "unverified"
    assert "score" in envelope.observation_payload


def test_caller_payload_cannot_override_envelope_or_authority_reference() -> None:
    reference = _canonical_reference()
    payload = {
        "observation_id": "forged",
        "schema_version": "forged",
        "authority_classification": "production",
        "authority_references": [{"record_id": "forged"}],
        "hunter_score": 99,
    }
    envelope = authorize_corpus_observation(
        observation_category="score",
        target_id="bitcoin",
        entity_type="project",
        payload=payload,
        recorded_at=NOW,
        authority_references=(reference,),
        resolver=Resolver(_resolved(reference)),
    )
    encoded = envelope.as_dict()
    assert encoded["observation_id"] != "forged"
    assert encoded["schema_version"] == CORPUS_OBSERVATION_SCHEMA_VERSION
    assert encoded["authority_references"][0]["record_id"] == reference.record_id
    assert encoded["observation_payload"] == payload


def test_resolver_failure_is_unavailable_without_fabricated_metric_or_side_effect() -> None:
    reference = _canonical_reference()
    resolver = Resolver(None, failure=True)
    envelope = authorize_corpus_observation(
        observation_category="accuracy",
        target_id="bitcoin",
        entity_type="project",
        payload={"accuracy": None},
        recorded_at=NOW,
        authority_references=(reference,),
        resolver=resolver,
    )
    assert envelope.status == "unavailable"
    assert envelope.observation_payload["accuracy"] is None
    assert resolver.calls == 1
    assert resolver.writes == 0


def test_corpus_authority_layer_has_no_forbidden_consumer_wiring() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/market_validation",
        "src/hunter/prediction_evaluation",
        "src/hunter/opportunity",
        "src/hunter/timing",
        "src/hunter/backtest",
        "src/hunter/automation",
        "src/hunter/dashboard_api.py",
        "src/hunter/cli.py",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("authorize_corpus_observation" not in item.read_text() for item in files)


def _canonical_reference() -> AuthorityReference:
    return AuthorityReference(
        source_store="canonical-market-validation",
        semantic_type="market-validation-project-result",
        record_id="market-validation-project:1",
        record_version="market-validation-v1",
        canonical_hash="sha256:abc",
        authority_classification="production",
        target_id="bitcoin",
        entity_type="project",
        effective_at=NOW,
        recorded_at=NOW,
        known_at=NOW,
    )


def _experimental_reference() -> AuthorityReference:
    return AuthorityReference(
        source_store="experimental-derived-reasoning",
        semantic_type="experimental.opportunity-assessment",
        record_id="experimental-derived:1",
        record_version="experimental-opportunity-v1",
        canonical_hash="sha256:def",
        authority_classification="experimental",
        target_id="bitcoin",
        entity_type="project",
        effective_at=NOW,
        recorded_at=NOW,
        known_at=NOW,
    )


def _resolved(reference: AuthorityReference) -> ResolvedAuthorityRecord:
    return ResolvedAuthorityRecord(
        source_store=reference.source_store,
        semantic_type=reference.semantic_type,
        record_id=reference.record_id,
        record_version=reference.record_version,
        canonical_hash=reference.canonical_hash,
        authority_classification=reference.authority_classification,
        target_id=reference.target_id,
        entity_type=reference.entity_type,
        effective_at=reference.effective_at,
        recorded_at=reference.recorded_at,
        known_at=reference.known_at,
    )
