"""ADR 0040: strict-known Registry-owned canonical entity history."""

from datetime import UTC, datetime, timedelta

import pytest

from hunter.discovery.canonical_history import CanonicalEntityRecord
from hunter.discovery.repository import CandidateRegistryRepository

NOW = datetime(2026, 10, 1, tzinfo=UTC)
REPO = "fafa33/Project-Hunter"


def record(candidate="candidate-a", entity="entity-a", *, known=NOW, outcome="exact"):
    return CanonicalEntityRecord(
        candidate_id=candidate,
        canonical_entity_id=entity,
        representation="ethereum:0x123",
        outcome=outcome,
        lifecycle_status="identified",
        source_digest="sha256:source",
        source_reference="registry:source",
        policy_version="v1",
        observed_at=NOW,
        known_at=known,
        upstream_attestation="external:attested",
    )


def test_history_cutoff_uniqueness_and_mutation_refusal(tmp_path):
    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")

    def verify(payload):
        return payload["upstream_attestation"] == "external:attested"

    repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=verify)
    assert repo.canonical_entities_as_of(repository=REPO, cutoff=NOW, verify_attestation=verify) == {
        "entity-a": "candidate-a"
    }
    later = NOW + timedelta(days=1)
    repo.append_canonical_entity(
        record("candidate-b", "entity-a", known=later), repository=REPO, clock=lambda: later, verify_attestation=verify
    )
    assert repo.canonical_entities_as_of(repository=REPO, cutoff=NOW, verify_attestation=verify) == {
        "entity-a": "candidate-a"
    }
    assert repo.canonical_entities_as_of(repository=REPO, cutoff=later, verify_attestation=verify) == {}
    with repo._connect() as conn:
        with pytest.raises(Exception, match="immutable canonical identity"):
            conn.execute("DELETE FROM canonical_entity_history")


def test_missing_attestation_and_future_known_rejected(tmp_path):
    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    with pytest.raises(ValueError, match="attestation unverified"):
        repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: False)
    with pytest.raises(ValueError, match="chronology"):
        repo.append_canonical_entity(
            record(known=NOW + timedelta(days=1)), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: True
        )
    assert repo.canonical_entity_history() == []


def test_replay_detects_tampering_and_conflict(tmp_path):
    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: True)
    with repo._connect() as conn:
        with pytest.raises(Exception, match="immutable canonical identity"):
            conn.execute("UPDATE canonical_entity_history SET digest = 'forged'")
    with pytest.raises(ValueError, match="attestation unverified"):
        repo.canonical_entities_as_of(repository=REPO, cutoff=NOW, verify_attestation=lambda _: False)
    with pytest.raises(ValueError, match="chain mismatch"):
        repo.canonical_entities_as_of(repository="other/repo", cutoff=NOW, verify_attestation=lambda _: True)


def test_registry_verifier_requires_historical_attestation(tmp_path):
    from hunter.market_validation.trusted_membership import RegistryIdentityVerifier

    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: True)
    verifier = RegistryIdentityVerifier(repo)
    assert not verifier.verify_entity("entity-a")
    assert verifier.verify_historical_entity(
        "entity-a", candidate_id="candidate-a", cutoff=NOW, repository=REPO, verify_attestation=lambda _: True
    )
    assert not verifier.verify_historical_entity(
        "entity-a", candidate_id="candidate-b", cutoff=NOW, repository=REPO, verify_attestation=lambda _: True
    )
    assert not verifier.verify_historical_entity(
        "entity-a", candidate_id="candidate-a", cutoff=NOW, repository=REPO, verify_attestation=lambda _: False
    )


def test_same_candidate_supersession_requires_strictly_newer_known_time(tmp_path):
    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: True)
    with pytest.raises(ValueError, match="supersession must advance"):
        repo.append_canonical_entity(
            record(entity="entity-b"),
            repository=REPO,
            clock=lambda: NOW + timedelta(seconds=1),
            verify_attestation=lambda _: True,
        )
    assert repo.canonical_entities_as_of(repository=REPO, cutoff=NOW, verify_attestation=lambda _: True) == {
        "entity-a": "candidate-a"
    }


def test_future_dated_attestation_cannot_hide_from_cutoff_verification(tmp_path):
    repo = CandidateRegistryRepository(tmp_path / "registry.sqlite")
    later = NOW + timedelta(days=1)
    repo.append_canonical_entity(record(), repository=REPO, clock=lambda: NOW, verify_attestation=lambda _: True)
    repo.append_canonical_entity(
        record("candidate-b", "entity-b", known=later),
        repository=REPO,
        clock=lambda: later,
        verify_attestation=lambda _: True,
    )
    with pytest.raises(ValueError, match="attestation unverified"):
        repo.canonical_entities_as_of(
            repository=REPO,
            cutoff=NOW,
            verify_attestation=lambda payload: payload["candidate_id"] != "candidate-b",
        )


def test_replay_normalizes_timezone_offsets_before_cutoff():
    from hunter.discovery.canonical_history import digest_payload, replay_canonical_entities

    payload = record().payload(repository=REPO, ingested_at=NOW, previous_digest=f"GENESIS:{REPO}")
    payload["known_at"] = "2026-10-01T01:00:00+01:00"
    payload["observed_at"] = "2026-10-01T00:00:00+00:00"
    payload["ingested_at"] = "2026-10-01T02:00:00+02:00"
    entries = [(digest_payload(payload), payload)]
    assert replay_canonical_entities(entries, repository=REPO, cutoff=NOW, verify_attestation=lambda _: True) == {
        "entity-a": "candidate-a"
    }


def test_signed_registry_ingress_uses_independent_ed25519_and_atomic_rollback(tmp_path):
    import json
    from dataclasses import replace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hunter.market_validation.trusted_membership import (
        EVIDENCE_DOMAIN,
        PinnedMembershipTrust,
        canonical_bytes,
    )

    signer = Ed25519PrivateKey.generate()
    trust = PinnedMembershipTrust(REPO, signer.public_key(), Ed25519PrivateKey.generate().public_key())
    registry = CandidateRegistryRepository(tmp_path / "registry.sqlite")

    def sign_digest(digest):
        claim = {"repository": REPO, "payload_digest": digest}
        return json.dumps({"payload": claim, "signature": signer.sign(EVIDENCE_DOMAIN + canonical_bytes(claim)).hex()})

    signed = replace(record(), upstream_attestation="")
    digest = registry.append_signed_canonical_entity(
        signed,
        repository=REPO,
        clock=lambda: NOW,
        sign_digest=sign_digest,
        verify_attestation=trust.verify_registry_identity,
    )
    assert len(digest) == 64
    assert registry.canonical_entities_as_of(
        repository=REPO, cutoff=NOW, verify_attestation=trust.verify_registry_identity
    ) == {"entity-a": "candidate-a"}
    assert not trust.verify_registry_identity({**registry.canonical_entity_history()[0][1], "outcome": "conflict"})

    with pytest.raises(ValueError, match="attestation unverified"):
        registry.append_signed_canonical_entity(
            replace(record("candidate-b", "entity-b"), upstream_attestation=""),
            repository=REPO,
            clock=lambda: NOW,
            sign_digest=lambda _: "not-signed",
            verify_attestation=trust.verify_registry_identity,
        )
    assert len(registry.canonical_entity_history()) == 1
    with pytest.raises(ValueError, match="supersession"):
        registry.append_signed_canonical_entity(
            signed,
            repository=REPO,
            clock=lambda: NOW,
            sign_digest=sign_digest,
            verify_attestation=trust.verify_registry_identity,
        )
    assert len(registry.canonical_entity_history()) == 1
