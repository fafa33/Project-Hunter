from pathlib import Path

WORKFLOW = Path(".github/workflows/hunter-knowledge-learning.yml").read_text()
COLLECTOR = Path("scripts/hunter_collect_learning_observations.py").read_text()


def test_learning_workflow_has_no_write_authority():
    assert "contents: read" in WORKFLOW and "pull-requests: read" in WORKFLOW
    assert "contents: write" not in WORKFLOW and "statuses: write" not in WORKFLOW and "actions: write" not in WORKFLOW


def test_learning_workflow_executes_trusted_default_branch_engine():
    assert "ref: ${{ github.event.repository.default_branch }}" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW


def test_collector_does_not_invent_defect_classification():
    compact = COLLECTOR.replace(" ", "")
    assert '"classification":None' in compact
    assert '"claimed_family_id":None' in compact
