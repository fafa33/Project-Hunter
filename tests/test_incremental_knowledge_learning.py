from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.evidence_intelligence.incremental_knowledge_learning import (
    LearningLedgerError,
    build_learning_ledger,
    historical_events,
)

REGISTRY = Path("docs/DEFECT_REGISTRY.json")
HEAD = "a" * 40
BASE = "b" * 40


def observation(**overrides):
    value = {
        "source": "github-review",
        "provider": "github-review",
        "event_id": "comment-1",
        "source_pr": 492,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "reviewer": "codex",
        "path": "src/hunter/example.py",
        "line": 12,
        "message": "A concrete finding",
        "availability": "available",
        "classification": None,
        "invariant": None,
        "affected_paths": [],
        "fix_reference": None,
        "regression_evidence": [],
        "claimed_family_id": None,
    }
    value.update(overrides)
    return value


def test_raw_reviewer_prose_never_invents_family_authority():
    ledger = build_learning_ledger(492, HEAD, BASE, [observation()], REGISTRY)
    assert ledger["items"][0]["state"] == "insufficient-evidence"
    assert ledger["items"][0]["proposal"] is None


def test_optional_provider_unavailability_is_nonblocking_observation():
    item = observation(
        source="sonar",
        provider="sonar",
        event_id="sonar-unavailable",
        availability="unavailable",
        message="provider unavailable",
    )
    ledger = build_learning_ledger(492, HEAD, BASE, [item], REGISTRY)
    assert ledger["items"][0]["state"] == "insufficient-evidence"
    assert ledger["source_availability"]["sonar"] == "unavailable"


def test_duplicate_delivery_collapses_deterministically():
    item = observation()
    assert build_learning_ledger(492, HEAD, BASE, [item, dict(item)], REGISTRY) == build_learning_ledger(
        492, HEAD, BASE, [dict(item)], REGISTRY
    )


def test_wrong_exact_head_fails_closed():
    with pytest.raises(LearningLedgerError, match="exact head"):
        build_learning_ledger(492, HEAD, BASE, [observation(reviewed_head_sha="c" * 40)], REGISTRY)


def test_complete_governed_event_enters_existing_family_pipeline():
    family = next(f for f in json.loads(REGISTRY.read_text())["families"] if f["id"] == "DFF-004")
    item = observation(
        classification="confirmed",
        invariant=family["invariant"],
        affected_paths=["src/hunter/example.py"],
        fix_reference="PR #492 commit deadbeef",
        regression_evidence=[family["regression_evidence"][0]],
        claimed_family_id="DFF-004",
    )
    ledger = build_learning_ledger(492, HEAD, BASE, [item], REGISTRY)
    assert ledger["items"][0]["state"] == "existing-family"
    assert ledger["items"][0]["proposal"]["canonical_family_id"] == "DFF-004"


def test_historical_backfill_translates_through_same_event_contract():
    events = historical_events(HEAD, BASE)
    confirmed = [e for e in events if e.get("classification") == "confirmed"]
    assert confirmed
    ledger = build_learning_ledger(confirmed[0]["source_pr"], HEAD, BASE, [confirmed[0]], REGISTRY)
    assert ledger["items"][0]["state"] == "existing-family"


def test_historical_nondefect_remains_excluded_observation():
    nondefect = next(e for e in historical_events(HEAD, BASE) if e.get("classification") != "confirmed")
    ledger = build_learning_ledger(nondefect["source_pr"], HEAD, BASE, [nondefect], REGISTRY)
    assert ledger["items"][0]["state"] == "excluded"


def test_reused_provider_event_identity_with_different_content_fails_closed():
    first = observation()
    second = observation(message="different evidence")
    with pytest.raises(LearningLedgerError, match="event identity"):
        build_learning_ledger(492, HEAD, BASE, [first, second], REGISTRY)


def test_ledger_rejects_non_hex_exact_head():
    with pytest.raises(LearningLedgerError, match="SHA"):
        build_learning_ledger(492, "z" * 40, BASE, [], REGISTRY)
