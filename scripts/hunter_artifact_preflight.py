from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
ADR_INDEX_PATH = ROOT / "docs" / "ADR" / "README.md"
AUDIT_PREFIX = "docs/ARCHITECTURE_AUDITS/"

REQUIRED_REGISTRY_FIELDS = {
    "id",
    "class",
    "source",
    "classification",
    "guard_boundary",
    "guard",
    "test",
    "status",
}
REQUIRED_DEFECT_IDS = {
    "PRH-001",
    "PRH-002",
    "PRH-003",
    "PRH-004",
    "PRH-005",
    "PRH-006",
    "PRH-007",
    "PRH-008",
    "PRH-009",
    "PRH-010",
    "PRH-011",
    "ARCH-AUD-001",
    "ARCH-AUD-002",
    "ARCH-AUD-003",
    "ARCH-AUD-004",
    "ARCH-AUD-005",
    "ARCH-AUD-006",
    "ARCH-AUD-007",
}
REQUIRED_AUDIT_HEADINGS = (
    "## Metadata",
    "## Audit Scope",
    "## Evidence Sources Examined",
    "## Dimension Results",
    "## Findings",
    "## Findings Matrix",
    "## Verdict Derivation",
    "## Final Verdict",
    "## Required Corrections or Conditions",
    "## Non-Blocking Follow-Up",
    "## Audit Completion Check",
)
REQUIRED_NONEMPTY_SECTIONS = (
    "## Audit Scope",
    "## Evidence Sources Examined",
    "## Dimension Results",
    "## Verdict Derivation",
    "## Audit Completion Check",
)
ALLOWED_VERDICTS = (
    "READY_FOR_ADR",
    "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    "CONDITIONAL_ADR_READY",
    "ADPR_REVISION_REQUIRED",
    "ARCHITECTURE_NOT_READY",
)
PENDING_PLACEHOLDER_RE = re.compile(r"(?im)(?:`PENDING[^`]*`|\|\s*PENDING\s*\||:\s*PENDING\b|\bPENDING\s*[—-])")
REVIEWED_ARTIFACT_RE = re.compile(r"(?im)^-\s*Reviewed artifact:\s*(.+?)\s*$")
AUDITOR_RE = re.compile(r"(?im)^-\s*Auditor:\s*(.+?)\s*$")
REVISION_RE = re.compile(r"(?im)^-\s*Reviewed revision:\s*`?([0-9a-f]{40})`?\s*$")
AUDIT_TYPE_RE = re.compile(r"(?im)^-\s*Audit type:\s*`?(FULL|TARGETED)`?\s*$")
CUTOFF_RE = re.compile(r"(?im)^-\s*Evidence cutoff:\s*`?([^`\n]+)`?\s*$")
MUTABLE_EVIDENCE_RE = re.compile(r"(?i)(?:https?://|\bPR\s*#\d+\b|\bIssue\s*#\d+\b)")
PRIOR_REVIEW_SCOPE_RE = re.compile(
    r"(?i)\b(?:prior\s+(?:review|finding(?:s)?)|previous\s+finding(?:s)?|previously\s+(?:blocking\s+)?finding(?:s)?)\b"
)
NO_PRIOR_FINDINGS_RE = re.compile(r"(?i)\bno\s+prior\s+(?:review\s+)?findings?\b")
PRIOR_FINDING_REFERENCE_RE = re.compile(r"(?i)\b(?:PR\d+-F\d+|F-\d{3})\b")
FINDING_CLASS_FIELD_RE = re.compile(r"(?im)^-\s*\*\*Class:\*\*")
ADR_DISPOSITION_RE = re.compile(
    r"(?i)\b(?:reviewed|verified|applicable|in\s+scope|out\s+of\s+scope|not\s+applicable|no\s+conflict|conflict)\b"
)
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?:[^\r\n]*)$")
FENCE_CLOSE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# CommonMark section 4.6 "HTML blocks". Inside an HTML block the contained lines
# are raw HTML, so Markdown structure -- headings, tables, list fields -- is not
# interpreted there. Masking them is what keeps semantic audit validation bound
# to rendered structure rather than to source text.
#
# Type 6 is the block-level tag list; a type 6 or 7 block ends at a blank line,
# while types 1-5 end at their own terminator (or end of document).
HTML_BLOCK_TYPE_6_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|"
    "dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|"
    "hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
    "section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
HTML_BLOCK_TYPE_1_OPEN_RE = re.compile(r"^[ ]{0,3}<(?:pre|script|style|textarea)(?=[\s>]|/>|$)", re.IGNORECASE)
HTML_BLOCK_TYPE_1_CLOSE_RE = re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE)
HTML_BLOCK_TYPE_2_OPEN_RE = re.compile(r"^[ ]{0,3}<!--")
HTML_BLOCK_TYPE_3_OPEN_RE = re.compile(r"^[ ]{0,3}<\?")
HTML_BLOCK_TYPE_4_OPEN_RE = re.compile(r"^[ ]{0,3}<![A-Za-z]")
HTML_BLOCK_TYPE_5_OPEN_RE = re.compile(r"^[ ]{0,3}<!\[CDATA\[")
HTML_BLOCK_TYPE_6_OPEN_RE = re.compile(rf"^[ ]{{0,3}}</?(?:{HTML_BLOCK_TYPE_6_TAGS})(?=[\s/>]|$)", re.IGNORECASE)
# Type 7: a complete open or close tag standing alone on its line. It may not
# interrupt a paragraph, which is what keeps ordinary inline HTML in rendered
# prose from starting a block.
_HTML_ATTRIBUTE = r"""(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?)*"""
HTML_BLOCK_TYPE_7_OPEN_RE = re.compile(
    rf"^[ ]{{0,3}}(?:<[A-Za-z][A-Za-z0-9-]*{_HTML_ATTRIBUTE}\s*/?>|</[A-Za-z][A-Za-z0-9-]*\s*>)[ \t]*$"
)
# docs/ARCHITECTURE_AUDIT_TEMPLATE.md defines the canonical Audit Completion
# Check as a named checklist, and the protocol's "Standard Audit Template"
# section binds auditors to that template. Each entry below is matched on the
# item's topic rather than on verbatim template wording, so canonically
# equivalent phrasing is accepted while a fabricated single "Complete" item
# cannot impersonate the contract.
REQUIRED_COMPLETION_ITEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exact artifact and revision identified", re.compile(r"(?is)(?:artifact.*revision|revision.*artifact)")),
    ("audit scope identified", re.compile(r"(?i)\bscope\b")),
    ("evidence sources listed", re.compile(r"(?i)\bevidence\b")),
    ("applicable dimensions assessed", re.compile(r"(?i)\bdimension")),
    ("every finding includes all mandatory fields", re.compile(r"(?is)\bfinding.*\b(?:mandatory|field)")),
    ("class C or D findings demonstrate decision consequence", re.compile(r"(?i)\bconsequence\b")),
    ("findings matrix completed", re.compile(r"(?i)\bmatrix\b")),
    ("verdict derived from severity and materiality", re.compile(r"(?i)\bverdict\b")),
    ("targeted re-audit rule followed where applicable", re.compile(r"(?i)re-?audit")),
    ("auditor did not recommend or rank options", re.compile(r"(?i)\b(?:recommend|rank)")),
)
TABLE_DELIMITER_CELL_RE = re.compile(r":?-+:?")
TASK_LIST_ITEM_RE = re.compile(r"(?m)^[ ]{0,3}[-*+][ \t]+\[(?P<state>[ xX])\][ \t]*(?P<text>[^\n]*)$")
THEMATIC_BREAK_RE = re.compile(r"^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
HTML_BLOCK_TERMINATORS = {
    1: HTML_BLOCK_TYPE_1_CLOSE_RE,
    2: re.compile(r"-->"),
    3: re.compile(r"\?>"),
    4: re.compile(r">"),
    5: re.compile(r"\]\]>"),
}
# CommonMark allows an ATX heading to be indented by up to three spaces; four or
# more spaces is indented code, not a heading. Every structural heading check
# shares this one indent boundary so section identity cannot disagree between
# helpers.
ATX_INDENT = r"[ ]{0,3}"
# One ATX heading grammar for the whole validator. CommonMark allows up to three
# leading spaces, requires whitespace between the opening hashes and the
# content, and permits an optional closing hash sequence that is only decoration
# when it is separated from the content by whitespace.
ATX_HEADING_RE = re.compile(rf"^{ATX_INDENT}(?P<hashes>#{{1,6}})(?:[ \t]+(?P<content>.*?))?[ \t]*$")
ATX_CLOSING_SEQUENCE_RE = re.compile(r"(?:^|[ \t])#+[ \t]*$")
FINDING_ID_RE = re.compile(r"(?i)^(?P<id>F-\d{3})\b")
FINDING_FIELD_RE = re.compile(
    r"(?im)^-\s*\*\*(?P<label>Evidence|Location|Category|Severity|Decision impact|Consequence if ignored|Required action|Blocks ADR):\*\*\s*(?P<value>[^\n]*)$"
)
CANONICAL_FINDING_FIELD_LABELS = {
    "evidence": "Evidence",
    "location": "Location",
    "category": "Category",
    "severity": "Severity",
    "decision impact": "Decision impact",
    "consequence if ignored": "Consequence if ignored",
    "required action": "Required action",
    "blocks adr": "Blocks ADR",
}
REQUIRED_FINDING_FIELDS = {
    "evidence",
    "location",
    "category",
    "severity",
    "decision impact",
    "consequence if ignored",
    "required action",
    "blocks adr",
}
SEVERITY_ORDER = {"A": 1, "B": 2, "C": 3, "D": 4}

# Canonical architecture-readiness (ADPR) signals.
#
# `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` scopes itself, under "Applicability", to
# independent review of ADPRs, architecture-decision readiness assessments,
# substantive revisions to architecture preparation records, option-set
# completeness reviews, and audits required by the Development Governance
# lifecycle. The same section states that "Implementation reviews remain
# governed by `docs/AI_REVIEW_PROTOCOL.md`", and "Document Precedence" repeats
# that AI_REVIEW_PROTOCOL governs implementation review. Repository location is
# therefore not the governing predicate; the artifact's own readiness claim is.
GOVERNING_PROTOCOL_RE = re.compile(
    r"(?im)^-[ \t]*Governing protocol:[ \t]*`?docs/ARCHITECTURE_AUDIT_PROTOCOL\.md`?[ \t]*$"
)
CANONICAL_SEVERITY_FIELD_RE = re.compile(r"(?im)^-[ \t]*\*\*Severity:\*\*[ \t]*`?[A-D]`?[ \t]*$")
READINESS_INSTRUMENT_HEADINGS = ("## Final Verdict", "## Findings Matrix", "## Verdict Derivation")


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def changed_paths() -> list[Path]:
    """Return changed paths against the proven branch merge-base with origin/main."""
    merge_base = _run_git("merge-base", "HEAD", "origin/main")
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise RuntimeError(
            "Unable to prove merge-base with origin/main. Fetch complete repository history "
            "before running artifact preflight."
        )

    base = merge_base.stdout.strip()
    diff = _run_git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    if diff.returncode != 0:
        raise RuntimeError("Unable to determine changed files from the proven branch merge-base.")
    return [Path(line.strip()) for line in diff.stdout.splitlines() if line.strip()]


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("DEFECT_REGISTRY version must be 1.")

    defects = data.get("defects")
    if not isinstance(defects, list):
        return errors + ["DEFECT_REGISTRY defects must be a list."]

    seen: set[str] = set()
    for index, item in enumerate(defects):
        if not isinstance(item, dict):
            errors.append(f"Registry item {index} must be an object.")
            continue
        missing = sorted(REQUIRED_REGISTRY_FIELDS - set(item))
        if missing:
            errors.append(f"Registry item {index} missing fields: {', '.join(missing)}.")
            continue

        defect_id = item["id"]
        if not isinstance(defect_id, str) or not re.fullmatch(r"[A-Z]+(?:-[A-Z]+)*-\d{3}", defect_id):
            errors.append(f"Registry item {index} has invalid id {defect_id!r}.")
        elif defect_id in seen:
            errors.append(f"Duplicate defect id: {defect_id}.")
        else:
            seen.add(defect_id)

        if item["classification"] not in {"systemic", "isolated"}:
            errors.append(f"{defect_id}: classification must be systemic or isolated.")
        if item["status"] not in {"guarded", "open", "retired"}:
            errors.append(f"{defect_id}: unsupported status {item['status']!r}.")
        if item["status"] == "guarded":
            for field in ("guard_boundary", "guard", "test"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{defect_id}: guarded entry requires non-empty {field}.")

    missing_ids = sorted(REQUIRED_DEFECT_IDS - seen)
    if missing_ids:
        errors.append("Registry dropped required understood defect classes: " + ", ".join(missing_ids) + ".")
    return errors


def accepted_adr_ids(index_text: str) -> list[str]:
    ids: list[str] = []
    for line in index_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        match = re.fullmatch(r"\[(\d{4})\]\([^)]+\)", cells[0])
        if match and cells[2].startswith("Accepted"):
            ids.append(match.group(1))
    return sorted(set(ids))


def _mask_span_preserving_newlines(value: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in value)


def _html_block_open_type(body: str, *, in_paragraph: bool) -> int | None:
    """Return the CommonMark HTML block type this line opens, if any."""
    if HTML_BLOCK_TYPE_1_OPEN_RE.match(body):
        return 1
    if HTML_BLOCK_TYPE_2_OPEN_RE.match(body):
        return 2
    if HTML_BLOCK_TYPE_3_OPEN_RE.match(body):
        return 3
    if HTML_BLOCK_TYPE_5_OPEN_RE.match(body):
        return 5
    if HTML_BLOCK_TYPE_4_OPEN_RE.match(body):
        return 4
    if HTML_BLOCK_TYPE_6_OPEN_RE.match(body):
        return 6
    # Type 7 is the only HTML block that cannot interrupt a paragraph. It may
    # still begin anywhere the parser is between blocks -- after a blank line,
    # but equally after a heading, a fence, or another block construct.
    if not in_paragraph and HTML_BLOCK_TYPE_7_OPEN_RE.match(body):
        return 7
    return None


def _mask_markdown_nonsemantic(text: str) -> str:
    """Mask Markdown regions that do not render as semantic audit structure.

    This is the single rendered-structure boundary for the whole validator. It
    masks CommonMark HTML blocks (section 4.6, all seven types), fenced code,
    indented code, and HTML comments, preserving line structure so downstream
    structural parsing stays stable.
    """
    without_comments = HTML_COMMENT_RE.sub(
        lambda match: _mask_span_preserving_newlines(match.group(0)),
        text,
    )
    masked: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    html_block_type: int | None = None
    # Whether a paragraph is currently open. This is the real CommonMark
    # boundary for type 7 blocks: a blank line is only one of several ways to
    # close a paragraph, and a heading or fence closes one just as well.
    in_paragraph = False

    for line in without_comments.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        blank = not body.strip()

        if html_block_type is not None:
            if html_block_type in (6, 7) and blank:
                # A type 6 or 7 block ends at a blank line, which is not part of
                # the block itself.
                html_block_type = None
                masked.append(line)
                in_paragraph = False
                continue
            masked.append(" " * len(body) + ending)
            if html_block_type not in (6, 7) and HTML_BLOCK_TERMINATORS[html_block_type].search(body):
                html_block_type = None
            in_paragraph = False
            continue

        if fence_char is not None:
            closing = FENCE_CLOSE_RE.fullmatch(body)
            if closing:
                fence = closing.group("fence")
                if fence[0] == fence_char and len(fence) >= fence_length:
                    fence_char = None
                    fence_length = 0
            masked.append(" " * len(body) + ending)
            in_paragraph = False
            continue

        opened = _html_block_open_type(body, in_paragraph=in_paragraph)
        if opened is not None:
            masked.append(" " * len(body) + ending)
            terminator = HTML_BLOCK_TERMINATORS.get(opened)
            # Types 1-5 may open and close on the same line.
            if terminator is None or not terminator.search(body):
                html_block_type = opened
            in_paragraph = False
            continue

        opening = FENCE_OPEN_RE.fullmatch(body)
        if opening:
            fence = opening.group("fence")
            fence_char = fence[0]
            fence_length = len(fence)
            masked.append(" " * len(body) + ending)
            in_paragraph = False
            continue

        if body.startswith("\t") or re.match(r"^ {4,}\S", body):
            masked.append(" " * len(body) + ending)
            in_paragraph = False
            continue

        masked.append(line)
        # A blank line, an ATX heading, and a thematic break each leave the
        # parser between blocks; anything else non-blank continues a paragraph.
        if blank or _parse_atx_heading(body) is not None or THEMATIC_BREAK_RE.match(body):
            in_paragraph = False
        else:
            in_paragraph = True

    return "".join(masked)


def _parse_atx_heading(body: str) -> tuple[int, str] | None:
    """Parse one line as a CommonMark ATX heading.

    Returns ``(level, rendered_content)`` or ``None``. A closing hash sequence
    is stripped only when CommonMark treats it as decoration; unseparated
    trailing hashes such as ``## Metadata###`` stay part of the content, so
    heading identity matches what actually renders.
    """
    match = ATX_HEADING_RE.match(body)
    if not match:
        return None
    content = match.group("content") or ""
    closing = ATX_CLOSING_SEQUENCE_RE.search(content)
    if closing:
        content = content[: closing.start()]
    return len(match.group("hashes")), content.strip()


@cache
def _heading_identity(heading: str) -> tuple[int, str]:
    """Normalize a canonical heading string such as ``## Metadata``."""
    parsed = _parse_atx_heading(heading)
    if parsed is None:  # pragma: no cover - canonical constants are well formed
        raise ValueError(f"not a canonical ATX heading: {heading!r}")
    return parsed


def _heading_spans(text: str) -> list[tuple[int, int, int, str]]:
    """Every rendered ATX heading as ``(start, end, level, content)``.

    Positions are offsets into ``text`` so section slicing, boundary detection
    and counting all share one notion of heading identity.
    """
    spans: list[tuple[int, int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        parsed = _parse_atx_heading(body)
        if parsed is not None:
            level, content = parsed
            spans.append((offset, offset + len(body), level, content))
        offset += len(line)
    return spans


def _finding_record_headings(text: str) -> list[tuple[int, int, str]]:
    """Rendered finding-record headings as ``(start, end, FINDING_ID)``."""
    headings: list[tuple[int, int, str]] = []
    for start, end, level, content in _heading_spans(text):
        if level < 3:
            continue
        match = FINDING_ID_RE.match(content)
        if match:
            headings.append((start, end, match.group("id").upper()))
    return headings


def _named_heading_spans(text: str, heading: str) -> list[tuple[int, int]]:
    level, content = _heading_identity(heading)
    return [
        (start, end)
        for start, end, span_level, span_content in _heading_spans(text)
        if span_level == level and span_content == content
    ]


def _heading_match(text: str, heading: str) -> tuple[int, int] | None:
    semantic_text = _mask_markdown_nonsemantic(text)
    spans = _named_heading_spans(semantic_text, heading)
    return spans[0] if spans else None


def _rendered_heading_count(text: str, heading: str) -> int:
    """Count real rendered occurrences of one level-two heading.

    `_section()` stops at the next level-two heading, so a validator that reads
    a section can never observe a second section with the same heading. Section
    identity therefore has to be counted separately from section content.
    """
    semantic_text = _mask_markdown_nonsemantic(text)
    return len(_named_heading_spans(semantic_text, heading))


def _section(text: str, heading: str) -> str:
    semantic_text = _mask_markdown_nonsemantic(text)
    spans = _named_heading_spans(semantic_text, heading)
    if not spans:
        return ""
    _, end = spans[0]
    boundary = len(semantic_text)
    for start, _span_end, level, _content in _heading_spans(semantic_text):
        if start >= end and level <= 2:
            boundary = start
            break
    return semantic_text[end:boundary].strip()


def _direct_section_lines(text: str, heading: str) -> list[str]:
    section = _section(text, heading)
    direct = section
    for start, _end, level, _content in _heading_spans(section):
        if level >= 3:
            direct = section[:start]
            break
    return [line.strip() for line in direct.splitlines() if line.strip()]


def _normalize_markdown_scalar(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith(("- ", "* ")):
        normalized = normalized[2:].strip()
    if len(normalized) >= 2 and normalized.startswith("`") and normalized.endswith("`"):
        normalized = normalized[1:-1].strip()
    return normalized


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []

    content = stripped[1:]
    if content.endswith("|"):
        content = content[:-1]

    cells: list[str] = []
    current: list[str] = []
    for char in content:
        if char == "|":
            backslashes = 0
            for previous in reversed(current):
                if previous != "\\":
                    break
                backslashes += 1
            if backslashes % 2 == 1:
                current.append(char)
                continue
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)

    cells.append("".join(current).strip())
    return cells


def _normalized_cell(value: str) -> str:
    return re.sub(r"[`*_]", "", value).strip().lower()


def _structured_adr_accounting_ids(text: str) -> set[str]:
    semantic_text = _mask_markdown_nonsemantic(text)
    accounted: set[str] = set()
    for line in semantic_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = _markdown_cells(stripped)
            if not cells:
                continue
            first_cell = _normalized_cell(cells[0])
            match = re.match(r"(?i)^ADR[ -]?(\d{4})\b", first_cell)
            remainder = " ".join(cells[1:])
        else:
            match = re.match(r"(?i)^[-*]\s+(?:[`*_]+)?ADR[ -]?(\d{4})\b", stripped)
            remainder = stripped[match.end() :] if match else ""
        if match and ADR_DISPOSITION_RE.search(remainder):
            accounted.add(match.group(1))
    return accounted


def _is_table_delimiter_row(cells: list[str]) -> bool:
    """Whether these cells form a Markdown table delimiter row."""
    return bool(cells) and all(TABLE_DELIMITER_CELL_RE.fullmatch(cell.strip()) for cell in cells)


def _finding_matrix_rows(matrix: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Parse the Findings Matrix, which is valid only as a rendered table.

    The protocol makes the final findings matrix mandatory, and a matrix that
    Markdown does not render as a table is not one. A canonical header line is
    therefore only a matrix when the immediately following line is a matching
    delimiter row; data rows are the contiguous table rows after it, and
    parsing stops where the rendered table ends.
    """
    semantic_matrix = _mask_markdown_nonsemantic(matrix)
    lines = semantic_matrix.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        row = _markdown_cells(line)
        headers = [_normalized_cell(cell) for cell in row]
        if not {"finding", "class", "blocks adr"}.issubset(headers):
            continue
        finding_index = headers.index("finding")
        class_index = headers.index("class")
        blocks_index = headers.index("blocks adr")
        parsed: list[tuple[str, str, str]] = []
        errors: list[str] = []

        width = len(row)
        delimiter = _markdown_cells(lines[index + 1]) if index + 1 < len(lines) else []
        if not _is_table_delimiter_row(delimiter) or len(delimiter) != width:
            return [], [
                "Findings Matrix header must be immediately followed by a Markdown table delimiter row "
                f"of {width} columns; without it the rows do not render as a table."
            ]

        body: list[list[str]] = []
        for following in lines[index + 2 :]:
            if not following.strip().startswith("|"):
                # The rendered table ends here; later pipe lines are a
                # different block and carry no Findings Matrix semantics.
                break
            body.append(_markdown_cells(following))

        for offset, candidate in enumerate(body, start=1):
            populated = [cell.strip() for cell in candidate if cell.strip()]
            if not populated:
                # An entirely empty row is the canonical template placeholder,
                # not a data row making a claim.
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in populated):
                continue

            # A data row beneath a recognized header may never be skipped: a
            # skipped row disappears from cardinality, severity, and verdict
            # derivation alike, which is exactly how a blocker gets hidden.
            if len(candidate) < width:
                label = _normalize_markdown_scalar(candidate[finding_index]) if finding_index < len(candidate) else ""
                identity = repr(label) if label else f"at position {offset}"
                errors.append(
                    f"Findings Matrix row {identity} is truncated; the header declares {width} columns "
                    f"but the row has {len(candidate)}."
                )
                continue

            finding_id = _normalize_markdown_scalar(candidate[finding_index])
            severity = _normalize_markdown_scalar(candidate[class_index]).upper()
            blocks_adr = _normalize_markdown_scalar(candidate[blocks_index]).upper()
            if not finding_id:
                errors.append(f"Findings Matrix row at position {offset} must declare a Finding identifier.")
                continue
            if not re.fullmatch(r"[A-D]", severity):
                errors.append(f"Findings Matrix row {finding_id!r} must use Class A, B, C, or D.")
                continue
            if blocks_adr not in {"YES", "NO"}:
                errors.append(f"Findings Matrix row {finding_id!r} must set Blocks ADR to YES or NO.")
                continue
            parsed.append((finding_id, severity, blocks_adr))

        return parsed, errors

    return [], ["Findings Matrix must identify Finding, Class, and Blocks ADR columns."]


def _finding_records(findings: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return every rendered finding record in document order.

    Both records and their fields are returned as ordered sequences rather than
    mappings. A mapping would let a duplicate Finding ID, or a duplicate
    canonical field inside one record, be silently collapsed by last-write-wins
    and erase the rendered evidence. Cardinality is validated before any
    normalized mapping is built.
    """
    semantic_findings = _mask_markdown_nonsemantic(findings)
    headings = _finding_record_headings(semantic_findings)
    records: list[tuple[str, list[tuple[str, str]]]] = []
    for index, (_start, heading_end, finding_id) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(semantic_findings)
        block = semantic_findings[heading_end:end]
        fields = [
            (field.group("label").lower(), _normalize_markdown_scalar(field.group("value")))
            for field in FINDING_FIELD_RE.finditer(block)
        ]
        records.append((finding_id, fields))
    return records


def _duplicate_field_errors(finding_id: str, fields: list[tuple[str, str]]) -> list[str]:
    """Each canonical field may appear exactly once per rendered record."""
    counts = Counter(label for label, _value in fields)
    return [
        f"Finding {finding_id} field {CANONICAL_FINDING_FIELD_LABELS.get(label, label)} must appear "
        f"exactly once per finding record; found {count}."
        for label, count in sorted(counts.items())
        if count > 1
    ]


def _validate_finding_records(findings: str, matrix: str) -> list[str]:
    rows, matrix_errors = _finding_matrix_rows(matrix)
    if matrix_errors:
        return []

    records = _finding_records(findings)
    errors: list[str] = []
    valid_records: set[str] = set()

    record_counts = Counter(finding_id for finding_id, _ in records)
    row_counts = Counter(finding_id.upper() for finding_id, _, _ in rows)

    # Field cardinality is proven before any normalized mapping exists, so a
    # duplicate canonical field cannot overwrite an earlier value and hide the
    # state the record actually renders.
    duplicate_field_ids: set[str] = set()
    for finding_id, fields in records:
        field_errors = _duplicate_field_errors(finding_id, fields)
        if field_errors:
            duplicate_field_ids.add(finding_id)
            errors.extend(field_errors)

    unique_records = {finding_id: dict(fields) for finding_id, fields in records}

    # Record and row cardinality next: a duplicate on either side can otherwise
    # hide or double-count a real finding during verdict derivation.
    for finding_id, count in sorted(record_counts.items()):
        if count > 1:
            errors.append(
                f"Finding {finding_id} must have exactly one complete finding record in ## Findings; found {count}."
            )
    for finding_id, count in sorted(row_counts.items()):
        if count > 1:
            errors.append(f"Finding {finding_id} must appear exactly once in the Findings Matrix; found {count}.")

    for finding_id, record in sorted(unique_records.items()):
        if finding_id in duplicate_field_ids:
            continue
        missing = sorted(field for field in REQUIRED_FINDING_FIELDS if not record.get(field, "").strip())
        if missing:
            errors.append(f"Finding {finding_id} record is incomplete; missing: {', '.join(missing)}.")
            continue

        severity = record["severity"].upper()
        blocks_adr = record["blocks adr"].upper()
        if severity not in SEVERITY_ORDER:
            errors.append(f"Finding {finding_id} Severity must be exactly A, B, C, or D.")
            continue
        if blocks_adr not in {"YES", "NO"}:
            errors.append(f"Finding {finding_id} Blocks ADR must be YES or NO.")
            continue
        valid_records.add(finding_id)

    # A rendered finding record that never reaches the matrix would be invisible
    # to verdict derivation, which reads matrix rows only.
    for finding_id in sorted(record_counts):
        if finding_id not in row_counts:
            errors.append(
                f"Finding {finding_id} has a rendered finding record but is missing from the Findings Matrix."
            )

    for finding_id, severity, blocks_adr in rows:
        normalized_id = finding_id.upper()
        record = unique_records.get(normalized_id)
        if record is None:
            errors.append(f"Finding {finding_id} must have a complete finding record in ## Findings.")
            continue
        if normalized_id not in valid_records:
            continue

        record_severity = record["severity"].upper()
        record_blocks = record["blocks adr"].upper()
        if record_severity != severity:
            errors.append(
                f"Finding {finding_id} severity disagrees between Findings ({record_severity}) "
                f"and Findings Matrix ({severity})."
            )
        if record_blocks != blocks_adr:
            errors.append(
                f"Finding {finding_id} Blocks ADR disagrees between Findings ({record_blocks}) "
                f"and Findings Matrix ({blocks_adr})."
            )

    return errors


def _unmatched_completion_topics(entries: list[str]) -> list[str]:
    """Canonical completion topics with no distinct checked entry of their own.

    The canonical Audit Completion Check in docs/ARCHITECTURE_AUDIT_TEMPLATE.md
    is an item-by-item completion contract, not a bag of words: each required
    topic is its own asserted piece of work. Topics are therefore matched
    injectively -- one checked entry may satisfy at most one topic -- so a
    single fabricated entry listing every keyword cannot stand in for ten
    separate assertions.

    A maximum bipartite matching is used rather than a greedy pass so that an
    entry able to satisfy several topics is never claimed by the wrong one,
    which would falsely reject a canonically valid checklist.
    """
    candidates = [
        [index for index, entry in enumerate(entries) if pattern.search(entry)]
        for _label, pattern in REQUIRED_COMPLETION_ITEMS
    ]
    entry_owner: dict[int, int] = {}

    def _assign(topic: int, visited: set[int]) -> bool:
        for entry in candidates[topic]:
            if entry in visited:
                continue
            visited.add(entry)
            if entry not in entry_owner or _assign(entry_owner[entry], visited):
                entry_owner[entry] = topic
                return True
        return False

    return [
        REQUIRED_COMPLETION_ITEMS[topic][0]
        for topic in range(len(REQUIRED_COMPLETION_ITEMS))
        if not _assign(topic, set())
    ]


def _validate_audit_completion(section: str) -> list[str]:
    """Require the Audit Completion Check to record completed required work.

    A non-empty section is not enough: the canonical checklist in
    docs/ARCHITECTURE_AUDIT_TEMPLATE.md is what the audit asserts it has done,
    so every rendered checklist item must be checked and each canonical topic
    must be covered by its own distinct entry. The section text is already
    rendered-masked by `_section()`, so checklist-looking content inside fenced,
    indented, commented, or raw HTML regions never reaches this check.
    """
    items = list(TASK_LIST_ITEM_RE.finditer(section))
    if not items:
        return ["Audit Completion Check must record the canonical completion checklist."]

    errors: list[str] = []
    unchecked = [match.group("text").strip() for match in items if match.group("state") == " "]
    for text in unchecked:
        errors.append(f"Audit Completion Check item is not complete: {text or '(unlabelled item)'}.")

    completed = [match.group("text").strip() for match in items if match.group("state") != " "]
    missing = _unmatched_completion_topics(completed)
    if missing:
        errors.append("Audit Completion Check is missing required completed items: " + "; ".join(missing) + ".")
    return errors


def _validate_iso8601(value: str) -> bool:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _selected_verdicts(text: str) -> list[str]:
    lines = _direct_section_lines(text, "## Final Verdict")
    if len(lines) != 1:
        return []
    declared = _normalize_markdown_scalar(lines[0])
    return [declared] if declared in ALLOWED_VERDICTS else []


def _validate_verdict_consistency(selected_verdict: str | None, matrix: str) -> list[str]:
    rows, errors = _finding_matrix_rows(matrix)
    if errors:
        return errors
    if selected_verdict is None:
        return []

    if selected_verdict in {"ADPR_REVISION_REQUIRED", "ARCHITECTURE_NOT_READY"} and not any(
        blocks_adr == "YES" for _, _, blocks_adr in rows
    ):
        errors.append(f"{selected_verdict} requires at least one finding with Blocks ADR = YES.")

    # Finding-dependent verdicts must be checked before any zero-row shortcut.
    # ARCHITECTURE_AUDIT_PROTOCOL.md defines READY_FOR_ADR_WITH_MINOR_FINDINGS as
    # "Use when only Class A and non-cumulative Class B findings exist" and
    # CONDITIONAL_ADR_READY as "Use when no Class C or D finding exists, but
    # cumulative Class B findings must be resolved". Both therefore require real
    # qualifying findings; the no-finding case maps to READY_FOR_ADR.
    if selected_verdict == "READY_FOR_ADR_WITH_MINOR_FINDINGS" and not rows:
        errors.append("READY_FOR_ADR_WITH_MINOR_FINDINGS requires at least one unresolved Class A or B finding.")
    if selected_verdict == "CONDITIONAL_ADR_READY" and not any(severity == "B" for _, severity, _ in rows):
        errors.append("CONDITIONAL_ADR_READY requires at least one unresolved Class B finding.")

    if not rows:
        return errors

    for finding_id, severity, blocks_adr in rows:
        if severity in {"C", "D"} and blocks_adr != "YES":
            errors.append(f"Class {severity} finding {finding_id} must set Blocks ADR = YES.")
        if severity in {"A", "B"} and blocks_adr == "YES":
            errors.append(f"Class {severity} finding {finding_id} may not set Blocks ADR = YES.")

    highest = max((severity for _, severity, _ in rows), key=SEVERITY_ORDER.__getitem__)
    if highest == "D" and selected_verdict != "ARCHITECTURE_NOT_READY":
        errors.append("Unresolved Class D finding requires ARCHITECTURE_NOT_READY.")
    elif highest == "C" and selected_verdict != "ADPR_REVISION_REQUIRED":
        errors.append("Unresolved Class C finding requires ADPR_REVISION_REQUIRED.")
    elif highest == "B" and selected_verdict not in {
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
        "CONDITIONAL_ADR_READY",
    }:
        errors.append(
            "Unresolved Class B finding requires READY_FOR_ADR_WITH_MINOR_FINDINGS " "or CONDITIONAL_ADR_READY."
        )
    elif highest == "A" and selected_verdict not in {
        "READY_FOR_ADR",
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    }:
        errors.append("Class A-only audit requires READY_FOR_ADR or READY_FOR_ADR_WITH_MINOR_FINDINGS.")

    if selected_verdict == "ARCHITECTURE_NOT_READY" and highest != "D":
        errors.append("ARCHITECTURE_NOT_READY requires at least one unresolved Class D finding.")
    if selected_verdict == "ADPR_REVISION_REQUIRED" and highest != "C":
        errors.append(
            "ADPR_REVISION_REQUIRED requires Class C as the highest unresolved severity and no Class D finding."
        )
    if selected_verdict in {
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
        "CONDITIONAL_ADR_READY",
    } and highest not in {"A", "B"}:
        errors.append(f"{selected_verdict} is valid only when the highest unresolved severity is A or B.")

    return errors


def validate_audit_text(text: str, *, accepted_adrs: list[str]) -> list[str]:
    """Validate canonical audit requirements without inventing prose-only ceremony."""
    errors: list[str] = []
    semantic_text = _mask_markdown_nonsemantic(text)

    for heading in REQUIRED_AUDIT_HEADINGS:
        if not _heading_match(semantic_text, heading):
            errors.append(f"Missing mandatory audit heading: {heading}.")

    for heading in REQUIRED_NONEMPTY_SECTIONS:
        if _heading_match(semantic_text, heading) and not _section(semantic_text, heading):
            errors.append(f"Mandatory audit section must not be empty: {heading}.")

    if PENDING_PLACEHOLDER_RE.search(semantic_text):
        errors.append("Audit contains unresolved PENDING placeholder content.")

    metadata = _section(semantic_text, "## Metadata")
    reviewed_artifact_match = REVIEWED_ARTIFACT_RE.search(metadata)
    if not reviewed_artifact_match or not reviewed_artifact_match.group(1).strip():
        errors.append("Audit must identify the Reviewed artifact.")
    elif "pending" in reviewed_artifact_match.group(1).lower():
        errors.append("Reviewed artifact may not be pending.")

    auditor_match = AUDITOR_RE.search(metadata)
    if not auditor_match or not auditor_match.group(1).strip():
        errors.append("Audit must record an independent auditor identity.")
    elif "pending" in auditor_match.group(1).lower():
        errors.append("Audit auditor identity may not be pending.")

    if not REVISION_RE.search(metadata):
        errors.append("Audit must pin Reviewed revision to an immutable 40-hex commit.")

    audit_type_match = AUDIT_TYPE_RE.search(metadata)
    if not audit_type_match:
        errors.append("Audit type must be FULL or TARGETED.")
        audit_type = None
    else:
        audit_type = audit_type_match.group(1)

    evidence = _section(semantic_text, "## Evidence Sources Examined")
    cutoff_match = CUTOFF_RE.search(metadata)
    if MUTABLE_EVIDENCE_RE.search(evidence):
        if not cutoff_match:
            errors.append("Audit using mutable PR/Issue/URL evidence must record an Evidence cutoff.")
        elif not _validate_iso8601(cutoff_match.group(1)):
            errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp.")
    elif cutoff_match and not _validate_iso8601(cutoff_match.group(1)):
        errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp when present.")

    if audit_type == "FULL":
        accounted_adrs = _structured_adr_accounting_ids(semantic_text)
        for adr in accepted_adrs:
            if adr not in accounted_adrs:
                errors.append(f"Accepted ADR {adr} is not structurally accounted for in FULL audit.")

    scope = _section(semantic_text, "## Audit Scope")
    if PRIOR_REVIEW_SCOPE_RE.search(scope):
        prior = _section(semantic_text, "## Prior Review Finding Re-Verification")
        if not prior:
            errors.append("Audit scope declares prior review history but lacks Prior Review Finding Re-Verification.")
        elif not NO_PRIOR_FINDINGS_RE.search(prior) and not (
            PRIOR_FINDING_REFERENCE_RE.search(prior)
            and re.search(r"(?i)\bevidence\b", prior)
            and re.search(r"(?i)\bconsequence\b", prior)
        ):
            errors.append(
                "Prior Review Finding Re-Verification must identify prior finding(s) with evidence and "
                "decision consequence, or explicitly state that none exist."
            )

    findings = _section(semantic_text, "## Findings")
    if FINDING_CLASS_FIELD_RE.search(findings):
        errors.append("Finding records must use canonical `Severity`; `Class` is reserved for the Findings Matrix.")

    final_verdict_sections = _rendered_heading_count(semantic_text, "## Final Verdict")
    verdicts = _selected_verdicts(semantic_text)
    selected_verdict: str | None
    if final_verdict_sections > 1:
        errors.append(
            f"Audit must contain exactly one rendered ## Final Verdict section; found {final_verdict_sections}."
        )
        selected_verdict = None
    elif len(verdicts) != 1:
        errors.append("Final Verdict must contain exactly one canonical declared audit verdict line.")
        selected_verdict = None
    else:
        selected_verdict = verdicts[0]

    corrections = _section(semantic_text, "## Required Corrections or Conditions")
    normalized_corrections = _normalize_markdown_scalar(corrections).strip().lower().rstrip(".;:")
    if selected_verdict == "CONDITIONAL_ADR_READY" and (
        not corrections or normalized_corrections in {"none", "n/a", "not applicable"}
    ):
        errors.append("CONDITIONAL_ADR_READY requires explicit mandatory conditions.")

    completion = _section(semantic_text, "## Audit Completion Check")
    if completion:
        errors.extend(_validate_audit_completion(completion))

    matrix = _section(semantic_text, "## Findings Matrix")
    errors.extend(_validate_verdict_consistency(selected_verdict, matrix))
    errors.extend(_validate_finding_records(findings, matrix))

    return errors


def is_architecture_readiness_audit(text: str) -> bool:
    """Report whether an artifact is governed by ARCHITECTURE_AUDIT_PROTOCOL.md.

    The predicate is a disjunction over independent canonical readiness
    instruments, evaluated on rendered content only:

    1. the template's `Governing protocol` declaration
       (`docs/ARCHITECTURE_AUDIT_TEMPLATE.md`, bound by the protocol's
       "Standard Audit Template" section);
    2. a rendered canonical readiness instrument heading -- Final Verdict,
       Findings Matrix, or Verdict Derivation -- each required by the protocol's
       "Audit Completion Requirements";
    3. a canonical verdict *declaration*, parsed by the same
       `_selected_verdicts()` semantics audit validation itself uses -- a
       rendered `## Final Verdict` section declaring exactly one canonical
       verdict. A bare verdict token is deliberately not a signal: an
       incidental, quoted, historical, or negated mention such as "this review
       does not issue READY_FOR_ADR" is not a readiness declaration, and
       treating it as one would pull implementation reviews governed by
       `docs/AI_REVIEW_PROTOCOL.md` into the wrong protocol;
    4. a canonical finding record (`F-NNN` heading plus an A-D `Severity`
       field) as defined by "Required Finding Record" and "Severity Classes".

    Being a disjunction is what makes it fail-safe rather than an opt-in flag.
    Deleting any single discriminator -- most obviously the `Governing protocol`
    metadata line -- does not remove the artifact from governance, because the
    remaining instruments still evidence a readiness claim. To escape the
    validator an artifact must abandon every instrument through which the
    protocol permits readiness to be established *or* blocked: the protocol
    states that "An audit without this matrix is incomplete and cannot establish
    readiness or block readiness". An artifact with no verdict, no matrix, no
    verdict derivation, and no canonical finding records asserts no readiness
    outcome at all, which is precisely the out-of-scope case the protocol
    assigns to `docs/AI_REVIEW_PROTOCOL.md`.
    """
    semantic_text = _mask_markdown_nonsemantic(text)
    if GOVERNING_PROTOCOL_RE.search(semantic_text):
        return True
    if any(_heading_match(semantic_text, heading) for heading in READINESS_INSTRUMENT_HEADINGS):
        return True
    if _selected_verdicts(semantic_text):
        return True
    has_record_heading = bool(_finding_record_headings(semantic_text))
    return bool(has_record_heading and CANONICAL_SEVERITY_FIELD_RE.search(semantic_text))


def _validate_baseline_artifact_integrity(text: str) -> list[str]:
    """Protocol-independent integrity floor for every governed audit artifact.

    Not being ADPR-governed does not mean unvalidated. This keeps the checks that
    hold for any governed artifact regardless of which review protocol applies,
    without importing architecture-readiness ceremony.
    """
    semantic_text = _mask_markdown_nonsemantic(text)
    if not semantic_text.strip():
        return ["Governed artifact must contain rendered content."]
    if not any(level == 1 and content for _s, _e, level, content in _heading_spans(semantic_text)):
        return ["Governed artifact must declare a rendered level-one title."]
    return []


def validate_governed_artifact_text(text: str, *, accepted_adrs: list[str]) -> list[str]:
    """Validate one governed artifact under the protocol that actually governs it."""
    if is_architecture_readiness_audit(text):
        return validate_audit_text(text, accepted_adrs=accepted_adrs)
    return _validate_baseline_artifact_integrity(text)


def validate_changed_artifacts(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    accepted = accepted_adr_ids(ADR_INDEX_PATH.read_text(encoding="utf-8"))
    for relative in paths:
        posix = relative.as_posix()
        if not posix.startswith(AUDIT_PREFIX) or relative.suffix.lower() != ".md":
            continue
        full_path = ROOT / relative
        if not full_path.exists():
            continue
        for error in validate_governed_artifact_text(
            full_path.read_text(encoding="utf-8"),
            accepted_adrs=accepted,
        ):
            errors.append(f"{posix}: {error}")
    return errors


def run_artifact_preflight(paths: list[Path] | None = None) -> int:
    errors = validate_registry(load_registry())
    if paths is None:
        try:
            paths = changed_paths()
        except RuntimeError as exc:
            print(f"[Hunter Artifact Preflight] FAIL: {exc}", flush=True)
            return 2
    errors.extend(validate_changed_artifacts(paths))

    if errors:
        for error in errors:
            print(f"[Hunter Artifact Preflight] FAIL: {error}", flush=True)
        return 1

    print(
        "[Hunter Artifact Preflight] PASS: defect registry and changed governed artifacts",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Validate the defect registry and changed governed artifacts deterministically.")
    )
    parser.add_argument(
        "--all-audits",
        action="store_true",
        help="Validate all architecture-audit markdown files instead of only changed files.",
    )
    args = parser.parse_args()
    if args.all_audits:
        paths = sorted((ROOT / "docs" / "ARCHITECTURE_AUDITS").glob("*.md"))
        return run_artifact_preflight([path.relative_to(ROOT) for path in paths])
    return run_artifact_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
