"""Diagnostic-only, non-interpretive field scan of a raw Sky disclosure document.

Purpose: given a raw HTML file already fetched by
`.github/workflows/acquire-sky-valuation-evidence.yml`, mechanically locate
candidate substrings that *look like* a percentage, a currency amount, or a
date/period expression, and record them verbatim with surrounding context.

This script makes no judgment about ADR 0022 compatibility. It does not decide
whether a candidate is "the" value-capture rate, "the" attributable amount, or
"the" accounting period -- it only reports what pattern-matched, where, and
explicitly reports when nothing matched (a parser failure/absence is itself
recorded, never silently omitted). A human operator reads this output and
decides; `hunter.valuation_evidence` still requires a hand-authored manifest
either way (see the workflow file's own header comment for why).

Not a canonical parser. Not registered with any `hunter.value_capture` source.
Diagnostic tooling only, mirroring the read-only nature of the workflow that
invokes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_PERCENT_PATTERN = re.compile(r"\d{1,3}(?:\.\d+)?\s?%")
_CURRENCY_AMOUNT_PATTERN = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:million|billion|thousand|M|B|K)?", re.IGNORECASE)
_DATE_OR_PERIOD_PATTERN = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"
    r"|\d{1,3}\s?-?\s?days?"
    r"|annual(?:ly|ized)?"
    r"|quarter(?:ly)?"
    r"|monthly"
    r"|per\s+(?:day|week|month|quarter|year)"
    r"|January|February|March|April|May|June|July|August|September|October|November|December)\b",
    re.IGNORECASE,
)
_CONTEXT_RADIUS = 60


def _matches_with_context(pattern: re.Pattern[str], text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - _CONTEXT_RADIUS)
        end = min(len(text), match.end() + _CONTEXT_RADIUS)
        found.append(
            {
                "matched_text": match.group(0),
                "char_offset": match.start(),
                "surrounding_context": text[start:end].replace("\n", " ").strip(),
            }
        )
    return found


def scan(raw_html_path: Path) -> dict[str, Any]:
    if not raw_html_path.exists():
        return {
            "source_file": str(raw_html_path),
            "parser_execution_status": "failed",
            "parser_failure_reason": f"file does not exist: {raw_html_path}",
        }
    raw_bytes = raw_html_path.read_bytes()
    if not raw_bytes:
        return {
            "source_file": str(raw_html_path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "byte_count": 0,
            "parser_execution_status": "failed",
            "parser_failure_reason": "fetched file is empty (zero bytes)",
        }
    text = raw_bytes.decode("utf-8", errors="replace")
    percent_candidates = _matches_with_context(_PERCENT_PATTERN, text)
    currency_candidates = _matches_with_context(_CURRENCY_AMOUNT_PATTERN, text)
    date_or_period_candidates = _matches_with_context(_DATE_OR_PERIOD_PATTERN, text)
    return {
        "source_file": str(raw_html_path),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "byte_count": len(raw_bytes),
        "parser_execution_status": "completed",
        "note": (
            "Mechanical pattern matches only. No candidate below is asserted to be a "
            "qualifying, attributable, exact-365-day amount or rate under ADR 0022 -- "
            "that determination requires human interpretation of the full disclosure "
            "and is explicitly out of this script's scope."
        ),
        "percent_like_candidates": {
            "count": len(percent_candidates),
            "matches": percent_candidates if percent_candidates else "NONE FOUND",
        },
        "currency_amount_like_candidates": {
            "count": len(currency_candidates),
            "matches": currency_candidates if currency_candidates else "NONE FOUND",
        },
        "date_or_period_like_candidates": {
            "count": len(date_or_period_candidates),
            "matches": date_or_period_candidates if date_or_period_candidates else "NONE FOUND",
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: diagnostic_parse_sky_disclosure.py RAW_HTML_PATH OUTPUT_JSON_PATH", file=sys.stderr)
        return 1
    raw_html_path = Path(argv[0])
    output_path = Path(argv[1])
    result = scan(raw_html_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("parser_execution_status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
