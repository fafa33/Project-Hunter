from __future__ import annotations

from pathlib import Path

import pytest

from hunter.governance_cutover import CutoverAuthority, CutoverEvidence, CutoverState, EvidenceKind, PublicationOwner


def ev(kind: EvidenceKind, **extra):
    payload = {"generation": 1, "implementation_sha": "impl", "policy_sha": "policy"}
    payload.update(extra)
    return CutoverEvidence(kind, "digest", "2026-09-22T05:00:00Z", "test", payload)


def installed(path: Path):
    a = CutoverAuthority(path)
    a.install_shadow(ev(EvidenceKind.SHADOW_PROOF, authorized_by="farhad"))
    return a


def candidate(path: Path):
    a = installed(path)
    a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, ev(EvidenceKind.REPLAY_EQUALITY))
    a.transition(CutoverState.SHADOW_RUNTIME_VERIFIED, ev(EvidenceKind.RUNTIME_PARITY))
    a.transition(
        CutoverState.CUTOVER_CANDIDATE,
        ev(EvidenceKind.FENCING_VERIFIED, transfer_authorized=True, consumers_migrated=True, consumer_generation=1),
    )
    return a


def enabled(path: Path):
    a = candidate(path)
    proof = ev(EvidenceKind.FENCING_VERIFIED)
    a.fence_legacy(proof, workflow_disabled=True, triggers_removed=True, writers_fenced=True, consumers_switched=True)
    a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    a.transition(CutoverState.NEW_AUTHORITY_ENABLED, ev(EvidenceKind.TRANSFER_AUTHORIZED))
    return a


def test_forward_cutover_and_single_owner(tmp_path):
    a = enabled(tmp_path / "state.json")
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert a.publication_allowed(PublicationOwner.NEW, 1)
    a.transition(CutoverState.POST_CUTOVER_VERIFIED, ev(EvidenceKind.POST_CUTOVER_PARITY))
    a.transition(CutoverState.LEGACY_CODE_REMOVABLE, ev(EvidenceKind.LEGACY_REMOVAL_VERIFIED))
    a.transition(CutoverState.LEGACY_CODE_REMOVED, ev(EvidenceKind.LEGACY_REMOVED))
    assert a.transition(CutoverState.CUTOVER_COMPLETE).state == CutoverState.CUTOVER_COMPLETE


def test_restart_is_durable(tmp_path):
    path = tmp_path / "state.json"
    a = candidate(path)
    assert CutoverAuthority(path).load() == a.load()


def test_stale_generation_and_delayed_publishers_fail_closed(tmp_path):
    a = enabled(tmp_path / "state.json")
    assert not a.publication_allowed(PublicationOwner.NEW, 0)
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    stale = CutoverEvidence(
        EvidenceKind.POST_CUTOVER_PARITY,
        "d",
        "t",
        "x",
        {"generation": 0, "implementation_sha": "impl", "policy_sha": "policy"},
    )
    with pytest.raises(ValueError, match="active transfer identity"):
        a.transition(CutoverState.POST_CUTOVER_VERIFIED, stale)


def test_corrupt_record_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad")
    with pytest.raises(ValueError, match="corrupt"):
        CutoverAuthority(path).load()


def test_duplicate_install_and_transition_are_idempotent(tmp_path):
    path = tmp_path / "state.json"
    a = installed(path)
    assert (
        a.install_shadow(ev(EvidenceKind.SHADOW_PROOF, authorized_by="farhad")).state == CutoverState.SHADOW_INSTALLED
    )
    proof = ev(EvidenceKind.REPLAY_EQUALITY)
    first = a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, proof)
    assert a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, proof) == first


def test_legacy_cannot_disable_until_all_fences_complete(tmp_path):
    a = candidate(tmp_path / "state.json")
    a.fence_legacy(ev(EvidenceKind.FENCING_VERIFIED), workflow_disabled=True)
    with pytest.raises(ValueError, match="fencing not complete"):
        a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)


def test_rollback_requires_successor_fence(tmp_path):
    a = enabled(tmp_path / "state.json")
    with pytest.raises(ValueError, match="successor is fenced"):
        a.rollback(ev(EvidenceKind.TRANSFER_AUTHORIZED))
    a.fence_successor(ev(EvidenceKind.ROLLBACK_EXCLUDED))
    assert not a.publication_allowed(PublicationOwner.NEW, 1)
    state = a.rollback(ev(EvidenceKind.TRANSFER_AUTHORIZED))
    assert state.state == CutoverState.CUTOVER_CANDIDATE
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not a.publication_allowed(PublicationOwner.NEW, 1)


def test_illegal_skip_rejected(tmp_path):
    a = installed(tmp_path / "state.json")
    with pytest.raises(ValueError, match="illegal transition"):
        a.transition(CutoverState.CUTOVER_CANDIDATE, ev(EvidenceKind.FENCING_VERIFIED))


def test_atomic_write_leaves_previous_record_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    a = installed(path)
    before = path.read_text()
    import hunter.governance_cutover.authority as module

    def fail_replace(src, dst):
        raise OSError("crash")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="crash"):
        a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, ev(EvidenceKind.REPLAY_EQUALITY))
    assert path.read_text() == before
    assert CutoverAuthority(path).load().state == CutoverState.SHADOW_INSTALLED


def test_consumer_generation_required_before_legacy_disable(tmp_path):
    a = candidate(tmp_path / "state.json")
    proof = ev(EvidenceKind.FENCING_VERIFIED)
    a.fence_legacy(proof, workflow_disabled=True, triggers_removed=True, writers_fenced=True)
    with pytest.raises(ValueError, match="fencing not complete"):
        a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)


def test_duplicate_target_with_different_evidence_is_rejected(tmp_path):
    a = installed(tmp_path / "state.json")
    a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, ev(EvidenceKind.REPLAY_EQUALITY))
    conflicting = CutoverEvidence(EvidenceKind.REPLAY_EQUALITY, "different", "2026-09-22T05:01:00Z", "other", {"generation": 1, "implementation_sha": "impl", "policy_sha": "policy"})
    with pytest.raises(ValueError, match="idempotent replay"):
        a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, conflicting)

def test_loaded_record_rejects_incoherent_authority_flags(tmp_path):
    import json
    path = tmp_path / "state.json"
    a = enabled(path)
    raw = json.loads(path.read_text())
    raw["legacy_writers_fenced"] = False
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="incoherent"):
        a.load()

def test_candidate_cannot_enable_successor_without_legacy_disable_and_transfer_authority(tmp_path):
    a = candidate(tmp_path / "state.json")
    with pytest.raises(ValueError, match="illegal transition"):
        a.transition(CutoverState.NEW_AUTHORITY_ENABLED, ev(EvidenceKind.TRANSFER_AUTHORIZED))
    assert a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not a.publication_allowed(PublicationOwner.NEW, 1)


def test_disabled_gap_has_no_publisher_even_with_current_generation(tmp_path):
    a = candidate(tmp_path / "state.json")
    a.fence_legacy(ev(EvidenceKind.FENCING_VERIFIED), workflow_disabled=True, triggers_removed=True, writers_fenced=True, consumers_switched=True)
    a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not a.publication_allowed(PublicationOwner.NEW, 1)
