from pathlib import Path

import hunter_governance_shadow as shadow


def test_shadow_projection_never_claims_semantic_parity():
    p = shadow.project({"head_sha": "abc", "draft": True, "mergeable": None, "checks": [], "reviews": [], "statuses": []})
    assert set(p) == set(shadow.DOMAINS)


def test_shadow_workflow_has_no_write_permission():
    text = Path(".github/workflows/hunter-governance-shadow.yml").read_text()
    assert ": write" not in text
    assert "statuses: read" in text


def test_shadow_script_has_no_mutating_rest_method():
    text = Path("scripts/hunter_governance_shadow.py").read_text()
    assert 'method="POST"' not in text
    assert 'method="PATCH"' not in text
    assert 'method="PUT"' not in text
    assert 'method="DELETE"' not in text


def test_draft_merge_readiness_can_compare_real_legacy_state(monkeypatch):
    monkeypatch.setattr(shadow.readiness, "REVIEWER_DISPOSITIONS_PATH", Path("/nonexistent"))
    snap = {
        "head_sha": "abc",
        "draft": True,
        "mergeable": True,
        "checks": [],
        "reviews": [],
        "statuses": [{"id": 9, "context": shadow.readiness.CONTEXT, "state": "pending"}],
    }
    item = shadow.project(snap)["merge-readiness"]
    assert item["semantic_state"] == "COMPARED"
    assert item["legacy_state"] == "pending"
    assert item["successor_state"] == "pending"
    assert item["parity"] is True


def test_non_draft_readiness_stays_unknown_without_complete_authority_snapshot():
    snap = {"head_sha": "abc", "draft": False, "mergeable": True, "checks": [], "reviews": [], "statuses": []}
    assert shadow.project(snap)["merge-readiness"]["semantic_state"] == "UNKNOWN"
