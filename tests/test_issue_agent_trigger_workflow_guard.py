"""Guard the hosted issue-agent's timeout and failure fanout."""

from pathlib import Path


def test_failed_validation_cannot_start_the_same_run_publisher() -> None:
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    publish = workflow.split("\n  publish:\n", 1)[1].split("\n  finalize:\n", 1)[0]
    assert "needs: [authorize, record-validation, validate, resume-bind]" in publish
    assert "needs.validate.result == 'success'" in publish
    assert "inputs.stage == 'publication'" in publish  # independently authenticated resume retained


def test_hosted_executor_has_time_for_terminal_advisory() -> None:
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    execute = workflow.split("\n  execute:\n", 1)[1].split("\n  bind:\n", 1)[0]
    assert "timeout-minutes: 20" in execute


def test_signed_automated_remediation_admitted_without_owner_actor() -> None:
    """#708: trusted bot dispatch must reach signature verification, not skip authorize."""
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    authorize = workflow.split("\n  authorize:\n", 1)[1].split("\n  bind:\n", 1)[0]
    assert "inputs.remediation_authorization != ''" in authorize
    assert "github.actor == 'github-actions[bot]'" in authorize
    assert "github.actor == github.repository_owner" in authorize
    assert "github.ref == 'refs/heads/main'" in authorize
    assert "needs.scope-admission.outputs.eligible == 'true'" in authorize
    assert "issuer signature verifies" in authorize
    assert "python scripts/hunter_issue_agent_lifecycle.py" in authorize


def test_terminal_signed_validation_failure_never_releases_publisher() -> None:
    workflow = Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text()
    record = workflow.split("\n  record-validation:\n", 1)[1].split("\n  publish:\n", 1)[0]
    publish = workflow.split("\n  publish:\n", 1)[1].split("\n  finalize:\n", 1)[0]
    assert "publication_eligible: ${{ steps.advance.outputs.publication_eligible }}" in record
    assert "id: advance" in record
    assert publish.count("needs.record-validation.outputs.publication_eligible == 'true'") == 2
    assert "inputs.stage == 'publication'" in publish  # independent signed resume path


def test_a_signed_terminal_failure_reports_publication_as_ineligible(monkeypatch, capsys) -> None:
    import argparse

    import hunter_issue_agent_lifecycle as lifecycle

    outputs: list[dict[str, str]] = []
    decision = lifecycle.state.Decision("fail", code="PRE_PUSH_RUFF_FAILED")
    monkeypatch.setattr(lifecycle, "_writer", lambda *_a, **_k: object())
    monkeypatch.setattr(lifecycle, "_github", lambda *_a, **_k: object())
    monkeypatch.setattr(lifecycle, "_store", lambda *_a, **_k: object())
    monkeypatch.setattr(lifecycle, "_ed25519", lambda *_a, **_k: object())
    monkeypatch.setattr(lifecycle, "_output", lambda **kw: outputs.append(kw))
    monkeypatch.setattr(lifecycle.control, "step", lambda **_k: decision)
    arguments = argparse.Namespace(role="record-validation", issue=705, advisory="", job="")
    assert lifecycle.cmd_step(object(), arguments) == 0
    assert outputs == [{"publication_eligible": "false"}]
