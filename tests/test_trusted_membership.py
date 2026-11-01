import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.market_validation.membership_history import HistoricalMembershipSnapshot, ImmutableMembershipHistory
from hunter.market_validation.models import ProjectValidationTarget
from hunter.market_validation.trusted_membership import (
    ANCHOR_DOMAIN,
    EVIDENCE_DOMAIN,
    PinnedMembershipTrust,
    canonical_bytes,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
REPO = "fafa33/Project-Hunter"


def signed(private, domain, payload):
    return json.dumps({"payload": payload, "signature": private.sign(domain + canonical_bytes(payload)).hex()})


def fixture(tmp_path):
    evidence_key, anchor_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    trust = PinnedMembershipTrust(REPO, evidence_key.public_key(), anchor_key.public_key())
    history = ImmutableMembershipHistory(tmp_path / "history.sqlite")
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
                ingested_at=NOW,
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
