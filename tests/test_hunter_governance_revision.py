"""The canonical governance revision: what it covers and what it must not.

The revision is the identity that binds a Hunter Governance Review verdict to the
exact pull-request state it evaluated. Its input set is the Governance Review
authority boundary and nothing else, so these tests are written as two matching
halves: every governance input must change the revision, and every non-input must
not.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pytest
from hunter_governance_review.__main__ import build_status_description
from hunter_governance_review.contracts import Outcome, ReviewPair
from hunter_governance_review.decision import Decision
from hunter_governance_revision import (
    REVISION_DIGEST_LENGTH,
    GovernanceInputs,
    conflicting_from_graphql,
    conflicting_from_rest,
    governance_revision,
    parse_marker,
    render_marker,
)

BASE = GovernanceInputs(
    pull_request_number=258,
    head_sha="a" * 40,
    base_sha="b" * 40,
    base_ref="main",
    title="fix: converge merge readiness from current state",
    body="## Summary\n\nEvidence package.\n",
    draft=False,
    conflicting=False,
    changed_paths=("scripts/hunter_merge_readiness.py", "tests/test_hunter_merge_readiness.py"),
)


def test_the_digest_is_stable_and_the_documented_length():
    assert governance_revision(BASE) == governance_revision(replace(BASE))
    assert len(governance_revision(BASE)) == REVISION_DIGEST_LENGTH
    assert set(governance_revision(BASE)) <= set("0123456789abcdef")


def test_the_digest_is_wide_enough_to_resist_an_offline_collision_search():
    """The width is a security parameter, not formatting.

    A pull request author controls the title and body with unlimited entropy and
    can grind variants offline against a public algorithm. At 48 bits a
    cross-collision between a governance-valid body and a governance-invalid one
    costs on the order of 2^24 candidates of each; the marker would then bind a
    stale approval to a body that never passed governance. 128 bits puts the
    same search at the 2^64 birthday bound.
    """
    assert REVISION_DIGEST_LENGTH * 4 >= 128


def test_author_controlled_text_actually_moves_the_digest():
    """The fields an attacker would grind must each be fingerprinted."""
    digests = {governance_revision(replace(BASE, body=f"## Summary\n\nvariant {n}\n")) for n in range(256)}
    assert len(digests) == 256

    titles = {governance_revision(replace(BASE, title=f"fix: variant {n}")) for n in range(256)}
    assert len(titles) == 256


@pytest.mark.parametrize(
    "field,value",
    [
        ("pull_request_number", 259),
        ("head_sha", "c" * 40),
        ("base_sha", "d" * 40),
        ("base_ref", "release"),
        ("title", "fix: something else entirely"),
        ("body", "## Summary\n\nA different evidence package.\n"),
        ("draft", True),
        ("conflicting", True),
        ("changed_paths", ("scripts/hunter_merge_readiness.py",)),
    ],
)
def test_every_governance_input_changes_the_revision(field, value):
    assert governance_revision(replace(BASE, **{field: value})) != governance_revision(BASE)


def test_changed_path_ordering_and_duplication_are_not_semantic():
    reordered = replace(BASE, changed_paths=tuple(reversed(BASE.changed_paths)))
    duplicated = replace(BASE, changed_paths=BASE.changed_paths + BASE.changed_paths)

    assert governance_revision(reordered) == governance_revision(BASE)
    assert governance_revision(duplicated) == governance_revision(BASE)


def test_surrounding_whitespace_on_shas_and_refs_is_normalized():
    padded = replace(BASE, head_sha=f" {BASE.head_sha} ", base_sha=f"\t{BASE.base_sha}", base_ref=" main ")

    assert governance_revision(padded) == governance_revision(BASE)


def test_line_ending_representation_is_not_semantic():
    """The two GitHub surfaces must not be able to disagree over CRLF alone."""
    crlf = replace(
        BASE,
        title=BASE.title,
        body=BASE.body.replace("\n", "\r\n"),
    )

    assert governance_revision(crlf) == governance_revision(BASE)


def test_the_payload_names_exactly_the_governance_authority_boundary():
    """A new fingerprinted input must be a deliberate, reviewed decision."""
    assert set(BASE.canonical_payload()) == {
        "schema",
        "pull_request",
        "head_sha",
        "base_sha",
        "base_ref",
        "title",
        "body",
        "draft",
        "conflicting",
        "changed_paths",
    }


# --- mergeability normalization --------------------------------------------


def test_graphql_mergeability_normalizes_to_the_evaluated_meaning():
    assert conflicting_from_graphql("CONFLICTING") is True
    assert conflicting_from_graphql("MERGEABLE") is False
    assert conflicting_from_graphql("UNKNOWN") is False
    assert conflicting_from_graphql(None) is False


def test_rest_mergeability_normalizes_to_the_same_meaning():
    assert conflicting_from_rest(False) is True
    assert conflicting_from_rest(True) is False
    assert conflicting_from_rest(None) is False


def test_the_two_api_representations_agree_on_every_state():
    for graphql, rest in (("CONFLICTING", False), ("MERGEABLE", True), ("UNKNOWN", None)):
        assert conflicting_from_graphql(graphql) == conflicting_from_rest(rest)


# --- the status marker ------------------------------------------------------


def test_marker_round_trip():
    revision = governance_revision(BASE)

    assert parse_marker(f"{render_marker(258, revision)} Approved for head aaaaaaa.") == (258, revision)


def test_parse_rejects_anything_that_is_not_a_well_formed_marker():
    assert parse_marker("Approved for head aaaaaaa on base bbbbbbb.") is None
    assert parse_marker("[hgr:258:zzzzzzzzzzzz] Approved") is None
    assert parse_marker("[hgr:258] Approved") is None


# --- the evaluator stamps it ------------------------------------------------


def _pair(number: int = 258) -> ReviewPair:
    return ReviewPair(
        repository="fafa33/Project-Hunter",
        pull_request_number=number,
        source_branch="feature",
        source_head_sha=BASE.head_sha,
        target_branch="main",
        target_base_sha=BASE.base_sha,
        workflow_run_id="12345",
        review_timestamp="2026-08-13T00:00:00Z",
    )


def test_the_marker_fits_the_status_limit_alongside_the_approval_prose():
    """Widening the digest must not push the approval prose out of the status.

    The marker is written first, so truncation can only eat the reason -- but an
    approval's own prose should still survive intact at the widest realistic
    pull-request number.
    """
    revision = governance_revision(BASE)
    for number in (1, 258, 999999):
        description = build_status_description(Decision(Outcome.APPROVED, "ok"), _pair(number), revision)
        assert len(description) <= 140
        assert parse_marker(description) == (number, revision)
        assert description.endswith("deterministic governance validation passed.")


@pytest.mark.parametrize(
    "outcome,reason",
    [
        (Outcome.APPROVED, "ok"),
        (Outcome.CHANGES_REQUIRED, "a" * 300),
        (Outcome.REVIEW_FAILED, "b" * 300),
    ],
)
def test_the_marker_survives_status_description_truncation(outcome, reason):
    revision = governance_revision(BASE)

    description = build_status_description(Decision(outcome, reason), _pair(), revision)

    assert len(description) <= 140
    assert parse_marker(description) == (258, revision)


def test_the_marker_names_the_evaluated_pull_request():
    revision = governance_revision(BASE)

    description = build_status_description(Decision(Outcome.APPROVED, "ok"), _pair(999), revision)

    assert parse_marker(description)[0] == 999


def test_the_published_status_is_stamped_with_the_evaluated_revision(monkeypatch):
    """End to end through run_review: the stamp describes what the run evaluated."""
    from hunter_governance_review import __main__ as entry
    from hunter_governance_review.contracts import ChangedFile, ContextManifest, PullRequest

    pr = PullRequest(
        number=258,
        title=BASE.title,
        body=BASE.body,
        state="OPEN",
        draft=False,
        head_ref_name="feature",
        head_oid=BASE.head_sha,
        base_ref_name="main",
        base_oid=BASE.base_sha,
        mergeable="MERGEABLE",
        changed_files=2,
        url="https://example.invalid/pr/258",
        author_login="fafa33",
    )
    published: dict[str, str] = {}

    class Runner:
        repository = "fafa33/Project-Hunter"

        def get_pull_request(self, number):
            return pr

        def get_pull_files(self, number):
            return [
                ChangedFile(filename=name, status="modified", additions=1, deletions=0) for name in BASE.changed_paths
            ]

        def post_commit_status(self, *, sha, state, context, description, target_url):
            published.update({"sha": sha, "state": state, "description": description})

    monkeypatch.setattr(entry, "resolve_context", lambda *a, **k: ContextManifest(entries=[], missing_references=()))

    args = argparse.Namespace(
        pr=258, repository="fafa33/Project-Hunter", root=None, protected_branches="main", dry_run=False
    )
    assert entry.run_review(args=args, env={"GITHUB_RUN_ID": "1"}, gh=Runner()) == 0

    assert published["sha"] == BASE.head_sha
    assert parse_marker(published["description"]) == (258, governance_revision(BASE))
