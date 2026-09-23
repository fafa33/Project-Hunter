#!/usr/bin/env python3
"""Validate one governed finding and emit a proposal-only Knowledge Extraction artifact."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from hunter.evidence_intelligence.knowledge_extraction_authority import (
    KnowledgeExtractionAuthority,
    KnowledgeExtractionError,
    finding_from_dict,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "docs" / "DEFECT_REGISTRY.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)

    try:
        try:
            raw = json.loads(args.input.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeExtractionError("finding input is unreadable") from exc
        finding = finding_from_dict(raw)
        proposal = KnowledgeExtractionAuthority(args.registry).extract(finding)
    except KnowledgeExtractionError as exc:
        print(f"KNOWLEDGE_EXTRACTION_REJECTED: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(asdict(proposal), indent=2, sort_keys=True) + "\n"
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
