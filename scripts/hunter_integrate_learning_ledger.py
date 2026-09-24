#!/usr/bin/env python3
"""Render a non-authoritative DPM registry candidate from a trusted learning ledger."""

from __future__ import annotations

import json
from pathlib import Path

from hunter.evidence_intelligence.controlled_learning_integration import integrate_learning_ledger

REGISTRY = Path("docs/DEFECT_REGISTRY.json")
LEDGER = Path("hunter-learning-ledger.json")


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    result = integrate_learning_ledger(ledger, REGISTRY.read_bytes())
    print(result.registry_bytes.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
