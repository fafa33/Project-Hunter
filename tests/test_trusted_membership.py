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
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
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


def test_external_anchor_detects_truncation(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        conn.execute("DROP TRIGGER membership_history_no_delete")
        conn.execute("DELETE FROM membership_history WHERE sequence=50")
    with pytest.raises(ValueError, match="anchor mismatch"):
        run_shadow(history, trust, anchor)


def test_unsigned_or_forged_anchor_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
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


def test_f1_modified_sector_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        row = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])
        att = row["upstream_attestation"]
        modified = dict(row)
        modified["project"] = dict(row["project"], sector="defi")
        modified_digest = _content_digest_without_attestation(modified)
    # Old attestation signed over old digest must not verify against modified content.
    assert not trust.verify_evidence(att, modified_digest)
    # Old attestation still verifies against the original digest.
    assert trust.verify_evidence(att, _content_digest_without_attestation(row))


def test_f1_modified_lifecycle_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        row = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])
        att = row["upstream_attestation"]
        modified = dict(row)
        modified["lifecycle_status"] = "rejected"
        modified_digest = _content_digest_without_attestation(modified)
    assert not trust.verify_evidence(att, modified_digest)


def test_f1_modified_timestamps_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        row = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])
        att = row["upstream_attestation"]
        modified = dict(row)
        modified["known_at"] = "2020-01-01T00:00:00+00:00"
        modified_digest = _content_digest_without_attestation(modified)
    assert not trust.verify_evidence(att, modified_digest)


def test_f1_modified_source_digest_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        row = json.loads(conn.execute("SELECT content FROM membership_history LIMIT 1").fetchone()[0])
        att = row["upstream_attestation"]
        modified = dict(row)
        modified["source_digest"] = "sha256:forged"
        modified_digest = _content_digest_without_attestation(modified)
    assert not trust.verify_evidence(att, modified_digest)


def test_f1_wrong_repository_anchor_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    claim = json.loads(anchor)
    claim["payload"]["repository"] = "other/repository"
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        run_shadow(history, trust, json.dumps(claim))


def test_f1_replayed_signature_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    with sqlite3.connect(history.path) as conn:
        rows = conn.execute("SELECT content FROM membership_history ORDER BY sequence LIMIT 2").fetchall()
        att1 = json.loads(rows[0][0])["upstream_attestation"]
    # Replaying candidate-1's attestation for candidate-2's content must fail.
    digest2 = _content_digest_without_attestation(json.loads(rows[1][0]))
    assert not trust.verify_evidence(att1, digest2)


def test_f1_malformed_signature_rejected(tmp_path):
    history, trust, anchor = fixture(tmp_path)
    assert not trust.verify_evidence("not-json", "0" * 64)
    assert not trust.verify_evidence(json.dumps({"payload": {}, "signature": "zzz"}), "0" * 64)


# --- F-2 hostile tests: anchor pin and ruleset verification ----------------


def test_f2_anchor_pin_rejects_wrong_ruleset_id():
    pin = MembershipAnchorPin(ruleset_id=99999, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="missing or replaced"):
        pin.verify(
            {"id": 24526712, "enforcement": "active", "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
            None,
        )


def test_f2_anchor_pin_rejects_inactive_ruleset():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="not active"):
        pin.verify(
            {"id": 24526712, "enforcement": "inactive", "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
            None,
        )


def test_f2_anchor_pin_rejects_weakened_rules():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="rules weakened"):
        pin.verify(
            {
                "id": 24526712,
                "enforcement": "active",
                "updated_at": "2026-10-06T20:52:31.773Z",
                "rules": [{"type": "deletion"}],
            },
            None,
        )


def test_f2_anchor_pin_rejects_stale_updated_at():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="modified since it was pinned"):
        pin.verify(
            {
                "id": 24526712,
                "enforcement": "active",
                "updated_at": "2026-10-07T00:00:00.000Z",
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            },
            None,
        )


def test_f2_anchor_pin_rejects_ruleset_not_covering_ref():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    with pytest.raises(ValueError, match="does not cover this ref"):
        pin.verify(
            {
                "id": 24526712,
                "enforcement": "active",
                "updated_at": "2026-10-06T20:52:31.773Z",
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            },
            [{"type": "deletion", "ruleset_id": 99999}],
        )


def test_f2_anchor_pin_accepts_intact_ruleset():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    pin.verify(
        {
            "id": 24526712,
            "enforcement": "active",
            "updated_at": "2026-10-06T20:52:31.773Z",
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        },
        [{"type": "deletion", "ruleset_id": 24526712}, {"type": "non_fast_forward", "ruleset_id": 24526712}],
    )  # should not raise


# --- F-3 hostile tests: trusted ingress and time ---------------------------


def test_f3_future_timestamp_poisoning_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
    # known_at in the future is rejected because ingested_at = now < known_at.
    late_known = datetime(2099, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="cannot be known before trusted ingestion"):
        history.append(snapshot(known_at=late_known))


def test_f3_forged_ingestion_timestamp_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
    # Caller cannot supply ingested_at; known_at after now is rejected because
    # ingested_at is assigned by the trusted service clock (= now).
    late_known = NOW + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot be known before trusted ingestion"):
        history.append(snapshot(known_at=late_known))


def test_f3_evidence_after_cutoff_not_visible(tmp_path):
    history = ImmutableMembershipHistory(
        tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW + timedelta(days=1)
    )
    history.append(snapshot())
    # Evidence ingested after the cutoff must not be visible at that cutoff.
    assert history.as_of(NOW, trusted_anchor=anchor(history)) == ()
    # At the future ingestion time, the evidence becomes visible.
    assert len(history.as_of(NOW + timedelta(days=1), trusted_anchor=anchor(history))) == 1


# --- F-2 hostile tests: whole-database rollback and stale anchor ----------


def test_f2_whole_database_rollback_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
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
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
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


def test_f5_registry_verifier_rejects_unmapped_entity(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)

    verifier = _Verifier({"entity-1"})  # only entity-1 is in the Registry

    with pytest.raises(ValueError, match="not verified by Registry"):
        history.append(snapshot(2), registry=verifier)


def test_f5_registry_verifier_accepts_mapped_entity(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)

    verifier = _Verifier({"entity-1", "entity-2"})

    history.append(snapshot(1), registry=verifier)
    history.append(snapshot(2), registry=verifier)
    trusted = anchor(history)
    result = history.as_of(NOW, trusted_anchor=trusted, verify_attestation=lambda *_: False)
    assert len(result) == 2


def test_f5_same_asset_multi_chain_deduplicates_to_one_entity(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)

    verifier = _Verifier(set())  # all entities verified by Registry (accept everything)

    # Override verify_entity to accept all
    def _accept_all(entity_id: str) -> bool:
        return True

    verifier.verify_entity = _accept_all  # type: ignore[method-assign]

    # ERC-20 on Ethereum and the same asset on Polygon map to one economic entity.
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
    # Both rows are stored; deduplication to one economic entity happens at the
    # authority level (universe_membership), not at append time.
    assert len(result) == 2


def test_f5_wrapped_token_representation(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)

    verifier = _Verifier({"weth-ethereum", "weth-polygon"})

    # Wrapped ETH on different chains are distinct chain representations but
    # may map to distinct or the same economic entity depending on the Registry.
    history.append(
        replace(
            snapshot(1),
            canonical_entity_id="weth-ethereum",
            project=ProjectValidationTarget("weth-ethereum", "WETH", "other", {}),
            registry_reference="registry:weth-eth",
        ),
        registry=verifier,
    )
    history.append(
        replace(
            snapshot(2),
            canonical_entity_id="weth-polygon",
            project=ProjectValidationTarget("weth-polygon", "WETH", "other", {}),
            registry_reference="registry:weth-poly",
        ),
        registry=verifier,
    )
    trusted = anchor(history)
    result = history.as_of(NOW, trusted_anchor=trusted, verify_attestation=lambda *_: False)
    assert len(result) == 2


def test_f5_conflicting_identity_mappings_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)

    verifier = _Verifier({"entity-1"})  # Registry says entity-2 is not eligible

    history.append(snapshot(1), registry=verifier)
    with pytest.raises(ValueError, match="not verified by Registry"):
        history.append(snapshot(2), registry=verifier)


def test_f5_forged_registry_reference_rejected(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
    # registry_reference must be non-empty and well-formed; a forged reference
    # cannot be used to claim Registry provenance.
    with pytest.raises(ValueError, match="source provenance required"):
        history.append(
            replace(snapshot(1), registry_reference=""),
        )


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


def test_f2_github_anchor_reader_missing_anchor():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    branch_rules = [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", []),
        pin=pin,
        ruleset=ruleset,
        branch_rules=branch_rules,
    )
    with pytest.raises(ValueError, match="external anchor unavailable"):
        reader.read_signed_head(REPO)


def test_f2_github_anchor_reader_malformed_anchor():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    branch_rules = [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", _make_entries("not valid json")),
        pin=pin,
        ruleset=ruleset,
        branch_rules=branch_rules,
    )
    # Reader returns the raw anchor text; signature verification happens in
    # PinnedMembershipTrust.verify_anchor().
    text = reader.read_signed_head(REPO)
    assert text == "not valid json"
    # The trust adapter rejects the malformed anchor.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hunter.market_validation.trusted_membership import PinnedMembershipTrust

    key = Ed25519PrivateKey.generate()
    trust = PinnedMembershipTrust(REPO, key.public_key(), key.public_key())
    with pytest.raises(ValueError, match="untrusted membership anchor"):
        trust.verify_anchor(text)


def test_f2_github_anchor_reader_unexpected_file_layout():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    branch_rules = [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", [("abc123", "not-a-dict")]),
        pin=pin,
        ruleset=ruleset,
        branch_rules=branch_rules,
    )
    with pytest.raises(ValueError, match="unexpected file layout"):
        reader.read_signed_head(REPO)


def test_f2_github_anchor_reader_mismatched_repository():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    branch_rules = [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", _make_entries("{}")),
        pin=pin,
        ruleset=ruleset,
        branch_rules=branch_rules,
    )
    with pytest.raises(ValueError, match="anchor repository mismatch"):
        reader.read_signed_head("other/repository")


def test_f2_github_anchor_reader_unprotected_reference():
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    # Ruleset missing required rules => fail closed.
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}],  # missing non_fast_forward
    }
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", _make_entries("{}")),
        pin=pin,
        ruleset=ruleset,
        branch_rules=None,
    )
    with pytest.raises(ValueError, match="rules weakened"):
        reader.read_signed_head(REPO)


def test_f2_github_anchor_reader_valid_anchor():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    anchor_key = Ed25519PrivateKey.generate()
    anchor_payload = {"repository": REPO, "sequence": 1, "digest": "0" * 64}
    signed_anchor = signed(anchor_key, ANCHOR_DOMAIN, anchor_payload)
    pin = MembershipAnchorPin(ruleset_id=24526712, updated_at="2026-10-06T20:52:31.773Z")
    ruleset = {
        "id": 24526712,
        "enforcement": "active",
        "updated_at": "2026-10-06T20:52:31.773Z",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    branch_rules = [
        {"type": "deletion", "ruleset_id": 24526712},
        {"type": "non_fast_forward", "ruleset_id": 24526712},
    ]
    reader = GitHubAnchorReader(
        REPO,
        store=_FakeLedgerStore("head", _make_entries(signed_anchor)),
        pin=pin,
        ruleset=ruleset,
        branch_rules=branch_rules,
    )
    result = reader.read_signed_head(REPO)
    assert result == signed_anchor


# --- F-3 integration tests: TrustedIngress service --------------------------


class _FakeClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def __call__(self) -> datetime:
        return self._value


def test_f3_trusted_ingress_assigns_timestamp(tmp_path):
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
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
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite", repository=REPO, clock=lambda: NOW)
    clock = _FakeClock(NOW)
    ingress = TrustedIngress(history, clock=clock)
    # Caller sets known_at after ingested_at; ingested_at is still the trusted clock (= NOW).
    # known_at must be <= ingested_at (= NOW) for the append to succeed.
    late_known = NOW + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot be known before trusted ingestion"):
        ingress.ingest(snapshot(known_at=late_known))


# --- F-5 integration tests: RegistryIdentityVerifier ------------------------


def test_f5_registry_identity_verifier_rejects_unmapped(tmp_path):
    from hunter.discovery.repository import CandidateRegistryRepository

    reg_path = tmp_path / "registry.sqlite"
    repository = CandidateRegistryRepository(reg_path)
    verifier = RegistryIdentityVerifier(repository)
    # Empty registry: nothing is verified.
    assert not verifier.verify_entity("entity-1")


def test_f5_registry_identity_verifier_accepts_exact_identity(tmp_path):

    from hunter.discovery.models import CandidateIdentifier, CandidateRecord
    from hunter.discovery.repository import CandidateRegistryRepository

    reg_path = tmp_path / "registry.sqlite"
    repository = CandidateRegistryRepository(reg_path)
    candidate = CandidateRecord(
        candidate_id="entity-1",
        slug="entity-1",
        name="Entity One",
        sector="other",
        primary_chain=None,
        candidate_type="token",
        symbol=None,
        lifecycle_status="identified",
        discovery_source="seed",
        first_seen_at=NOW,
        last_seen_at=NOW,
        confidence=1.0,
        identifiers=(
            CandidateIdentifier(
                candidate_id="entity-1",
                namespace="hunter_project",
                value="entity-1",
                source="registry",
            ),
        ),
    )
    repository.upsert_candidate(candidate)
    verifier = RegistryIdentityVerifier(repository)
    # Candidate with no identity result yet -> not verified.
    assert not verifier.verify_entity("entity-1")


def test_f5_registry_identity_verifier_accepts_with_exact_outcome(tmp_path):
    from hunter.discovery.models import CandidateIdentifier, CandidateIdentity, CandidateRecord
    from hunter.discovery.repository import CandidateRegistryRepository

    reg_path = tmp_path / "registry.sqlite"
    repository = CandidateRegistryRepository(reg_path)
    candidate = CandidateRecord(
        candidate_id="entity-1",
        slug="entity-1",
        name="Entity One",
        sector="other",
        primary_chain=None,
        candidate_type="token",
        symbol=None,
        lifecycle_status="identified",
        discovery_source="seed",
        first_seen_at=NOW,
        last_seen_at=NOW,
        confidence=1.0,
        identifiers=(
            CandidateIdentifier(
                candidate_id="entity-1",
                namespace="hunter_project",
                value="entity-1",
                source="registry",
            ),
        ),
    )
    repository.upsert_candidate(candidate)
    repository.save_identity_results(
        (
            CandidateIdentity(
                candidate_id="entity-1",
                outcome="exact",
                confidence=1.0,
                evidence_ids=(),
                reason="exact identity",
            ),
        )
    )
    verifier = RegistryIdentityVerifier(repository)
    assert verifier.verify_entity("entity-1")


def test_f5_registry_identity_verifier_rejects_probable_identity(tmp_path):
    from hunter.discovery.models import CandidateIdentifier, CandidateIdentity, CandidateRecord
    from hunter.discovery.repository import CandidateRegistryRepository

    reg_path = tmp_path / "registry.sqlite"
    repository = CandidateRegistryRepository(reg_path)
    candidate = CandidateRecord(
        candidate_id="entity-1",
        slug="entity-1",
        name="Entity One",
        sector="other",
        primary_chain=None,
        candidate_type="token",
        symbol=None,
        lifecycle_status="identified",
        discovery_source="seed",
        first_seen_at=NOW,
        last_seen_at=NOW,
        confidence=1.0,
        identifiers=(
            CandidateIdentifier(
                candidate_id="entity-1",
                namespace="hunter_project",
                value="entity-1",
                source="registry",
            ),
        ),
    )
    repository.upsert_candidate(candidate)
    repository.save_identity_results(
        (
            CandidateIdentity(
                candidate_id="entity-1",
                outcome="probable",
                confidence=0.85,
                evidence_ids=(),
                reason="probable identity",
            ),
        )
    )
    verifier = RegistryIdentityVerifier(repository)
    # probable is not eligible.
    assert not verifier.verify_entity("entity-1")
