from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hunter.governance_cutover import CutoverAuthority, CutoverEvidence, CutoverState, EvidenceKind, PublicationOwner

LAB = Path(
    "/Users/farhadafshari/Projects/Project-Hunter/.worktrees/lean-review-architecture-simulation/architecture_lab/cutover.py"
)
spec = importlib.util.spec_from_file_location("accepted_lab_cutover", LAB)
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lab
spec.loader.exec_module(lab)


def pev(kind, **extra):
    payload = {"generation": 1, "implementation_sha": "impl", "policy_sha": "policy"}
    payload.update(extra)
    return CutoverEvidence(kind, "sha", "2026-09-22T05:00:00Z", "parity", payload)


def lev(kind, **extra):
    payload = {"generation": 1, "implementation_sha": "impl", "policy_sha": "policy"}
    payload.update(extra)
    return lab.TransferEvidence(kind, "sha", "2026-09-22T05:00:00Z", "parity", payload)


def test_forward_state_sequence_matches_accepted_lab(tmp_path):
    prod = CutoverAuthority(tmp_path / "state.json")
    model = lab.MigrationAuthority()
    prod.install_shadow(pev(EvidenceKind.SHADOW_PROOF, authorized_by="human"))
    model.transition(lab.TransferState.SHADOW_INSTALLED, lev(lab.EvidenceKind.SHADOW_PROOF, authorized_by="human"))
    pairs = [
        (
            CutoverState.HISTORICAL_REPLAY_VERIFIED,
            EvidenceKind.REPLAY_EQUALITY,
            lab.TransferState.HISTORICAL_REPLAY_VERIFIED,
            lab.EvidenceKind.REPLAY_EQUALITY,
            {},
        ),
        (
            CutoverState.SHADOW_RUNTIME_VERIFIED,
            EvidenceKind.RUNTIME_PARITY,
            lab.TransferState.SHADOW_RUNTIME_VERIFIED,
            lab.EvidenceKind.RUNTIME_PARITY,
            {},
        ),
        (
            CutoverState.CUTOVER_CANDIDATE,
            EvidenceKind.FENCING_VERIFIED,
            lab.TransferState.CUTOVER_CANDIDATE,
            lab.EvidenceKind.FENCING_VERIFIED,
            {"transfer_authorized": True, "consumers_migrated": True, "consumer_generation": 1},
        ),
    ]
    for ps, pk, ls, lk, extra in pairs:
        prod.transition(ps, pev(pk, **extra))
        model.transition(ls, lev(lk, **extra))
        assert prod.load().state.value == model.state.transfer_state.value
    prod.fence_legacy(
        pev(EvidenceKind.FENCING_VERIFIED),
        workflow_disabled=True,
        triggers_removed=True,
        writers_fenced=True,
        consumers_switched=True,
    )
    model.fence_legacy(
        evidence=lev(lab.EvidenceKind.FENCING_VERIFIED),
        workflow_disabled=True,
        triggers_removed=True,
        writers_wrapped=True,
        consumers_switched=True,
    )
    prod.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    model.transition(lab.TransferState.LEGACY_AUTHORITY_DISABLED)
    prod.transition(CutoverState.NEW_AUTHORITY_ENABLED, pev(EvidenceKind.TRANSFER_AUTHORIZED))
    model.transition(lab.TransferState.NEW_AUTHORITY_ENABLED, lev(lab.EvidenceKind.TRANSFER_AUTHORIZED))
    assert prod.load().state.value == model.state.transfer_state.value
    assert prod.publication_allowed(PublicationOwner.LEGACY, 1) == model.accept_publication(
        owner="legacy", generation=1
    )
    assert prod.publication_allowed(PublicationOwner.NEW, 1) == model.accept_publication(owner="new", generation=1)


def test_rollback_is_at_least_as_fail_closed_as_lab(tmp_path):
    prod = CutoverAuthority(tmp_path / "state.json")
    prod.install_shadow(pev(EvidenceKind.SHADOW_PROOF, authorized_by="human"))
    prod.transition(CutoverState.HISTORICAL_REPLAY_VERIFIED, pev(EvidenceKind.REPLAY_EQUALITY))
    prod.transition(CutoverState.SHADOW_RUNTIME_VERIFIED, pev(EvidenceKind.RUNTIME_PARITY))
    prod.transition(
        CutoverState.CUTOVER_CANDIDATE,
        pev(EvidenceKind.FENCING_VERIFIED, transfer_authorized=True, consumers_migrated=True, consumer_generation=1),
    )
    prod.fence_legacy(
        pev(EvidenceKind.FENCING_VERIFIED),
        workflow_disabled=True,
        triggers_removed=True,
        writers_fenced=True,
        consumers_switched=True,
    )
    prod.transition(CutoverState.LEGACY_AUTHORITY_DISABLED)
    prod.transition(CutoverState.NEW_AUTHORITY_ENABLED, pev(EvidenceKind.TRANSFER_AUTHORIZED))
    prod.fence_successor(pev(EvidenceKind.ROLLBACK_EXCLUDED))
    prod.rollback(pev(EvidenceKind.TRANSFER_AUTHORIZED))
    assert not prod.publication_allowed(PublicationOwner.LEGACY, 1)
    assert not prod.publication_allowed(PublicationOwner.NEW, 1)
