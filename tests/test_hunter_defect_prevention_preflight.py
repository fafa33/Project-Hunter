from __future__ import annotations

import json
import re

import hunter_defect_prevention_preflight as prevention


def test_current_defect_prevention_lifecycle_is_valid() -> None:
    assert prevention.validate_defect_prevention_lifecycle() == []


def test_legacy_guarded_is_not_treated_as_prevented() -> None:
    lifecycle = json.loads(prevention.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    assert lifecycle["legacy_status_semantics"]["guarded"] == "detected"
    assert lifecycle["stages"][-1] == "prevented"


def test_prh_007_has_local_hosted_merge_and_recurrence_evidence() -> None:
    lifecycle = json.loads(prevention.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    evidence = lifecycle["explicit_enforcement"]["PRH-007"]
    assert evidence["state"] == "prevented"
    for field in prevention.REQUIRED_ENFORCEMENT_FIELDS:
        assert isinstance(evidence[field], str)
        assert evidence[field].strip()


def test_prevented_state_requires_all_enforcement_evidence(monkeypatch, tmp_path) -> None:
    registry = {"defects": [{"id": "X-001", "status": "guarded"}]}
    lifecycle = {
        "version": 1,
        "stages": list(prevention.EXPECTED_STAGES),
        "legacy_status_semantics": {"guarded": "detected"},
        "explicit_enforcement": {
            "X-001": {
                "state": "prevented",
                "local": "hook",
                "hosted": "ci",
                "merge": "",
                "recurrence": "escalate",
            }
        },
    }
    registry_path = tmp_path / "registry.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    lifecycle_path.write_text(json.dumps(lifecycle), encoding="utf-8")
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(prevention, "LIFECYCLE_PATH", lifecycle_path)

    errors = prevention.validate_defect_prevention_lifecycle()

    assert any("requires non-empty merge evidence" in error for error in errors)


def test_unknown_legacy_status_fails_closed(monkeypatch, tmp_path) -> None:
    registry = {"defects": [{"id": "X-001", "status": "mystery"}]}
    lifecycle = {
        "version": 1,
        "stages": list(prevention.EXPECTED_STAGES),
        "legacy_status_semantics": {"guarded": "detected"},
        "explicit_enforcement": {},
    }
    registry_path = tmp_path / "registry.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    lifecycle_path.write_text(json.dumps(lifecycle), encoding="utf-8")
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(prevention, "LIFECYCLE_PATH", lifecycle_path)

    errors = prevention.validate_defect_prevention_lifecycle()

    assert "legacy registry status has no prevention semantics: mystery" in errors


# --- DFF-025 guard: adversarial bypass pass -----------------------------------
#
# Both directions are checked for every invariant: content that does not
# actually enforce the boundary must be rejected, and a canonically equivalent
# implementation must not be.


def _collector_tree(tmp_path, source):
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "hunter_reviewer_collector.py").write_text(source, encoding="utf-8")
    return tmp_path


PER_REVIEWER_ISOLATION_SOURCE = """
import governance


def reviewer_dispatch_unavailable(exc):
    if isinstance(exc, governance.transport.GitHubUnavailable):
        return False
    return exc.status_code in REVIEWER_UNAVAILABLE_DISPATCH_STATUS


def collect_attempts(pool, head, backend):
    for agent in pool:
        try:
            trigger = backend.trigger(agent, 1)
        except governance.transport.GitHubRequestError as exc:
            if not reviewer_dispatch_unavailable(exc):
                raise
            continue
"""


def test_dff025_guard_accepts_the_shipped_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, PER_REVIEWER_ISOLATION_SOURCE))

    assert prevention.validate_reviewer_unavailability_is_per_reviewer() == []


def test_dff025_guard_accepts_a_renamed_but_equivalent_implementation(tmp_path, monkeypatch):
    """A canonically valid equivalent must not be blocked.

    Different local names, quoting and ordering change no behaviour; a guard
    that rejected them would create false merge blockage.
    """

    equivalent = re.sub(r"\bexc\b", "failure", PER_REVIEWER_ISOLATION_SOURCE).replace("trigger =", "record =")
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, equivalent))

    assert prevention.validate_reviewer_unavailability_is_per_reviewer() == []


def test_dff025_guard_rejects_a_missing_single_decision_point(tmp_path, monkeypatch):
    source = PER_REVIEWER_ISOLATION_SOURCE.replace("def reviewer_dispatch_unavailable(exc):", "def unused(exc):")
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("single" in error for error in errors)


def test_dff025_guard_rejects_classifying_exhausted_infrastructure_as_unavailable(tmp_path, monkeypatch):
    source = PER_REVIEWER_ISOLATION_SOURCE.replace(
        "    if isinstance(exc, governance.transport.GitHubUnavailable):\n        return False\n", ""
    )
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("GitHubUnavailable" in error for error in errors)


def test_dff025_guard_rejects_a_second_decision_point(tmp_path, monkeypatch):
    """The exact defect: one call site honours the policy, another re-derives it."""

    source = PER_REVIEWER_ISOLATION_SOURCE + """

def _dispatch(exc):
    if exc.status_code in {403, 404, 422}:
        return "unavailable"
    raise exc
"""
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("one decision point" in error for error in errors)


def test_dff025_guard_rejects_an_unisolated_pool_loop(tmp_path, monkeypatch):
    """The shape that stranded PR #482: trigger outside any handler."""

    source = PER_REVIEWER_ISOLATION_SOURCE.replace(
        """        try:
            trigger = backend.trigger(agent, 1)
        except governance.transport.GitHubRequestError as exc:
            if not reviewer_dispatch_unavailable(exc):
                raise
            continue""",
        "        trigger = backend.trigger(agent, 1)",
    )
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("try" in error for error in errors)


def test_lifecycle_contract_is_not_satisfied_by_a_comment(tmp_path, monkeypatch):
    """A marker written as prose executes nothing and must not satisfy the guard."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    for name in (
        "hunter_pre_ready_review.py",
        "hunter_pre_push.py",
        "hunter_review_orchestrator.py",
        "hunter_merge_readiness_v2.py",
    ):
        (tmp_path / "scripts" / name).write_text(
            '"""WAITING_FOR_REVIEW_REQUEST WAITING_FOR_PREREQUISITE PREREQUISITE_BLOCKED"""\n'
            "# def verify_local_review_request() -- WAITING_FOR_PREREQUISITE\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("function missing" in error for error in errors)
    assert any("state missing" in error for error in errors)
