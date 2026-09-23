#!/usr/bin/env python3
"""Build an exact-head knowledge-learning artifact from trusted bounded observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hunter.evidence_intelligence.incremental_knowledge_learning import build_learning_ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--observations", type=Path)
    args = parser.parse_args()
    observations = []
    if args.observations:
        raw = json.loads(args.observations.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise SystemExit("observations must be a JSON list")
        observations.extend(raw)
    ledger = build_learning_ledger(args.pr, args.head, args.base, observations, Path("docs/DEFECT_REGISTRY.json"))
    print(json.dumps(ledger, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
