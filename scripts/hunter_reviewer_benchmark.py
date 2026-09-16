"""Deterministic quality benchmark for the local Ollama reviewer."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hunter_local_reviewer as local

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests/fixtures/reviewer_benchmark_cases.json"


def score(cases: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> tuple[float, float]:
    positives = [case for case in cases if case["expected"] == "finding"]
    clean = [case for case in cases if case["expected"] == "clear"]
    recall = sum(bool(results[case["id"]]["found"]) for case in positives) / len(positives)
    false_positive_rate = sum(bool(results[case["id"]]["found"]) for case in clean) / len(clean)
    return recall, false_positive_rate


def benchmark(model: str, reviewer: Callable[..., dict[str, Any]] = local.ollama_review) -> dict[str, Any]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    for case in cases:
        review = reviewer(case["text"], model=model)
        found = review.get("verdict") == "findings" or bool(review.get("findings"))
        results[case["id"]] = {"found": found, "verdict": review.get("verdict"), "summary": review.get("summary", "")}
    recall, fp = score(cases, results)
    return {
        "schema": "hunter.reviewer-benchmark.v1",
        "model": model,
        "recall": recall,
        "false_positive_rate": fp,
        "latency_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=local.MODEL)
    parser.add_argument("--output", default=".hunter/local-reviewer-benchmark.json")
    args = parser.parse_args()
    result = benchmark(args.model)
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("model", "recall", "false_positive_rate", "latency_seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
