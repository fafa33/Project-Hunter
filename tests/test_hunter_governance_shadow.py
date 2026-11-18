from pathlib import Path

import hunter_governance_shadow as shadow


def test_shadow_projection_never_claims_semantic_parity():
    p = shadow.project(
        {"head_sha": "abc", "draft": True, "mergeable": None, "checks": [], "reviews": [], "statuses": []}
    )
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


def test_shadow_output_path_is_not_cli_controlled():
    text = Path("scripts/hunter_governance_shadow.py").read_text()
    assert 'add_argument("--output"' not in text
    assert 'workspace / "shadow" / "observation.json"' in text


def test_shadow_workflow_does_not_install_dependencies():
    text = Path(".github/workflows/hunter-governance-shadow.yml").read_text()
    assert "pip install" not in text


def test_non_draft_live_shadow_records_exact_head_parity(monkeypatch):
    snap = {
        "pr_number": 756,
        "head_sha": "a" * 40,
        "draft": False,
        "mergeable": True,
        "checks": [],
        "reviews": [],
        "statuses": [{"id": 7, "context": shadow.readiness.CONTEXT, "state": "success"}],
    }
    monkeypatch.setattr(
        shadow.readiness, "decide", lambda _pr: ("a" * 40, shadow.readiness.Decision("success", "ready"))
    )
    monkeypatch.setattr(shadow, "get_json", lambda *_args: {"head": {"sha": "a" * 40}})
    item = shadow.compare_live_readiness("owner/repo", "token", 756, snap)
    assert item["semantic_state"] == "COMPARED"
    assert item["parity"] is True
    assert item["false_ready"] is False
    assert item["missed_blocker"] is False


def test_non_draft_live_shadow_flags_false_ready_and_missed_blocker(monkeypatch):
    snap = {
        "pr_number": 756,
        "head_sha": "a" * 40,
        "draft": False,
        "mergeable": True,
        "checks": [],
        "reviews": [],
        "statuses": [{"id": 7, "context": shadow.readiness.CONTEXT, "state": "success"}],
    }
    monkeypatch.setattr(
        shadow.readiness,
        "decide",
        lambda _pr: ("a" * 40, shadow.readiness.Decision("failure", "Unresolved review threads remain: 1.")),
    )
    monkeypatch.setattr(shadow, "get_json", lambda *_args: {"head": {"sha": "a" * 40}})
    item = shadow.compare_live_readiness("owner/repo", "token", 756, snap)
    assert item["parity"] is False
    assert item["false_ready"] is True
    assert item["missed_blocker"] is True


def test_non_draft_live_shadow_refuses_cross_head_comparison(monkeypatch):
    snap = {
        "pr_number": 756,
        "head_sha": "a" * 40,
        "draft": False,
        "mergeable": True,
        "checks": [],
        "reviews": [],
        "statuses": [{"id": 7, "context": shadow.readiness.CONTEXT, "state": "success"}],
    }
    monkeypatch.setattr(
        shadow.readiness, "decide", lambda _pr: ("b" * 40, shadow.readiness.Decision("success", "ready"))
    )
    item = shadow.compare_live_readiness("owner/repo", "token", 756, snap)
    assert item["semantic_state"] == "UNKNOWN"
    assert item["reason"] == "head changed during shadow comparison"


def test_non_draft_live_shadow_rechecks_head_after_successor_evaluation(monkeypatch):
    snap = {
        "pr_number": 756,
        "head_sha": "a" * 40,
        "draft": False,
        "mergeable": True,
        "checks": [],
        "reviews": [],
        "statuses": [{"id": 7, "context": shadow.readiness.CONTEXT, "state": "success"}],
    }
    monkeypatch.setattr(
        shadow.readiness, "decide", lambda _pr: ("a" * 40, shadow.readiness.Decision("success", "ready"))
    )
    monkeypatch.setattr(shadow, "get_json", lambda *_args: {"head": {"sha": "b" * 40}})
    item = shadow.compare_live_readiness("owner/repo", "token", 756, snap)
    assert item["semantic_state"] == "UNKNOWN"
    assert item["reason"] == "head changed during shadow comparison"
    assert item["observed_live_head"] == "b" * 40
