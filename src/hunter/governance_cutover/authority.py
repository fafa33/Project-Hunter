from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any


class CutoverState(StrEnum):
    LAB_VALIDATED = "LAB_VALIDATED"
    SHADOW_INSTALLED = "SHADOW_INSTALLED"
    HISTORICAL_REPLAY_VERIFIED = "HISTORICAL_REPLAY_VERIFIED"
    SHADOW_RUNTIME_VERIFIED = "SHADOW_RUNTIME_VERIFIED"
    CUTOVER_CANDIDATE = "CUTOVER_CANDIDATE"
    LEGACY_AUTHORITY_DISABLED = "LEGACY_AUTHORITY_DISABLED"
    NEW_AUTHORITY_ENABLED = "NEW_AUTHORITY_ENABLED"
    POST_CUTOVER_VERIFIED = "POST_CUTOVER_VERIFIED"
    LEGACY_CODE_REMOVABLE = "LEGACY_CODE_REMOVABLE"
    LEGACY_CODE_REMOVED = "LEGACY_CODE_REMOVED"
    CUTOVER_COMPLETE = "CUTOVER_COMPLETE"


class EvidenceKind(StrEnum):
    SHADOW_PROOF = "SHADOW_PROOF"
    REPLAY_EQUALITY = "REPLAY_EQUALITY"
    RUNTIME_PARITY = "RUNTIME_PARITY"
    FENCING_VERIFIED = "FENCING_VERIFIED"
    TRANSFER_AUTHORIZED = "TRANSFER_AUTHORIZED"
    POST_CUTOVER_PARITY = "POST_CUTOVER_PARITY"
    LEGACY_REMOVAL_VERIFIED = "LEGACY_REMOVAL_VERIFIED"
    LEGACY_REMOVED = "LEGACY_REMOVED"
    ROLLBACK_EXCLUDED = "ROLLBACK_EXCLUDED"


class PublicationOwner(StrEnum):
    LEGACY = "legacy"
    NEW = "new"


@dataclass(frozen=True)
class CutoverEvidence:
    kind: EvidenceKind
    sha256: str
    produced_at: str
    produced_by: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CutoverRecord:
    generation: int
    implementation_sha: str
    policy_sha: str
    authorized_by: str
    shadow_installed_at: str
    state: CutoverState
    enabled_at: str | None = None
    legacy_workflow_disabled: bool = False
    legacy_triggers_removed: bool = False
    legacy_writers_fenced: bool = False
    consumers_switched: bool = False
    consumer_generation: int | None = None
    new_authority_fenced: bool = False
    rollback_hold: bool = False
    evidence: tuple[CutoverEvidence, ...] = ()

    @property
    def legacy_fence_complete(self) -> bool:
        return (
            self.legacy_workflow_disabled
            and self.legacy_triggers_removed
            and self.legacy_writers_fenced
            and self.consumers_switched
            and self.consumer_generation == self.generation
        )


_NEXT: dict[CutoverState, CutoverState] = {
    CutoverState.LAB_VALIDATED: CutoverState.SHADOW_INSTALLED,
    CutoverState.SHADOW_INSTALLED: CutoverState.HISTORICAL_REPLAY_VERIFIED,
    CutoverState.HISTORICAL_REPLAY_VERIFIED: CutoverState.SHADOW_RUNTIME_VERIFIED,
    CutoverState.SHADOW_RUNTIME_VERIFIED: CutoverState.CUTOVER_CANDIDATE,
    CutoverState.CUTOVER_CANDIDATE: CutoverState.LEGACY_AUTHORITY_DISABLED,
    CutoverState.LEGACY_AUTHORITY_DISABLED: CutoverState.NEW_AUTHORITY_ENABLED,
    CutoverState.NEW_AUTHORITY_ENABLED: CutoverState.POST_CUTOVER_VERIFIED,
    CutoverState.POST_CUTOVER_VERIFIED: CutoverState.LEGACY_CODE_REMOVABLE,
    CutoverState.LEGACY_CODE_REMOVABLE: CutoverState.LEGACY_CODE_REMOVED,
    CutoverState.LEGACY_CODE_REMOVED: CutoverState.CUTOVER_COMPLETE,
}
_REQUIRED_KIND = {
    CutoverState.HISTORICAL_REPLAY_VERIFIED: EvidenceKind.REPLAY_EQUALITY,
    CutoverState.SHADOW_RUNTIME_VERIFIED: EvidenceKind.RUNTIME_PARITY,
    CutoverState.CUTOVER_CANDIDATE: EvidenceKind.FENCING_VERIFIED,
    CutoverState.NEW_AUTHORITY_ENABLED: EvidenceKind.TRANSFER_AUTHORIZED,
    CutoverState.POST_CUTOVER_VERIFIED: EvidenceKind.POST_CUTOVER_PARITY,
    CutoverState.LEGACY_CODE_REMOVABLE: EvidenceKind.LEGACY_REMOVAL_VERIFIED,
    CutoverState.LEGACY_CODE_REMOVED: EvidenceKind.LEGACY_REMOVED,
}


class CutoverAuthority:
    """Durable single-owner authority transfer. It has no publication side effects."""

    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def install_shadow(self, evidence: CutoverEvidence) -> CutoverRecord:
        if self.exists():
            current = self.load()
            if current.state == CutoverState.SHADOW_INSTALLED and self._same_install(current, evidence):
                return current
            raise ValueError("cutover record already exists")
        self._validate_evidence_identity(evidence)
        if evidence.kind != EvidenceKind.SHADOW_PROOF:
            raise ValueError("shadow installation requires SHADOW_PROOF")
        generation = evidence.payload.get("generation")
        implementation_sha = evidence.payload.get("implementation_sha")
        policy_sha = evidence.payload.get("policy_sha")
        authorized_by = evidence.payload.get("authorized_by")
        if (
            not isinstance(generation, int)
            or generation < 1
            or not all(isinstance(v, str) and v.strip() for v in (implementation_sha, policy_sha, authorized_by))
        ):
            raise ValueError("invalid shadow transfer identity")
        record = CutoverRecord(
            generation=generation,
            implementation_sha=implementation_sha,
            policy_sha=policy_sha,
            authorized_by=authorized_by,
            shadow_installed_at=evidence.produced_at,
            state=CutoverState.SHADOW_INSTALLED,
            evidence=(evidence,),
        )
        self._write(record)
        return record

    def load(self) -> CutoverRecord:
        try:
            raw = json.loads(self.path.read_text())
            evidence = tuple(
                CutoverEvidence(EvidenceKind(e["kind"]), e["sha256"], e["produced_at"], e["produced_by"], e["payload"])
                for e in raw.pop("evidence")
            )
            raw["state"] = CutoverState(raw["state"])
            record = CutoverRecord(**raw, evidence=evidence)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid or corrupt cutover record") from exc
        if record.generation < 1 or not all(
            isinstance(v, str) and v.strip()
            for v in (record.implementation_sha, record.policy_sha, record.authorized_by, record.shadow_installed_at)
        ):
            raise ValueError("invalid or corrupt cutover record")
        new_states = {
            CutoverState.NEW_AUTHORITY_ENABLED,
            CutoverState.POST_CUTOVER_VERIFIED,
            CutoverState.LEGACY_CODE_REMOVABLE,
            CutoverState.LEGACY_CODE_REMOVED,
            CutoverState.CUTOVER_COMPLETE,
        }
        if record.state in new_states and not record.legacy_fence_complete:
            raise ValueError("incoherent cutover record: successor active without complete legacy fence")
        if record.state == CutoverState.LEGACY_AUTHORITY_DISABLED and not record.legacy_fence_complete:
            raise ValueError("incoherent cutover record: legacy disabled without complete fence")
        if record.state in new_states and not record.enabled_at:
            raise ValueError("incoherent cutover record: successor active without enablement time")
        self._validate_persisted_evidence_chain(record)
        return record

    def transition(self, target: CutoverState, evidence: CutoverEvidence | None = None) -> CutoverRecord:
        current = self.load()
        if current.state == target:
            required = _REQUIRED_KIND.get(target)
            if required is None:
                return current
            proof = self._require_bound(current, evidence, required)
            if proof not in current.evidence:
                raise ValueError("idempotent replay evidence does not match accepted transition")
            return current
        if _NEXT.get(current.state) != target:
            raise ValueError(f"illegal transition {current.state.value} -> {target.value}")
        if target == CutoverState.LEGACY_AUTHORITY_DISABLED:
            if not current.legacy_fence_complete:
                raise ValueError("legacy fencing not complete")
        elif target == CutoverState.CUTOVER_COMPLETE:
            if not current.legacy_writers_fenced or not current.consumers_switched:
                raise ValueError("cutover completion requires durable fencing")
        else:
            required = _REQUIRED_KIND.get(target)
            if required is not None:
                proof = self._require_bound(current, evidence, required)
                if target == CutoverState.CUTOVER_CANDIDATE:
                    if proof.payload.get("transfer_authorized") is not True:
                        raise ValueError("transfer authorization proof missing")
                    if proof.payload.get("consumers_migrated") is not True:
                        raise ValueError("consumer migration proof missing")
                    if proof.payload.get("consumer_generation") != current.generation:
                        raise ValueError("consumer generation mismatch")
                current = replace(current, evidence=current.evidence + (proof,))
        if target == CutoverState.NEW_AUTHORITY_ENABLED:
            current = replace(
                current,
                enabled_at=evidence.produced_at if evidence else None,
                new_authority_fenced=False,
                rollback_hold=False,
            )
        current = replace(current, state=target)
        self._write(current)
        return current

    def fence_legacy(
        self,
        evidence: CutoverEvidence,
        *,
        workflow_disabled: bool = False,
        triggers_removed: bool = False,
        writers_fenced: bool = False,
        consumers_switched: bool = False,
    ) -> CutoverRecord:
        current = self.load()
        proof = self._require_bound(current, evidence, EvidenceKind.FENCING_VERIFIED)
        updated = replace(
            current,
            legacy_workflow_disabled=current.legacy_workflow_disabled or workflow_disabled,
            legacy_triggers_removed=current.legacy_triggers_removed or triggers_removed,
            legacy_writers_fenced=current.legacy_writers_fenced or writers_fenced,
            consumers_switched=current.consumers_switched or consumers_switched,
            consumer_generation=current.generation if (current.consumers_switched or consumers_switched) else None,
            evidence=current.evidence if proof in current.evidence else current.evidence + (proof,),
        )
        self._write(updated)
        return updated

    def publication_allowed(self, owner: PublicationOwner, generation: int) -> bool:
        current = self.load()
        if generation != current.generation or current.rollback_hold:
            return False
        if current.state in {
            CutoverState.NEW_AUTHORITY_ENABLED,
            CutoverState.POST_CUTOVER_VERIFIED,
            CutoverState.LEGACY_CODE_REMOVABLE,
            CutoverState.LEGACY_CODE_REMOVED,
            CutoverState.CUTOVER_COMPLETE,
        }:
            return owner == PublicationOwner.NEW and not current.new_authority_fenced
        if current.state == CutoverState.LEGACY_AUTHORITY_DISABLED:
            return False
        return owner == PublicationOwner.LEGACY

    def fence_successor(self, evidence: CutoverEvidence) -> CutoverRecord:
        current = self.load()
        self._require_bound(current, evidence, EvidenceKind.ROLLBACK_EXCLUDED)
        if current.state not in {
            CutoverState.NEW_AUTHORITY_ENABLED,
            CutoverState.POST_CUTOVER_VERIFIED,
            CutoverState.LEGACY_CODE_REMOVABLE,
        }:
            return current
        updated = replace(current, new_authority_fenced=True, evidence=current.evidence + (evidence,))
        self._write(updated)
        return updated

    def rollback(self, evidence: CutoverEvidence) -> CutoverRecord:
        current = self.load()
        proof = self._require_bound(current, evidence, EvidenceKind.TRANSFER_AUTHORIZED)
        if not current.new_authority_fenced:
            raise ValueError("cannot rollback until successor is fenced")
        updated = replace(
            current,
            state=CutoverState.CUTOVER_CANDIDATE,
            enabled_at=None,
            legacy_workflow_disabled=False,
            legacy_triggers_removed=False,
            legacy_writers_fenced=False,
            consumers_switched=False,
            consumer_generation=None,
            new_authority_fenced=False,
            rollback_hold=True,
            evidence=current.evidence + (proof,),
        )
        self._write(updated)
        return updated

    @staticmethod
    def _validate_persisted_evidence_chain(record: CutoverRecord) -> None:
        state_order = list(CutoverState)
        reached = state_order.index(record.state)
        required = [EvidenceKind.SHADOW_PROOF]
        for state, kind in _REQUIRED_KIND.items():
            if state_order.index(state) <= reached:
                required.append(kind)
        by_kind = {evidence.kind: evidence for evidence in record.evidence}
        for kind in required:
            evidence = by_kind.get(kind)
            if evidence is None:
                raise ValueError(f"incoherent cutover record: evidence chain missing {kind.value}")
            payload = evidence.payload
            if (
                payload.get("generation") != record.generation
                or payload.get("implementation_sha") != record.implementation_sha
                or payload.get("policy_sha") != record.policy_sha
            ):
                raise ValueError(f"incoherent cutover record: evidence chain identity mismatch for {kind.value}")

    @staticmethod
    def _same_install(current: CutoverRecord, evidence: CutoverEvidence) -> bool:
        return (
            current.generation == evidence.payload.get("generation")
            and current.implementation_sha == evidence.payload.get("implementation_sha")
            and current.policy_sha == evidence.payload.get("policy_sha")
            and current.authorized_by == evidence.payload.get("authorized_by")
        )

    @staticmethod
    def _validate_evidence_identity(evidence: CutoverEvidence) -> None:
        if not all(
            isinstance(v, str) and v.strip() for v in (evidence.sha256, evidence.produced_at, evidence.produced_by)
        ):
            raise ValueError("invalid evidence identity")

    def _require_bound(
        self, record: CutoverRecord, evidence: CutoverEvidence | None, kind: EvidenceKind
    ) -> CutoverEvidence:
        if evidence is None:
            raise ValueError(f"missing {kind.value} evidence")
        self._validate_evidence_identity(evidence)
        if evidence.kind != kind:
            raise ValueError(f"evidence must be {kind.value}")
        expected = {
            "generation": record.generation,
            "implementation_sha": record.implementation_sha,
            "policy_sha": record.policy_sha,
        }
        if any(evidence.payload.get(k) != v for k, v in expected.items()):
            raise ValueError("evidence is not bound to active transfer identity")
        return evidence

    def _write(self, record: CutoverRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(record)
        data["state"] = record.state.value
        data["evidence"] = [{**asdict(e), "kind": e.kind.value} for e in record.evidence]
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
