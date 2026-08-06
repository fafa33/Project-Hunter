"""Deterministic diff chunking for the LLM Architecture Audit.

Complete diff coverage requires every byte of a pull request's diff to reach
the hostile audit -- approval must never be granted on a truncated view of
the change. A single prompt cannot hold an arbitrarily large diff within the
pinned model's provider token budget (see ``llm_audit.PROMPT_CHAR_BUDGET``),
so the diff is split into deterministic, ordered chunks, each independently
reviewed; ``aggregate.py`` combines the per-chunk verdicts.

No content is ever dropped. A single file whose own diff exceeds one chunk's
budget is itself split further (on line boundaries, or mid-line only for a
single line longer than the budget) -- never truncated. Splitting is a pure
function of (diff, max_chunk_chars): no randomness, no clock, no network, so
the same input always produces the same chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FILE_HEADER_PATTERN = re.compile(r"^diff --git a/.* b/.*$", re.MULTILINE)
_FILE_HEADER_NAME_PATTERN = re.compile(r"^diff --git a/(.*) b/(.*)$")


@dataclass(frozen=True)
class DiffChunk:
    """One deterministic, ordered slice of a pull request's diff."""

    index: int
    total: int
    files: tuple[str, ...]
    text: str


def _split_by_file(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into ``(filename, segment_text)`` pairs, in order.

    Every character of ``diff`` appears in exactly one segment; segments are
    contiguous, non-overlapping substrings of ``diff`` in original order, so
    concatenating them reconstructs ``diff`` exactly.
    """
    matches = list(_FILE_HEADER_PATTERN.finditer(diff))
    if not matches:
        return [("(unparsed diff)", diff)] if diff.strip() else []
    segments: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff)
        segment = diff[start:end]
        header_line = segment.splitlines()[0] if segment else match.group(0)
        name_match = _FILE_HEADER_NAME_PATTERN.match(header_line)
        filename = name_match.group(2) if name_match else header_line
        segments.append((filename, segment))
    return segments


def _split_oversized_segment(filename: str, text: str, max_chars: int) -> list[tuple[str, str]]:
    """Split one file's diff segment into ordered sub-segments <= max_chars.

    Splits on line boundaries only, except a single line longer than
    ``max_chars`` (e.g. a minified asset), which is hard-split mid-line.
    Zero content is lost either way.
    """
    if len(text) <= max_chars:
        return [(filename, text)]
    lines = text.splitlines(keepends=True)
    parts: list[tuple[str, str]] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) > max_chars:
            parts.append((filename, current))
            current = ""
        if len(line) > max_chars:
            for offset in range(0, len(line), max_chars):
                parts.append((filename, line[offset : offset + max_chars]))
            continue
        current += line
    if current:
        parts.append((filename, current))
    return parts


def split_diff_into_chunks(diff: str, max_chunk_chars: int) -> list[DiffChunk]:
    """Deterministically split a diff into chunks, each <= ``max_chunk_chars``.

    Guarantees: every character of ``diff`` appears in exactly one chunk, in
    original order; no chunk exceeds ``max_chunk_chars``. An empty (or
    whitespace-only) diff produces zero chunks.
    """
    if not diff.strip():
        return []
    if max_chunk_chars < 1:
        raise ValueError("max_chunk_chars must be a positive integer")

    atomic: list[tuple[str, str]] = []
    for filename, text in _split_by_file(diff):
        atomic.extend(_split_oversized_segment(filename, text, max_chunk_chars))

    grouped: list[tuple[list[str], str]] = []
    current_files: list[str] = []
    current_text = ""
    for filename, text in atomic:
        if current_text and len(current_text) + len(text) > max_chunk_chars:
            grouped.append((current_files, current_text))
            current_files, current_text = [], ""
        current_text += text
        if filename not in current_files:
            current_files.append(filename)
    if current_text:
        grouped.append((current_files, current_text))

    total = len(grouped)
    return [
        DiffChunk(index=i + 1, total=total, files=tuple(files), text=text) for i, (files, text) in enumerate(grouped)
    ]


def covered_filenames(chunks: list[DiffChunk]) -> set[str]:
    """Every distinct filename that appears across all chunks."""
    names: set[str] = set()
    for chunk in chunks:
        names.update(chunk.files)
    return names


def split_document_into_chunks(path: str, text: str, max_chunk_chars: int) -> list[DiffChunk]:
    """Deterministically, losslessly split an authoritative document's full text.

    A resolved document is not the same as a reviewed one: reviewing only a
    bounded excerpt can silently hide a rule stated later in the document.
    This reuses ``split_diff_into_chunks``'s generic, format-agnostic
    line-boundary splitting -- a governance document has no ``diff --git``
    headers, so it is treated as a single (or, if oversized, line-split)
    segment, exactly like an unparsed diff. No separate document-chunking
    algorithm exists; this is a thin, self-documenting wrapper that gives
    document callers their own entry point and attaches ``path`` as each
    chunk's ``files`` tuple so downstream reporting reads naturally.
    """
    chunks = split_diff_into_chunks(text, max_chunk_chars)
    return [DiffChunk(index=c.index, total=c.total, files=(path,), text=c.text) for c in chunks]
