"""Writer pre-commit review finalization: staged-content binding and fail-closed staleness.

Issue #585 regression coverage: a remediation commit must never silently publish
a stale exact-head review request, the committed request's own claims are never
reused as a judgement for changed staged code, unstaged working-tree edits to
the review artifact are never read or staged, and a governed commit without a
freshly substantiated trusted judgement fails closed while ordinary commits
stay non-blocking.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import hunter_pre_ready_review as review
import hunter_writer_commit_finalizer as finalizer
from review_request_fixture import ISSUE, JUDGEMENT, REGISTRY


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


def test_stale_request_fails_closed_without_a_fresh_judgement(tmp_path, monkeypatch, capsys):
    """Test 1: a remediation commit must not silently publish a stale request.

    The committed request's own claims are never reused as a judgement for
    changed staged code: without a freshly substantiated trusted judgement the
    commit fails closed and the staged request is left exactly as committed.
    """

    _governed_repo(tmp_path, monkeypatch)
    before = _committed_document(tmp_path)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 2
    assert "HUNTER_REVIEW_JUDGEMENT" in capsys.readouterr().err
    after = _committed_document(tmp_path)
    assert after["review_id"] == before["review_id"]
    bound = {item["path"]: item["blob_sha"] for item in after["claims"]["review_target"]}
    assert bound["src/module.py"] == _git(tmp_path, "rev-parse", "HEAD:src/module.py")
    # Nothing was re-minted or staged beyond the writer's own staged change.
    assert _git(tmp_path, "diff", "--cached", "--name-only") == "src/module.py"


def test_committed_request_claims_are_never_reused_for_changed_staged_code(tmp_path, monkeypatch):
    """The auto-refresh path is gone: no judgement can be synthesized from old claims."""

    _governed_repo(tmp_path, monkeypatch)
    before = _committed_document(tmp_path)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 2
    after = _committed_document(tmp_path)
    # Still the committed request itself, never a regenerated one.
    assert after["claims"] == before["claims"]
    assert after["review_request"]["schema"] == "hunter.review-request.v1"


def test_unstaged_review_edits_are_never_read_or_staged(tmp_path, monkeypatch):
    """Test 2: the staleness check reads the index's git blob, not the working tree."""

    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    path = tmp_path / review.REVIEW_RELATIVE_PATH
    document = _committed_document(tmp_path)
    document["unstaged_injection"] = "attacker-supplied claims"
    path.write_text(json.dumps(document))
    assert finalizer.main(tmp_path) == 2
    # The unstaged edit was never consumed: the finalizer staged nothing of it.
    assert _git(tmp_path, "diff", "--cached", "--name-only") == "src/module.py"
    status = subprocess.check_output(("git", "status", "--porcelain"), cwd=tmp_path, text=True).splitlines()
    assert [line for line in status if ".hunter/" in line] == [" M .hunter/pre-ready-hostile-review.json"]
    staged = json.loads(_git(tmp_path, "show", f":{review.REVIEW_RELATIVE_PATH}"))
    assert "unstaged_injection" not in staged


def test_refresh_binds_exact_staged_candidate_content(tmp_path, monkeypatch):
    """Test 3: a fresh judgement binds the request digest to the exact staged content."""

    _governed_repo(tmp_path, monkeypatch)
    (tmp_path / "src" / "module.py").write_text("remediated\n")
    _git(tmp_path, "add", "src/module.py")
    staged_head = finalizer._staged_review_head(tmp_path)
    base = _git(tmp_path, "merge-base", "HEAD", "origin/main")
    changes = review.local_changes(base, staged_head, cwd=tmp_path)
    assert _committed_document(tmp_path)["claims"]["review_target_digest"] != review.target_digest(changes)
    assert finalizer.main(tmp_path) == 0
    assert _committed_document(tmp_path)["claims"]["review_target_digest"] == review.target_digest(changes)


def test_refreshed_request_carries_no_review_authority(tmp_path, monkeypatch):
    """Test 4: a refresh from a fresh judgement never fabricates or carries review authority."""

    _governed_repo(tmp_path, monkeypatch)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 0
    document = _committed_document(tmp_path)
    assert "authority" not in document


def test_current_request_is_passes_without_live_issue_verification(tmp_path, monkeypatch):
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


def test_unusable_staged_request_fails_closed(tmp_path, monkeypatch):
    """A malformed staged request is governed evidence that does not verify; fail closed."""

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
    assert finalizer.main(tmp_path) == 2


def test_inherited_base_review_request_does_not_block_new_candidate(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "README.md").write_text("new issue draft\n")
    _git(tmp_path, "add", "README.md")
    assert finalizer.main(tmp_path) == 0


def test_malformed_staged_request_is_rejected(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / review.REVIEW_RELATIVE_PATH).write_text("{ broken json")
    _git(tmp_path, "add", review.REVIEW_RELATIVE_PATH)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 2


def test_non_object_staged_request_is_rejected(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / review.REVIEW_RELATIVE_PATH).write_text("[]\n")
    _git(tmp_path, "add", review.REVIEW_RELATIVE_PATH)
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 2


def test_current_bounded_correction_request_does_not_deadlock_offline_hook(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    path = tmp_path / review.REVIEW_RELATIVE_PATH
    document = json.loads(path.read_text())
    document["coverage_scope"] = {
        "issue": ISSUE,
        "mode": "bounded_correction",
        "authorized_by": "owner",
        "authorization": "trusted-authorization",
        "reason": "bounded remediation",
        "criteria": ["criterion one"],
    }
    path.write_text(json.dumps(document))
    _git(tmp_path, "add", review.REVIEW_RELATIVE_PATH)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main(tmp_path) == 0


def test_stale_bounded_correction_request_still_fails_closed(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    path = tmp_path / review.REVIEW_RELATIVE_PATH
    document = json.loads(path.read_text())
    document["coverage_scope"] = {
        "issue": ISSUE,
        "mode": "bounded_correction",
        "authorized_by": "owner",
        "authorization": "trusted-authorization",
        "reason": "bounded remediation",
        "criteria": ["criterion one"],
    }
    path.write_text(json.dumps(document))
    _git(tmp_path, "add", review.REVIEW_RELATIVE_PATH)
    (tmp_path / "src" / "module.py").write_text("stale change\n")
    _git(tmp_path, "add", "src/module.py")
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main(tmp_path) == 2


def test_unstaged_malformed_registry_cannot_bypass_governed_freshness(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text("{ malformed unstaged")
    (tmp_path / "src" / "module.py").write_text("remediation v2\n")
    _git(tmp_path, "add", "src/module.py")
    assert finalizer.main(tmp_path) == 2
    assert _git(tmp_path, "show", ":docs/DEFECT_REGISTRY.json").startswith("{")


def test_unstaged_malformed_registry_does_not_block_unchanged_governed_request(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text("{ malformed unstaged")
    assert finalizer.main(tmp_path) == 0


def test_invalid_staged_registry_fails_closed_for_governed_request(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text("{ malformed staged")
    _git(tmp_path, "add", "docs/DEFECT_REGISTRY.json")
    assert finalizer.main(tmp_path) == 2


def test_missing_merge_base_fails_closed_for_governed_candidate(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    (tmp_path / "src" / "module.py").write_text("remediation v2\n")
    _git(tmp_path, "add", "src/module.py")
    _git(tmp_path, "update-ref", "-d", "refs/remotes/origin/main")
    assert finalizer.main(tmp_path) == 2


def test_missing_merge_base_without_governed_request_remains_optional(tmp_path, monkeypatch):
    _governed_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    _git(tmp_path, "rm", "--cached", ".hunter/pre-ready-hostile-review.json", check=False)
    _git(tmp_path, "update-ref", "-d", "refs/remotes/origin/main")
    assert finalizer.main(tmp_path) == 0
