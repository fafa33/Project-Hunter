from __future__ import annotations

import json
from pathlib import Path

from hunter.governance_cutover import CutoverAuthority, CutoverEvidence, CutoverState, EvidenceKind, PublicationOwner

CONTRACT_PATH = Path("tests/fixtures/governance_cutover/accepted_phase4_contract.json")


def contract():
    return json.loads(CONTRACT_PATH.read_text())


def ev(kind, **extra):
    payload = {"generation": 1, "implementation_sha": "impl", "policy_sha": "policy"}
    payload.update(extra)
    return CutoverEvidence(kind, "sha", "2026-09-22T05:00:00Z", "parity", payload)


def test_production_state_and_evidence_vocab_match_versioned_accepted_contract():
    accepted = contract()
    assert [state.value for state in CutoverState] == accepted["states"]
    assert {kind.value for kind in EvidenceKind} == set(accepted["evidence"])


def test_forward_state_sequence_matches_accepted_lab(tmp_path):
    accepted = contract()
    a = CutoverAuthority(tmp_path / "state.json")
    a.install_shadow(ev(EvidenceKind.SHADOW_PROOF, authorized_by="human"))
    a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, ev(EvidenceKind.REPLAY_EQUALITY))
    a.transition(CutoverState.SHADOW_RUNTIME_VERIFIED, ev(EvidenceKind.RUNTIME_PARITY))
    a.transition(
        CutoverState.CUTOVER_CANDIDATE,
        ev(EvidenceKind.FENCING_VERIFIED, transfer_authorized=True, consumers_migrated=True, consumer_generation=1),
    )
    a.fence_legacy(
        ev(EvidenceKind.FENCING_VERIFIED),
        workflow_disabled=True,
        triggers_removed=True,
        writers_fenced=True,
        consumers_switched=True,
    )
    a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    assert accepted["invariants"]["disabled_gap_has_no_owner"]
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not a.publication_allowed(PublicationOwner.NEW, 1)
    a.transition(CutoverState.NEW_AUTHORITY_ENABLED, ev(EvidenceKind.TRANSFER_AUTHORIZED))
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert a.publication_allowed(PublicationOwner.NEW, 1)


def test_rollback_is_at_least_as_fail_closed_as_lab(tmp_path):
    a = CutoverAuthority(tmp_path / "state.json")
    a.install_shadow(ev(EvidenceKind.SHADOW_PROOF, authorized_by="human"))
    a.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, ev(EvidenceKind.REPLAY_EQUALITY))
    a.transition(CutoverState.SHADOW_RUNTIME_VERIFIED, ev(EvidenceKind.RUNTIME_PARITY))
    a.transition(
        CutoverState.CUTOVER_CANDIDATE,
        ev(EvidenceKind.FENCING_VERIFIED, transfer_authorized=True, consumers_migrated=True, consumer_generation=1),
    )
    a.fence_legacy(
        ev(EvidenceKind.FENCING_VERIFIED),
        workflow_disabled=True,
        triggers_removed=True,
        writers_fenced=True,
        consumers_switched=True,
    )
    a.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    a.transition(CutoverState.NEW_AUTHORITY_ENABLED, ev(EvidenceKind.TRANSFER_AUTHORIZED))
    a.fence_successor(ev(EvidenceKind.ROLLBACK_EXCLUDED))
    a.rollback(ev(EvidenceKind.TRANSFER_AUTHORIZED))
    assert not a.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not a.publication_allowed(PublicationOwner.NEW, 1)


def test_cutover_tests_are_ci_portable():
    import ast

    tree = ast.parse(Path(__file__).read_text())
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    forbidden = (chr(47) + "Users" + chr(47), ".work" + "trees" + chr(47))
    assert not any(token in value for value in literals for token in forbidden)
