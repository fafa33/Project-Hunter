from __future__ import annotations

import hunter_writer_commit_finalizer as finalizer


def test_no_judgement_is_nonblocking(monkeypatch):
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main() == 0


def test_partial_judgement_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("HUNTER_REVIEW_JUDGEMENT", "judgement.json")
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main() == 2


def test_commit_hook_uses_shared_finalizer():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hook = (root / ".githooks/pre-commit").read_text(encoding="utf-8")
    assert "scripts/hunter_writer_commit_finalizer.py" in hook
    assert "hunter_pre_ready_review as review" in (root / "scripts/hunter_writer_commit_finalizer.py").read_text(
        encoding="utf-8"
    )
