"""Writer pre-commit review finalization: staged-content binding and automatic refresh.

Issue #585 regression coverage: a remediation commit must never silently publish
a stale exact-head review request, a refresh must stay bound to the real
governing Issue and the exact staged content, and it must never fabricate or
mutate anything beyond the request artifact itself.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import hunter_pre_ready_review as review
import hunter_writer_commit_finalizer as finalizer

ISSUE = "585"
CRITERION = "Required proof"
JUDGEMENT = {
    "acceptance_criteria": [
        {"id": "AC-1", "criterion": CRITERION, "evidence": "verified against the change", "verdict": "satisfied"}
    ],
    "defect_families": [{"family": "FAM-SRC", "evidence": "adversarial sweep complete", "outcome": "clear"}],
    "findings": [],
    "adversarial_dimensions": review.REQUIRED_ADVERSARIAL_DIMENSIONS,
}
REGISTRY = {
    "version": 1,
    "families": [
        {
            "id": "FAM-SRC",
            "title": "source-scope",
            "invariant": "test scope",
            "applicability": {"changed_paths": ["src/"], "rationale": "test"},
            "prevention": {"mechanism": "test"},
        }
    ],
}


def _git(tmp_path, *args, check=True):
    result = subprocess.run(("git", *args), cwd=tmp_path, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def _fake_issue_api(monkeypatch, calls=None, issue_payload=None):
    """Answer only the live governing-Issue verification; every other command is real."""

    def fake_run(command, **kwargs):
        if command and command[0] == "gh":
            if calls is not None:
                calls.append(tuple(command))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(issue_payload or {"number": 585, "body": "## Acceptance Criteria\n- Required proof"}),
            )
        return real_run(command, **kwargs)

    real_run = subprocess.run
    monkeypatch.setattr(review.subprocess, "run", fake_run)
    monkeypatch.setattr(
        review,
        "_trusted_judgement_path",
        lambda value: _resolve_judgement(value),
    )


def _resolve_judgement(value):
    path = review.Path(value).resolve(strict=True)
    if not path.is_file():
        raise ValueError("review judgement must be a regular file inside the authorized checkout")
    return path


def _governed_repo(tmp_path, monkeypatch, calls=None):
    """One governed candidate at base, carrying a committed current review request."""

    _fake_issue_api(monkeypatch, calls=calls)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Hunter Test")
    _git(tmp_path, "config", "user.email", "hunter@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text(json.dumps(REGISTRY))
    _git(tmp_path, "add", "docs/DEFECT_REGISTRY.json")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", base)
    # The governed candidate change is staged on top of the base, exactly as the
    # writer finalizer observes it inside a real pre-commit hook.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("v1\n")
    _git(tmp_path, "add", "src/module.py")
    judgement_path = tmp_path / "review-judgement.json"
    judgement_path.write_text(json.dumps(JUDGEMENT))
    monkeypatch.setenv("HUNTER_REVIEW_JUDGEMENT", str(judgement_path))
    monkeypatch.setenv("HUNTER_GOVERNING_ISSUE", ISSUE)
    assert finalizer.main(tmp_path) == 0
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "governed candidate")
    return base, judgement_path


def _committed_document(tmp_path):
    return json.loads((tmp_path / review.REVIEW_RELATIVE_PATH).read_text(encoding="utf-8"))


def test_no_judgement_and_no_request_is_nonblocking(tmp_path, monkeypatch):
    """An ordinary commit without a governed candidate is never blocked (optional review)."""

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Hunter Test")
    _git(tmp_path, "config", "user.email", "hunter@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text(json.dumps(REGISTRY))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main(tmp_path) == 0


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


def test_staged_snapshot_binds_index_not_worktree(tmp_path):
    """A remediation staged after HEAD must be visible to the review finalizer."""

    def git(*args):
        return subprocess.check_output(("git", *args), cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Hunter Test")
    git("config", "user.email", "hunter@example.invalid")
    path = tmp_path / "change.txt"
    path.write_text("base\n")
    git("add", "change.txt")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    path.write_text("staged remediation\n")
    git("add", "change.txt")
    path.write_text("unstaged later edit\n")
    staged_head = finalizer._staged_review_head(tmp_path)
    assert git("rev-parse", "HEAD") == base
    assert git("show", f"{staged_head}:change.txt") == "staged remediation"
    assert path.read_text() == "unstaged later edit\n"
    assert git("diff", "--name-only", base, staged_head) == "change.txt"


def test_staged_snapshot_repeat_does_not_change_index(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(("git", *args), cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Hunter Test")
    git("config", "user.email", "hunter@example.invalid")
    (tmp_path / "a").write_text("one")
    git("add", "a")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
    (tmp_path / "a").write_text("two")
    git("add", "a")
    before = git("write-tree")
    first = finalizer._staged_review_head(tmp_path)
    second = finalizer._staged_review_head(tmp_path)
    assert git("write-tree") == before
    assert git("show", f"{first}:a") == git("show", f"{second}:a") == "two"


def test_stale_request_is_refreshed_after_remediation_without_new_judgement(tmp_path, monkeypatch):
    """Test 1: a remediation commit must not silently publish a stale request."""

    _governed_repo(tmp_path, monkeypatch)
    before = _committed_document(tmp_path)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 0
    after = _committed_document(tmp_path)
    bound = {item["path"]: item["blob_sha"] for item in after["claims"]["review_target"]}
    staged_blob = _git(tmp_path, "rev-parse", f"{finalizer._staged_review_head(tmp_path)}:src/module.py")
    assert bound["src/module.py"] == staged_blob
    assert after["review_id"] != before["review_id"]
    # Still a request: refreshed evidence is review-request state, never review authority.
    assert after["review_request"]["schema"] == "hunter.review-request.v1"


def test_refresh_binds_exact_staged_candidate_content(tmp_path, monkeypatch):
    """Test 3: the refreshed request digest is the digest of the exact staged content."""

    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("remediated\n")
    _git(tmp_path, "add", "src/module.py")
    staged_head = finalizer._staged_review_head(tmp_path)
    base = _git(tmp_path, "merge-base", "HEAD", "origin/main")
    changes = review.local_changes(base, staged_head, cwd=tmp_path)
    assert _committed_document(tmp_path)["claims"]["review_target_digest"] != review.target_digest(changes)
    assert finalizer.main(tmp_path) == 0
    assert _committed_document(tmp_path)["claims"]["review_target_digest"] == review.target_digest(changes)


def test_refreshed_request_carries_no_review_authority(tmp_path, monkeypatch):
    """Test 4: an automatic refresh never fabricates or carries review authority."""

    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 0
    document = _committed_document(tmp_path)
    assert "authority" not in document


def test_auto_refresh_is_idempotent_and_needs_no_live_issue_when_current(tmp_path, monkeypatch):
    """A current request short-circuits before any live governing-Issue verification."""

    calls: list[tuple] = []
    _governed_repo(tmp_path, monkeypatch, calls=calls)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert calls, "the governing Issue must be verified live when a request is minted"
    calls.clear()
    assert finalizer.main(tmp_path) == 0
    assert not calls, "an already-current request must not consume live verification"


def test_refresh_only_mutates_the_review_request_artifact(tmp_path, monkeypatch):
    """Test 10: a refresh never mutates the publisher's validated candidate content."""

    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    head_before = _git(tmp_path, "rev-parse", "HEAD")
    assert finalizer.main(tmp_path) == 0
    assert _git(tmp_path, "rev-parse", "HEAD") == head_before
    assert sorted(_git(tmp_path, "diff", "--cached", "--name-only").splitlines()) == [
        ".hunter/pre-ready-hostile-review.json",
        "src/module.py",
    ]
    assert (tmp_path / "src" / "module.py").read_text() == "v2\n"


def test_wrong_governing_issue_is_refused_not_borrowed(tmp_path, monkeypatch):
    """Test 2: the writer cannot bind the request to an Issue identity it cannot verify."""

    _governed_repo(tmp_path, monkeypatch)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    judgement_path = tmp_path / "other.json"
    judgement_path.write_text(json.dumps(JUDGEMENT))
    monkeypatch.setenv("HUNTER_REVIEW_JUDGEMENT", str(judgement_path))
    monkeypatch.setenv("HUNTER_GOVERNING_ISSUE", "999")
    assert finalizer.main(tmp_path) == 2


def test_unusable_committed_request_is_never_auto_refreshed(tmp_path, monkeypatch):
    """A malformed committed request cannot become an automatic judgement source."""

    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    path = tmp_path / review.REVIEW_RELATIVE_PATH
    document = _committed_document(tmp_path)
    del document["claims"]["acceptance_criteria"]
    path.write_text(json.dumps(document))
    _git(tmp_path, "add", ".hunter/pre-ready-hostile-review.json")
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 0
