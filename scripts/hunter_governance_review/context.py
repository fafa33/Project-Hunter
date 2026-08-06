"""Authoritative Repository Context Resolver.

Resolves canonical governance documents and referenced ADRs/docs at the
EXACT base commit via the GitHub Contents API -- never from whatever
happens to be checked out locally. The gate engine's own checkout
(``--root``) is a single shallow checkout of the default branch made once
per workflow run; it is not guaranteed to be pinned to the exact recorded
``ReviewPair.target_base_sha`` for every validator that reads it. Resolving
via the API against the exact SHA closes that gap and produces a
deterministic, auditable manifest of every document actually consulted.

The document set is not hardcoded: the canonical hierarchy is parsed
directly out of ``docs/CANONICAL_ARCHITECTURE_MAP.md``'s own numbered
"Canonical Document Authority Hierarchy" list (fetched at the same exact
base commit), so if governance changes the hierarchy, resolution adapts
without a code change -- per that document's own Maintenance Rule.

Fail-closed: any document from the canonical hierarchy that cannot be
retrieved at the exact base SHA raises ``ContextResolutionError``, which the
caller must map to ``REVIEW_FAILED``. Documents referenced only in the PR
body (ADRs, other docs) are optional context: missing ones are recorded in
the manifest but do not by themselves fail the review -- the deterministic
V-070 validator already blocks a PR that references a non-existent ADR.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from hunter_governance_review.contracts import ContextEntry, ContextManifest

CANONICAL_MAP_PATH = "docs/CANONICAL_ARCHITECTURE_MAP.md"
DEFAULT_EXCERPT_CHARS = 900
# Deliberately small: this brief is re-sent, identically, on every one of a
# potentially large number of sequential per-chunk audit calls (see
# chunking.py). A large context budget doesn't make it any more authoritative
# -- the full-fidelity evidence trail is the coverage manifest (every
# resolved document's exact path/ref/sha256/length), not this inline
# excerpt -- but it does directly shrink how much diff budget each chunk has
# left, which was the proximate cause of a 116-chunk review on PR #200's own
# real diff hitting Groq's tokens-per-minute rate limit on almost every
# sequential call (workflow run 31065137201).
DEFAULT_TOTAL_CHAR_BUDGET = 2_000

_NUMBERED_LINE_PATTERN = re.compile(r"^\d+\.\s")
_BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
_DOC_REFERENCE_PATTERN = re.compile(r"\bdocs/[A-Za-z0-9_./-]+\.md\b")
_ADPR_PATTERN = re.compile(r"\bADPR-(\d{4})\b", re.IGNORECASE)
_ADR_PATTERN = re.compile(r"\bADR[- ]?(\d{4})\b", re.IGNORECASE)


class ContextResolutionError(RuntimeError):
    """Raised when required (mandatory) canonical context cannot be resolved."""


class FileResolver(Protocol):
    def get_file_content(self, path: str, ref: str) -> str | None: ...
    def list_directory(self, path: str, ref: str) -> list[str] | None: ...


@dataclass(frozen=True)
class _PendingEntry:
    path: str
    ref: str
    mandatory: bool


def extract_hierarchy_paths(canonical_map_text: str) -> list[str]:
    """Parse the ordered ``.md`` doc paths out of the map's numbered hierarchy.

    Purely structural (regex over Markdown): a numbered line's backtick-quoted
    segments are candidates; only ``docs/....md`` paths are returned, so
    directory entries (e.g. ``docs/ADR/``, ``docs/SPRINTS/``) are excluded --
    those are resolved separately, only when the PR body references them.
    """
    paths: list[str] = []
    for line in canonical_map_text.splitlines():
        if not _NUMBERED_LINE_PATTERN.match(line):
            continue
        for match in _BACKTICK_PATTERN.finditer(line):
            candidate = match.group(1)
            if candidate.startswith("docs/") and candidate.endswith(".md"):
                paths.append(candidate)
    return paths


def extract_doc_references(text: str) -> list[str]:
    """Every ``docs/....md`` path literally mentioned in free text, deduplicated."""
    return sorted(set(_DOC_REFERENCE_PATTERN.findall(text)))


def resolve_referenced_records(
    resolver: FileResolver,
    *,
    base_sha: str,
    pr_body: str,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> tuple[list[tuple[ContextEntry, str]], list[str]]:
    """Resolve every ADR/ADPR number referenced in the PR body at the exact base SHA.

    This is the single, exact-SHA-pinned resolution mechanism for ADR/ADPR
    references -- ``deterministic.py``'s V-070 validator consumes its
    ``missing_references`` result rather than globbing a local checkout,
    which is not guaranteed to reflect the exact recorded base commit (see
    the module docstring).

    Returns ``(resolved, missing_references)`` where ``resolved`` pairs each
    entry with its actual fetched content (so callers can build a real
    excerpt, not just a hash); ``missing_references`` lists each unresolved
    reference exactly as V-070 reports it (e.g. ``"ADR 0028"``,
    ``"ADPR-0012"``).
    """
    resolved: list[tuple[ContextEntry, str]] = []
    missing: list[str] = []

    def _resolve_one(number: str, directory: str, prefix: str, label: str, names: list[str]) -> None:
        match = next((n for n in names if n.startswith(prefix)), None)
        if match is None:
            missing.append(label)
            return
        path = f"{directory}/{match}"
        content = resolver.get_file_content(path, base_sha)
        if content is None:
            missing.append(label)
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        entry = ContextEntry(path, base_sha, False, "resolved", digest, len(content), min(len(content), excerpt_chars))
        resolved.append((entry, content))

    adpr_numbers = sorted(set(_ADPR_PATTERN.findall(pr_body)))
    if adpr_numbers:
        names = resolver.list_directory("docs/architecture-records", base_sha) or []
        for number in adpr_numbers:
            _resolve_one(number, "docs/architecture-records", f"ADPR-{number}-", f"ADPR-{number}", names)

    adr_numbers = sorted(set(_ADR_PATTERN.findall(pr_body)))
    if adr_numbers:
        names = resolver.list_directory("docs/ADR", base_sha) or []
        for number in adr_numbers:
            _resolve_one(number, "docs/ADR", f"{number}-", f"ADR {number}", names)

    return resolved, sorted(set(missing))


def resolve_context(
    resolver: FileResolver,
    *,
    base_sha: str,
    pr_body: str,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    total_char_budget: int = DEFAULT_TOTAL_CHAR_BUDGET,
) -> ContextManifest:
    """Resolve authoritative governance context at the exact base commit.

    Raises ``ContextResolutionError`` if the canonical map itself, or any
    document its hierarchy lists, cannot be retrieved at ``base_sha``.
    """
    entries: list[ContextEntry] = []
    excerpts: list[str] = []

    def _resolve(pending: _PendingEntry) -> str | None:
        content = resolver.get_file_content(pending.path, pending.ref)
        if content is None:
            entries.append(ContextEntry(pending.path, pending.ref, pending.mandatory, "missing", "", 0, 0))
            return None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        included = min(len(content), excerpt_chars)
        entries.append(
            ContextEntry(pending.path, pending.ref, pending.mandatory, "resolved", digest, len(content), included)
        )
        excerpts.append(
            f"--- {pending.path} (base {pending.ref[:12]}, sha256 {digest[:12]}) ---\n{content[:excerpt_chars]}"
        )
        return content

    map_text = _resolve(_PendingEntry(CANONICAL_MAP_PATH, base_sha, mandatory=True))
    if map_text is None:
        raise ContextResolutionError(
            f"required canonical document could not be retrieved at base commit {base_sha}: {CANONICAL_MAP_PATH}"
        )

    hierarchy_paths = extract_hierarchy_paths(map_text)
    if not hierarchy_paths:
        raise ContextResolutionError(
            f"could not parse the canonical document authority hierarchy from {CANONICAL_MAP_PATH} at {base_sha}"
        )

    for path in hierarchy_paths:
        if path == CANONICAL_MAP_PATH:
            continue
        _resolve(_PendingEntry(path, base_sha, mandatory=True))

    missing_mandatory = [e.path for e in entries if e.mandatory and e.status == "missing"]
    if missing_mandatory:
        raise ContextResolutionError(
            f"required canonical document(s) could not be retrieved at base commit {base_sha}: "
            + ", ".join(missing_mandatory)
        )

    resolved_paths = {e.path for e in entries}
    for path in extract_doc_references(pr_body):
        if path in resolved_paths:
            continue
        _resolve(_PendingEntry(path, base_sha, mandatory=False))
        resolved_paths.add(path)

    resolved_records, missing_references = resolve_referenced_records(
        resolver, base_sha=base_sha, pr_body=pr_body, excerpt_chars=excerpt_chars
    )
    for entry, content in resolved_records:
        entries.append(entry)
        excerpts.append(
            f"--- {entry.path} (base {entry.ref[:12]}, sha256 {entry.sha256[:12]}) ---\n{content[:excerpt_chars]}"
        )

    budget = total_char_budget
    bounded: list[str] = []
    for excerpt in excerpts:
        if budget <= 0:
            break
        bounded.append(excerpt[:budget])
        budget -= len(excerpt[:budget])
    brief = "\n\n".join(bounded)

    return ContextManifest(entries=tuple(entries), brief=brief, missing_references=tuple(missing_references))
