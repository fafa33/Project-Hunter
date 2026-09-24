from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/hunter-knowledge-learning.yml")
COLLECTOR = Path("scripts/hunter_collect_learning_observations.py").read_text()


def test_collector_does_not_invent_defect_classification():
    assert '"classification": None' in COLLECTOR
    assert '"claimed_family_id": None' in COLLECTOR


def test_learning_workflow_has_no_write_authority():
    text = WORKFLOW_PATH.read_text()
    assert "contents: read" in text
    assert "pull-requests: read" in text
    assert "contents: write" not in text
    assert "statuses: write" not in text
    assert "actions: write" not in text


def test_learning_workflow_executes_trusted_default_branch_engine():
    text = WORKFLOW_PATH.read_text()
    assert "github.event.repository.default_branch" in text
    assert "persist-credentials: false" in text


def test_collector_binds_only_exact_head(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head = "a" * 40
    base = "b" * 40
    payload = [
        {"id": 1, "commit_id": head, "user": {"login": "codex"}, "path": "x.py", "line": 7, "body": "finding"},
        {"id": 2, "commit_id": "c" * 40, "user": {"login": "old"}, "path": "y.py", "line": 9, "body": "stale"},
    ]

    def fake(_repo, _token, _method, path):
        return payload if "/comments?" in path else []

    monkeypatch.setattr(collector.governance, "request_json", fake)
    result = collector.collect("fafa33/Project-Hunter", "token", 492, head, base)
    assert len(result) == 1
    assert result[0]["event_id"] == "review-comment-1"
    assert result[0]["reviewed_head_sha"] == head
    assert result[0]["classification"] is None


def test_learning_workflow_never_uses_pull_request_target():
    text = WORKFLOW_PATH.read_text()
    assert "pull_request_target:" not in text
    assert "pull_request_target:" not in text
    assert "pull_request:\n" not in text
    assert "pull_request_review:" in text


def test_bootstrap_never_executes_candidate_learning_code():
    assert "Detect trusted learning engine" in WORKFLOW_PATH.read_text()
    assert "available=false" in WORKFLOW_PATH.read_text()
    assert "if: steps.engine.outputs.available == 'true'" in WORKFLOW_PATH.read_text()
    assert "no candidate code will be executed" in WORKFLOW_PATH.read_text()


def test_learning_workflow_exposes_src_package():
    assert "PYTHONPATH: src" in WORKFLOW_PATH.read_text()


def test_collector_reads_top_level_reviews(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head = "a" * 40
    base = "b" * 40

    def fake(_repo, _token, _method, path):
        if "/comments?" in path:
            return []
        if "/reviews?" in path:
            return [
                {
                    "id": 7,
                    "commit_id": head,
                    "user": {"login": "codex"},
                    "body": "top level finding",
                    "state": "CHANGES_REQUESTED",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", fake)
    result = collector.collect("fafa33/Project-Hunter", "token", 492, head, base)
    assert [item["event_id"] for item in result] == ["review-7"]


def test_learning_workflow_collects_optional_sonar_without_granting_write_authority():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "hunter_collect_sonar_observations.py" in text
    assert '--head "$HEAD_SHA"' in text
    assert '--base "$BASE_SHA"' in text
    assert "sonar-learning-observations.json" in text
    assert "pull_request_target" not in text
    assert "contents: write" not in text


def test_learning_workflow_renders_controlled_registry_candidate_without_write_authority():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "hunter_integrate_learning_ledger.py" in text
    assert "hunter-defect-registry-candidate.json" in text
    assert "contents: write" not in text
    assert "git commit" not in text
    assert "git push" not in text
