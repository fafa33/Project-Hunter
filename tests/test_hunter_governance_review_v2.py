from __future__ import annotations

from pathlib import Path

import hunter_governance_review_v2 as core


def _pr(mergeable: bool | None) -> dict:
    return {
        "state": "open",
        "mergeable": mergeable,
        "title": "no canonical title or issue identity",
        "body": "no matrix, no readiness declaration, no reaction ceremony",
        "head": {"sha": "b" * 40, "ref": "feature/no-governance-metadata"},
        "base": {"sha": "c" * 40, "ref": "main"},
    }


def test_governance_review_ignores_process_metadata(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "success"


def test_governance_review_fails_real_merge_conflict(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(False))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "failure"
    assert "merge conflicts" in published[0][4]


def test_governance_review_waits_for_unknown_mergeability(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(None))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "pending"


def test_governance_review_skips_non_main_target(monkeypatch):
    published = []
    pr = _pr(True)
    pr["base"]["ref"] = "release"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_workflow_uses_only_trusted_v2_controller_without_bootstrap():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")

    assert "Checkout installation controller" not in workflow
    assert "installation-engine" not in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'PR_NUMBER} = "283"' not in workflow
    assert "python -m hunter_governance_review" not in workflow
    assert "bootstrap" not in workflow.lower()
    assert "hunter_governance_review_v2.py" in workflow


def test_reconcile_continues_after_one_pr_failure_and_drops_checkout_credentials():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-reconcile.yml"
    ).read_text(encoding="utf-8")

    assert "persist-credentials: false" in workflow
    assert "failures=0" in workflow
    assert "if ! python scripts/hunter_governance_review_v2.py" in workflow
    assert "failures=1" in workflow
    assert 'exit "${failures}"' in workflow
