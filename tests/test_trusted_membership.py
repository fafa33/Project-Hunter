import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.market_validation.membership_history import (
    HistoricalMembershipSnapshot,
    ImmutableMembershipHistory,
    TrustedHistoryAnchor,
    TrustedIngress,
)
from hunter.market_validation.models import ProjectValidationTarget
from hunter.market_validation.trusted_membership import (
    ANCHOR_DOMAIN,
    EVIDENCE_DOMAIN,
    GitHubAnchorReader,
    MembershipAnchorPin,
    PinnedMembershipTrust,
    RegistryIdentityVerifier,
    canonical_bytes,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
REPO = "fafa33/Project-Hunter"


def snapshot(i=1, *, known_at=NOW, sector="other"):
    return HistoricalMembershipSnapshot(
        candidate_id=f"candidate-{i}",
        canonical_entity_id=f"entity-{i}",
        project=ProjectValidationTarget(f"entity-{i}", f"Entity {i}", sector),
        identity_outcome="exact",
        lifecycle_status="identified",
        observed_at=NOW,
        known_at=known_at,
        source_digest=f"sha256:{i}",
        registry_reference=f"registry:{i}",
        upstream_attestation=f"attestation:{i}",
    )


def anchor(history):
    import sqlite3

    with sqlite3.connect(history.path) as conn:
        row = conn.execute("SELECT sequence, digest FROM membership_history ORDER BY sequence DESC LIMIT 1").fetchone()
    return TrustedHistoryAnchor(repository=REPO, sequence=row[0], digest=row[1])


def signed(private, domain, payload):
    return json.dumps({"payload": payload, "signature": private.sign(domain + canonical_bytes(payload)).hex()})


def fixture(tmp_path):
    evidence_key, anchor_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    trust = PinnedMembershipTrust(REPO, evidence_key.public_key(), anchor_key.public_key())
    history = _make_history(tmp_path)
    for i in range(50):
        project = ProjectValidationTarget(f"entity-{i}", f"Entity {i}", "other")
        payload = {
            "candidate_id": f"candidate-{i}",
            "canonical_entity_id": f"entity-{i}",
            "project": {"project_id": f"entity-{i}", "name": f"Entity {i}", "sector": "other", "metadata": {}},
            "identity_outcome": "exact",
            "lifecycle_status": "identified",
            "known_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "ingested_at": NOW.isoformat(),
            "source_digest": f"sha256:{i}",
            "registry_reference": f"registry:{i}",
        }
        digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        attestation = signed(evidence_key, EVIDENCE_DOMAIN, {"repository": REPO, "payload_digest": digest})
        history.append(
            HistoricalMembershipSnapshot(
                candidate_id=f"candidate-{i}",
                canonical_entity_id=f"entity-{i}",
                project=project,
                identity_outcome="exact",
                lifecycle_status="identified",
                observed_at=NOW,
                known_at=NOW,
                source_digest=f"sha256:{i}",
                registry_reference=f"registry:{i}",
                upstream_attestation=attestation,
            )
        )
    with sqlite3.connect(history.path) as conn:
        sequence, head = conn.execute(
            "SELECT sequence, digest FROM membership_history ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    anchor = signed(anchor_key, ANCHOR_DOMAIN, {"repository": REPO, "sequence": sequence, "digest": head})
    return history, trust, anchor


def run_shadow(history, trust, anchor):
    return trust.authorize_shadow(
        history,
        signed_external_anchor=anchor,
        cutoff=NOW,
        run_id="candidate",
        baseline_run_id="baseline",
        policy_version="v1",
        output_directory="reports/candidates",
        baseline_output_directory="reports/baseline",
    )


def _make_history(tmp_path, clock=None, filename="history.sqlite"):
    if clock is None:

        def clock():
            return NOW

    return ImmutableMembershipHistory(tmp_path / filename, repository=REPO, clock=clock)


def test_signed_evidence_and_external_anchor_allow_shadow_only(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    result = run_shadow(history, trust, anchor)
    assert result.runnable and len(result.targets) == 50


def test_wrong_key_and_repository_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    wrong = replace(trust, anchor_public_key=Ed25519PrivateKey.generate().public_key())
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        run_shadow(history, wrong, anchor)
    wrong_repo = replace(trust, repository="other/repository")
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        run_shadow(history, wrong_repo, anchor)


def test_attestation_replay_fails_on_modified_entity(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        att = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])[
            "upstream_attestation"
        ]
    assert not trust.verify_evidence(att, "0" * 64)
    assert trust.verify_evidence(att, json.loads(att)["payload"]["payload_digest"])


def test_external_anchor_truncation_and_forgery_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    # Truncation
    with sqlite3.connect(history.path) as conn:
        conn.execute("DROP TRIGGER membership_history_no_delete")
        conn.execute("DELETE FROM membership_history WHERE sequence=50")
    with pytest.raises(ValueError, match="anchor mismatch"):
        run_shadow(history, trust, anchor)
    # Forged anchor
    claim = json.loads(anchor)
    claim["payload"]["sequence"] = 49
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        run_shadow(history, trust, json.dumps(claim))


def test_unprovisioned_trust_roots_fail_closed():
    from hunter.market_validation.trusted_membership import load_pinned_membership_trust

    with pytest.raises(ValueError, match="not provisioned"):
        load_pinned_membership_trust({"schema_version": "hunter-membership-trust-v1", "provisioned": False})


def test_external_anchor_reader_must_supply_signed_head(tmp_path):
    from hunter.market_validation.trusted_membership import ExternalAnchorReader, authorize_from_external_anchor

    history, trust, anchor = fixture(tmp_path)

    class Reader(ExternalAnchorReader):
        def __init__(self, value):
            self.value = value

        def read_signed_head(self, repository):
            assert repository == REPO
            return self.value

    kwargs = dict(
        history=history,
        trust=trust,
        cutoff=NOW,
        run_id="candidate",
        baseline_run_id="baseline",
        policy_version="v1",
        output_directory="reports/candidates",
        baseline_output_directory="reports/baseline",
    )
    with pytest.raises(ValueError, match="external anchor unavailable"):
        authorize_from_external_anchor(**kwargs, anchor_reader=Reader(""))
    assert authorize_from_external_anchor(**kwargs, anchor_reader=Reader(anchor)).runnable


# --- F-1 hostile tests: attestation binding ---------------------------------


def _content_digest_without_attestation(data: dict) -> str:
    return hashlib.sha256(canonical_bytes({k: v for k, v in data.items() if k != "upstream_attestation"})).hexdigest()


def _first_row_and_attestation(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        row = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])
    return trust, row["upstream_attestation"], row


@pytest.mark.parametrize(
    "field, value",
    [
        ("sector", "defi"),
        ("lifecycle_status", "rejected"),
        ("known_at", "2020-01-01T00:00:00+00:00"),
        ("source_digest", "sha256:forged"),
    ],
)
def test_f1_modified_field_rejected(tmp_path, field, value):
    trust, att, row = _first_row_and_attestation(tmp_path)
    modified = dict(row)
    if field == "sector":
        modified["project"] = dict(row["project"], sector=value)
    else:
        modified[field] = value
    assert not trust.verify_evidence(att, _content_digest_without_attestation(modified))
    assert trust.verify_evidence(att, _content_digest_without_attestation(row))


def test_f1_signature_replay_and_malformed_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    # Wrong repository
    claim = json.loads(anchor)
    claim["payload"]["repository"] = "other/repository"
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        run_shadow(history, trust, json.dumps(claim))
    # Replayed signature
    with sqlite3.connect(history.path) as conn:
        rows = conn.execute("SELECT content FROM membership_history ORDER BY sequence LIMIT 2").fetchall()
        att1 = json.loads(rows[0][0])["upstream_attestation"]
    digest2 = _content_digest_without_attestation(json.loads(rows[1][0]))
    assert not trust.verify_evidence(att1, digest2)
    # Malformed signature
    assert not trust.verify_evidence("not-json", "0" * 64)
    assert not trust.verify_evidence(json.dumps({"payload": {}, "signature": "zzz"}), "0" * 64)


# --- F-2 hostile tests: anchor pin and ruleset verification ----------------


def test_f2_anchor_pin_rejects_invalid():
    # Wrong ruleset ID
    pin = MembershipAnchorPin(ruleset_id=99999, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="missing or replaced"):
        pin.verify(
            {"id": 24526712, "enforcement": "active", "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
            None,
        )
    # Inactive ruleset
    pin2 = _make_pin()
    with pytest.raises(ValueError, match="not active"):
        pin2.verify(
            {"id": 24526712, "enforcement": "inactive", "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
            None,
        )
    # Weakened rules
    with pytest.raises(ValueError, match="rules weakened"):
        pin2.verify(
            {
                "id": 24526712,
                "enforcement": "active",
                "updated_at": "2026-10-06T20:52:31.773Z",
                "rules": [{"type": "deletion"}],
            },
            None,
        )
    # Stale updated_at
    with pytest.raises(ValueError, match="modified since it was pinned"):
        pin2.verify(
            {
                "id": 24526712,
                "enforcement": "active",
                "updated_at": "2026-10-07T00:00:00.000Z",
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            },
            None,
        )
    # Ruleset not covering ref
    with pytest.raises(ValueError, match="does not cover this ref"):
        pin2.verify(_intact_ruleset(), [{"type": "deletion", "ruleset_id": 99999}])


def test_f2_anchor_pin_accepts_intact():
    pin = _make_pin()
    pin.verify(_intact_ruleset(), _intact_branch_rules())  # should not raise


# --- F-3 hostile tests: trusted ingress and time ---------------------------


def test_f3_trusted_ingress_rejected_and_filtered(tmp_path):
    # Future known_at is rejected (ingested_at = now < known_at).
    history = _make_history(tmp_path)
    with pytest.raises(ValueError, match="cannot be known before trusted ingestion"):
        history.append(snapshot(known_at=NOW + timedelta(days=1)))
    # Evidence ingested after cutoff is not visible at that cutoff.
    history2 = _make_history(tmp_path, clock=lambda: NOW + timedelta(days=1), filename="history2.sqlite")
    history2.append(snapshot())
    assert history2.as_of(NOW, trusted_anchor=anchor(history2)) == ()
    assert len(history2.as_of(NOW + timedelta(days=1), trusted_anchor=anchor(history2))) == 1


# --- F-2 hostile tests: whole-database rollback and stale anchor ----------


def test_f2_whole_database_rollback_rejected(tmp_path):
    history = _make_history(tmp_path)
    history.append(snapshot(1))
    history.append(snapshot(2))
    trusted = anchor(history)
    # Simulate whole-database rollback: drop trigger, delete tail row.
    import sqlite3

    with sqlite3.connect(history.path) as conn:
        conn.execute("DROP TRIGGER membership_history_no_delete")
        conn.execute("DELETE FROM membership_history WHERE sequence=2")
    with pytest.raises(ValueError, match="anchor mismatch"):
        history.as_of(NOW, trusted_anchor=trusted)


def test_f2_stale_signed_anchor_replay_rejected(tmp_path):
    history = _make_history(tmp_path)
    history.append(snapshot(1))
    history.append(snapshot(2))
    # Anchor for sequence 1 (stale) must not validate a chain of length 2.
    import sqlite3

    with sqlite3.connect(history.path) as conn:
        row = conn.execute("SELECT sequence, digest FROM membership_history WHERE sequence=1").fetchone()
    stale = TrustedHistoryAnchor(repository=REPO, sequence=row[0], digest=row[1])
    with pytest.raises(ValueError, match="anchor mismatch"):
        history.as_of(NOW, trusted_anchor=stale)


# --- F-5 hostile tests: canonical economic entity mapping -------------------


class _Verifier:
    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed

    def verify_entity(self, entity_id: str) -> bool:
        return entity_id in self._allowed


def test_f5_registry_verifier(tmp_path):
    # Unmapped entity rejected
    history = _make_history(tmp_path)
    verifier = _Verifier({"entity-1"})
    with pytest.raises(ValueError, match="not verified by Registry"):
        history.append(snapshot(2), registry=verifier)
    # Mapped entity accepted
    history2 = _make_history(tmp_path / "h2")
    verifier2 = _Verifier({"entity-1", "entity-2"})
    history2.append(snapshot(1), registry=verifier2)
    history2.append(snapshot(2), registry=verifier2)
    trusted = anchor(history2)
    result = history2.as_of(NOW, trusted_anchor=trusted, verify_attestation=lambda *_: False)
    assert len(result) == 2
    # Forged registry reference rejected
    history3 = _make_history(tmp_path / "h3")
    with pytest.raises(ValueError, match="source provenance required"):
        history3.append(replace(snapshot(1), registry_reference=""))


def test_f5_registry_multi_chain(tmp_path):
    history = _make_history(tmp_path)
    verifier = _Verifier(set())

    def _accept_all(entity_id: str) -> bool:
        return True

    verifier.verify_entity = _accept_all  # type: ignore[method-assign]
    # Multi-chain: same entity on different chains
    history.append(
        replace(
            snapshot(1),
            canonical_entity_id="usd-coin",
            project=ProjectValidationTarget("usd-coin", "USD Coin", "other", {}),
            registry_reference="registry:usdc-eth",
        ),
        registry=verifier,
    )
    history.append(
        replace(
            snapshot(2),
            canonical_entity_id="usd-coin",
            project=ProjectValidationTarget("usd-coin", "USD Coin", "other", {}),
            registry_reference="registry:usdc-polygon",
        ),
        registry=verifier,
    )
    trusted = anchor(history)
    result = history.as_of(NOW, trusted_anchor=trusted, verify_attestation=lambda *_: False)
    assert len(result) == 2
    # Wrapped token representations
    history2 = _make_history(tmp_path / "h2")
    verifier2 = _Verifier({"weth-ethereum", "weth-polygon"})
    history2.append(
        replace(
            snapshot(1),
            canonical_entity_id="weth-ethereum",
            project=ProjectValidationTarget("weth-ethereum", "WETH", "other", {}),
            registry_reference="registry:weth-eth",
        ),
        registry=verifier2,
    )
    history2.append(
        replace(
            snapshot(2),
            canonical_entity_id="weth-polygon",
            project=ProjectValidationTarget("weth-polygon", "WETH", "other", {}),
            registry_reference="registry:weth-poly",
        ),
        registry=verifier2,
    )
    trusted2 = anchor(history2)
    result2 = history2.as_of(NOW, trusted_anchor=trusted2, verify_attestation=lambda *_: False)
    assert len(result2) == 2
    # Conflicting mappings rejected
    history3 = _make_history(tmp_path / "h3")
    verifier3 = _Verifier({"entity-1"})
    history3.append(snapshot(1), registry=verifier3)
    with pytest.raises(ValueError, match="not verified by Registry"):
        history3.append(snapshot(2), registry=verifier3)


# --- F-2 integration tests: GitHubAnchorReader with GitLedgerStore ----------


class _FakeLedgerStore:
    """Test double implementing GitLedgerStore.read_files() return structure.

    read_files(ref, names) -> (head, [(commit_sha, {filename: file_bytes}), ...])
    """

    def __init__(self, head: str | None, entries: list[tuple[str, dict[str, bytes]]]) -> None:
        self._head = head
        self._entries = entries

    def read_files(self, ref: str, names: frozenset[str]):
        return self._head, self._entries


def _make_entries(anchor_text: str) -> list[tuple[str, dict[str, bytes]]]:
    return [("abc123", {"membership-anchor.json": anchor_text.encode("utf-8")})]


def _intact_ruleset() -> dict:
    return {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }


def _intact_branch_rules() -> list[dict]:
    return [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]


def _make_pin() -> MembershipAnchorPin:
    return MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")


def _make_reader(store, ruleset=None, branch_rules=None):
    return GitHubAnchorReader(
        REPO,
        store=store,
        pin=_make_pin(),
        ruleset=ruleset or _intact_ruleset(),
        branch_rules=branch_rules if branch_rules is not None else _intact_branch_rules(),
    )


def test_f2_github_anchor_reader():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # Missing anchor
    reader = _make_reader(_FakeLedgerStore("head", []))
    with pytest.raises(ValueError, match="external anchor unavailable"):
        reader.read_signed_head(REPO)
    # Unexpected file layout
    reader2 = _make_reader(_FakeLedgerStore("head", [("abc123", "not-a-dict")]))
    with pytest.raises(ValueError, match="unexpected file layout"):
        reader2.read_signed_head(REPO)
    # Mismatched repository
    reader3 = _make_reader(_FakeLedgerStore("head", _make_entries("{}")))
    with pytest.raises(ValueError, match="anchor repository mismatch"):
        reader3.read_signed_head("other/repository")
    # Unprotected reference
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}],
    }
    reader4 = GitHubAnchorReader(
        REPO, store=_FakeLedgerStore("head", _make_entries("{}")), pin=_make_pin(), ruleset=ruleset, branch_rules=None
    )
    with pytest.raises(ValueError, match="rules weakened"):
        reader4.read_signed_head(REPO)
    # Valid anchor
    anchor_key = Ed25519PrivateKey.generate()
    anchor_payload = {"repository": REPO, "sequence": 1, "digest": "0" * 64}
    signed_anchor = signed(anchor_key, ANCHOR_DOMAIN, anchor_payload)
    reader5 = _make_reader(_FakeLedgerStore("head", _make_entries(signed_anchor)))
    assert reader5.read_signed_head(REPO) == signed_anchor
    # Malformed anchor
    reader6 = _make_reader(_FakeLedgerStore("head", _make_entries("not valid json")))
    assert reader6.read_signed_head(REPO) == "not valid json"


# --- F-3 integration tests: TrustedIngress service --------------------------


class _FakeClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def __call__(self) -> datetime:
        return self._value


def test_f3_trusted_ingress_assigns_timestamp(tmp_path):
    history = _make_history(tmp_path)
    clock = _FakeClock(NOW)
    ingress = TrustedIngress(history, clock=clock)
    result = ingress.ingest(snapshot())
    assert result is not None
    # Ingestion timestamp is set by the trusted clock, not the caller.
    with sqlite3.connect(history.path) as conn:
        row = conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()
        content = json.loads(row[0])
        assert content["ingested_at"] == NOW.isoformat()


def test_f3_trusted_ingress_caller_cannot_backdate(tmp_path):
    history = _make_history(tmp_path)
    clock = _FakeClock(NOW)
    ingress = TrustedIngress(history, clock=clock)
    # Caller sets known_at after ingested_at; ingested_at is still the trusted clock (= NOW).
    # known_at must be <= ingested_at (= NOW) for the append to succeed.
    late_known = NOW + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot be known before trusted ingestion"):
        ingress.ingest(snapshot(known_at=late_known))


# --- F-5 integration tests: RegistryIdentityVerifier ------------------------


def _setup_registry_candidate(tmp_path, outcome="exact", confidence=1.0, candidate_id="entity-1"):
    """Helper to create a Registry candidate with the given identity outcome."""
    from hunter.discovery.models import CandidateIdentifier, CandidateIdentity, CandidateRecord
    from hunter.discovery.repository import CandidateRegistryRepository

    reg_path = tmp_path / "registry.sqlite"
    repository = CandidateRegistryRepository(reg_path)
    candidate = CandidateRecord(
        candidate_id=candidate_id,
        slug=candidate_id,
        name="Entity One",
        sector="other",
        primary_chain=None,
        candidate_type="token",
        symbol=None,
        lifecycle_status="identified",
        discovery_source="seed",
        first_seen_at=NOW,
        last_seen_at=NOW,
        confidence=confidence,
        identifiers=(
            CandidateIdentifier(
                candidate_id=candidate_id,
                namespace="hunter_project",
                value=candidate_id,
                source="registry",
            ),
        ),
    )
    repository.upsert_candidate(candidate)
    repository.save_identity_results(
        (
            CandidateIdentity(
                candidate_id=candidate_id,
                outcome=outcome,
                confidence=confidence,
                evidence_ids=(),
                reason=f"{outcome} identity",
            ),
        )
    )
    return repository


def test_f5_registry_identity_verifier(tmp_path):
    from hunter.discovery.repository import CandidateRegistryRepository

    # Empty registry: nothing is verified.
    empty_repo = CandidateRegistryRepository(tmp_path / "empty.sqlite")
    assert not RegistryIdentityVerifier(empty_repo).verify_entity("entity-1")
    # Exact identity: verified.
    repo = _setup_registry_candidate(tmp_path, outcome="exact")
    assert RegistryIdentityVerifier(repo).verify_entity("entity-1")
    # Probable identity: not eligible.
    repo2 = _setup_registry_candidate(tmp_path / "probable", outcome="probable", confidence=0.85)
    assert not RegistryIdentityVerifier(repo2).verify_entity("entity-1")
