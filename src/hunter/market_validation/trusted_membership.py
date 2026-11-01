"""ADR 0040 shadow trust adapter: pinned Ed25519 evidence and external head proof.

The signed anchor must be retrieved by an authorized caller from an independently
protected authority, NOT from the membership SQLite database or its directory.
No production CLI is wired to this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hunter.market_validation.membership_history import (
    ImmutableMembershipHistory,
    TrustedHistoryAnchor,
)
from hunter.market_validation.universe_membership import AuthorizedUniverse

EVIDENCE_DOMAIN = b"hunter.market-validation.membership-evidence.v1\x00"
ANCHOR_DOMAIN = b"hunter.market-validation.membership-anchor.v1\x00"


def canonical_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_signed_claim(
    claim: dict[str, Any],
    *,
    public_key: Ed25519PublicKey,
    domain: bytes,
    expected_repository: str,
) -> dict[str, Any]:
    if set(claim) != {"payload", "signature"} or not isinstance(claim["payload"], dict):
        raise ValueError("invalid signed membership claim")
    payload = claim["payload"]
    if payload.get("repository") != expected_repository:
        raise ValueError("membership claim repository mismatch")
    try:
        public_key.verify(bytes.fromhex(claim["signature"]), domain + canonical_bytes(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("membership signature invalid") from exc
    return payload


@dataclass(frozen=True)
class PinnedMembershipTrust:
    repository: str
    evidence_public_key: Ed25519PublicKey
    anchor_public_key: Ed25519PublicKey

    def verify_evidence(self, attestation: str, payload_digest: str) -> bool:
        try:
            claim = json.loads(attestation)
            payload = verify_signed_claim(
                claim,
                public_key=self.evidence_public_key,
                domain=EVIDENCE_DOMAIN,
                expected_repository=self.repository,
            )
            return payload == {"repository": self.repository, "payload_digest": payload_digest}
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False

    def verify_anchor(self, signed_anchor: str) -> TrustedHistoryAnchor:
        try:
            claim = json.loads(signed_anchor)
            payload = verify_signed_claim(
                claim,
                public_key=self.anchor_public_key,
                domain=ANCHOR_DOMAIN,
                expected_repository=self.repository,
            )
            if set(payload) != {"repository", "sequence", "digest"}:
                raise ValueError("invalid anchor payload")
            if payload["repository"] != self.repository:
                raise ValueError("anchor repository mismatch")
            if type(payload["sequence"]) is not int or payload["sequence"] < 1:
                raise ValueError("invalid anchor sequence")
            if not isinstance(payload["digest"], str) or len(payload["digest"]) != 64:
                raise ValueError("invalid anchor digest")
            bytes.fromhex(payload["digest"])
            return TrustedHistoryAnchor(
                repository=payload["repository"],
                sequence=payload["sequence"],
                digest=payload["digest"],
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("untrusted membership anchor") from exc

    def authorize_shadow(
        self,
        history: ImmutableMembershipHistory,
        *,
        signed_external_anchor: str,
        cutoff: datetime,
        run_id: str,
        baseline_run_id: str,
        policy_version: str,
        output_directory: str,
        baseline_output_directory: str,
    ) -> AuthorizedUniverse:
        return history._authorize_verified_shadow(
            cutoff=cutoff,
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            policy_version=policy_version,
            output_directory=output_directory,
            baseline_output_directory=baseline_output_directory,
            trusted_anchor=self.verify_anchor(signed_external_anchor),
            verify_attestation=self.verify_evidence,
        )


class ExternalAnchorReader:
    """An independently authenticated anti-rollback read, never a SQLite-derived head."""

    def read_signed_head(self, repository: str) -> str:
        raise NotImplementedError


class MembershipAnchorPin:
    """Pinned anti-rollback ruleset for the membership history anchor (F-2).

    Protects ``refs/heads/hunter-state/v1/knowledge`` with deletion and
    non_fast_forward rules so that no actor can rewrite, truncate or roll back
    the signed membership head.
    """

    def __init__(
        self,
        *,
        ruleset_id: int,
        updated_at: str,
        ref: str = "refs/heads/hunter-state/v1/knowledge",
        required_rules: frozenset[str] | None = None,
    ) -> None:
        self.ruleset_id = ruleset_id
        self.updated_at = updated_at
        self.ref = ref
        self.required_rules = required_rules or frozenset({"deletion", "non_fast_forward"})

    def verify(self, ruleset: dict[str, Any] | None, branch_rules: list[dict[str, Any]] | None) -> None:
        """Fail closed unless the anchor ruleset is provably intact for this ref."""
        if ruleset is None or ruleset.get("id") != self.ruleset_id:
            raise ValueError("membership anchor ruleset missing or replaced")
        if ruleset.get("enforcement") != "active":
            raise ValueError("membership anchor ruleset not active")

        def instant(value: object) -> datetime:
            if not isinstance(value, str):
                raise ValueError("timestamp is not a string")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp has no timezone")
            return parsed.astimezone(UTC)

        try:
            same_updated_at = instant(ruleset.get("updated_at")) == instant(self.updated_at)
        except (TypeError, ValueError):
            same_updated_at = False
        if not same_updated_at:
            raise ValueError("membership anchor ruleset modified since it was pinned")

        declared = {str(rule.get("type")) for rule in ruleset.get("rules") or [] if isinstance(rule, dict)}
        if not self.required_rules <= declared:
            raise ValueError("membership anchor ruleset rules weakened")

        if branch_rules is not None:
            applied = {
                str(rule.get("type"))
                for rule in branch_rules
                if isinstance(rule, dict) and rule.get("ruleset_id") == self.ruleset_id
            }
            if not self.required_rules <= applied:
                raise ValueError("membership anchor ruleset does not cover this ref")


class GitHubAnchorReader(ExternalAnchorReader):
    """Reads signed membership head anchors from GitHub protected storage.

    Retrieves the anchor from ``refs/heads/hunter-state/v1/knowledge`` under
    ruleset 24526712 (deletion + non_fast_forward). Ruleset integrity is
    verified before the anchor is accepted, so a weakened or removed ruleset
    causes fail-closed behavior (F-2).

    The caller supplies a ``store`` object with a ``read_files(ref, names)``
    method (compatible with :class:`GitLedgerStore`) and pre-fetched
    ``ruleset`` / ``branch_rules`` from authenticated GitHub API calls.
    """

    ANCHOR_FILE = "membership-anchor.json"

    def __init__(
        self,
        repository: str,
        *,
        store: Any,
        pin: MembershipAnchorPin,
        ruleset: dict[str, Any] | None = None,
        branch_rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self._store = store
        self._pin = pin
        self._ruleset = ruleset
        self._branch_rules = branch_rules

    def read_signed_head(self, repository: str) -> str:
        if repository != self.repository:
            raise ValueError("anchor repository mismatch")
        # Verify protection before accepting any anchor (F-2: ruleset removed or
        # weakened => fail closed).
        self._pin.verify(self._ruleset, self._branch_rules)
        head, entries = self._store.read_files(self._pin.ref, frozenset({self.ANCHOR_FILE}))
        if head is None:
            raise ValueError("external anchor unavailable")
        for _commit, files in entries:
            for name, content in files:
                if name == self.ANCHOR_FILE:
                    text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else str(content)
                    if not text.strip():
                        raise ValueError("external anchor unavailable")
                    return text
        raise ValueError("external anchor unavailable")


def load_pinned_membership_trust(document: dict[str, Any]) -> PinnedMembershipTrust:
    """Explicitly provisioned keys only; no generated or environment fallback."""
    if document.get("schema_version") != "hunter-membership-trust-v1" or document.get("provisioned") is not True:
        raise ValueError("membership trust roots not provisioned")
    if set(document) != {"schema_version", "provisioned", "repository", "evidence_public_key", "anchor_public_key"}:
        raise ValueError("unexpected membership trust configuration")
    repository = document["repository"]
    if not isinstance(repository, str) or "/" not in repository:
        raise ValueError("invalid trusted repository")
    try:
        evidence_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["evidence_public_key"]))
        anchor_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["anchor_public_key"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid pinned membership public keys") from exc
    return PinnedMembershipTrust(repository, evidence_key, anchor_key)


def authorize_from_external_anchor(
    history: ImmutableMembershipHistory,
    *,
    trust: PinnedMembershipTrust,
    anchor_reader: ExternalAnchorReader,
    cutoff: datetime,
    run_id: str,
    baseline_run_id: str,
    policy_version: str,
    output_directory: str,
    baseline_output_directory: str,
) -> AuthorizedUniverse:
    signed_head = anchor_reader.read_signed_head(trust.repository)
    if not isinstance(signed_head, str) or not signed_head:
        raise ValueError("external anchor unavailable")
    return trust.authorize_shadow(
        history,
        signed_external_anchor=signed_head,
        cutoff=cutoff,
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        policy_version=policy_version,
        output_directory=output_directory,
        baseline_output_directory=baseline_output_directory,
    )
