from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hunter.evidence_intelligence.canonical_knowledge_integration import (
    CanonicalIntegrationAuthority,
    CanonicalIntegrationError,
)
from hunter.evidence_intelligence.knowledge_event_ingestion import ingest_event
from hunter.evidence_intelligence.knowledge_extraction_authority import KnowledgeExtractionAuthority

REGISTRY = Path("docs/DEFECT_REGISTRY.json")


def payload():
    f = next(x for x in json.loads(REGISTRY.read_text())["families"] if x["id"] == "DFF-008")
    return {
        "schema_version": "hunter-finding-event-v1",
        "provider": "sonar",
        "event_id": "evt-new",
        "source_pr": 490,
        "reviewed_head_sha": "e" * 40,
        "reviewed_base_sha": "c" * 40,
        "reviewer": "sonarcloud",
        "classification": "confirmed",
        "invariant": f["invariant"],
        "affected_paths": ["scripts/hunter_knowledge_extraction.py"],
        "fix_reference": "PR #490 commit future",
        "regression_evidence": ["tests/test_canonical_knowledge_integration.py::test_existing_family_strengthened"],
        "claimed_family_id": "DFF-008",
    }


def proposal(path=REGISTRY):
    return KnowledgeExtractionAuthority(path).extract(ingest_event("sonar", payload()))


def test_existing_family_strengthened():
    before = json.loads(REGISTRY.read_text())
    result = CanonicalIntegrationAuthority().integrate(proposal(), REGISTRY.read_bytes())
    after = json.loads(result.registry_bytes)
    b = next(f for f in before["families"] if f["id"] == "DFF-008")
    a = next(f for f in after["families"] if f["id"] == "DFF-008")
    for field in set(b) - {"sources", "regression_evidence"}:
        assert a[field] == b[field]
    assert result.changed


def test_repeated_integration_idempotent():
    p = proposal()
    first = CanonicalIntegrationAuthority().integrate(p, REGISTRY.read_bytes())
    # Re-extraction is required after registry mutation; same stale proposal must not mutate the new snapshot.
    with pytest.raises(CanonicalIntegrationError, match="stale"):
        CanonicalIntegrationAuthority().integrate(p, first.registry_bytes)


def test_stale_registry_fails_closed():
    p = proposal()
    raw = json.loads(REGISTRY.read_text())
    raw["purpose"] += " changed"
    stale = (json.dumps(raw, indent=2) + "\n").encode()
    with pytest.raises(CanonicalIntegrationError, match="stale"):
        CanonicalIntegrationAuthority().integrate(p, stale)


def test_new_family_never_auto_created():
    q = payload()
    q["claimed_family_id"] = None
    q["invariant"] = "A genuinely new invariant."
    p = KnowledgeExtractionAuthority(REGISTRY).extract(ingest_event("sonar", q))
    assert p.outcome == "candidate-new-family"
    with pytest.raises(CanonicalIntegrationError, match="existing-family"):
        CanonicalIntegrationAuthority().integrate(p, REGISTRY.read_bytes())


def test_forged_proposal_refused():
    p = replace(proposal(), canonical_family_id="DFF-004")
    with pytest.raises(CanonicalIntegrationError, match="family claim|replay"):
        CanonicalIntegrationAuthority().integrate(p, REGISTRY.read_bytes())


def test_redelivery_after_reextract_is_true_noop(tmp_path: Path) -> None:
    first_proposal = proposal()
    first = CanonicalIntegrationAuthority().integrate(first_proposal, REGISTRY.read_bytes())
    updated = tmp_path / "registry.json"
    updated.write_bytes(first.registry_bytes)
    fresh = KnowledgeExtractionAuthority(updated).extract(ingest_event("sonar", payload()))
    second = CanonicalIntegrationAuthority().integrate(fresh, first.registry_bytes)
    assert second.changed is False
    assert second.registry_bytes == first.registry_bytes


def test_mutated_proposal_identity_cannot_bypass_replay() -> None:
    p = replace(proposal(), proposal_id="KXP-" + "0" * 24)
    with pytest.raises(CanonicalIntegrationError, match="replay"):
        CanonicalIntegrationAuthority().integrate(p, REGISTRY.read_bytes())


def test_invalid_regression_target_fails_before_registry_candidate() -> None:
    q = payload()
    q["regression_evidence"] = ["tests/does_not_exist.py::test_missing"]
    finding = ingest_event("sonar", q)
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(finding)
    with pytest.raises(CanonicalIntegrationError, match="regression evidence"):
        CanonicalIntegrationAuthority().integrate(proposal, REGISTRY.read_bytes())


def test_reused_provider_event_id_with_changed_evidence_is_rejected_after_first_learning(tmp_path: Path) -> None:
    original = payload()
    first_finding = ingest_event("sonar", original)
    first_proposal = KnowledgeExtractionAuthority(REGISTRY).extract(first_finding)
    first = CanonicalIntegrationAuthority().integrate(first_proposal, REGISTRY.read_bytes())
    updated = tmp_path / "registry.json"
    updated.write_bytes(first.registry_bytes)

    changed = payload()
    changed["fix_reference"] = "PR #490 different remediation"
    changed_finding = ingest_event("sonar", changed)
    changed_proposal = KnowledgeExtractionAuthority(updated).extract(changed_finding)
    with pytest.raises(CanonicalIntegrationError, match="event identity"):
        CanonicalIntegrationAuthority().integrate(changed_proposal, first.registry_bytes)
