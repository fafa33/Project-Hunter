from pathlib import Path

from hunter_issue_scope_admission import eligible


def test_scope_admission_accepts_canonical_complete_scope():
    from test_issue_agent_trigger import _event

    assert eligible(_event())


def test_scope_admission_rejects_marker_fragments_and_malformed_blocks():
    from test_issue_agent_trigger import _event

    for body in (
        "<!-- hunter-task-scope-v1",
        "<!-- hunter-task-scope-v1\n{}",
        "<!-- hunter-task-scope-v1\n{}\n-->",
        "",
    ):
        event = _event()
        event["issue"]["body"] = body
        assert not eligible(event)


def test_privileged_authorize_requires_unprivileged_scope_gate():
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    assert "needs: scope-admission" in workflow
    assert "needs.scope-admission.outputs.eligible == 'true'" in workflow
    assert "environment: hunter-issue-agent-control" in workflow
