"""Issue #496: governed prevention build identity and exact-head Ready admission.

These boundary regressions do not simulate a real provider network or execute
arbitrary defect-regression commands; the trusted proof is mocked at admission.
"""

from __future__ import annotations

import hunter_candidate_admission as admission
from test_hunter_candidate_admission import HEAD, _pr
from test_issue_496_prevention_loop_binding import _build, _compiled_prompt_content, _family, _request, _scope


def test_known_defect_reintroduced_blocks_ready_then_corrected_proof_allows_ready(monkeypatch, tmp_path):
    """An applicable prevention rule remains in the signed build; a failing
    exact-head trusted preflight must not transition Draft to Ready.
    """
    _, machine = _build(monkeypatch, tmp_path, [_family("DFF-RECURRENCE")])
    build = machine.compile_task(_request(), implementation_scope=_scope())
    assert "DFF-RECURRENCE" in _compiled_prompt_content(build)
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=True), _pr(draft=True), _pr(draft=False)])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    verdicts = iter([("failure", "known-defect regression failed"), ("success", "trusted exact-head proof")])
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: next(verdicts))
    marked = []
    monkeypatch.setattr(admission, "mark_ready", lambda _token, node: marked.append(node) or True)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 496, HEAD) == 1
    assert marked == []
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 496, HEAD) == 0
    assert marked == ["PR_test_node"]


def test_provider_retry_preserves_build_and_stale_or_cancelled_event_cannot_ready(monkeypatch, tmp_path):
    """A provider retry must reuse the governed build; a superseded event
    cannot admit a new HEAD even when its previous proof was successful.
    """
    _, machine = _build(monkeypatch, tmp_path, [_family("DFF-FAILOVER")])
    first = machine.compile_task(_request(), implementation_scope=_scope())
    retry = machine.compile_task(_request(), implementation_scope=_scope())
    assert "DFF-FAILOVER" in _compiled_prompt_content(first)
    assert first.compilation.manifest.build_record_id == retry.compilation.manifest.build_record_id
    assert first.envelope.build_record_id == retry.envelope.build_record_id

    new_head = "c" * 40
    current = _pr(draft=True)
    current["head"]["sha"] = new_head
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: current)

    def forbidden(*_args):
        raise AssertionError("stale/cancelled provider event must not reach admission or Ready")

    monkeypatch.setattr(admission.governance, "candidate_admission", forbidden)
    monkeypatch.setattr(admission, "mark_ready", forbidden)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 496, HEAD) == 0


def test_github_workflow_latest_exact_head_controls_admission(monkeypatch):
    core = admission.governance
    state = {"conclusion": "failure", "current_head": HEAD}

    def github_request(_repository, _token, method, path):
        assert method == "GET"
        if path == "pulls/496":
            return {"head": {"sha": state["current_head"]}}
        if path.startswith("actions/runs?"):
            return {
                "workflow_runs": [
                    {
                        "id": 100,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "head_sha": HEAD,
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 101,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "head_sha": HEAD,
                        "status": "completed",
                        "conclusion": state["conclusion"],
                    },
                ]
            }
        raise AssertionError(f"unexpected GitHub request: {path}")

    monkeypatch.setattr(core, "request_json", github_request)
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_: (True, ("src/hunter/example.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_: ("normal", None))
    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", lambda *_: ("success", "trusted ingress"))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *_: ((), None))
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_: ("success", "reviewed"))

    for conclusion in ("failure", "cancelled"):
        state["conclusion"] = conclusion
        verdict, detail = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 496)
        assert verdict == "failure" and conclusion in detail

    state["current_head"] = "c" * 40
    assert core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 496)[0] == "failure"
    state["current_head"] = HEAD
    state["conclusion"] = "success"
    assert core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 496)[0] == "success"


def test_real_model_adapter_retry_retains_build_and_rejects_uncertain_delivery(tmp_path):
    """Exercise the active Model Adapter, never the retired OpenCode runtime."""
    import model_adapter_fixture as fixture
    import pytest
    from test_model_adapter_phase_b import dispatch, prepare

    from hunter.evidence_intelligence.model_adapter import ModelAdapterService, RetryNotAuthorized
    from hunter.evidence_intelligence.model_adapter_persistence import ModelAdapterPersistenceRepository
    from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

    repository = ModelAdapterPersistenceRepository(EvidenceIntelligenceRepository(tmp_path / "adapter.sqlite"))
    service = ModelAdapterService(repository, transport_endpoints=fixture.PHASE_B_ENDPOINTS)
    first = prepare(service)
    refused = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="PROVIDER_REFUSED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_400",
            )
        ),
    ).outcome
    retry = prepare(
        service,
        authority=fixture.attempt_authority(cutoff=fixture.later(30)),
        attempt_ordinal=2,
        predecessor_attempt_id=first.attempt.attempt_id,
        predecessor_outcome=refused,
        recorded_at=fixture.later(31),
    )
    assert retry.attempt.build_record_id == first.attempt.build_record_id
    assert retry.attempt.prompt_artifact_id == first.attempt.prompt_artifact_id
    assert retry.attempt.attempt_id != first.attempt.attempt_id
    assert retry.attempt.predecessor_attempt_id == first.attempt.attempt_id
    with pytest.raises(RetryNotAuthorized):
        prepare(
            service,
            authority=fixture.attempt_authority(cutoff=fixture.later(60)),
            attempt_ordinal=3,
            predecessor_attempt_id=retry.attempt.attempt_id,
            recorded_at=fixture.later(61),
        )


def test_injected_known_defect_regression_rejected_until_fixed(monkeypatch, tmp_path):
    """Run a real failing/passing regression, then feed its result through GitHub admission."""
    import subprocess
    import sys

    core = admission.governance
    candidate = tmp_path / "test_prevent_regression.py"
    candidate.write_text("def test_known_invariant():\n    assert 1 + 1 == 3\n", encoding="utf-8")

    import hunter_defect_prevention_preflight as trusted_preflight

    # The production trusted gate runner owns the verdict. Other gates are
    # deterministic fixtures; its actual Pytest gate executes the candidate.
    real_subprocess_run = subprocess.run
    observed_pytest = []

    def execute_gate(command, **kwargs):
        if command[0] == "pytest":
            result = real_subprocess_run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(candidate)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=35,
            )
            observed_pytest.append(result)
            return result
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(trusted_preflight.subprocess, "run", execute_gate)
    monkeypatch.setattr(trusted_preflight, "trusted_lane_problem", lambda _opts: None)
    state = {"conclusion": None, "head": HEAD}

    def publish_trusted_preflight():
        result = trusted_preflight.run_candidate_quality_gates(tmp_path)
        assert observed_pytest, "trusted preflight must execute the regression gate"
        state["conclusion"] = "success" if result == 0 else "failure"
        return result

    assert publish_trusted_preflight() != 0
    assert "FAILED" in observed_pytest[-1].stdout

    def github_api(_repo, _token, method, path):
        assert method == "GET"
        if path == "pulls/496":
            return {"head": {"sha": state["head"]}}
        if path.startswith("actions/runs?"):
            return {
                "workflow_runs": [
                    {
                        "id": 201,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "head_sha": state["head"],
                        "status": "completed",
                        "conclusion": state["conclusion"],
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", github_api)
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_: (True, ("src/hunter/example.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_: ("normal", None))
    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", lambda *_: ("success", "ingress"))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *_: ((), None))
    monkeypatch.setattr(core, "verify_pre_ready_hostile_review", lambda *_: ("success", "review"))
    assert core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 496)[0] == "failure"

    candidate.write_text("def test_known_invariant():\n    assert 1 + 1 == 2  # fixed\n", encoding="utf-8")
    for cache in (tmp_path / "__pycache__").glob("test_prevent_regression*.pyc"):
        cache.unlink()
    assert publish_trusted_preflight() == 0
    assert "1 passed" in observed_pytest[-1].stdout
    assert core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 496)[0] == "success"
