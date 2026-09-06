"""Issue #415: a proof may only be reused for the work it actually proves.

Every branch of the reuse decision that is not an explicit, identity-matched
acceptance has to end in the full lane running again. These fixtures are the
adversarial pass over that boundary: each one supplies something that *looks*
like proof -- a receipt for another head, a receipt from before the gate chain
changed, a successful run of a workflow that merely shares a name -- and
asserts the boundary refuses it.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import hunter_governance_review_v2 as governance
import hunter_pr_preflight as preflight
import hunter_pre_push
import hunter_validation_receipt as receipts
import hunter_validation_reuse as reuse
import pytest

ROOT = Path(__file__).resolve().parents[1]

HEAD = "a" * 40
FOREIGN_HEAD = "b" * 40
CONTENT = f"tree:{'c' * 40}"
OTHER_CONTENT = f"tree:{'d' * 40}"
DEFINITION = "sha256:definition"
TOOLCHAIN = "sha256:toolchain"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

IDENTITY = receipts.ValidationIdentity(content=CONTENT, definition=DEFINITION, toolchain=TOOLCHAIN)


def _receipt(**overrides: Any) -> receipts.ValidationReceipt:
    values: dict[str, Any] = {
        "lane": receipts.FULL_LANE,
        "result": receipts.PASSED,
        "head_sha": HEAD,
        "content_identity": CONTENT,
        "definition_identity": DEFINITION,
        "toolchain_identity": TOOLCHAIN,
        "produced_at": NOW - timedelta(minutes=5),
        "produced_by": "local-preflight",
    }
    values.update(overrides)
    return receipts.ValidationReceipt(**values)


# --------------------------------------------------------------------------
# Receipt verification: what may and may not stand in for a full-lane run.
# --------------------------------------------------------------------------


def test_matching_identity_is_reusable() -> None:
    assert receipts.verify(_receipt(), IDENTITY, head_sha=HEAD, now=NOW) is None


def test_verification_cannot_be_asked_to_skip_the_head_binding() -> None:
    """The binding has no default, so no caller can drop it by omission.

    This is the property the defect turned on: `head_sha` was optional, the push
    boundary passed it and the local reuse path did not, and nothing detected
    the difference. A required keyword makes that divergence impossible to
    write rather than merely discouraged.
    """
    with pytest.raises(TypeError):
        receipts.verify(_receipt(), IDENTITY, now=NOW)  # type: ignore[call-arg]


def test_changed_head_invalidates_reusable_proof() -> None:
    """A new commit is new content, so its tree identity is a different identity."""
    blocker = receipts.verify(_receipt(content_identity=OTHER_CONTENT), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker is not None
    assert "content identity" in blocker


def test_foreign_head_proof_fails_closed() -> None:
    """Identical content is not enough when the proof names another head.

    Two heads can carry the same tree, so the content check alone would let a
    proof recorded for one candidate authorize another. The head binding is
    what keeps a proof attached to the candidate it was made for.
    """
    blocker = receipts.verify(_receipt(head_sha=FOREIGN_HEAD), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker is not None
    assert "foreign" in blocker


def test_changed_validation_definition_invalidates_reusable_proof() -> None:
    blocker = receipts.verify(_receipt(definition_identity="sha256:other"), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker == "validation definition changed since the receipt was produced"


def test_changed_toolchain_invalidates_reusable_proof() -> None:
    blocker = receipts.verify(_receipt(toolchain_identity="sha256:other"), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker == "validation toolchain changed since the receipt was produced"


def test_stale_receipt_fails_closed() -> None:
    stale = _receipt(produced_at=NOW - timedelta(seconds=receipts.DEFAULT_MAX_AGE_SECONDS + 1))

    blocker = receipts.verify(stale, IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker is not None
    assert "stale" in blocker


def test_receipt_dated_in_the_future_fails_closed() -> None:
    blocker = receipts.verify(_receipt(produced_at=NOW + timedelta(minutes=1)), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker == "receipt was produced in the future"


def test_failed_result_is_never_a_proof() -> None:
    blocker = receipts.verify(_receipt(result=receipts.FAILED), IDENTITY, head_sha=HEAD, now=NOW)

    assert blocker is not None
    assert "not a proof" in blocker


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("not-an-object", id="not-an-object"),
        pytest.param([], id="list"),
        pytest.param({}, id="empty"),
        pytest.param({"schema": "hunter.validation-receipt/99"}, id="unsupported-schema"),
        pytest.param({"schema": receipts.RECEIPT_SCHEMA}, id="no-fields"),
    ],
)
def test_malformed_receipt_documents_fail_closed(document: Any) -> None:
    receipt, problem = receipts.ValidationReceipt.from_document(document)

    assert receipt is None
    assert problem


def test_receipt_with_missing_field_fails_closed() -> None:
    document = _receipt().to_document()
    del document["content_identity"]

    receipt, problem = receipts.ValidationReceipt.from_document(document)

    assert receipt is None
    assert "content_identity" in problem


def test_receipt_with_naive_timestamp_fails_closed() -> None:
    document = _receipt().to_document()
    document["produced_at"] = "2026-09-06T12:00:00"

    receipt, problem = receipts.ValidationReceipt.from_document(document)

    assert receipt is None
    assert "timezone" in problem


def test_receipt_with_unknown_lane_fails_closed() -> None:
    document = _receipt().to_document()
    document["lane"] = "push-safety"

    receipt, problem = receipts.ValidationReceipt.from_document(document)

    assert receipt is None
    assert "lane" in problem


def test_receipt_round_trips_through_its_document_form() -> None:
    receipt, problem = receipts.ValidationReceipt.from_document(_receipt().to_document())

    assert problem == ""
    assert receipt == _receipt()


def test_unreadable_receipt_file_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "full.json"
    path.write_text("{ not json", encoding="utf-8")

    receipt, problem = receipts.load(path)

    assert receipt is None
    assert "unreadable" in problem


def test_absent_receipt_file_fails_closed(tmp_path: Path) -> None:
    receipt, problem = receipts.load(tmp_path / "full.json")

    assert receipt is None
    assert problem


def test_stored_receipt_is_loaded_back_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "receipts" / "full.json"
    receipts.store(_receipt(), path)

    loaded, problem = receipts.load(path)

    assert problem == ""
    assert loaded == _receipt()


# --------------------------------------------------------------------------
# Identity derivation.
# --------------------------------------------------------------------------


def _definition_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for relative in receipts.DEFINITION_PATHS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    return root


def test_definition_identity_is_stable_for_identical_definition_files(tmp_path: Path) -> None:
    first = _definition_root(tmp_path / "a")
    second = _definition_root(tmp_path / "b")

    assert receipts.definition_identity(first) == receipts.definition_identity(second)


@pytest.mark.parametrize("relative", receipts.DEFINITION_PATHS)
def test_every_declared_definition_file_changes_the_definition_identity(tmp_path: Path, relative: str) -> None:
    """A declared definition file that cannot move the identity is not governing it."""
    root = _definition_root(tmp_path)
    before = receipts.definition_identity(root)
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n# validation definition drift\n")

    assert receipts.definition_identity(root) != before


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _committed_definition_repo(tmp_path: Path) -> Path:
    """A real repository carrying the validation-definition files, one commit deep."""
    root = _definition_root(tmp_path)
    # The receipt store is ignored in the real repository; without that here the
    # recorded receipt would itself dirty the tree and there would be no content
    # identity to bind.
    (root / ".gitignore").write_text(f"{receipts.RECEIPT_DIR.as_posix()}/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "definition files")
    return root


def test_a_receipt_for_one_commit_is_refused_on_another_commit_with_the_same_tree(tmp_path: Path) -> None:
    """The production reuse path binds the head, not only the content.

    Two commits can carry a byte-identical tree -- amending a message is the
    everyday way to produce one -- so content identity alone would let a proof
    recorded for one candidate authorize another. This drives `reuse_blocker`,
    the function the preflight actually calls, rather than `verify` directly:
    the binding was previously present in the verifier and lost at that caller,
    so a fixture that called the verifier could not have caught it.
    """
    root = _committed_definition_repo(tmp_path)
    first_head = receipts.resolve_head_sha(root)
    tree = receipts.content_identity(root)
    receipts.record(root, head_sha=first_head, produced_by="local-preflight")

    assert receipts.reuse_blocker(root) is None, "the commit the receipt was recorded on must reuse it"

    _git(root, "commit", "-q", "--amend", "-m", "same tree, different commit")
    second_head = receipts.resolve_head_sha(root)

    # The premise of the test: genuinely the same content, genuinely a new commit.
    assert second_head != first_head
    assert receipts.content_identity(root) == tree

    blocker = receipts.reuse_blocker(root)

    assert blocker is not None, "a receipt for another commit must not authorize this one"
    assert "foreign" in blocker


def test_missing_definition_file_fails_closed(tmp_path: Path) -> None:
    root = _definition_root(tmp_path)
    (root / receipts.DEFINITION_PATHS[0]).unlink()

    with pytest.raises(receipts.ValidationEvidenceUnavailable):
        receipts.definition_identity(root)


def test_gate_chain_is_part_of_the_definition_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing the gate list has to retire prior proof even if no file moved.

    The gate chain is a module constant, so a definition identity built only
    from file digests would happily reuse a proof produced by a different set
    of gates.
    """
    root = _definition_root(tmp_path)
    before = receipts.definition_identity(root)
    monkeypatch.setattr(preflight, "NORMAL_QUALITY_GATES", preflight.NORMAL_QUALITY_GATES[:-1])

    assert receipts.definition_identity(root) != before


def test_pinned_toolchain_reads_the_repository_pins() -> None:
    pinned = receipts.pinned_toolchain(ROOT)

    assert set(pinned) == {"python", *receipts.TOOLCHAIN_DISTRIBUTIONS}
    constraints = (ROOT / "requirements" / "ci-constraints.txt").read_text(encoding="utf-8")
    for name in receipts.TOOLCHAIN_DISTRIBUTIONS:
        assert f"{name}=={pinned[name]}" in constraints


def test_measured_toolchain_names_every_pinned_tool() -> None:
    assert set(receipts.measured_toolchain()) == {"python", *receipts.TOOLCHAIN_DISTRIBUTIONS}


def test_toolchain_identity_is_order_independent() -> None:
    assert receipts.toolchain_identity({"a": "1", "b": "2"}) == receipts.toolchain_identity({"b": "2", "a": "1"})


def test_toolchain_identity_separates_different_versions() -> None:
    assert receipts.toolchain_identity({"a": "1"}) != receipts.toolchain_identity({"a": "2"})


def test_dirty_tree_has_no_content_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipts, "_run_git", lambda root, *args: " M src/hunter/example.py")

    with pytest.raises(receipts.ValidationEvidenceUnavailable, match="not clean"):
        receipts.content_identity(tmp_path)


def test_unresolved_tree_object_is_refused() -> None:
    with pytest.raises(receipts.ValidationEvidenceUnavailable):
        receipts.tree_identity("HEAD")


# --------------------------------------------------------------------------
# CI reuse: when pull-request CI may stand on the trusted hosted proof.
# --------------------------------------------------------------------------

TREE = "e" * 40


def _stub_identity(monkeypatch: pytest.MonkeyPatch, *, integration: str | None = None) -> None:
    monkeypatch.setattr(receipts, "content_identity", lambda root: integration or f"tree:{TREE}")
    monkeypatch.setattr(receipts, "commit_tree_identity", lambda root, commit: f"tree:{TREE}")
    monkeypatch.setattr(receipts, "definition_identity", lambda root: DEFINITION)
    monkeypatch.setattr(receipts, "measured_toolchain", lambda: {"python": "3.11.13"})
    monkeypatch.setattr(receipts, "pinned_toolchain", lambda root: {"python": "3.11.13"})


def _run(**overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": 10,
        "head_sha": HEAD,
        "name": reuse.PRE_PR_WORKFLOW_NAME,
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "updated_at": (NOW - timedelta(minutes=3)).isoformat().replace("+00:00", "Z"),
    }
    run.update(overrides)
    return run


def _resolve(tmp_path: Path, runs: list[Any], **overrides: Any) -> reuse.ReuseDecision:
    options: dict[str, Any] = {
        "event_name": "pull_request",
        "head_sha": HEAD,
        "repository": "fafa33/Project-Hunter",
        "token": "",
        "now": NOW,
        "fetch": lambda repository, token, path: {"workflow_runs": runs},
    }
    options.update(overrides)
    return reuse.resolve(tmp_path, **options)


def test_identical_integration_tree_reuses_the_trusted_exact_head_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run()])

    assert decision.reusable, decision.reason


def test_advanced_main_producing_a_different_integration_tree_runs_the_full_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A materially different merge tree is different work, so it is proven again."""
    _stub_identity(monkeypatch, integration=f"tree:{'f' * 40}")

    decision = _resolve(tmp_path, [_run()])

    assert not decision.reusable
    assert "integration tree differs" in decision.reason


def test_reuse_requires_a_trusted_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [])

    assert not decision.reusable
    assert "no exact-head" in decision.reason


def test_foreign_head_run_cannot_authorize_this_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(head_sha=FOREIGN_HEAD)])

    assert not decision.reusable
    assert "no exact-head" in decision.reason


def test_another_workflow_wearing_the_same_name_cannot_authorize_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name and path are matched together; a workflow file may be added freely."""
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(path=".github/workflows/impostor.yml")])

    assert not decision.reusable


def test_non_push_event_run_cannot_authorize_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(event="workflow_dispatch")])

    assert not decision.reusable


def test_unsuccessful_run_cannot_authorize_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(conclusion="failure")])

    assert not decision.reusable
    assert "concluded failure" in decision.reason


def test_incomplete_run_cannot_authorize_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(status="in_progress", conclusion=None)])

    assert not decision.reusable
    assert "not completed" in decision.reason


def test_latest_run_for_the_head_decides_rather_than_any_green_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A superseded green run never outvotes the current one for the same head."""
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(id=10), _run(id=11, conclusion="failure")])

    assert not decision.reusable


def test_stale_trusted_run_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)
    old = (NOW - timedelta(seconds=receipts.DEFAULT_MAX_AGE_SECONDS + 60)).isoformat().replace("+00:00", "Z")

    decision = _resolve(tmp_path, [_run(updated_at=old)])

    assert not decision.reusable
    assert "stale" in decision.reason


def test_unreadable_run_timestamp_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run(updated_at="whenever", created_at="")])

    assert not decision.reusable
    assert "unreadable" in decision.reason


def test_push_event_never_reuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A push to main validates the branch it is creating, not a candidate."""
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run()], event_name="push")

    assert not decision.reusable


def test_missing_head_sha_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)

    decision = _resolve(tmp_path, [_run()], head_sha="")

    assert not decision.reusable


def test_tests_first_red_head_never_authorizes_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)
    (tmp_path / reuse.MODE_MARKER).write_text("tests-first-red\n", encoding="utf-8")

    decision = _resolve(tmp_path, [_run()])

    assert not decision.reusable
    assert "tests-first-red" in decision.reason


def test_runner_toolchain_that_does_not_match_the_pin_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching the pin is how one boundary concludes anything about another's."""
    _stub_identity(monkeypatch)
    monkeypatch.setattr(receipts, "measured_toolchain", lambda: {"python": "3.12.0"})

    decision = _resolve(tmp_path, [_run()])

    assert not decision.reusable
    assert "toolchain" in decision.reason


def test_unavailable_git_evidence_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(root: Path) -> str:
        raise receipts.ValidationEvidenceUnavailable("no such object")

    monkeypatch.setattr(receipts, "content_identity", unavailable)

    decision = _resolve(tmp_path, [_run()])

    assert not decision.reusable
    assert "unavailable" in decision.reason


@pytest.mark.parametrize("payload", [None, {}, {"workflow_runs": "not-a-list"}])
def test_malformed_run_payload_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    _stub_identity(monkeypatch)

    decision = reuse.resolve(
        tmp_path,
        event_name="pull_request",
        head_sha=HEAD,
        repository="fafa33/Project-Hunter",
        token="",
        now=NOW,
        fetch=lambda repository, token, path: payload,
    )

    assert not decision.reusable
    assert "malformed" in decision.reason


def test_api_failure_fails_closed_onto_the_full_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_identity(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("PR_HEAD_SHA", HEAD)
    monkeypatch.setenv("GH_REPO", "fafa33/Project-Hunter")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("GitHub is unavailable")

    monkeypatch.setattr(reuse, "resolve", exploding)

    assert reuse.main([]) == 0


def test_reuse_decision_is_written_to_the_step_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    reuse._emit(reuse.ReuseDecision(False, "because"))

    assert output.read_text(encoding="utf-8").strip() == "reusable=false"


def test_reuse_names_the_same_trusted_workflow_candidate_admission_requires() -> None:
    """The reuse decision and candidate admission must mean the same proof.

    They are deliberately not the same module -- one runs untrusted inside CI,
    the other trusted from the default branch -- so the binding between them is
    asserted here rather than left to a shared import.
    """
    assert reuse.PRE_PR_WORKFLOW_NAME == governance.PRE_PR_WORKFLOW_NAME
    assert reuse.PRE_PR_WORKFLOW_PATH == governance.PRE_PR_WORKFLOW_PATH


def test_reuse_marker_matches_the_canonical_preflight_mode_marker() -> None:
    assert reuse.MODE_MARKER == hunter_pre_push.MODE_MARKER.as_posix()


def test_ci_workflow_guards_the_full_lane_on_the_reuse_decision() -> None:
    """The reuse decision must actually gate the expensive step in CI."""
    workflow = json.loads(json.dumps(_ci_workflow()))
    steps = workflow["jobs"]["quality"]["steps"]
    resolver = next(step for step in steps if "hunter_validation_reuse.py" in str(step.get("run", "")))
    preflight_step = next(step for step in steps if "hunter_pr_preflight.py" in str(step.get("run", "")))

    assert resolver["id"], "the reuse resolver step must expose an id for the guard to reference"
    assert steps.index(resolver) < steps.index(preflight_step)
    assert f"steps.{resolver['id']}.outputs.reusable" in preflight_step["if"]


def _ci_workflow() -> Any:
    import yaml

    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_ci_workflow_can_read_the_trusted_run_record() -> None:
    workflow = _ci_workflow()

    assert workflow["permissions"]["actions"] == "read"
