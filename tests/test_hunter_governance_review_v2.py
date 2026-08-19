from __future__ import annotations

import hunter_governance_review_v2 as core


def _pr(mergeable: bool | None) -> dict:
    return {"state": "open", "mergeable": mergeable, "head": {"sha": "b" * 40}}


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
