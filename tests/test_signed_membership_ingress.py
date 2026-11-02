import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from membership_helpers import NOW, REPO, snapshot

from hunter.discovery.canonical_history import CanonicalEntityRecord
from hunter.discovery.repository import CandidateRegistryRepository
from hunter.market_validation.membership_history import ImmutableMembershipHistory, TrustedIngress
from hunter.market_validation.trusted_membership import (
    EVIDENCE_DOMAIN,
    PinnedMembershipTrust,
    RegistryIdentityVerifier,
    canonical_bytes,
)


def signed(key, digest):
    claim = {"repository": REPO, "payload_digest": digest}
    return json.dumps({"payload": claim, "signature": key.sign(EVIDENCE_DOMAIN + canonical_bytes(claim)).hex()})


def test_signed_ingress_binds_trusted_clock_and_registry(tmp_path):
    registry = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    evidence_key = Ed25519PrivateKey.generate()
    trust = PinnedMembershipTrust(REPO, evidence_key.public_key(), Ed25519PrivateKey.generate().public_key())

    def sign(digest):
        return signed(evidence_key, digest)

    registry.append_signed_canonical_entity(
        CanonicalEntityRecord(
            candidate_id="candidate-1",
            canonical_entity_id="entity-1",
            representation="bitcoin:mainnet:native",
            outcome="exact",
            lifecycle_status="identified",
            source_digest="sha256:source",
            source_reference="registry:source",
            policy_version="v1",
            observed_at=NOW,
            known_at=NOW,
            upstream_attestation="",
        ),
        repository=REPO,
        clock=lambda: NOW,
        sign_digest=sign,
        verify_attestation=trust.verify_registry_identity,
    )
    history = ImmutableMembershipHistory(tmp_path / "membership.sqlite", repository=REPO, clock=lambda: NOW)
    ingress = TrustedIngress(
        history,
        registry=RegistryIdentityVerifier(registry),
        clock=lambda: NOW,
        verify_registry_attestation=trust.verify_registry_identity,
        sign_evidence_digest=sign,
        verify_evidence=trust.verify_evidence,
    )
    assert ingress.ingest(replace(snapshot(1), upstream_attestation=""))
    with sqlite3.connect(history.path) as conn:
        payload = json.loads(conn.execute("SELECT content FROM membership_history").fetchone()[0])
    assert payload["ingested_at"] == NOW.isoformat()
    digest = hashlib.sha256(
        canonical_bytes({k: v for k, v in payload.items() if k != "upstream_attestation"})
    ).hexdigest()
    assert trust.verify_evidence(payload["upstream_attestation"], digest)
    assert not trust.verify_evidence(payload["upstream_attestation"], "0" * 64)


def test_signed_ingress_fails_closed_without_verified_authority(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "membership.sqlite", repository=REPO, clock=lambda: NOW)

    class Registry:
        def verify_historical_entity(self, *args, **kwargs):
            return True

    with pytest.raises(ValueError, match="authenticated historical Registry verifier"):
        TrustedIngress(history, registry=Registry())
    with pytest.raises(ValueError, match="signed membership ingress requires authenticated Registry"):
        TrustedIngress(history, sign_evidence_digest=lambda _: "fake")
    with pytest.raises(ValueError, match="pinned membership evidence verifier"):
        TrustedIngress(
            history,
            registry=Registry(),
            verify_registry_attestation=lambda _: True,
            sign_evidence_digest=lambda _: "fake",
        )
    ingress = TrustedIngress(
        history,
        registry=Registry(),
        verify_registry_attestation=lambda _: True,
        clock=lambda: NOW,
        sign_evidence_digest=lambda _: "fake",
        verify_evidence=lambda *_: False,
    )
    with pytest.raises(ValueError, match="membership evidence attestation unverified"):
        ingress.ingest(replace(snapshot(), upstream_attestation=""))
    with sqlite3.connect(history.path) as conn:
        assert conn.execute("SELECT count(*) FROM membership_history").fetchone()[0] == 0
