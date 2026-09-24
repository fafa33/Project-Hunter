"""Materialize a verified Hunter learning ledger into a registry candidate."""

from __future__ import annotations

import argparse
from pathlib import Path

from hunter.evidence_intelligence.controlled_learning_integration import (
    ControlledLearningIntegrationError,
    materialize_learning_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    registry = Path("docs/DEFECT_REGISTRY.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = materialize_learning_ledger(args.ledger, registry, dry_run=args.dry_run)
    except ControlledLearningIntegrationError as error:
        print(f"[Hunter Learning Promotion] FAIL: {error}")
        return 1
    mode = "DRY-RUN" if args.dry_run else "PASS"
    print(
        f"[Hunter Learning Promotion] {mode}: changed={str(result.changed).lower()} "
        f"integrated={len(result.integrated_proposal_ids)} skipped={result.skipped_items}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
