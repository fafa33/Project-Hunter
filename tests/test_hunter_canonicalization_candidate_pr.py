from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path("scripts/hunter_canonicalization_candidate_pr.py")
_MODULE_NAME = "hunter_canonicalization_candidate_pr"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT)
assert _spec and _spec.loader
candidate_pr = importlib.util.module_from_spec(_spec)
# Registering in sys.modules before exec_module matters here (unlike the
# simpler existing hunter_canonicalize_learning_cli test module): this
# script's frozen dataclass, combined with `from __future__ import
# annotations`, needs `sys.modules[cls.__module__]` resolvable while the
# class body executes.
sys.modules[_MODULE_NAME] = candidate_pr
_spec.loader.exec_module(candidate_pr)

REGISTRY = Path("docs/DEFECT_REGISTRY.json")
HEAD = "a" * 40
BASE = "b" * 40


def _observation(
    pr: int,
    classification: str = "confirmed",
    regression_test: str = "test_compute_plan_is_pure_and_deterministic",
) -> dict[str, object]:
    if classification == "false_positive":
        classification = "false-positive"
    family = next(f for f in json.loads(REGISTRY.read_text())["families"] if f["id"] == "DFF-008")
    return {
        "source": "sonar",
        "provider": "sonar",
        "event_id": f"issue-{pr}",
        "source_pr": pr,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "reviewer": "deterministic-fixture",
        "path": "scripts/hunter_knowledge_extraction.py",
        "line": 1,
        "message": "validated recurrence",
        "availability": "available",
        "classification": classification,
        "invariant": family["invariant"] if classification == "confirmed" else "",
        "affected_paths": ["scripts/hunter_knowledge_extraction.py"] if classification == "confirmed" else [],
        "fix_reference": f"PR #{pr} focused remediation" if classification == "confirmed" else "",
        "regression_evidence": (
            # Must resolve to a real pytest target (AST-checked by
            # CanonicalIntegrationAuthority); reuse a real test in this file,
            # matching the same self-referential pattern
            # tests/test_canonicalize_learning_cli.py already uses.
            [f"tests/test_hunter_canonicalization_candidate_pr.py::{regression_test}"]
            if classification == "confirmed"
            else []
        ),
        "claimed_family_id": "DFF-008" if classification == "confirmed" else None,
    }


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def origin_repo(tmp_path: Path) -> Path:
    """A bare local repo seeded with the real registry, used as the git remote."""

    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    origin.mkdir()
    _git(["init", "-q", "--bare", "-b", "main"], cwd=origin)
    _git(["init", "-q", "-b", "main"], cwd=seed)
    _git(["config", "user.email", "fixture@example.invalid"], cwd=seed)
    _git(["config", "user.name", "Fixture"], cwd=seed)
    (seed / "docs").mkdir()
    (seed / "docs" / "DEFECT_REGISTRY.json").write_bytes(REGISTRY.read_bytes())
    (seed / "docs" / "REVIEWER_FINDING_DISPOSITIONS.json").write_bytes(
        Path("docs/REVIEWER_FINDING_DISPOSITIONS.json").read_bytes()
    )
    _git(["add", "."], cwd=seed)
    _git(["commit", "-q", "-m", "seed"], cwd=seed)
    _git(["remote", "add", "origin", str(origin)], cwd=seed)
    _git(["push", "-q", "origin", "main"], cwd=seed)
    return origin


class RecordingRun:
    """Passes real `git` commands through; fakes `gh` so tests need no network/auth."""

    def __init__(self, *, existing_pr: int | None = None, fail_pr_create: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.existing_pr = existing_pr
        self.fail_pr_create = fail_pr_create
        self.pr_create_calls = 0

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        if command[0] == "gh":
            return self._fake_gh(command)
        return subprocess.run(list(command), check=True, capture_output=True, text=True)

    def _fake_gh(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["pr", "list"]:
            stdout = str(self.existing_pr) if self.existing_pr is not None else ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[1:3] == ["pr", "create"]:
            if self.fail_pr_create:
                raise subprocess.CalledProcessError(1, command, output="", stderr="simulated transient gh failure")
            self.pr_create_calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="https://example.invalid/pull/1\n", stderr="")
        raise AssertionError(f"unexpected gh command: {command}")


def test_compute_plan_is_pure_and_deterministic() -> None:
    registry_bytes = REGISTRY.read_bytes()
    observations = [_observation(101)]

    first = candidate_pr.compute_plan(
        pr=101, head=HEAD, base=BASE, observations=observations, origin_registry_bytes=registry_bytes
    )
    second = candidate_pr.compute_plan(
        pr=101, head=HEAD, base=BASE, observations=observations, origin_registry_bytes=registry_bytes
    )

    assert first.changed is True
    assert first == second


def test_compute_plan_noop_for_non_confirmed_observation() -> None:
    registry_bytes = REGISTRY.read_bytes()
    observations = [_observation(102, classification="false_positive")]

    plan = candidate_pr.compute_plan(
        pr=102, head=HEAD, base=BASE, observations=observations, origin_registry_bytes=registry_bytes
    )

    assert plan.changed is False
    assert plan.registry_bytes == registry_bytes


def test_propose_noop_makes_no_git_write_or_pr_calls(origin_repo: Path, tmp_path: Path) -> None:
    recorder = RecordingRun()
    observations = [_observation(201, classification="false_positive")]

    message = candidate_pr.propose(
        pr=201,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=recorder,
    )

    assert "NO-OP" in message
    assert not any("push" in call for call in recorder.calls)
    assert not any(call[0] == "gh" for call in recorder.calls)


def test_propose_opens_draft_pr_for_confirmed_finding(origin_repo: Path, tmp_path: Path) -> None:
    recorder = RecordingRun(existing_pr=None)
    observations = [_observation(301)]

    message = candidate_pr.propose(
        pr=301,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=recorder,
    )

    assert "OPENED" in message
    assert recorder.pr_create_calls == 1

    push_calls = [call for call in recorder.calls if call[:1] == ["git"] and "push" in call]
    assert len(push_calls) == 1
    assert push_calls[0][-1] == f"HEAD:refs/heads/{candidate_pr.DEDICATED_BRANCH}"

    create_calls = [call for call in recorder.calls if call[:1] == ["gh"] and call[1:3] == ["pr", "create"]]
    assert create_calls[0][create_calls[0].index("--head") + 1] == candidate_pr.DEDICATED_BRANCH
    assert create_calls[0][create_calls[0].index("--base") + 1] == "main"
    assert "--draft" in create_calls[0]


def test_propose_never_targets_main_for_write_operations(origin_repo: Path, tmp_path: Path) -> None:
    recorder = RecordingRun()
    observations = [_observation(401)]

    candidate_pr.propose(
        pr=401,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=recorder,
    )

    for call in recorder.calls:
        # Check for an actual merge *command token*, not the word "merge"
        # appearing inside free-text PR-body prose (which legitimately
        # explains that this script never self-merges).
        assert "merge" not in (token.lower() for token in call)
        if "checkout" in call and "-B" in call:
            assert "main" not in call
        if "push" in call:
            assert "refs/heads/main" not in " ".join(call)


def test_propose_second_run_with_identical_observations_is_idempotent_noop(origin_repo: Path, tmp_path: Path) -> None:
    observations = [_observation(501)]

    first = candidate_pr.propose(
        pr=501,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(),
    )
    assert "OPENED" in first

    second_recorder = RecordingRun(existing_pr=1)
    second = candidate_pr.propose(
        pr=501,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=second_recorder,
    )

    assert "NO-OP" in second
    assert not any("push" in call for call in second_recorder.calls)
    assert second_recorder.pr_create_calls == 0


def test_propose_updates_existing_pr_without_creating_duplicate(origin_repo: Path, tmp_path: Path) -> None:
    recorder = RecordingRun(existing_pr=42)
    observations = [_observation(601)]

    message = candidate_pr.propose(
        pr=601,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=recorder,
    )

    assert "UPDATED" in message
    assert "#42" in message
    assert recorder.pr_create_calls == 0
    assert any("push" in call for call in recorder.calls)


def test_propose_accumulates_a_second_finding_onto_an_unmerged_first_candidate(
    origin_repo: Path, tmp_path: Path
) -> None:
    """Regression for a real defect found in adversarial self-review.

    Basing every run on a fresh `main` checkout unconditionally would let a
    second finding's push silently replace a first, still-unmerged finding's
    proposal on the same dedicated branch/PR -- losing the first finding's
    canonicalization work. This proves two findings processed back-to-back,
    before either PR merges, both end up present on the branch.
    """

    first = candidate_pr.propose(
        pr=701,
        head=HEAD,
        base=BASE,
        observations=[_observation(701, regression_test="test_compute_plan_is_pure_and_deterministic")],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(existing_pr=None),
    )
    assert "OPENED" in first

    second = candidate_pr.propose(
        pr=702,
        head=HEAD,
        base=BASE,
        observations=[_observation(702, regression_test="test_compute_plan_noop_for_non_confirmed_observation")],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(existing_pr=99),
    )
    assert "UPDATED" in second
    assert "#99" in second

    branch_registry = subprocess.run(
        ["git", "show", f"{candidate_pr.DEDICATED_BRANCH}:docs/DEFECT_REGISTRY.json"],
        cwd=origin_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "test_compute_plan_is_pure_and_deterministic" in branch_registry
    assert "test_compute_plan_noop_for_non_confirmed_observation" in branch_registry


def test_propose_rebuilds_fresh_once_dedicated_branch_is_stale(origin_repo: Path, tmp_path: Path) -> None:
    """A dedicated branch already merged into main (an ancestor of it) must not
    be treated as unmerged accumulation state -- it should be discarded and
    rebuilt from current main instead of being built on top of forever."""

    first = candidate_pr.propose(
        pr=801,
        head=HEAD,
        base=BASE,
        observations=[_observation(801)],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(existing_pr=None),
    )
    assert "OPENED" in first

    # Simulate the first candidate PR having been merged into main.
    _git(["fetch", "-q", str(origin_repo), candidate_pr.DEDICATED_BRANCH], cwd=tmp_path / "seed")
    _git(["checkout", "-q", "main"], cwd=tmp_path / "seed")
    _git(["merge", "-q", "--no-edit", "FETCH_HEAD"], cwd=tmp_path / "seed")
    _git(["push", "-q", "origin", "main"], cwd=tmp_path / "seed")

    second_recorder = RecordingRun(existing_pr=None)
    second = candidate_pr.propose(
        pr=802,
        head=HEAD,
        base=BASE,
        observations=[_observation(802, regression_test="test_compute_plan_noop_for_non_confirmed_observation")],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=second_recorder,
    )

    assert "OPENED" in second
    assert second_recorder.pr_create_calls == 1


def test_propose_reconciles_an_orphaned_branch_left_by_a_failed_pr_create(origin_repo: Path, tmp_path: Path) -> None:
    """Regression for Codex P1 (PR #530 review): if a prior run's push
    succeeded but its own `gh pr create` call then failed (a transient
    network blip, a crash), the dedicated branch is left with real,
    unmerged content and no PR representing it. Every later run computing
    the identical, already-applied observations must not just report NO-OP
    forever -- it must notice the branch is real, unmerged and PR-less, and
    open the missing PR without needing a new commit."""

    failing_recorder = RecordingRun(existing_pr=None, fail_pr_create=True)
    observations = [_observation(901)]

    with pytest.raises(candidate_pr.CanonicalizationCandidatePrError):
        candidate_pr.propose(
            pr=901,
            head=HEAD,
            base=BASE,
            observations=observations,
            repo=str(origin_repo),
            repo_root=tmp_path / "seed",
            run=failing_recorder,
        )

    # The push itself must have succeeded before the simulated gh failure:
    assert any("push" in call for call in failing_recorder.calls)
    assert failing_recorder.pr_create_calls == 0

    recovery_recorder = RecordingRun(existing_pr=None)
    message = candidate_pr.propose(
        pr=901,
        head=HEAD,
        base=BASE,
        observations=observations,
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=recovery_recorder,
    )

    assert "RECONCILED" in message
    assert recovery_recorder.pr_create_calls == 1
    assert not any("push" in call for call in recovery_recorder.calls), "reconciliation must not need a new push"


def test_github_review_finding_is_durably_captured_even_before_classification(
    origin_repo: Path, tmp_path: Path
) -> None:
    observation = {
        "source": "github-review",
        "provider": "github-review",
        "event_id": "review-comment-4114624029",
        "source_pr": 530,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "source_event_head_sha": "c" * 40,
        "reviewer": "chatgpt-codex-connector[bot]",
        "path": "scripts/x.py",
        "line": 9,
        "message": "validated reviewer finding",
        "availability": "available",
        "classification": None,
        "invariant": None,
        "affected_paths": [],
        "fix_reference": None,
        "regression_evidence": [],
        "claimed_family_id": None,
    }
    message = candidate_pr.propose(
        pr=530,
        head=HEAD,
        base=BASE,
        observations=[observation],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(existing_pr=None),
    )
    assert "OPENED" in message
    captured = subprocess.run(
        ["git", "show", f"{candidate_pr.DEDICATED_BRANCH}:docs/REVIEWER_FINDING_DISPOSITIONS.json"],
        cwd=origin_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    document = json.loads(captured)
    entry = next(item for item in document["findings"] if item["id"] == "RFD-AUTO-530-review-comment-4114624029")
    assert entry["validation_state"] == "unvalidated"
    assert "source_head=" + "c" * 40 in entry["source_provenance"]["reference"]


def test_duplicate_github_review_capture_is_idempotent(origin_repo: Path, tmp_path: Path) -> None:
    observation = {
        "source": "github-review",
        "provider": "github-review",
        "event_id": "review-comment-99",
        "source_pr": 530,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "source_event_head_sha": HEAD,
        "reviewer": "chatgpt-codex-connector[bot]",
        "path": "x.py",
        "line": 1,
        "message": "finding",
        "availability": "available",
        "classification": None,
        "invariant": None,
        "affected_paths": [],
        "fix_reference": None,
        "regression_evidence": [],
        "claimed_family_id": None,
    }
    first = candidate_pr.propose(
        pr=530,
        head=HEAD,
        base=BASE,
        observations=[observation],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(),
    )
    assert "OPENED" in first
    second = candidate_pr.propose(
        pr=530,
        head=HEAD,
        base=BASE,
        observations=[observation],
        repo=str(origin_repo),
        repo_root=tmp_path / "seed",
        run=RecordingRun(existing_pr=1),
    )
    assert "NO-OP" in second


DISPOSITIONS = Path("docs/REVIEWER_FINDING_DISPOSITIONS.json")


def _github_review_observation(
    *,
    event_id: str = "review-comment-1001",
    source_pr: int = 530,
    message: str = "deleted comment's substantive finding text",
    path: str | None = "scripts/x.py",
    line: int | None = 9,
    reviewed_head_sha: str = HEAD,
) -> dict[str, object]:
    return {
        "source": "github-review",
        "provider": "github-review",
        "event_id": event_id,
        "source_pr": source_pr,
        "reviewed_head_sha": reviewed_head_sha,
        "reviewed_base_sha": BASE,
        "source_event_head_sha": "c" * 40,
        "reviewer": "chatgpt-codex-connector[bot]",
        "path": path,
        "line": line,
        "message": message,
        "availability": "available",
        "classification": None,
        "invariant": None,
        "affected_paths": [],
        "fix_reference": None,
        "regression_evidence": [],
        "claimed_family_id": None,
    }


def test_capture_reviewer_findings_persists_message_path_and_line_for_deleted_finding() -> None:
    """Codex P1-B (PR #530), invariants 1 and 2: the durable RFD record must
    carry the finding's own substantive content, not just provenance, so it
    stays useful after the source GitHub comment is deleted and the 90-day
    transient lifecycle artifact has expired."""
    observation = _github_review_observation(
        message="the deleted comment's actual finding text", path="scripts/example.py", line=42
    )
    rendered, added = candidate_pr.capture_reviewer_findings(
        observations=[observation], origin_bytes=DISPOSITIONS.read_bytes()
    )
    assert added == ("RFD-AUTO-530-review-comment-1001",)
    document = json.loads(rendered)
    entry = next(item for item in document["findings"] if item["id"] == "RFD-AUTO-530-review-comment-1001")
    assert entry["finding_evidence"] == {
        "message": "the deleted comment's actual finding text",
        "path": "scripts/example.py",
        "line": 42,
    }
    # Provenance identity (already captured before this fix) must still be present.
    assert entry["source_provenance"]["reviewer"] == "chatgpt-codex-connector[bot]"
    assert entry["source_provenance"]["pr_number"] == 530
    assert "c" * 40 in entry["source_provenance"]["reference"]


def test_capture_reviewer_findings_evidence_omits_absent_path_and_line() -> None:
    """A top-level review body (no path/line) still durably captures its
    message; absent optional fields are omitted rather than fabricated."""
    observation = _github_review_observation(
        event_id="review-2002", message="top-level review body", path=None, line=None
    )
    rendered, added = candidate_pr.capture_reviewer_findings(
        observations=[observation], origin_bytes=DISPOSITIONS.read_bytes()
    )
    assert added == ("RFD-AUTO-530-review-2002",)
    document = json.loads(rendered)
    entry = next(item for item in document["findings"] if item["id"] == "RFD-AUTO-530-review-2002")
    assert entry["finding_evidence"] == {"message": "top-level review body"}


def test_capture_reviewer_findings_replay_is_idempotent_and_does_not_mutate_identity() -> None:
    """Codex P1-B (PR #530), invariant 3: replaying the same provider event
    must not create a duplicate RFD record or mutate its identity/content,
    even if the caller's HEAD/base later moved."""
    first_observation = _github_review_observation(reviewed_head_sha=HEAD)
    rendered_once, added_once = candidate_pr.capture_reviewer_findings(
        observations=[first_observation], origin_bytes=DISPOSITIONS.read_bytes()
    )
    assert len(added_once) == 1

    # Replay of the identical provider event, but as if HEAD/base moved since.
    replayed_observation = _github_review_observation(reviewed_head_sha="9" * 40)
    rendered_twice, added_twice = candidate_pr.capture_reviewer_findings(
        observations=[replayed_observation], origin_bytes=rendered_once
    )
    assert added_twice == ()
    assert rendered_twice == rendered_once
    document = json.loads(rendered_twice)
    matches = [item for item in document["findings"] if item["id"] == "RFD-AUTO-530-review-comment-1001"]
    assert len(matches) == 1


def test_capture_reviewer_findings_never_promotes_validation_state() -> None:
    """Codex P1-B (PR #530), invariant 4: durable capture alone must never
    mark a finding confirmed/resolved/canonical -- only the governed
    validation/canonicalization lifecycle may do that."""
    observation = _github_review_observation()
    rendered, _added = candidate_pr.capture_reviewer_findings(
        observations=[observation], origin_bytes=DISPOSITIONS.read_bytes()
    )
    document = json.loads(rendered)
    entry = next(item for item in document["findings"] if item["id"] == "RFD-AUTO-530-review-comment-1001")
    assert entry["validation_state"] == "unvalidated"
    assert "classification" not in entry
    assert "resolution_state" not in entry
    assert "mapped_defect_id" not in entry
