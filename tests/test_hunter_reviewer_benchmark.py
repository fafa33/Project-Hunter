import json
from pathlib import Path

import hunter_pre_ready_review as review
import hunter_reviewer_benchmark as benchmark

ROOT = Path(__file__).resolve().parents[1]


def test_local_reviewer_cannot_gain_authority_without_benchmark_evidence():
    pool, error = review.load_reviewer_pool()
    assert not error
    local = next(a for a in pool["agents"] if a["id"] == "local-ollama")
    assert local["quality_gate"]["required"] is True
    assert local["quality_gate"]["benchmark_id"]


def test_score_measures_recall_and_false_positive_rate():
    cases = [{"id": "bug", "expected": "finding"}, {"id": "clean", "expected": "clear"}]
    recall, fp = benchmark.score(cases, {"bug": {"found": True}, "clean": {"found": False}})
    assert recall == 1.0
    assert fp == 0.0


def test_benchmark_fixture_covers_recorded_failure_classes():
    data = json.loads((ROOT / "tests/fixtures/reviewer_benchmark_cases.json").read_text())
    ids = {case["id"] for case in data}
    assert {"stale-head", "missing-authority", "retry-exhaustion", "workflow-permission", "clean-control"} <= ids


def test_failed_quality_threshold_keeps_local_reviewer_triage_only():
    pool, error = review.load_reviewer_pool()
    assert not error
    local = next(a for a in pool["agents"] if a["id"] == "local-ollama")
    gate = local["quality_gate"]
    result = gate["last_benchmark"]
    passed = (
        result["recall"] >= gate["minimum_recall"]
        and result["false_positive_rate"] <= gate["maximum_false_positive_rate"]
    )
    assert passed is False
    assert result["passed"] is False
    assert gate["authority_until_pass"] == "triage-only"
