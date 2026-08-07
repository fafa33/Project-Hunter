"""Authoritative Repository Context Resolver.

Confirms that the repository's canonical governance documents, and any
ADR/ADPR record referenced by the pull request body, actually exist --
either as TRUSTED, already-accepted authority at the EXACT base commit, or
as a genuinely NEW record this exact pull request itself proposes -- via the
GitHub Contents API, never from whatever happens to be checked out locally.

Authority/provenance boundary (the core invariant this module enforces):

    BASE = trusted, pre-PR canonical authority.
    HEAD = the proposed change currently being evaluated.

A record resolved from BASE is already-accepted historical authority; this
module never re-validates its content. A record that does not exist at BASE
but genuinely IS part of this PR's own changes may be resolved as a
PROPOSED record -- but only after confirming it is actually present among
the PR's own changed files (never merely "present at head", which could
also be true of stale, unrelated content on a PR that has not caught up
with an upstream deletion/rename) and passing a structural/identity check
against the record type's own documented contract (``docs/ADR/README.md``,
``docs/architecture-records/README.md``). A PR can never fabricate trusted
historical authority merely by adding a file with a canonical-looking name:
a proposed record's ``ContextEntry.provenance`` is always ``"head"``, never
``"base"``, so a caller can never mistake it for pre-existing authority. See
``ContextEntry``'s docstring for the full field-level contract.

The document set is not hardcoded: the canonical hierarchy is parsed
directly out of ``docs/CANONICAL_ARCHITECTURE_MAP.md``'s own numbered
"Canonical Document Authority Hierarchy" list (fetched at the same exact
base commit), so if governance changes the hierarchy, resolution adapts
without a code change -- per that document's own Maintenance Rule. That
mandatory hierarchy is deliberately NEVER subject to the base/proposed
fallback below: a PR must never be able to expand what governance considers
mandatory by editing the map (or introducing a new mandatory document)
within the same change that is judged against it.

Fail-closed: any document from the canonical hierarchy that cannot be
retrieved at the exact base SHA raises ``ContextResolutionError``, which the
caller must map to ``REVIEW_FAILED`` -- required repository evidence is
missing. Documents referenced only in the PR body (ADRs, other docs) are
optional context: missing ones (neither at base nor genuinely proposed by
the PR) are recorded in the manifest but do not by themselves fail the
review -- the deterministic V-070 validator already blocks a PR that
references a non-existent, or an invalid/fabricated, ADR/ADPR.

This module only confirms EXISTENCE/VALIDITY and records provenance (path,
ref, sha256, byte length, base-vs-head) -- it never builds prompt text or a
bounded excerpt, since nothing downstream of it consumes document content
for an LLM anymore.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from hunter_governance_review.contracts import ContextEntry, ContextManifest

CANONICAL_MAP_PATH = "docs/CANONICAL_ARCHITECTURE_MAP.md"

_NUMBERED_LINE_PATTERN = re.compile(r"^\d+\.\s")
_BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
_DOC_REFERENCE_PATTERN = re.compile(r"\bdocs/[A-Za-z0-9_./-]+\.md\b")
_ADPR_PATTERN = re.compile(r"\bADPR-(\d{4})\b", re.IGNORECASE)
_ADR_PATTERN = re.compile(r"\bADR[- ]?(\d{4})\b", re.IGNORECASE)

# Required structure for a PROPOSED (not-yet-trusted) ADR, per
# docs/ADR/README.md's own documented "Required Structure" and "Status
# Values" sections and docs/ADR/TEMPLATE.md. Never applied to a record
# resolved from base -- base content is already-accepted historical
# authority that went through governance when it was originally accepted,
# not something this gate re-validates on every later PR that merely
# references it.
_ADR_REQUIRED_SECTIONS = ("Status", "Context", "Decision", "Consequences", "Alternatives Considered")
_ADR_ALLOWED_STATUS_VALUES = ("proposed", "accepted", "superseded", "deprecated")
_ADR_FILENAME_PATTERN = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
_ADR_HEADER_PATTERN = re.compile(r"^#\s*ADR[- ]?(\d{4})\b", re.IGNORECASE | re.MULTILINE)
_ADR_STATUS_VALUE_PATTERN = re.compile(r"(?ms)^##\s+Status\s*\n+\s*([A-Za-z]+)")

# Required structure for a PROPOSED ADPR, per
# docs/architecture-records/README.md's "Lifecycle States" and
# docs/templates/ADPR_TEMPLATE.md's Metadata block.
_ADPR_ALLOWED_STATUS_VALUES = (
    "proposed",
    "in_research",
    "ready_for_review",
    "approved",
    "implemented",
    "validated",
    "superseded",
    "archived",
)
_ADPR_FILENAME_PATTERN = re.compile(r"^ADPR-\d{4}-[a-z0-9-]+\.md$")
_ADPR_ID_PATTERN = re.compile(r"ADPR\s*ID:\s*`?ADPR-(\d{4})`?", re.IGNORECASE)
_ADPR_STATUS_LINE_PATTERN = re.compile(r"(?im)^[\s-]*Status:\s*`?([A-Za-z_]+)`?")


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


def _validate_proposed_adr_structure(*, number: str, filename: str, content: str) -> str | None:
    """Structural/identity validation for a newly PROPOSED ADR. Returns
    ``None`` when valid, else a short human-readable reason. Deliberately
    shallow: filename pattern, in-content identity (the ADR's own declared
    number must match what it was referenced as), required section headers,
    and a recognized status value -- exactly the contract
    ``docs/ADR/README.md`` itself documents, never a semantic review of the
    decision's substance (this gate performs no external/LLM review; see
    ``docs/HUNTER_GOVERNANCE_REVIEW.md``)."""
    if not _ADR_FILENAME_PATTERN.match(filename) or not filename.startswith(f"{number}-"):
        return f"filename {filename!r} does not match the required NNNN-short-title.md pattern for ADR {number}"
    header_match = _ADR_HEADER_PATTERN.search(content)
    if header_match is None:
        return "missing a '# ADR NNNN: Title' header"
    if header_match.group(1) != number:
        return f"header declares ADR {header_match.group(1)} but was referenced as ADR {number}"
    missing_sections = [s for s in _ADR_REQUIRED_SECTIONS if not re.search(rf"(?m)^##\s+{re.escape(s)}\b", content)]
    if missing_sections:
        return f"missing required section(s): {', '.join(missing_sections)}"
    status_match = _ADR_STATUS_VALUE_PATTERN.search(content)
    if status_match is None or status_match.group(1).lower() not in _ADR_ALLOWED_STATUS_VALUES:
        return "Status section does not declare a recognized status (Proposed/Accepted/Superseded/Deprecated)"
    return None


def _validate_proposed_adpr_structure(*, number: str, filename: str, content: str) -> str | None:
    """Structural/identity validation for a newly PROPOSED ADPR -- mirrors
    ``_validate_proposed_adr_structure`` for the ADPR contract (filename,
    declared ``ADPR ID:``, and a recognized lifecycle status)."""
    if not _ADPR_FILENAME_PATTERN.match(filename) or not filename.startswith(f"ADPR-{number}-"):
        return f"filename {filename!r} does not match the required ADPR-NNNN-short-title.md pattern for ADPR-{number}"
    id_match = _ADPR_ID_PATTERN.search(content)
    if id_match is None:
        return "missing an 'ADPR ID: `ADPR-NNNN`' metadata line"
    if id_match.group(1) != number:
        return f"metadata declares ADPR-{id_match.group(1)} but was referenced as ADPR-{number}"
    status_match = _ADPR_STATUS_LINE_PATTERN.search(content)
    if status_match is None or status_match.group(1).lower() not in _ADPR_ALLOWED_STATUS_VALUES:
        return "metadata does not declare a recognized lifecycle status"
    return None


def resolve_referenced_records(
    resolver: FileResolver,
    *,
    base_sha: str,
    pr_body: str,
    head_sha: str | None = None,
    changed_paths: frozenset[str] = frozenset(),
) -> tuple[list[tuple[ContextEntry, str]], list[str]]:
    """Resolve every ADR/ADPR number referenced in the PR body.

    This is the single, exact-SHA-pinned resolution mechanism for ADR/ADPR
    references -- ``deterministic.py``'s V-070 validator consumes its
    ``missing_references`` result rather than globbing a local checkout,
    which is not guaranteed to reflect the exact recorded base commit (see
    the module docstring).

    Resolution order for each referenced number:

    1. **Base** (trusted authority): if a matching file exists at
       ``base_sha``, it is resolved from there, full stop -- this is true
       whether or not the PR also modifies that same file at head; base
       content is always what is trusted, never head's version of an
       already-existing record. Renaming or deleting the file at head
       cannot change this: the base directory listing is unaffected by the
       PR's own changes, so governance cannot be evaded that way either.
    2. **Proposed** (head, only if ``head_sha``/``changed_paths`` given): if
       no match exists at base, but a matching file exists at ``head_sha``
       AND that exact path is present in the PR's own changed-files list
       (proof the PR itself introduces it, not merely stale/unrelated
       content the PR's branch happens to still carry), it is structurally
       validated (see ``_validate_proposed_adr_structure``/
       ``_validate_proposed_adpr_structure``) and, if valid, resolved with
       ``provenance="head"``. An invalid/fabricated proposed record is
       reported as missing with its validation failure reason, never
       silently accepted.
    3. **Missing**: neither of the above -- reported exactly as before.

    Returns ``(resolved, missing_references)`` where ``resolved`` pairs each
    entry with its actual fetched content (so callers can record a real
    sha256, not just existence); ``missing_references`` lists each
    unresolved reference exactly as V-070 reports it (e.g. ``"ADR 0028"``,
    ``"ADPR-0012"``, or a proposed-but-invalid record's reason appended).
    """
    resolved: list[tuple[ContextEntry, str]] = []
    missing: list[str] = []

    def _resolve_one(
        number: str,
        directory: str,
        prefix: str,
        label: str,
        kind: str,
        base_names: list[str],
        head_names: list[str],
    ) -> None:
        base_match = next((n for n in base_names if n.startswith(prefix)), None)
        if base_match is not None:
            path = f"{directory}/{base_match}"
            content = resolver.get_file_content(path, base_sha)
            if content is not None:
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                resolved.append(
                    (
                        ContextEntry(path, base_sha, False, "resolved", digest, len(content), provenance="base"),
                        content,
                    )
                )
                return
            # Base's own directory listing claims this file exists but its
            # content could not be fetched -- a resolution failure, not an
            # opening for the proposed-record path to substitute for it.
            missing.append(label)
            return

        if head_sha is not None:
            head_match = next((n for n in head_names if n.startswith(prefix)), None)
            if head_match is not None:
                path = f"{directory}/{head_match}"
                if path in changed_paths:
                    content = resolver.get_file_content(path, head_sha)
                    if content is not None:
                        validator = (
                            _validate_proposed_adr_structure if kind == "ADR" else _validate_proposed_adpr_structure
                        )
                        error = validator(number=number, filename=head_match, content=content)
                        if error is None:
                            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                            resolved.append(
                                (
                                    ContextEntry(
                                        path, head_sha, False, "resolved", digest, len(content), provenance="head"
                                    ),
                                    content,
                                )
                            )
                            return
                        missing.append(f"{label} (proposed but invalid: {error})")
                        return
        missing.append(label)

    adpr_numbers = sorted(set(_ADPR_PATTERN.findall(pr_body)))
    if adpr_numbers:
        base_names = resolver.list_directory("docs/architecture-records", base_sha) or []
        head_names = (resolver.list_directory("docs/architecture-records", head_sha) or []) if head_sha else []
        for number in adpr_numbers:
            _resolve_one(
                number, "docs/architecture-records", f"ADPR-{number}-", f"ADPR-{number}", "ADPR", base_names, head_names
            )

    adr_numbers = sorted(set(_ADR_PATTERN.findall(pr_body)))
    if adr_numbers:
        base_names = resolver.list_directory("docs/ADR", base_sha) or []
        head_names = (resolver.list_directory("docs/ADR", head_sha) or []) if head_sha else []
        for number in adr_numbers:
            _resolve_one(number, "docs/ADR", f"{number}-", f"ADR {number}", "ADR", base_names, head_names)

    return resolved, sorted(set(missing))


def _resolve_doc_reference(
    resolver: FileResolver,
    *,
    path: str,
    base_sha: str,
    head_sha: str | None,
    changed_paths: frozenset[str],
) -> ContextEntry:
    """Resolve one plain ``docs/*.md`` reference (not an ADR/ADPR): base
    first (trusted), then head only if the PR's own changed-files list
    proves the PR itself introduces that exact path. No structural
    contract is enforced for an arbitrary referenced document (unlike ADRs/
    ADPRs, no fixed template exists to validate against); this only proves
    genuine introduction versus stale/unrelated head content, and always
    records the correct base-vs-head provenance."""
    content = resolver.get_file_content(path, base_sha)
    if content is not None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ContextEntry(path, base_sha, False, "resolved", digest, len(content), provenance="base")
    if head_sha is not None and path in changed_paths:
        head_content = resolver.get_file_content(path, head_sha)
        if head_content is not None:
            digest = hashlib.sha256(head_content.encode("utf-8")).hexdigest()
            return ContextEntry(path, head_sha, False, "resolved", digest, len(head_content), provenance="head")
    return ContextEntry(path, base_sha, False, "missing", "", 0)


def resolve_context(
    resolver: FileResolver,
    *,
    base_sha: str,
    pr_body: str,
    head_sha: str | None = None,
    changed_paths: frozenset[str] = frozenset(),
) -> ContextManifest:
    """Confirm required repository evidence exists at the exact base commit,
    or -- for referenced (non-mandatory) records only -- is genuinely
    proposed by this exact PR.

    Raises ``ContextResolutionError`` if the canonical map itself, or any
    document its MANDATORY hierarchy lists, cannot be retrieved at
    ``base_sha`` -- required repository evidence is missing, which the
    caller maps to ``REVIEW_FAILED``. The mandatory hierarchy is always
    resolved from base only, with no proposed/head fallback: see the module
    docstring for why.

    ``head_sha``/``changed_paths`` are optional (default: no head fallback,
    identical to previous behavior) so existing callers that only care about
    base-trusted evidence are unaffected; ``__main__.py`` always supplies
    both so a PR that legitimately introduces a new canonical record is not
    forced through the bootstrap deadlock of "it can't exist yet because
    this is the PR that adds it."
    """
    entries: list[ContextEntry] = []

    def _resolve(pending: _PendingEntry) -> str | None:
        content = resolver.get_file_content(pending.path, pending.ref)
        if content is None:
            entries.append(ContextEntry(pending.path, pending.ref, pending.mandatory, "missing", "", 0))
            return None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        entries.append(ContextEntry(pending.path, pending.ref, pending.mandatory, "resolved", digest, len(content)))
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

    already_resolved_paths = {e.path for e in entries}
    for path in extract_doc_references(pr_body):
        if path in already_resolved_paths:
            continue
        entries.append(
            _resolve_doc_reference(
                resolver, path=path, base_sha=base_sha, head_sha=head_sha, changed_paths=changed_paths
            )
        )
        already_resolved_paths.add(path)

    resolved_records, missing_references = resolve_referenced_records(
        resolver, base_sha=base_sha, pr_body=pr_body, head_sha=head_sha, changed_paths=changed_paths
    )
    for record_entry, _content in resolved_records:
        entries.append(record_entry)

    return ContextManifest(entries=tuple(entries), missing_references=tuple(missing_references))
