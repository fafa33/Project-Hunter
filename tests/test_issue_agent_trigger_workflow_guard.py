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
