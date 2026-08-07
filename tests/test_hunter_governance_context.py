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
    """Test double for the FileResolver protocol.

    ``files``/``directories`` are the ref-agnostic (base) defaults every
    existing test already used. ``files_by_ref``/``directories_by_ref`` let
    a test give a SPECIFIC ref (typically a head SHA) different content than
    base -- e.g. a file that exists at head but not at base, simulating a
    PR that genuinely introduces a new record.
    """

    def __init__(
        self,
        files: dict[str, str] | None = None,
        directories: dict[str, list[str]] | None = None,
        files_by_ref: dict[str, dict[str, str]] | None = None,
        directories_by_ref: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        self.files = files or {}
        self.directories = directories or {}
        self.files_by_ref = files_by_ref or {}
        self.directories_by_ref = directories_by_ref or {}
        self.calls: list[tuple[str, str]] = []

    def get_file_content(self, path: str, ref: str) -> str | None:
        self.calls.append(("file", path))
        if ref in self.files_by_ref and path in self.files_by_ref[ref]:
            return self.files_by_ref[ref][path]
        return self.files.get(path)

    def list_directory(self, path: str, ref: str) -> list[str] | None:
        self.calls.append(("dir", path))
        if ref in self.directories_by_ref and path in self.directories_by_ref[ref]:
            return self.directories_by_ref[ref][path]
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
        assert entry.byte_length == len(resolver.files[entry.path])


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


# --- Base-trusted-authority vs. proposed-by-PR record resolution (bootstrap fix) ----


def _valid_adr_text(number: str, title: str = "Something", status: str = "Proposed") -> str:
    """A structurally valid ADR body per docs/ADR/README.md's own documented
    contract -- mirrors the real shape of PR #201's ADR 0029/0030."""
    return (
        f"# ADR {number}: {title}\n"
        "\n"
        "## Status\n"
        "\n"
        f"{status}.\n"
        "\n"
        "## Context\n"
        "\n"
        "Some context.\n"
        "\n"
        "## Decision\n"
        "\n"
        "Some decision.\n"
        "\n"
        "## Consequences\n"
        "\n"
        "- Some consequence.\n"
        "\n"
        "## Alternatives Considered\n"
        "\n"
        "- Some alternative.\n"
    )


BASE_SHA = "b" * 40
HEAD_SHA = "h" * 40


def _bootstrap_resolver(**kwargs: object) -> FakeResolver:
    defaults: dict[str, object] = dict(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
        },
        directories={"docs/ADR": []},
    )
    defaults.update(kwargs)
    return FakeResolver(**defaults)  # type: ignore[arg-type]


# A. referenced ADR exists in base -> resolves normally.
def test_a_referenced_adr_existing_in_base_resolves_normally() -> None:
    resolver = _bootstrap_resolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/ADR/0028-existing.md": _valid_adr_text("0028", status="Accepted"),
        },
        directories={"docs/ADR": ["0028-existing.md"]},
    )
    resolved, missing = resolve_referenced_records(resolver, base_sha=BASE_SHA, pr_body="See ADR-0028.")
    assert missing == []
    entry, content = resolved[0]
    assert entry.provenance == "base"
    assert entry.ref == BASE_SHA
    assert "Accepted" in content


# B. referenced ADR absent from base but legitimately added by the PR -> proposed, not missing.
def test_b_referenced_adr_added_by_pr_is_recognized_as_proposed_not_missing() -> None:
    adr_text = _valid_adr_text("0029", "Hunter Development Methodology")
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0029-hunter-development-methodology.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0029-hunter-development-methodology.md": adr_text}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADR-0029.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0029-hunter-development-methodology.md"}),
    )
    assert missing == []
    entry, content = resolved[0]
    assert entry.provenance == "head"
    assert entry.ref == HEAD_SHA
    assert entry.path == "docs/ADR/0029-hunter-development-methodology.md"
    assert content == adr_text


# C. referenced ADR absent from both base and PR -> rejected.
def test_c_referenced_adr_absent_from_base_and_head_is_rejected() -> None:
    resolver = _bootstrap_resolver()
    resolved, missing = resolve_referenced_records(
        resolver, base_sha=BASE_SHA, pr_body="See ADR-0099.", head_sha=HEAD_SHA, changed_paths=frozenset()
    )
    assert resolved == []
    assert missing == ["ADR 0099"]


# D. PR merely references a nonexistent ADR without adding it -> rejected.
def test_d_pr_references_nonexistent_adr_without_adding_it_is_rejected() -> None:
    # The file exists at head (e.g. a coincidental, unrelated file) but is
    # NOT part of this PR's own changed files -- must not be trusted.
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0099-unrelated.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0099-unrelated.md": _valid_adr_text("0099")}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="See ADR-0099.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset(),  # PR does not actually touch this path
    )
    assert resolved == []
    assert missing == ["ADR 0099"]


# E. PR adds a fake/malformed canonical-looking record -> rejected.
@pytest.mark.parametrize(
    "malformed_text,expected_reason_fragment",
    [
        ("# ADR 0029: X\n\nnot even sections", "missing required section"),
        (
            "# ADR 0099: Wrong Number\n\n## Status\n\nProposed.\n\n## Context\n\nc\n\n## Decision\n\nd\n\n"
            "## Consequences\n\nc\n\n## Alternatives Considered\n\na\n",
            "but was referenced as ADR 0029",
        ),
        (
            "# ADR 0029: Bad Status\n\n## Status\n\nWishful.\n\n## Context\n\nc\n\n## Decision\n\nd\n\n"
            "## Consequences\n\nc\n\n## Alternatives Considered\n\na\n",
            "does not declare a recognized status",
        ),
    ],
)
def test_e_pr_adds_a_malformed_canonical_looking_record_is_rejected(
    malformed_text: str, expected_reason_fragment: str
) -> None:
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0029-fake.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0029-fake.md": malformed_text}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADR-0029.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0029-fake.md"}),
    )
    assert resolved == []
    assert len(missing) == 1
    assert missing[0].startswith("ADR 0029 (proposed but invalid:")
    assert expected_reason_fragment in missing[0]


def test_e_pr_adds_a_record_with_a_non_matching_filename_is_rejected() -> None:
    # Directory listing prefix-matches "0029-" (so it is even considered a
    # candidate) but the stricter filename pattern check must still reject
    # a malformed name (uppercase/underscore is not a valid slug).
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0029-Bad_Name.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0029-Bad_Name.md": _valid_adr_text("0029")}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADR-0029.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0029-Bad_Name.md"}),
    )
    assert resolved == []
    assert "does not match the required NNNN-short-title.md pattern" in missing[0]


# F. existing ADR modified by PR -> classified as a modification of base
# authority, never reinterpreted as a newly introduced/proposed record.
def test_f_existing_adr_modified_by_pr_is_still_resolved_from_base() -> None:
    base_text = _valid_adr_text("0028", status="Accepted")
    head_text = _valid_adr_text("0028", title="Modified Title", status="Accepted")
    assert base_text != head_text
    resolver = _bootstrap_resolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/ADR/0028-existing.md": base_text,
        },
        directories={"docs/ADR": ["0028-existing.md"]},
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0028-existing.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0028-existing.md": head_text}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Amends ADR-0028.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0028-existing.md"}),
    )
    assert missing == []
    entry, content = resolved[0]
    assert entry.provenance == "base"
    assert entry.ref == BASE_SHA
    assert content == base_text  # base authority, never the PR's modified head version


# G. existing canonical record deleted/renamed by the PR at head cannot
# bypass governance -- base's own directory listing is unaffected by the
# PR's changes, so the reference still resolves to the trusted base record.
def test_g_existing_adr_deleted_at_head_still_resolves_from_base() -> None:
    resolver = _bootstrap_resolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/ADR/0028-existing.md": _valid_adr_text("0028", status="Accepted"),
        },
        directories={"docs/ADR": ["0028-existing.md"]},
        directories_by_ref={HEAD_SHA: {"docs/ADR": []}},  # deleted at head
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="References ADR-0028.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0028-existing.md"}),
    )
    assert missing == []
    entry, _content = resolved[0]
    assert entry.provenance == "base"


def test_g_existing_adr_renamed_at_head_still_resolves_from_base() -> None:
    resolver = _bootstrap_resolver(
        files={
            "docs/CANONICAL_ARCHITECTURE_MAP.md": MAP_TEXT,
            "docs/PROJECT_CONSTITUTION.md": "c",
            "docs/PROJECT_PRINCIPLES.md": "p",
            "docs/ADR/0028-existing.md": _valid_adr_text("0028", status="Accepted"),
        },
        directories={"docs/ADR": ["0028-existing.md"]},
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0028-renamed.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0028-renamed.md": "renamed content, should never be trusted"}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="References ADR-0028.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0028-existing.md", "docs/ADR/0028-renamed.md"}),
    )
    assert missing == []
    entry, content = resolved[0]
    assert entry.provenance == "base"
    assert entry.path == "docs/ADR/0028-existing.md"
    assert "renamed content" not in content


# H. multiple new records introduced atomically in one PR, referencing each
# other, handled deterministically if valid.
def test_h_multiple_new_records_introduced_atomically_resolve_deterministically() -> None:
    adr_0029 = _valid_adr_text("0029", "First") + "\nSee also ADR-0030.\n"
    adr_0030 = _valid_adr_text("0030", "Second") + "\nSee also ADR-0029.\n"
    resolver = _bootstrap_resolver(
        directories_by_ref={
            HEAD_SHA: {"docs/ADR": ["0029-first.md", "0030-second.md"]},
        },
        files_by_ref={
            HEAD_SHA: {
                "docs/ADR/0029-first.md": adr_0029,
                "docs/ADR/0030-second.md": adr_0030,
            }
        },
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADR-0029 and ADR-0030, which reference each other.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/ADR/0029-first.md", "docs/ADR/0030-second.md"}),
    )
    assert missing == []
    assert len(resolved) == 2
    paths = {e.path for e, _ in resolved}
    assert paths == {"docs/ADR/0029-first.md", "docs/ADR/0030-second.md"}
    assert all(e.provenance == "head" for e, _ in resolved)


# I. the concrete structural pattern PR #201 exercises (two new ADRs
# referenced from the PR body, resolved via resolve_context end-to-end)
# passes the bootstrap condition when the records are genuinely valid.
def test_i_pr201_style_bootstrap_pattern_passes_when_records_are_valid() -> None:
    adr_0029 = _valid_adr_text("0029", "Hunter Development Methodology")
    adr_0030 = _valid_adr_text("0030", "Hunter Intelligence Evolution")
    resolver = _bootstrap_resolver(
        directories_by_ref={
            HEAD_SHA: {"docs/ADR": ["0029-hunter-development-methodology.md", "0030-hunter-intelligence-evolution.md"]}
        },
        files_by_ref={
            HEAD_SHA: {
                "docs/ADR/0029-hunter-development-methodology.md": adr_0029,
                "docs/ADR/0030-hunter-intelligence-evolution.md": adr_0030,
            }
        },
    )
    manifest = resolve_context(
        resolver,
        base_sha=BASE_SHA,
        pr_body=(
            "This PR proposes ADR 0029 -- Hunter Development Methodology and "
            "ADR 0030 -- Hunter Intelligence Evolution."
        ),
        head_sha=HEAD_SHA,
        changed_paths=frozenset(
            {
                "docs/ADR/0029-hunter-development-methodology.md",
                "docs/ADR/0030-hunter-intelligence-evolution.md",
            }
        ),
    )
    assert manifest.missing_references == ()
    proposed = [e for e in manifest.entries if e.provenance == "head"]
    assert {e.path for e in proposed} == {
        "docs/ADR/0029-hunter-development-methodology.md",
        "docs/ADR/0030-hunter-intelligence-evolution.md",
    }


# J. a PR that references no ADR/ADPR at all (the equivalent shape of a
# dependency-bump PR like #202) is completely unaffected by the base/head
# fallback machinery -- no regression for PRs that never touch this path.
def test_j_pr_with_no_adr_references_is_unaffected_by_the_bootstrap_fix() -> None:
    resolver = _bootstrap_resolver()
    manifest = resolve_context(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Bumps black and pytest in requirements.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"requirements/ci-constraints.txt"}),
    )
    assert manifest.missing_references == ()
    assert not any(e.provenance == "head" for e in manifest.entries)


def test_resolve_context_without_head_sha_behaves_exactly_as_before() -> None:
    """Backward compatibility: omitting head_sha/changed_paths (every
    pre-fix caller) must produce identical behavior to before this fix --
    a referenced-but-not-at-base record is simply missing, never proposed."""
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/ADR": ["0029-x.md"]}},
        files_by_ref={HEAD_SHA: {"docs/ADR/0029-x.md": _valid_adr_text("0029")}},
    )
    manifest = resolve_context(resolver, base_sha=BASE_SHA, pr_body="Adds ADR-0029.")
    assert manifest.missing_references == ("ADR 0029",)


# --- Same base/proposed/missing distinction applies generically to ADPRs -----------


def _valid_adpr_text(number: str, status: str = "PROPOSED") -> str:
    return f"# ADPR-{number} — Something\n\n## Metadata\n\n- ADPR ID: `ADPR-{number}`\n- Status: `{status}`\n"


def test_adpr_absent_from_base_but_added_by_pr_is_recognized_as_proposed_not_missing() -> None:
    adpr_text = _valid_adpr_text("0006")
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/architecture-records": ["ADPR-0006-something.md"]}},
        files_by_ref={HEAD_SHA: {"docs/architecture-records/ADPR-0006-something.md": adpr_text}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADPR-0006.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/architecture-records/ADPR-0006-something.md"}),
    )
    assert missing == []
    entry, content = resolved[0]
    assert entry.provenance == "head"
    assert content == adpr_text


def test_adpr_added_by_pr_with_invalid_lifecycle_status_is_rejected() -> None:
    bad_text = "# ADPR-0006 — Something\n\n## Metadata\n\n- ADPR ID: `ADPR-0006`\n- Status: `MADE_UP_STATUS`\n"
    resolver = _bootstrap_resolver(
        directories_by_ref={HEAD_SHA: {"docs/architecture-records": ["ADPR-0006-something.md"]}},
        files_by_ref={HEAD_SHA: {"docs/architecture-records/ADPR-0006-something.md": bad_text}},
    )
    resolved, missing = resolve_referenced_records(
        resolver,
        base_sha=BASE_SHA,
        pr_body="Adds ADPR-0006.",
        head_sha=HEAD_SHA,
        changed_paths=frozenset({"docs/architecture-records/ADPR-0006-something.md"}),
    )
    assert resolved == []
    assert "does not declare a recognized lifecycle status" in missing[0]


def test_adpr_referenced_but_not_added_by_pr_is_rejected() -> None:
    resolver = _bootstrap_resolver()
    resolved, missing = resolve_referenced_records(
        resolver, base_sha=BASE_SHA, pr_body="See ADPR-0099.", head_sha=HEAD_SHA, changed_paths=frozenset()
    )
    assert resolved == []
    assert missing == ["ADPR-0099"]
