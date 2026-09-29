from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import hunter_pr_preflight
import hunter_pre_push
import hunter_writer_provenance
import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _update(
    sha: str = HEAD_A, *, local_ref: str = "refs/heads/feature", remote_ref: str = "refs/heads/feature"
) -> list[str]:
    return [f"{local_ref} {sha} {remote_ref} {hunter_pre_push.ZERO_SHA}\n"]


def _stub_issue_412_boundaries(monkeypatch) -> None:
    """Neutralise the Issue #412 provenance and receipt checks for this fixture.

    Both are enforced by ``enforce_pre_push`` and both have their own regressions
    in tests/test_issue_412_prevention_gate.py. Stubbing them here keeps each
    fixture below on the exact-head/clean-tree/preflight contract it was written
    for, instead of turning every one of them into an end-to-end git fixture.
    """

    monkeypatch.setattr(hunter_pre_push, "_validate_writer_provenance", lambda *_args: None)
    monkeypatch.setattr(hunter_pre_push, "_validate_receipt_freshness", lambda _head: None)
    monkeypatch.setattr(hunter_pre_push, "report_pre_ready_review_state", lambda _head, _updates: None)
    monkeypatch.setattr(hunter_pre_push, "require_current_review_request_if_present", lambda _head, _updates: None)


def test_pre_push_blocks_known_deterministic_failure_before_network_push(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)
    monkeypatch.setattr(
        hunter_pre_push.subprocess,
        "run",
        lambda command, *, check: SimpleNamespace(returncode=7),
    )

    assert hunter_pre_push.enforce_pre_push(_update()) == 7


def test_pre_push_proof_for_commit_a_cannot_authorize_commit_b(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="exact HEAD"):
        hunter_pre_push.enforce_pre_push(_update(HEAD_B))


def test_non_branch_source_refspec_targeting_remote_branch_cannot_bypass_exact_head(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="exact HEAD"):
        hunter_pre_push.enforce_pre_push(_update(HEAD_B, local_ref=HEAD_B, remote_ref="refs/heads/feature"))


def test_pre_push_rejects_dirty_tree_before_preflight(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return " M src/hunter/example.py"
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="working tree must be clean"):
        hunter_pre_push.enforce_pre_push(_update())


def test_pre_push_rechecks_head_after_successful_preflight(monkeypatch, tmp_path) -> None:
    head_reads = iter((HEAD_A, HEAD_B))

    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return next(head_reads)
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)
    monkeypatch.setattr(
        hunter_pre_push.subprocess,
        "run",
        lambda command, *, check: SimpleNamespace(returncode=0),
    )

    assert hunter_pre_push.enforce_pre_push(_update()) == 2


def test_tests_first_marker_selects_same_supported_mode_as_hosted_preflight(monkeypatch) -> None:
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: "tests-first-red\n")

    assert hunter_pre_push._select_preflight_mode(HEAD_A) == hunter_pre_push.TESTS_FIRST_RED_MODE
    assert hunter_pre_push._preflight_command(hunter_pre_push.TESTS_FIRST_RED_MODE) == (
        "python",
        "scripts/hunter_pr_preflight.py",
        "--mode",
        "tests-first-red",
    )


def test_invalid_tests_first_marker_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: "normal\n")

    with pytest.raises(RuntimeError, match="exactly tests-first-red"):
        hunter_pre_push._select_preflight_mode(HEAD_A)


def test_local_only_ignored_mode_marker_cannot_authorize_tests_first_red(monkeypatch, tmp_path) -> None:
    marker = tmp_path / ".hunter-preflight-mode"
    marker.write_text("tests-first-red\n", encoding="utf-8")
    monkeypatch.setattr(hunter_pre_push, "MODE_MARKER", marker)
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: None)

    assert hunter_pre_push._select_preflight_mode(HEAD_A) == hunter_pre_push.NORMAL_MODE


def test_repository_hook_is_executable_and_calls_canonical_enforcer() -> None:
    hook = ROOT / ".githooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    text = hook.read_text(encoding="utf-8")
    assert "python scripts/hunter_pre_push.py" in text


def test_normal_mode_runs_the_push_safety_lane_rather_than_the_full_repository_suite() -> None:
    """Issue #415: the full lane is the hosted boundary's proof, not this one's.

    The canonical command spelling is unchanged -- normal mode still means the
    same seven gates wherever it is invoked. What changed is that the push
    boundary no longer invokes it, because re-proving the whole repository here
    established nothing candidate admission did not already require.
    """
    assert hunter_pre_push._preflight_command(hunter_pre_push.NORMAL_MODE) == (
        "python",
        "scripts/hunter_pr_preflight.py",
        "--mode",
        "normal",
    )
    assert hunter_pre_push._lane_label(hunter_pre_push.NORMAL_MODE) == "push-safety lane"
    assert hunter_pr_preflight.PYTEST_GATE not in hunter_pr_preflight.PUSH_SAFETY_GATES


def test_pre_push_still_blocks_every_pre_network_rewrite_defect(monkeypatch, tmp_path) -> None:
    """The checks that are only repairable by rewriting history all still run.

    Each one is stubbed to fire in turn, so this fails if any of them stops
    being reached from the boundary rather than merely stops failing.
    """
    order: list[str] = []

    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)

    def failing(name: str) -> Callable[..., None]:
        def check(*_args: object) -> None:
            order.append(name)
            raise RuntimeError(name)

        return check

    for name in ("_validate_writer_provenance", "_validate_receipt_freshness"):
        monkeypatch.setattr(hunter_pre_push, name, failing(name))
        with pytest.raises(RuntimeError, match=name):
            hunter_pre_push.enforce_pre_push(_update())
        monkeypatch.setattr(hunter_pre_push, name, lambda *_args: None)

    assert order == ["_validate_writer_provenance", "_validate_receipt_freshness"]


def test_hook_installer_owns_repository_hooks_path() -> None:
    text = (ROOT / "scripts" / "install_hunter_git_hooks.py").read_text(encoding="utf-8")
    assert 'HOOKS_PATH = ".githooks"' in text
    assert '"git", "config", "core.hooksPath", HOOKS_PATH' in text


def test_repository_derivation_prefers_canonical_upstream_over_the_fork(monkeypatch) -> None:
    """Fork workflow: origin names the fork, upstream the canonical base.

    Governing Issue criteria are read from the base repository the pull request
    targets (the one hosted Candidate Admission reads from). In a fork workflow
    that is the upstream remote, never the fork that happens to be ``origin``.
    """

    def fake_git(*args: str) -> str:
        if args == ("config", "--get", "remote.origin.url"):
            return "git@github.com:fafa33/Project-Hunter-Fork.git"
        if args == ("config", "--get", "remote.upstream.url"):
            return "https://github.com/fafa33/Project-Hunter.git"
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)

    assert hunter_pre_push._repository_from_remotes() == "fafa33/Project-Hunter"


def test_repository_derivation_falls_back_to_origin_in_a_direct_clone(monkeypatch) -> None:
    """No fork: origin names the canonical repository and is the only remote."""

    def fake_git(*args: str) -> str:
        if args == ("config", "--get", "remote.origin.url"):
            return "https://github.com/fafa33/Project-Hunter.git"
        raise RuntimeError("remote.upstream does not exist")

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)

    assert hunter_pre_push._repository_from_remotes() == "fafa33/Project-Hunter"


# --------------------------------------------------------------------------
# Issue #545: provenance governs what a push publishes, not the whole fork point
# --------------------------------------------------------------------------


def _bound_binding() -> hunter_writer_provenance.WriterIdentityBinding:
    return hunter_writer_provenance.WriterIdentityBinding(
        identities=(
            hunter_writer_provenance.WriterIdentity(
                login="fafa33",
                names=frozenset({"farhad5778"}),
                emails=frozenset({"34549283+fafa33@users.noreply.github.com"}),
                canonical_name="Farhad5778",
                canonical_email="34549283+fafa33@users.noreply.github.com",
            ),
        ),
        require_single_writer_per_range=True,
    )


def _second_bound_binding() -> hunter_writer_provenance.WriterIdentityBinding:
    """The same binding plus a second authorized writer, as the real policy declares."""

    return hunter_writer_provenance.WriterIdentityBinding(
        identities=_bound_binding().identities
        + (
            hunter_writer_provenance.WriterIdentity(
                login="claude",
                # Bound sets are stored normalised, as parse_binding produces them.
                names=frozenset({"claude"}),
                emails=frozenset({"noreply@anthropic.com"}),
                canonical_name="Claude",
                canonical_email="noreply@anthropic.com",
            ),
        ),
        require_single_writer_per_range=True,
    )


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=True,
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
    )
    return completed.stdout.strip()


def _commit(
    repo: Path,
    message: str,
    *,
    name: str = "Farhad5778",
    email: str = "34549283+fafa33@users.noreply.github.com",
) -> str:
    """One commit whose author and committer are the identities under test."""

    ident = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    }
    (repo / "file.txt").write_text(message + "\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-m", message, env={**_git_env(), **ident})
    return _git(repo, "rev-parse", "HEAD")


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


@pytest.fixture
def publish_repo(tmp_path: Path) -> Path:
    """A repository whose branch carries one already-remote unbound commit.

    The unbound commit stands in for a squash-merged main commit: its committer is
    ``GitHub <noreply@github.com>``, which is deliberately not on the allowlist, and
    it is already reachable from the destination ref. A local ``origin/main`` that
    still predates it reproduces the lag that made Issue #545 walk that far back.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "base")
    _git(repo, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _git(repo, "update-ref", "refs/remotes/origin/main", base)

    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "remote tip", name="GitHub", email="noreply@github.com")
    return repo


def _bind_publish_provenance(monkeypatch, binding: hunter_writer_provenance.WriterIdentityBinding) -> None:
    monkeypatch.setattr(hunter_pre_push.provenance, "load_binding", lambda *_a, **_k: (binding, ""))


def _validate(repo: Path, local_ref: str, local_sha: str, remote_sha: str) -> None:
    monkey_repo = hunter_pre_push.os.getcwd()
    hunter_pre_push.os.chdir(repo)
    try:
        hunter_pre_push._validate_writer_provenance(
            local_sha,
            [(local_ref, local_sha, remote_sha)],
        )
    finally:
        hunter_pre_push.os.chdir(monkey_repo)


def test_issue_545_history_already_on_the_remote_is_not_re_governed(monkeypatch, publish_repo: Path) -> None:
    """The Issue #545 false block: one new bound commit over remote history.

    The destination ref already carries an unbound commit that predates it. The
    fork point of the branch from a lagging ``origin/main`` reaches that commit,
    so re-governing the fork-point range refuses a push that publishes only the
    new, correctly signed commit.
    """

    _bind_publish_provenance(monkeypatch, _bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    local_tip = _commit(publish_repo, "new authorized commit")

    # The old behaviour: the fork point from a lagging origin/main still contains
    # the unbound commit, so the governed range is inadmissible.
    fork_point = _git(publish_repo, "merge-base", "HEAD", "origin/main")
    assert _git(publish_repo, "rev-list", "--count", f"{fork_point}..HEAD") == "2"
    assert hunter_pre_push.provenance.check_range(local_tip) is not None

    # The behaviour Issue #545 asks for: only the published commit is governed.
    assert _validate_publishes(publish_repo, remote_tip, local_tip) == (local_tip,)
    _validate(publish_repo, "refs/heads/feature", local_tip, remote_tip)


def _validate_publishes(repo: Path, remote_tip: str, local_tip: str) -> tuple[str, ...]:
    published = _git(repo, "log", "--format=%H", f"{remote_tip}..{local_tip}").split()
    return tuple(reversed(published))


def test_issue_545_only_the_published_commits_are_evaluated(monkeypatch, publish_repo: Path) -> None:
    """Requirement 3: the range evaluated is exactly ``remote_sha..local_sha``."""

    _bind_publish_provenance(monkeypatch, _bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    first = _commit(publish_repo, "first new commit")
    second = _commit(publish_repo, "second new commit")

    _validate(publish_repo, "refs/heads/feature", first, remote_tip)
    _validate(publish_repo, "refs/heads/feature", second, first)
    assert _validate_publishes(publish_repo, remote_tip, second) == (first, second)


def test_issue_545_a_newly_introduced_unbound_commit_is_refused(monkeypatch, publish_repo: Path) -> None:
    """Requirement 2: a bound remote tip does not launder an unbound new commit."""

    _bind_publish_provenance(monkeypatch, _bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    _commit(publish_repo, "unbound new commit", name="Claude", email="noreply@anthropic.com")

    with pytest.raises(RuntimeError, match="authorization-bound writer identity"):
        _validate(publish_repo, "refs/heads/feature", _git(publish_repo, "rev-parse", "HEAD"), remote_tip)


def test_issue_545_a_new_ref_keeps_the_governed_base_and_fails_closed(monkeypatch, publish_repo: Path) -> None:
    """Requirement 5: a zero remote SHA has no published range, so the fork point governs."""

    _bind_publish_provenance(monkeypatch, _bound_binding())
    local_tip = _git(publish_repo, "rev-parse", "HEAD")

    recorded: list[str] = []

    def _record(head: str, **_kwargs: object) -> None:
        recorded.append(head)

    monkeypatch.setattr(hunter_pre_push.provenance, "check_range", _record)
    _validate(publish_repo, "refs/heads/brand-new", local_tip, hunter_pre_push.ZERO_SHA)
    assert recorded == [local_tip]


def test_issue_545_a_new_ref_fails_closed_when_the_fork_point_is_unavailable(monkeypatch, publish_repo: Path) -> None:
    _bind_publish_provenance(monkeypatch, _bound_binding())
    _git(publish_repo, "update-ref", "-d", "refs/remotes/origin/main")
    local_tip = _git(publish_repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="fork point"):
        _validate(publish_repo, "refs/heads/brand-new", local_tip, hunter_pre_push.ZERO_SHA)


def test_issue_545_a_non_fast_forward_update_is_refused(monkeypatch, publish_repo: Path) -> None:
    """Requirement 6: a rewritten destination has no governed range to validate."""

    _bind_publish_provenance(monkeypatch, _bound_binding())
    first = _commit(publish_repo, "first")
    second = _commit(publish_repo, "second")
    _git(publish_repo, "reset", "-q", "--hard", first)

    with pytest.raises(RuntimeError, match="rewrites published history"):
        _validate(publish_repo, "refs/heads/feature", first, second)


def test_issue_545_unreadable_remote_tip_fails_closed(monkeypatch, publish_repo: Path) -> None:
    _bind_publish_provenance(monkeypatch, _bound_binding())
    local_tip = _git(publish_repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="could not be read"):
        _validate(publish_repo, "refs/heads/feature", local_tip, "d" * 40)


def test_issue_545_each_ref_update_is_evaluated_independently(monkeypatch, publish_repo: Path) -> None:
    """Requirement 4: one ref's defect never excuses, and never is excused by, another."""

    _bind_publish_provenance(monkeypatch, _bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    good = _commit(publish_repo, "good commit")
    bad = _commit(publish_repo, "unbound commit", name="Jules", email="jules@example.invalid")

    ranges = [("refs/heads/one", good, remote_tip), ("refs/heads/two", bad, remote_tip)]
    monkey_repo = os.getcwd()
    hunter_pre_push.os.chdir(publish_repo)
    try:
        with pytest.raises(RuntimeError, match="refs/heads/two"):
            hunter_pre_push._validate_writer_provenance(good, ranges)
    finally:
        hunter_pre_push.os.chdir(monkey_repo)

    hunter_pre_push.os.chdir(publish_repo)
    try:
        hunter_pre_push._validate_writer_provenance(good, [("refs/heads/one", good, remote_tip)])
    finally:
        hunter_pre_push.os.chdir(monkey_repo)


def test_issue_545_publish_range_reads_only_the_destination_ref() -> None:
    """The parser keeps the remote SHA that Issue #545 depends on."""

    lines = [
        "refs/heads/feature " + "1" * 40 + " refs/heads/feature " + "2" * 40 + "\n",
        "refs/tags/v1 " + "3" * 40 + " refs/tags/v1 " + hunter_pre_push.ZERO_SHA + "\n",
        f"refs/heads/gone {hunter_pre_push.ZERO_SHA} refs/heads/gone " + "4" * 40 + "\n",
    ]
    assert hunter_pre_push._parse_publish_ranges(lines) == [
        ("refs/heads/feature", "1" * 40, "2" * 40),
        ("refs/tags/v1", "3" * 40, hunter_pre_push.ZERO_SHA),
    ]
    assert hunter_pre_push._parse_updates(lines) == [
        ("refs/heads/feature", "1" * 40, "refs/heads/feature"),
        ("refs/tags/v1", "3" * 40, "refs/tags/v1"),
    ]


def test_issue_545_check_publish_range_does_not_re_resolve_the_base(monkeypatch, publish_repo: Path) -> None:
    """The publish base is the caller's remote SHA, never a re-derived fork point."""

    monkeypatch.setattr(hunter_pre_push.provenance, "load_binding", lambda *_a, **_k: (_bound_binding(), ""))
    monkeypatch.setattr(
        hunter_pre_push.provenance,
        "resolve_governed_base",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("the fork point must not be re-derived")),
    )
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    local_tip = _commit(publish_repo, "new authorized commit")

    monkey_repo = os.getcwd()
    hunter_pre_push.os.chdir(publish_repo)
    try:
        assert hunter_pre_push.provenance.check_publish_range(remote_tip, local_tip) is None
    finally:
        hunter_pre_push.os.chdir(monkey_repo)


# --------------------------------------------------------------------------
# Issue #545 P1: the candidate-wide single-writer invariant survives the fix
# --------------------------------------------------------------------------


def test_issue_545_p1_same_writer_may_push_twice(monkeypatch, publish_repo: Path) -> None:
    """The invariant is single-writer per candidate, not single-push.

    Writer A publishes the branch, then A publishes again. Both pushes publish
    only commits bound to A, so the candidate resolves to one writer and the
    second push is admitted.
    """

    _bind_publish_provenance(monkeypatch, _second_bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    first = _commit(publish_repo, "A: first push")
    _validate(publish_repo, "refs/heads/feature", first, remote_tip)

    second = _commit(publish_repo, "A: second push")
    _validate(publish_repo, "refs/heads/feature", second, first)
    assert _validate_publishes(publish_repo, first, second) == (second,)


def test_issue_545_p1_a_second_authorized_writer_cannot_take_the_branch_over(monkeypatch, publish_repo: Path) -> None:
    """The P1 finding: B takes A's branch across two pushes, and the second must fail.

    B's commit is itself authorization-bound, so the published-range check that
    Issue #545 added cannot see the problem -- it sees exactly one commit, bound to
    one writer. Only the candidate-wide rule can, because the candidate carries
    both A's commit and B's.
    """

    _bind_publish_provenance(monkeypatch, _second_bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    by_a = _commit(publish_repo, "A: the branch")
    _validate(publish_repo, "refs/heads/feature", by_a, remote_tip)

    by_b = _commit(publish_repo, "B: takeover", name="Claude", email="noreply@anthropic.com")

    # B's own commit is admissible in isolation: one commit, one authorized writer.
    assert hunter_writer_provenance.evaluate_range(
        _second_bound_binding(),
        hunter_writer_provenance.read_range_commits(by_a, by_b, cwd=publish_repo),
    ).ok
    assert hunter_writer_provenance.check_publish_range(by_a, by_b, cwd=publish_repo) is None

    # The candidate does not stay silent about it.
    with pytest.raises(RuntimeError, match="mixes authorization-bound writers"):
        _validate(publish_repo, "refs/heads/feature", by_b, by_a)


def test_issue_545_p1_single_writer_check_ignores_unbound_history(monkeypatch, publish_repo: Path) -> None:
    """Restoring the candidate-wide check must not re-govern inherited history.

    The fixture's destination ref carries a ``GitHub <noreply@github.com>`` commit
    that no authorized writer claims. That commit is inside the governed candidate
    range, and it is already on the remote, so it must not become admissible to
    re-govern: the candidate still resolves to the single writer A. This is the
    Issue #545 bug, and restoring the single-writer check must not reintroduce it.
    """

    _bind_publish_provenance(monkeypatch, _second_bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    local_tip = _commit(publish_repo, "A: the branch")

    candidate = hunter_writer_provenance.read_range_commits(
        hunter_writer_provenance.resolve_governed_base(local_tip, cwd=publish_repo),
        local_tip,
        cwd=publish_repo,
    )
    # The unbound commit really is inside the candidate range the check walks.
    assert any(commit.committer_name == "GitHub" for commit in candidate)
    # ...and it contributes no writer, so the candidate stays single-writer.
    assert hunter_writer_provenance.check_candidate_single_writer(local_tip, cwd=publish_repo) is None
    _validate(publish_repo, "refs/heads/feature", local_tip, remote_tip)


def test_issue_545_p1_base_commits_outside_the_candidate_are_not_governed(monkeypatch, publish_repo: Path) -> None:
    """The governed range is the candidate's; the base branch is nobody's candidate.

    ``origin/main`` is advanced past the branch point with a commit under a second
    authorized writer. The candidate does not contain that commit, so it cannot make
    the candidate's own single writer ambiguous -- and the base work stays ungoverned.
    """

    _bind_publish_provenance(monkeypatch, _second_bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    local_tip = _commit(publish_repo, "A: the branch")

    _git(publish_repo, "checkout", "-q", "main")
    base_by_b = _commit(publish_repo, "base work by B", name="Claude", email="noreply@anthropic.com")
    _git(publish_repo, "update-ref", "refs/remotes/origin/main", base_by_b)
    _git(publish_repo, "checkout", "-q", "feature")

    fork_point = _git(publish_repo, "merge-base", local_tip, "origin/main")
    governed = _git(publish_repo, "rev-list", f"{fork_point}..{local_tip}").split()
    assert base_by_b not in governed, "the base commit is outside the candidate range"
    assert hunter_writer_provenance.check_candidate_single_writer(local_tip, cwd=publish_repo) is None
    _validate(publish_repo, "refs/heads/feature", local_tip, remote_tip)


def test_issue_545_p1_single_writer_check_fails_closed_without_evidence(monkeypatch, publish_repo: Path) -> None:
    """The candidate-wide check is itself fail-closed, not a best-effort extra.

    A publishable range over a candidate whose fork point cannot be established is
    not a candidate whose single writer is unknown-and-therefore-fine; it is unknown.
    """

    _bind_publish_provenance(monkeypatch, _second_bound_binding())
    remote_tip = _git(publish_repo, "rev-parse", "HEAD")
    local_tip = _commit(publish_repo, "A: the branch")
    _git(publish_repo, "update-ref", "-d", "refs/remotes/origin/main")

    # The published range is clean, so only the candidate-wide check can refuse this.
    assert hunter_writer_provenance.check_publish_range(remote_tip, local_tip, cwd=publish_repo) is None
    with pytest.raises(RuntimeError, match="fork point"):
        _validate(publish_repo, "refs/heads/feature", local_tip, remote_tip)


def test_issue_545_p1_single_writer_check_depends_on_the_policy_flag(monkeypatch, publish_repo: Path) -> None:
    """A policy that does not require one writer per range does not get the check."""

    relaxed = hunter_writer_provenance.WriterIdentityBinding(
        identities=_second_bound_binding().identities,
        require_single_writer_per_range=False,
    )
    _bind_publish_provenance(monkeypatch, relaxed)
    local_tip = _git(publish_repo, "rev-parse", "HEAD")

    assert hunter_writer_provenance.check_candidate_single_writer(local_tip, cwd=publish_repo) is None
