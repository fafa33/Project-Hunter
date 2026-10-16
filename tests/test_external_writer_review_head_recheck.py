"""External code writers cannot bypass the trusted exact-head review boundary."""

from __future__ import annotations

import hunter_governance_review_v2 as governance


def _stub_valid_review(monkeypatch, *, changed_head: bool) -> None:
    """Supply immutable review evidence while varying only the live PR head."""
    monkeypatch.setattr(
        governance, "read_head_pre_ready_review", lambda *_: ("present", {"claims": {"issue": "532"}}, None)
    )
    monkeypatch.setattr(governance, "read_pr_refs", lambda *_: (True, "feature", "main", None))
    monkeypatch.setattr(governance, "read_merge_base", lambda *_: (True, "a" * 40, None))
    monkeypatch.setattr(governance, "read_pr_changed_files", lambda *_: (True, (), None))
    monkeypatch.setattr(governance.pre_ready, "load_families", lambda: ((), ""))
    monkeypatch.setattr(governance, "read_issue_acceptance_criteria", lambda *_: ("present", (), ""))
    monkeypatch.setattr(governance.pre_ready, "load_coverage_scopes", lambda: ((), ""))
    monkeypatch.setattr(
        governance.pre_ready,
        "verify_review_request",
        lambda *_a, **_kw: governance.pre_ready.ReviewVerdict("valid", "valid"),
    )
    heads = iter(("c" * 40, "b" * 40 if changed_head else "c" * 40))
    monkeypatch.setattr(governance, "request_json", lambda *_: {"head": {"sha": next(heads)}})


def test_external_writer_stale_head_fails_closed(monkeypatch):
    _stub_valid_review(monkeypatch, changed_head=True)
    valid, reason = governance.valid_current_review_request(
        "fafa33/Project-Hunter", "token", 611, "c" * 40, {"claims": {"issue": "532"}}
    )
    assert not valid
    assert "head changed" in reason


def test_external_writer_exact_head_can_pass(monkeypatch):
    _stub_valid_review(monkeypatch, changed_head=False)
    valid, reason = governance.valid_current_review_request(
        "fafa33/Project-Hunter", "token", 611, "c" * 40, {"claims": {"issue": "532"}}
    )
    assert valid, reason


def test_external_writer_uncommitted_request_rejected(monkeypatch):
    monkeypatch.setattr(governance, "request_json", lambda *_: {"head": {"sha": "c" * 40}})
    monkeypatch.setattr(governance, "read_head_pre_ready_review", lambda *_: ("absent", None, None))
    valid, reason = governance.valid_current_review_request(
        "fafa33/Project-Hunter", "token", 611, "c" * 40, {"claims": {"issue": "532"}}
    )
    assert not valid
    assert "committed evidence mismatch" in reason
