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


def test_collector_preserves_findings_from_older_heads_for_catch_up(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head = "a" * 40
    base = "b" * 40
    payload = [
        {
            "id": 1,
            "commit_id": head,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "x.py",
            "line": 7,
            "body": "finding",
        },
        {
            "id": 2,
            "commit_id": "c" * 40,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "y.py",
            "line": 9,
            "body": "stale",
        },
    ]

    def fake(_repo, _token, _method, path):
        return payload if "/comments?" in path else []

    monkeypatch.setattr(collector.governance, "request_json", fake)
    result = collector.collect("fafa33/Project-Hunter", "token", 492, head, base)
    assert [item["event_id"] for item in result] == ["review-comment-1", "review-comment-2"]
    assert result[0]["event_id"] == "review-comment-1"
    assert result[0]["reviewed_head_sha"] == head
    assert result[0]["source_event_head_sha"] == head
    assert result[1]["source_event_head_sha"] == "c" * 40
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
                    "user": {"login": "chatgpt-codex-connector[bot]"},
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


def test_learning_workflow_bootstrap_gate_covers_the_canonicalization_cli():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    detect_step = text.split("Detect trusted learning engine", 1)[1].split("Build exact-head", 1)[0]
    assert "scripts/hunter_canonicalize_learning.py" in detect_step


def test_learning_workflow_proves_materialization_automatically_and_only_as_dry_run():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    # The one and only invocation of the CLI in this workflow must carry
    # --dry-run on the same line: this job has no contents: write permission
    # and must never attempt the real atomic registry write.
    assert (
        'python scripts/hunter_canonicalize_learning.py --pr "$PR_NUMBER" --head "$HEAD_SHA" '
        '--base "$BASE_SHA" --observations learning-observations.json --dry-run' in text
    )
    invocations = [line for line in text.splitlines() if "hunter_canonicalize_learning.py" in line and "python" in line]
    assert len(invocations) == 1
    assert all("--dry-run" in line for line in invocations)


def test_learning_workflow_materialization_proof_cannot_block_the_job():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    step = text.split("Prove canonicalization materialization", 1)[1].split("Publish non-authoritative", 1)[0]
    assert "continue-on-error: true" in step


def test_learning_workflow_publishes_the_materialization_proof_log():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    publish_step = text.split("Publish non-authoritative learning artifact", 1)[1]
    assert "hunter-canonicalization-dry-run.log" in publish_step


def test_automatic_canonicalization_uses_existing_local_git_push_without_token_write_authority():
    path = Path(".github/workflows/hunter-canonicalization-auto.yml")
    text = path.read_text(encoding="utf-8")
    assert "hunter_collect_learning_observations.py" in text
    assert "hunter_canonicalization_candidate_pr.py" in text
    assert "Detect trusted canonicalization engine" in text
    assert "candidate code will not be executed" in text
    assert "actions/setup-python@v6" in text
    assert "pip install --only-binary" in text
    assert "steps.engine.outputs.available == 'true'" in text
    assert 'HEAD_SHA="$(gh pr view' in text
    assert "runs-on: [self-hosted, macOS]" in text
    assert "contents: read" in text
    assert "pull-requests: read" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
    # GitHub Actions does not support pull_request_review_thread as an `on:` event.
    # Declaring it invalidates the workflow before any job can be created.
    assert "pull_request_review_thread:" not in text
    assert "pull_request_review:" in text
    assert "pull_request_review_comment:" in text
    assert "workflow_dispatch:" in text
    assert "path: reviewed-pr" in text
    assert "HUNTER_REGRESSION_ROOT: ${{ github.workspace }}/reviewed-pr" in text
    assert "git@github.com:${REPOSITORY}.git" in text
    assert "core.hooksPath .githooks" in text
    assert "gh pr merge" not in text
    assert "refs/heads/main" not in text


def test_structured_owner_disposition_requires_replayable_evidence():
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    parsed = collector._structured_owner_disposition(
        "confirmed and fixed 0123456789abcdef0123456789abcdef01234567: "
        "One canonical invariant [family:DFF-008] "
        "[test:tests/test_hunter_knowledge_learning_workflow.py::test_collector_does_not_invent_defect_classification]"
    )
    assert parsed == (
        "confirmed",
        "One canonical invariant",
        "0123456789abcdef0123456789abcdef01234567",
        "DFF-008",
        ["tests/test_hunter_knowledge_learning_workflow.py::test_collector_does_not_invent_defect_classification"],
    )


def test_collector_rejects_untrusted_reviewer_and_keeps_trusted_history(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head, base = "a" * 40, "b" * 40
    payload = [
        {
            "id": 11,
            "commit_id": "c" * 40,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "x.py",
            "line": 3,
            "body": "trusted",
        },
        {"id": 12, "commit_id": head, "user": {"login": "random-user"}, "path": "x.py", "line": 4, "body": "untrusted"},
    ]
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    monkeypatch.setattr(
        collector.governance, "request_json", lambda _r, _t, _m, path: payload if "/comments?" in path else []
    )
    result = collector.collect("fafa33/Project-Hunter", "token", 530, head, base)
    assert [item["event_id"] for item in result] == ["review-comment-11"]
    assert result[0]["classification"] is None


def test_resolved_owner_disposition_becomes_replayable_confirmed_observation(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head, base = "a" * 40, "b" * 40
    fix = "1" * 40
    test_ref = "tests/test_hunter_knowledge_learning_workflow.py::test_collector_does_not_invent_defect_classification"
    comments = [
        {
            "id": 21,
            "commit_id": "c" * 40,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "scripts/x.py",
            "line": 8,
            "body": "real defect",
        },
        {
            "id": 22,
            "in_reply_to_id": 21,
            "user": {"login": "fafa33"},
            "body": f"confirmed and fixed {fix}: canonical invariant [family:DFF-008] [test:{test_ref}]",
        },
    ]
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    monkeypatch.setattr(collector, "_review_threads", lambda *_args: {21: True})
    monkeypatch.setattr(
        collector.governance, "request_json", lambda _r, _t, _m, path: comments if "/comments?" in path else []
    )
    result = collector.collect("fafa33/Project-Hunter", "token", 530, head, base)
    assert len(result) == 1
    finding = result[0]
    assert finding["classification"] == "confirmed"
    assert finding["reviewed_head_sha"] == head
    assert finding["invariant"] == "canonical invariant"
    assert finding["fix_reference"] == fix
    assert finding["claimed_family_id"] == "DFF-008"
    assert finding["regression_evidence"] == [test_ref]


def test_unresolved_thread_cannot_be_falsely_canonicalized(monkeypatch):
    import sys

    sys.path.insert(0, str(Path("scripts").resolve()))
    import hunter_collect_learning_observations as collector

    head, base = "a" * 40, "b" * 40
    comments = [
        {
            "id": 31,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "scripts/x.py",
            "line": 8,
            "body": "real defect",
        },
        {
            "id": 32,
            "in_reply_to_id": 31,
            "user": {"login": "fafa33"},
            "body": "confirmed and fixed "
            + "1" * 40
            + ": invariant [family:DFF-008] [test:tests/test_hunter_knowledge_learning_workflow.py::test_collector_does_not_invent_defect_classification]",
        },
    ]
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    monkeypatch.setattr(collector, "_review_threads", lambda *_args: {31: False})
    monkeypatch.setattr(
        collector.governance, "request_json", lambda _r, _t, _m, path: comments if "/comments?" in path else []
    )
    result = collector.collect("fafa33/Project-Hunter", "token", 530, head, base)
    assert result[0]["classification"] is None
    assert result[0]["claimed_family_id"] is None


def test_collector_preserves_deleted_review_comment_from_event_payload(tmp_path, monkeypatch):
    import json

    import hunter_collect_learning_observations as collector

    event = {
        "repository": {"full_name": "fafa33/Project-Hunter"},
        "pull_request": {"number": 530},
        "comment": {
            "id": 991,
            "user": {"login": "chatgpt-codex-connector"},
            "body": "P1 durable finding",
            "commit_id": "a" * 40,
            "path": "scripts/example.py",
            "line": 7,
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    monkeypatch.setattr(collector, "_review_threads", lambda *args: {})

    def request_json(repository, token, method, endpoint):
        if "/comments?" in endpoint or "/reviews?" in endpoint:
            return []
        raise AssertionError(endpoint)

    monkeypatch.setattr(collector.governance, "request_json", request_json)
    observations = collector.collect("fafa33/Project-Hunter", "token", 530, "b" * 40, "c" * 40)
    assert [item["event_id"] for item in observations] == ["review-comment-991"]
    assert observations[0]["message"] == "P1 durable finding"
    assert observations[0]["source_event_head_sha"] == "a" * 40


def test_collector_preserves_deleted_trusted_reviewer_finding_via_deleted_action(tmp_path, monkeypatch):
    """Codex P1-A (PR #530), invariant 1: a deleted trusted-reviewer opening
    finding is still recovered as durable neutral evidence when the webhook
    event explicitly carries ``action: deleted``."""
    import json

    import hunter_collect_learning_observations as collector

    event = {
        "action": "deleted",
        "repository": {"full_name": "fafa33/Project-Hunter"},
        "pull_request": {"number": 530},
        "comment": {
            "id": 992,
            "user": {"login": "chatgpt-codex-connector"},
            "body": "deleted trusted finding",
            "commit_id": "a" * 40,
            "path": "scripts/example.py",
            "line": 7,
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    monkeypatch.setattr(collector, "_review_threads", lambda *args: {})

    def request_json(_repository, _token, _method, endpoint):
        if "/comments?" in endpoint or "/reviews?" in endpoint:
            return []
        raise AssertionError(endpoint)

    monkeypatch.setattr(collector.governance, "request_json", request_json)
    observations = collector.collect("fafa33/Project-Hunter", "token", 530, "b" * 40, "c" * 40)
    assert [item["event_id"] for item in observations] == ["review-comment-992"]
    assert observations[0]["message"] == "deleted trusted finding"
    assert observations[0]["classification"] is None


def test_deleted_owner_disposition_reply_is_not_revived_as_active_disposition(tmp_path, monkeypatch):
    """Codex P1-A (PR #530), invariants 2 and 3: a deleted OWNER structured
    disposition reply recovered from the webhook payload must not be treated
    as an active disposition, and must not confirm/canonicalize the finding
    it replied to -- even though the thread itself remains resolved."""
    import json

    import hunter_collect_learning_observations as collector

    fix = "1" * 40
    test_ref = "tests/test_hunter_knowledge_learning_workflow.py::test_collector_does_not_invent_defect_classification"
    live_comments = [
        {
            "id": 21,
            "commit_id": "c" * 40,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "path": "scripts/x.py",
            "line": 8,
            "body": "real defect",
        }
    ]
    event = {
        "action": "deleted",
        "repository": {"full_name": "fafa33/Project-Hunter"},
        "pull_request": {"number": 530},
        "comment": {
            "id": 22,
            "in_reply_to_id": 21,
            "user": {"login": "fafa33"},
            "body": f"confirmed and fixed {fix}: canonical invariant [family:DFF-008] [test:{test_ref}]",
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(collector, "_trusted_reviewer_logins", lambda: frozenset({"chatgpt-codex-connector"}))
    # The underlying thread is (and remains) resolved -- exactly the condition
    # under which a live owner "confirmed" disposition would canonicalize the
    # finding. A retracted/deleted one must not be able to do the same.
    monkeypatch.setattr(collector, "_review_threads", lambda *args: {21: True})
    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda _r, _t, _m, endpoint: live_comments if "/comments?" in endpoint else [],
    )
    observations = collector.collect("fafa33/Project-Hunter", "token", 530, "b" * 40, "c" * 40)
    assert [item["event_id"] for item in observations] == ["review-comment-21"]
    finding = observations[0]
    assert finding["classification"] is None
    assert finding["claimed_family_id"] is None
    assert finding["invariant"] is None
    assert finding["fix_reference"] is None
