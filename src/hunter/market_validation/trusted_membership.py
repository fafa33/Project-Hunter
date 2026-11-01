"""ADR 0040 shadow trust adapter: pinned Ed25519 evidence and external head proof.

The signed anchor must be retrieved by an authorized caller from an independently
protected authority, NOT from the membership SQLite database or its directory.
No production CLI is wired to this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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
            if type(payload["sequence"]) is not int or payload["sequence"] < 1:
                raise ValueError("invalid anchor sequence")
            if not isinstance(payload["digest"], str) or len(payload["digest"]) != 64:
                raise ValueError("invalid anchor digest")
            bytes.fromhex(payload["digest"])
            return TrustedHistoryAnchor(payload["sequence"], payload["digest"])
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
