from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.evidence_intelligence.controlled_learning_integration import (
    ControlledLearningIntegrationError,
    integrate_learning_ledger,
)
from hunter.evidence_intelligence.incremental_knowledge_learning import build_learning_ledger

REGISTRY = Path("docs/DEFECT_REGISTRY.json")
HEAD = "a" * 40
BASE = "b" * 40


def observation(classification="confirmed"):
    family = next(f for f in json.loads(REGISTRY.read_text())["families"] if f["id"] == "DFF-008")
    return {
        "source": "sonar",
        "provider": "sonar",
        "event_id": "issue-504",
        "source_pr": 504,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "reviewer": "deterministic-fixture",
        "path": "scripts/hunter_knowledge_extraction.py",
        "line": 1,
        "message": "validated recurrence",
        "availability": "available",
        "classification": classification,
        "invariant": family["invariant"] if classification == "confirmed" else "",
        "affected_paths": ["scripts/hunter_knowledge_extraction.py"] if classification == "confirmed" else [],
        "fix_reference": "PR #504 focused remediation" if classification == "confirmed" else "",
        "regression_evidence": (
            [
                "tests/test_controlled_learning_integration.py::test_existing_family_ledger_produces_candidate_without_persisting"
            ]
            if classification == "confirmed"
            else []
        ),
        "claimed_family_id": "DFF-008" if classification == "confirmed" else None,
    }


def test_existing_family_ledger_produces_candidate_without_persisting():
    before = REGISTRY.read_bytes()
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], REGISTRY)
    result = integrate_learning_ledger(ledger, before)
    assert result.changed is True and len(result.integrated_proposal_ids) == 1
    assert REGISTRY.read_bytes() == before
    candidate = json.loads(result.registry_bytes)
    family = next(f for f in candidate["families"] if f["id"] == "DFF-008")
    assert any("issue-504" in source for source in family["sources"])


def test_non_existing_family_outcomes_never_integrate():
    ledger = build_learning_ledger(504, HEAD, BASE, [observation("false-positive")], REGISTRY)
    result = integrate_learning_ledger(ledger, REGISTRY.read_bytes())
    assert result.changed is False and result.integrated_proposal_ids == () and result.skipped_items == 1


def test_tampered_ledger_fails_closed():
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], REGISTRY)
    ledger["reviewed_head_sha"] = "c" * 40
    with pytest.raises(ControlledLearningIntegrationError, match="digest"):
        integrate_learning_ledger(ledger, REGISTRY.read_bytes())


def test_tampered_existing_family_proposal_fails_replay():
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], REGISTRY)
    ledger["items"][0]["proposal"]["canonical_family_id"] = "DFF-004"
    body = dict(ledger)
    body.pop("ledger_digest")
    import hashlib

    ledger["ledger_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    with pytest.raises(ControlledLearningIntegrationError, match="replay"):
        integrate_learning_ledger(ledger, REGISTRY.read_bytes())


def test_materialize_verified_ledger_atomically_updates_candidate_registry(tmp_path):
    from hunter.evidence_intelligence.controlled_learning_integration import materialize_learning_ledger

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], registry)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = materialize_learning_ledger(ledger_path, registry)

    assert result.changed is True
    assert registry.read_bytes() == result.registry_bytes
    family = next(f for f in json.loads(registry.read_text())["families"] if f["id"] == "DFF-008")
    assert any("issue-504" in source for source in family["sources"])


def test_materialize_replay_is_idempotent(tmp_path):
    from hunter.evidence_intelligence.controlled_learning_integration import materialize_learning_ledger

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], registry)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    first = materialize_learning_ledger(ledger_path, registry)
    before = registry.read_bytes()

    # Replaying the exact same accepted ledger is a no-op, not an error.
    second = materialize_learning_ledger(ledger_path, registry)

    assert registry.read_bytes() == second.registry_bytes
    family = next(f for f in json.loads(registry.read_text())["families"] if f["id"] == "DFF-008")
    assert sum("issue-504" in source for source in family["sources"]) == 1
    assert first.changed is True
    assert registry.read_bytes() == before


def test_materialize_tampered_ledger_leaves_registry_unchanged(tmp_path):
    from hunter.evidence_intelligence.controlled_learning_integration import materialize_learning_ledger

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    before = registry.read_bytes()
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], registry)
    ledger["reviewed_head_sha"] = "c" * 40
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(ControlledLearningIntegrationError, match="digest"):
        materialize_learning_ledger(ledger_path, registry)
    assert registry.read_bytes() == before


def test_materialize_non_defect_evidence_never_mutates_registry(tmp_path):
    from hunter.evidence_intelligence.controlled_learning_integration import materialize_learning_ledger

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    before = registry.read_bytes()
    ledger = build_learning_ledger(504, HEAD, BASE, [observation("provider-unavailable")], registry)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = materialize_learning_ledger(ledger_path, registry)
    assert result.changed is False
    assert registry.read_bytes() == before


def test_materialize_replay_rejects_tampered_full_proposal_contract(tmp_path):
    from hunter.evidence_intelligence.controlled_learning_integration import materialize_learning_ledger

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], registry)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    materialize_learning_ledger(ledger_path, registry)

    ledger["items"][0]["proposal"]["schema_version"] = "tampered-schema"
    ledger["items"][0]["proposal"]["canonical_write_authorized"] = True
    ledger["items"][0]["proposal"]["proposal_id"] = "tampered-proposal"
    body = dict(ledger)
    body.pop("ledger_digest")
    import hashlib

    ledger["ledger_digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(ControlledLearningIntegrationError, match="does not replay"):
        materialize_learning_ledger(ledger_path, registry)


def test_materialize_detects_concurrent_registry_change_before_replace(tmp_path, monkeypatch):
    from hunter.evidence_intelligence import controlled_learning_integration as module

    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    ledger = build_learning_ledger(504, HEAD, BASE, [observation()], registry)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    real_integrate = module.integrate_learning_ledger

    def racing_integrate(payload, snapshot):
        result = real_integrate(payload, snapshot)
        registry.write_bytes(snapshot + b"\n")
        return result

    monkeypatch.setattr(module, "integrate_learning_ledger", racing_integrate)
    with pytest.raises(ControlledLearningIntegrationError, match="changed during"):
        module.materialize_learning_ledger(ledger_path, registry)


def test_materialization_cli_has_no_caller_selected_registry_write_target():
    script = Path("scripts/hunter_materialize_learning_candidate.py").read_text(encoding="utf-8")
    assert 'add_argument("--registry"' not in script
    assert 'registry = Path("docs/DEFECT_REGISTRY.json")' in script
