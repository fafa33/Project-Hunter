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


class ReviewerDispatchRefused(RuntimeError):
    def __init__(self, request_error):
        self.request_error = request_error


def reviewer_dispatch_unavailable(exc):
    if isinstance(exc, governance.transport.GitHubUnavailable):
        return False
    return exc.status_code in REVIEWER_UNAVAILABLE_DISPATCH_STATUS


def collect_attempts(pool, head, backend):
    for agent in pool:
        try:
            trigger = backend.trigger(agent, 1)
        except ReviewerDispatchRefused:
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
        except ReviewerDispatchRefused:
            continue""",
        "        trigger = backend.trigger(agent, 1)",
    )
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("try" in error for error in errors)


def test_dff025_guard_rejects_a_broad_pool_exception_catcher(tmp_path, monkeypatch):
    """Catching GitHubRequestError in the outer loop erases post-trigger identities."""

    source = PER_REVIEWER_ISOLATION_SOURCE.replace(
        "        except ReviewerDispatchRefused:\n            continue",
        "        except governance.transport.GitHubRequestError as exc:\n            if not reviewer_dispatch_unavailable(exc): raise\n            continue",
    )
    monkeypatch.setattr(prevention, "ROOT", _collector_tree(tmp_path, source))

    errors = prevention.validate_reviewer_unavailability_is_per_reviewer()

    assert any("GitHubRequestError" in error for error in errors)


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

    # Functions are read from the AST, so a commented-out definition is absent.
    # The lifecycle states are no longer text-checked at all: they are verified
    # by exercising the classifier, which prose cannot satisfy either way.
    assert any("function missing" in error for error in errors)


# --- The lifecycle contract is exercised, not searched for --------------------


def test_lifecycle_contract_passes_against_the_live_implementation():
    assert prevention.validate_review_request_lifecycle_contract() == []


def test_lifecycle_contract_rejects_a_transient_wait_for_a_decided_failure(monkeypatch):
    """The finding: a failed or missing proof must never classify as waiting.

    A source-text guard could not catch this -- the state names all still appear
    in the module. Only exercising the classifier does.
    """

    import hunter_review_orchestrator as orchestration

    monkeypatch.setattr(
        orchestration,
        "prerequisite_cycle_state",
        lambda reason: (
            "WAITING_FOR_PREREQUISITE" if reason.startswith("TRUSTED_PREFLIGHT_") else "PREREQUISITE_BLOCKED"
        ),
    )

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("TRUSTED_PREFLIGHT_FAILURE" in error for error in errors)
    assert any("TRUSTED_PREFLIGHT_MISSING" in error for error in errors)


def test_lifecycle_contract_rejects_an_unknown_reason_inheriting_a_wait(monkeypatch):
    import hunter_review_orchestrator as orchestration

    monkeypatch.setattr(orchestration, "prerequisite_cycle_state", lambda reason: "WAITING_FOR_PREREQUISITE")

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("SOME_FUTURE_PREREQUISITE" in error for error in errors)


def test_lifecycle_contract_rejects_a_blocked_prerequisite_projected_as_pending(monkeypatch):
    import hunter_merge_readiness_v2 as readiness

    monkeypatch.setattr(readiness, "review_wait_state", lambda state, detail: ("pending", state))

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("PREREQUISITE_BLOCKED" in error and "decided block" in error for error in errors)


def test_lifecycle_contract_rejects_a_waiting_state_projected_as_red(monkeypatch):
    import hunter_merge_readiness_v2 as readiness

    monkeypatch.setattr(readiness, "review_wait_state", lambda state, detail: None)

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("WAITING_FOR_PREREQUISITE" in error and "pending" in error for error in errors)


def test_lifecycle_contract_rejects_a_prerequisite_block_that_records_a_dispatch(tmp_path, monkeypatch):
    """A prerequisite cycle carrying a trigger id suppresses the first dispatch."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    for name, body in {
        "hunter_pre_ready_review.py": "def verify_local_review_request():\n    pass\n",
        "hunter_pre_push.py": "def enforce_declared_review_request():\n    pass\n",
        "hunter_review_orchestrator.py": (
            "def publish_prerequisite_block(repository, token, pr, head, reason):\n"
            "    # trigger_id=None\n"
            "    return ReviewCycle(state='x', trigger_id=current_run_id())\n"
        ),
    }.items():
        (tmp_path / "scripts" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("previous.trigger_id" in error or "current reconcile run" in error for error in errors)


def test_lifecycle_contract_is_not_satisfied_by_a_commented_trigger_id(tmp_path, monkeypatch):
    """The comment in the fixture above says trigger_id=None; the code does not."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    for name, body in {
        "hunter_pre_ready_review.py": "def verify_local_review_request():\n    pass\n",
        "hunter_pre_push.py": "def enforce_declared_review_request():\n    pass\n",
        "hunter_review_orchestrator.py": (
            '"""trigger_id=None WAITING_FOR_PREREQUISITE PREREQUISITE_BLOCKED"""\n'
            "def publish_prerequisite_block(repository, token, pr, head, reason):\n"
            "    return ReviewCycle(state='x', trigger_id=7)\n"
        ),
    }.items():
        (tmp_path / "scripts" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    assert any("previous.trigger_id" in error for error in prevention.validate_review_request_lifecycle_contract())


def test_lifecycle_contract_accepts_an_equivalent_prerequisite_publisher(tmp_path, monkeypatch):
    """A canonically valid equivalent must not be blocked."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    for name, body in {
        "hunter_pre_ready_review.py": "def verify_local_review_request():\n    pass\n",
        "hunter_pre_push.py": "def enforce_declared_review_request():\n    pass\n",
        "hunter_review_orchestrator.py": (
            "def publish_prerequisite_block(repo, tok, number, sha, why, *, previous=None):\n"
            "    record = ReviewCycle(\n"
            "        state=prerequisite_cycle_state(why),\n"
            "        trigger_id=previous.trigger_id if previous is not None else None,\n"
            "    )\n"
            "    return record\n"
            "def ensure_current(repo, tok, number):\n"
            "    previous = object()\n"
            "    return publish_prerequisite_block(repo, tok, number, 'h', 'x', previous=previous)\n"
        ),
    }.items():
        (tmp_path / "scripts" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    assert not [
        e for e in prevention.validate_review_request_lifecycle_contract() if "trigger_id" in e or "existing cycle" in e
    ]


def test_lifecycle_contract_rejects_unreadable_evidence_treated_as_absent(monkeypatch):
    """The second-round finding: only `absent` may map to the transient code."""

    import hunter_review_orchestrator as orchestration

    monkeypatch.setattr(orchestration, "_request_read_code", lambda state, document: "REVIEW_REQUEST_MISSING")

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("invalid" in error for error in errors)
    assert any("unavailable" in error for error in errors)


def test_lifecycle_contract_rejects_a_non_object_present_document_read_as_missing(monkeypatch):
    import hunter_review_orchestrator as orchestration

    real = orchestration._request_read_code
    monkeypatch.setattr(
        orchestration,
        "_request_read_code",
        lambda state, document: "REVIEW_REQUEST_MISSING" if state == "present" else real(state, document),
    )

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("not an object" in error for error in errors)


def test_lifecycle_contract_rejects_fake_review_verifier_receiver(tmp_path, monkeypatch):
    """Calling another object's same-named method must not satisfy the push-boundary edge."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    sources = {
        "hunter_pre_ready_review.py": "def verify_local_review_request():\n    return True\n",
        "hunter_pre_push.py": (
            "def enforce_declared_review_request():\n"
            "    return fake.verify_local_review_request()\n"
            "def enforce_pre_push():\n"
            "    enforce_declared_review_request()\n"
        ),
        "hunter_review_orchestrator.py": (
            "def publish_prerequisite_block(repo, tok, number, sha, why, *, previous=None):\n"
            "    return ReviewCycle(trigger_id=previous.trigger_id if previous else None)\n"
            "def ensure_current():\n"
            "    previous = object()\n"
            "    return publish_prerequisite_block('r', 't', 1, 'h', 'x', previous=previous)\n"
        ),
    }
    for name, body in sources.items():
        (tmp_path / "scripts" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("verify_local_review_request" in error for error in errors)


def test_lifecycle_contract_rejects_previous_keyword_hardcoded_to_none(tmp_path, monkeypatch):
    """A keyword named previous is not enough; the existing cycle must flow through it."""

    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    sources = {
        "hunter_pre_ready_review.py": "def verify_local_review_request():\n    return True\n",
        "hunter_pre_push.py": (
            "def enforce_declared_review_request():\n"
            "    return review.verify_local_review_request()\n"
            "def enforce_pre_push():\n"
            "    enforce_declared_review_request()\n"
        ),
        "hunter_review_orchestrator.py": (
            "def publish_prerequisite_block(repo, tok, number, sha, why, *, previous=None):\n"
            "    return ReviewCycle(trigger_id=previous.trigger_id if previous else None)\n"
            "def ensure_current():\n"
            "    return publish_prerequisite_block('r', 't', 1, 'h', 'x', previous=None)\n"
        ),
    }
    for name, body in sources.items():
        (tmp_path / "scripts" / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(prevention, "ROOT", tmp_path)

    errors = prevention.validate_review_request_lifecycle_contract()

    assert any("existing cycle" in error for error in errors)


def test_dff028_guard_accepts_single_owner_cutover_contract():
    assert prevention.validate_authority_cutover_single_owner() == []


def test_dff028_guard_rejects_reintroduced_legacy_bridge_runtime_edge():
    workflows = {
        "hunter-governance-review.yml": "actions: read\npython engine/scripts/hunter_governance_review_v2.py",
        "hunter-governance-reconcile.yml": "actions: write\npython scripts/hunter_governance_review_v2.py\npython scripts/hunter_review_orchestrator.py ensure",
        "hunter-merge-readiness.yml": "python scripts/hunter_merge_readiness_v2.py\nbootstrap_external_review_469.py readiness",
    }
    errors = prevention._authority_cutover_contract_errors(workflows)
    assert any("legacy bootstrap bridge remains runtime-reachable" in error for error in errors)


def test_dff028_guard_rejects_missing_canonical_readiness_projection():
    workflows = {
        "hunter-governance-review.yml": "actions: read\npython engine/scripts/hunter_governance_review_v2.py",
        "hunter-governance-reconcile.yml": "actions: write\npython scripts/hunter_governance_review_v2.py\npython scripts/hunter_review_orchestrator.py ensure",
        "hunter-merge-readiness.yml": "echo no canonical readiness",
    }
    errors = prevention._authority_cutover_contract_errors(workflows)
    assert any("canonical readiness projector" in error for error in errors)
