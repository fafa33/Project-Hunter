#!/usr/bin/env python3
"""Single deterministic local/offline entry point that closes the learning loop.

Composes the existing, independently tested `build_learning_ledger` and
`materialize_learning_ledger` authorities end to end: bounded observations in,
either a previewed candidate or an atomically-applied registry update out.
It defines no new registry, persistence, replay, or write-path semantics --
those remain exactly where `incremental_knowledge_learning.py` and
`controlled_learning_integration.py` already own them. This script exists
because nothing previously called `materialize_learning_ledger`, so the
render-only CI candidate never had a documented, GitHub-Actions-independent
path back into the canonical registry. Requires no network, provider, or
LLM: observations are supplied as a file, exactly like
`hunter_incremental_knowledge.py` already requires.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from hunter.evidence_intelligence import controlled_learning_integration as learning
from hunter.evidence_intelligence.incremental_knowledge_learning import (
    LearningLedgerError,
    build_learning_ledger,
)

_PREFIX = "[Hunter Learning Canonicalization]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.observations.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{_PREFIX} FAIL: observations input is unreadable: {exc}")
        return 2
    if not isinstance(raw, list):
        print(f"{_PREFIX} FAIL: observations must be a JSON list")
        return 2

    try:
        ledger = build_learning_ledger(args.pr, args.head, args.base, raw, learning.CANONICAL_DEFECT_REGISTRY)
    except LearningLedgerError as exc:
        print(f"{_PREFIX} FAIL: {exc}")
        return 2

    # A per-invocation temporary ledger file, swapped in for the shared
    # CANONICAL_LEARNING_LEDGER path only for the duration of this call and
    # always restored: two invocations processing different PRs against the
    # same working tree must never race on one shared scratch file, where a
    # later write could make an earlier invocation materialize the wrong
    # PR's evidence while silently dropping its own.
    fd, temp_ledger_name = tempfile.mkstemp(
        prefix=".hunter-learning-ledger-", suffix=".json", dir=learning.CANONICAL_LEARNING_LEDGER.parent
    )
    os.close(fd)
    temp_ledger_path = Path(temp_ledger_name)
    temp_ledger_path.write_text(json.dumps(ledger, sort_keys=True, indent=2), encoding="utf-8")
    original_ledger_path = learning.CANONICAL_LEARNING_LEDGER
    learning.CANONICAL_LEARNING_LEDGER = temp_ledger_path
    try:
        try:
            result = learning.materialize_learning_ledger(dry_run=args.dry_run)
        except learning.ControlledLearningIntegrationError as exc:
            print(f"{_PREFIX} FAIL: {exc}")
            return 1
    finally:
        learning.CANONICAL_LEARNING_LEDGER = original_ledger_path
        temp_ledger_path.unlink(missing_ok=True)

    mode = "DRY-RUN" if args.dry_run else "PASS"
    print(
        f"{_PREFIX} {mode}: changed={str(result.changed).lower()} "
        f"integrated={len(result.integrated_proposal_ids)} skipped={result.skipped_items}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
