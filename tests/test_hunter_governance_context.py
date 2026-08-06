"""Unit tests for the Authoritative Context Resolver."""

from __future__ import annotations

import hashlib

import pytest
from hunter_governance_review.context import (
    ContextResolutionError,
    extract_doc_references,
    extract_hierarchy_paths,
    resolve_context,
    resolve_referenced_records,
)

MAP_TEXT = """# Map

## Canonical Document Authority Hierarchy

1. `docs/PROJECT_CONSTITUTION.md`
2. `docs/PROJECT_PRINCIPLES.md`
3. `docs/CANONICAL_ARCHITECTURE_MAP.md`
7. Accepted ADRs in `docs/ADR/`
13. Versioned sprint specifications in `docs/SPRINTS/`
"""


class FakeResolver:
    def __init__(
        self,
        files: dict[str, str] | None = None,
        directories: dict[str, list[str]] | None = None,
    ) -> None:
        self.files = files or {}
        self.directories = directories or {}
        self.calls: list[tuple[str, str]] = []

    def get_file_content(self, path: str, ref: str) -> str | None:
        self.calls.append(("file", path))
        return self.files.get(path)

    def list_directory(self, path: str, ref: str) -> list[str] | None:
        self.calls.append(("dir", path))
        return self.directories.get(path)


def test_extract_hierarchy_paths_skips_directory_entries() -> None:
    paths = extract_hierarchy_paths(MAP_TEXT)
    assert paths == [
        "docs/PROJECT_CONSTITUTION.md",
        "docs/PROJECT_PRINCIPLES.md",
        "docs/CANONICAL_ARCHITECTURE_MAP.md",
    ]


def test_extract_doc_references_finds_and_dedupes_md_paths() -> None:
    body = "See docs/AI_REVIEW_PROTOCOL.md and docs/AI_REVIEW_PROTOCOL.md again, plus docs/CI.md."
    assert extract_doc_references(body) == ["docs/AI_REVIEW_PROTOCOL.md", "docs/CI.md"]


def test_resolve_context_raises_when_map_itself_missing() -> None:
    resolver = FakeResolver(files={})
    with pytest.raises(ContextResolutionError, match="CANONICAL_ARCHITECTURE_MAP"):
        resolve_context(resolver, base_sha="abc", pr_body="")


def test_resolve_context_raises_when_hierarchy_doc_missing() -> None:
    resolver = FakeResolver(files={"docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT, "docs/PROJECT_CONSTITUTION.md": "x"})
    with pytest.raises(ContextResolutionError, match="PROJECT_PRINCIPLES"):
        resolve_context(resolver, base_sha="abc", pr_body="")


def test_resolve_context_succeeds_and_records_manifest() -> None:
    resolver = FakeResolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "constitution text",
            "docs/PROJECT_PRINCIPLES.md": "principles text",
        }
    )
    manifest = resolve_context(resolver, base_sha="abc123", pr_body="")
    assert manifest.missing_mandatory == ()
    paths = {e.path for e in manifest.entries}
    assert paths == {
        "docs/CANONICAL_ARCHITECTURE_MAP.md",
        "docs/PROJECT_CONSTITUTION.md",
        "docs/PROJECT_PRINCIPLES.md",
    }
    for entry in manifest.entries:
        assert entry.ref == "abc123"
        assert entry.status == "resolved"
        assert entry.sha256 == hashlib.sha256(resolver.files[entry.path].encode()).hexdigest()
    assert "constitution text" in manifest.brief


def test_resolve_context_records_referenced_doc_as_optional() -> None:
    resolver = FakeResolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/CI.md": "ci text",
        }
    )
    manifest = resolve_context(resolver, base_sha="abc", pr_body="see docs/CI.md for details")
    entry = next(e for e in manifest.entries if e.path == "docs/CI.md")
    assert entry.mandatory is False
    assert entry.status == "resolved"


def test_resolve_context_records_missing_referenced_doc_without_failing() -> None:
    resolver = FakeResolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
        }
    )
    manifest = resolve_context(resolver, base_sha="abc", pr_body="see docs/DOES_NOT_EXIST.md")
    entry = next(e for e in manifest.entries if e.path == "docs/DOES_NOT_EXIST.md")
    assert entry.mandatory is False
    assert entry.status == "missing"
    assert manifest.missing_mandatory == ()  # optional-doc absence never fails the review


def test_resolve_context_bounds_total_brief_length() -> None:
    big_content = "x" * 100_000
    resolver = FakeResolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": big_content,
            "docs/PROJECT_PRINCIPLES.md": big_content,
        }
    )
    manifest = resolve_context(resolver, base_sha="abc", pr_body="", total_char_budget=500)
    assert len(manifest.brief) < len(big_content)
    assert len(manifest.brief) <= 600  # small slack for the per-entry header/separator text


def test_resolve_referenced_records_finds_adr_by_number() -> None:
    resolver = FakeResolver(
        directories={"docs/ADR": ["0001-discovery-first.md", "0028-governance-docs.md"]},
        files={"docs/ADR/0028-governance-docs.md": "adr content"},
    )
    resolved, missing = resolve_referenced_records(resolver, base_sha="abc", pr_body="See ADR-0028 for context.")
    assert missing == []
    assert len(resolved) == 1
    entry, content = resolved[0]
    assert entry.path == "docs/ADR/0028-governance-docs.md"
    assert content == "adr content"


def test_resolve_referenced_records_reports_missing_adr() -> None:
    resolver = FakeResolver(directories={"docs/ADR": ["0001-discovery-first.md"]})
    resolved, missing = resolve_referenced_records(resolver, base_sha="abc", pr_body="See ADR 0099.")
    assert resolved == []
    assert missing == ["ADR 0099"]


def test_resolve_referenced_records_handles_adpr() -> None:
    resolver = FakeResolver(
        directories={"docs/architecture-records": ["ADPR-0012-something.md"]},
        files={"docs/architecture-records/ADPR-0012-something.md": "adpr content"},
    )
    resolved, missing = resolve_referenced_records(resolver, base_sha="abc", pr_body="Per ADPR-0012.")
    assert missing == []
    assert resolved[0][1] == "adpr content"


def test_resolve_context_integrates_adr_resolution() -> None:
    resolver = FakeResolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/ADR/0028-governance-docs.md": "adr content",
        },
        directories={"docs/ADR": ["0028-governance-docs.md"]},
    )
    manifest = resolve_context(resolver, base_sha="abc", pr_body="Accepts ADR-0028.")
    assert manifest.missing_references == ()
    assert any(e.path == "docs/ADR/0028-governance-docs.md" for e in manifest.entries)
