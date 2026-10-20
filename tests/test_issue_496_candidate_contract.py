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
