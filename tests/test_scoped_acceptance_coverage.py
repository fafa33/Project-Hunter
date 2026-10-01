"""Scoped acceptance-coverage regression suite.

A bounded corrective candidate may legitimately owe only a subset of its
governing Issue's acceptance criteria. Before this mechanism existed the only
available verdicts were "cover every criterion the Issue defines" or "admit
nothing", so a correct two-file correction was permanently non-admissible.

The relaxation this suite pins is deliberately narrow. A candidate may never
widen its own obligation: the subset it claims is compared against an
owner-authored authorization carried by the trusted default branch, and the
only outcomes a valid bounded correction can produce are a *scoped* verdict --
which is not the verdict that satisfies the Issue -- or a failure. Every other
invariant (exact head, exact content, path-derived defect families, findings,
regression evidence, correction-commit range) is unchanged and is re-proved
here through the bounded path, because a relaxation is only safe if everything
it does not touch still bites.

Every guard is paired with the canonically valid equivalent: a guard that
blocks authorized work is itself a defect.
"""

from __future__ import annotations

from typing import Any

import hunter_connector_write_ingress as ingress
import hunter_pre_ready_review as review
import pytest

BASE = "b" * 40
HEAD = "c" * 40
OTHER_HEAD = "e" * 40
CORRECTION = "1" * 40
ISSUE = "412"
OTHER_ISSUE = "499"

CRITERIA = (
    "the collector reports provenance for every reviewed finding",
    "the review binds the exact head sha",
    "the governance status is published from the trusted controller",
)
AUTHORIZED = CRITERIA[:2]
UNAUTHORIZED = CRITERIA[2]

CHANGES = (
    ingress.ConnectorFileChange("modified", "scripts/hunter_reviewer_collector.py", "", "a" * 40),
    ingress.ConnectorFileChange("modified", "tests/test_hunter_reviewer_collector.py", "", "b" * 40),
)
OTHER_CHANGES = (
    ingress.ConnectorFileChange("modified", "scripts/hunter_reviewer_collector.py", "", "a" * 40),
    ingress.ConnectorFileChange("modified", "scripts/hunter_pre_ready_review.py", "", "f" * 40),
)

FAMILIES = (
    {"id": "DFF-010", "applicability": {"changed_paths": ["scripts/"]}},
    {"id": "DFF-013", "applicability": {"changed_paths": ["tests/"]}},
    {"id": "DFF-001", "applicability": {"changed_paths": ["src/hunter/"]}},
)

APPLICABLE = ("DFF-010", "DFF-013")


def _policy(entries: Any) -> dict[str, Any]:
    """A CODE_WRITE_POLICY mapping carrying owner-authored coverage scopes."""

    return {
        "review_progression": {
            "review_authority": {
                "primary": "deterministic-governance",
                "reviewer_pool": {
                    "model": "ordered",
                    "ordering": "priority",
                    "exhaustion_semantics": "recorded",
                    "last_resort": "hunter-guard",
                    "agents": [],
                },
                "coverage_scopes": entries,
            }
        }
    }


def _entry(
    *,
    issue: str = ISSUE,
    mode: str = "bounded_correction",
    criteria: Any = AUTHORIZED,
    head_sha: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "issue": issue,
        "mode": mode,
        "criteria": [review.normalize_criterion(text) for text in criteria],
        "authorized_by": "repo-owner",
        "authorization": "Issue #412 owner authorization",
        "reason": "bounded corrective candidate for the collector provenance defect",
    }
    if head_sha is not None:
        entry["head_sha"] = head_sha
    return entry


def _trusted(*entries: Any) -> tuple[tuple[review.CoverageScope, ...] | None, str]:
    return review.load_coverage_scopes(_policy(list(entries)))


def _scope(
    *,
    issue: str = ISSUE,
    mode: str = "bounded_correction",
    criteria: Any = AUTHORIZED,
) -> dict[str, Any]:
    return {
        "issue": issue,
        "mode": mode,
        "criteria": [text for text in criteria],
        "authorized_by": "repo-owner",
        "authorization": "Issue #412 owner authorization",
        "reason": "bounded corrective candidate for the collector provenance defect",
    }


def _authority(*, head_sha: str = HEAD) -> dict[str, Any]:
    return {
        "type": "codex",
        "tool": "codex-cli",
        "head_sha": head_sha,
        "reviewed_at": "2026-09-13T00:00:00Z",
        "artifact": review.REVIEW_RELATIVE_PATH,
    }


def _criterion(text: str, identifier: str = "AC-1") -> dict[str, Any]:
    return {"id": identifier, "criterion": text, "verdict": "satisfied", "evidence": "this suite"}


def _resolved(finding_id: str, *, regression_test: str, correction: str) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": "blocking",
        "resolution": "resolved",
        "evidence": "fixed",
        "resolution_evidence": {"correction": correction, "regression_test": regression_test},
    }


def _document(
    *,
    changes: tuple[ingress.ConnectorFileChange, ...] = CHANGES,
    criteria: Any = AUTHORIZED,
    families: Any = APPLICABLE,
    findings: Any = (),
    issue: str = ISSUE,
    base: str = BASE,
    head: str = HEAD,
    coverage_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claims = review.build_claims(
        issue=issue,
        base_ref="main",
        base_sha=base,
        changes=changes,
        acceptance_criteria=tuple(_criterion(text, f"AC-{index}") for index, text in enumerate(criteria, 1)),
        defect_families=tuple({"family": name, "outcome": "clear", "evidence": "swept"} for name in families),
        findings=tuple(findings),
        adversarial_dimensions=tuple(review.REQUIRED_ADVERSARIAL_DIMENSIONS),
    )
    return review.document_for(claims, authority=_authority(head_sha=head), coverage_scope=coverage_scope)


def _verify(
    document: dict[str, Any],
    *,
    changes: tuple[ingress.ConnectorFileChange, ...] = CHANGES,
    base: str = BASE,
    head_sha: str = HEAD,
    coverage_scopes: Any = (),
    resolution_corrections: Any = None,
) -> review.ReviewVerdict:
    return review.verify_claims(
        document,
        base_sha=base,
        changes=changes,
        families=FAMILIES,
        issue_criteria=CRITERIA,
        coverage_scopes=coverage_scopes,
        resolution_corrections=resolution_corrections,
        head_sha=head_sha,
    )


def _authorized_scopes(*entries: Any) -> tuple[review.CoverageScope, ...] | None:
    scopes, error = _trusted(*(entries or (_entry(),)))
    assert error == "", error
    return scopes


# --- 1. The strict default is unchanged ------------------------------------


def test_without_a_declared_scope_the_full_issue_requirement_still_applies() -> None:
    """No scope declared means the Issue's every criterion is owed, as before."""

    document = _document(criteria=CRITERIA)
    verdict = review.verify_claims(
        document,
        base_sha=BASE,
        changes=CHANGES,
        families=FAMILIES,
        issue_criteria=CRITERIA,
        head_sha=HEAD,
    )

    assert verdict.ok is True, verdict.reason
    assert "all 3 the Issue defines" in verdict.reason


def test_a_partial_review_under_the_default_is_still_incomplete() -> None:
    """The relaxation is opt-in: a partial review with no scope stays rejected."""

    verdict = _verify(_document(criteria=AUTHORIZED))

    assert verdict.state == "incomplete"
    assert "acceptance criteria" in verdict.reason


# --- 2. A candidate cannot authorize itself --------------------------------


def test_a_self_declared_scope_without_owner_authorization_is_refused() -> None:
    """The trusted branch declares nothing, so the claim is unauthenticated."""

    verdict = _verify(_document(coverage_scope=_scope()))

    assert verdict.state == "incomplete"
    assert "no owner authorization" in verdict.reason


def test_a_self_declared_scope_naming_an_unauthorized_mode_is_refused() -> None:
    """Mode is authenticated too; a candidate cannot invent a weaker mode."""

    verdict = _verify(
        _document(coverage_scope=_scope(mode="partial_issue")),
        coverage_scopes=_authorized_scopes(_entry()),
    )

    assert verdict.state == "incomplete"
    assert "no owner authorization" in verdict.reason


# --- 3. Scope is bound to the exact Issue ----------------------------------


def test_a_scope_for_a_different_issue_is_refused() -> None:
    """Issue #412's authorization must not license Issue #499's review."""

    verdict = _verify(
        _document(issue=OTHER_ISSUE, coverage_scope=_scope(issue=OTHER_ISSUE)),
        coverage_scopes=_authorized_scopes(_entry(issue=ISSUE)),
    )

    assert verdict.state == "incomplete"
    assert "no owner authorization" in verdict.reason


def test_a_scope_declared_for_another_issue_than_the_review_names_is_refused() -> None:
    """The declared Issue must be the Issue the review claims to satisfy."""

    verdict = _verify(
        _document(issue=ISSUE, coverage_scope=_scope(issue=OTHER_ISSUE)),
        coverage_scopes=_authorized_scopes(_entry(issue=OTHER_ISSUE)),
    )

    assert verdict.state == "incomplete"
    assert "does not match" in verdict.reason


# --- 4. Scope is bound to the exact candidate ------------------------------


def test_a_review_of_different_content_is_stale_under_a_bounded_scope() -> None:
    """A scoped review is a statement about a diff, exactly as a full one is."""

    verdict = _verify(
        _document(changes=CHANGES, coverage_scope=_scope()),
        changes=OTHER_CHANGES,
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "stale"
    assert "different content" in verdict.reason


# --- 5. Scope is bound to the exact head -----------------------------------


def test_a_review_recorded_for_another_head_is_stale_under_a_bounded_scope() -> None:
    """Content equality cannot detect an amended commit; the head record can."""

    verdict = _verify(
        _document(head=HEAD, coverage_scope=_scope()),
        head_sha=OTHER_HEAD,
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "stale"
    assert str(OTHER_HEAD)[:10] in verdict.reason or "exact head" in verdict.reason


def test_a_review_taken_against_another_base_is_stale_under_a_bounded_scope() -> None:
    """A bounded correction is still taken against its own base."""

    verdict = _verify(
        _document(base=BASE, coverage_scope=_scope()),
        base="d" * 40,
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "stale"
    assert "not this candidate's base" in verdict.reason


# --- 6. Affected criteria cannot exceed or escape authorization -----------


def test_declaring_a_criterion_beyond_the_authorized_subset_is_refused() -> None:
    """The owner authorized two criteria; claiming the third is not permitted."""

    verdict = _verify(
        _document(criteria=CRITERIA, coverage_scope=_scope(criteria=CRITERIA)),
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "incomplete"
    assert "not authorized" in verdict.reason


def test_declaring_a_criterion_the_issue_does_not_define_is_refused() -> None:
    """A criterion outside the Issue cannot be satisfied by the Issue's review."""

    phantom = "the candidate may be merged without an independent review"
    verdict = _verify(
        _document(criteria=AUTHORIZED + (phantom,), coverage_scope=_scope(criteria=AUTHORIZED + (phantom,))),
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "incomplete"
    assert "does not define" in verdict.reason


def test_a_review_that_does_not_cover_its_own_authorized_scope_is_incomplete() -> None:
    """Declaring a scope is a claim; the review must actually address it."""

    verdict = _verify(
        _document(criteria=AUTHORIZED[:1], coverage_scope=_scope(criteria=AUTHORIZED)),
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "incomplete"
    assert "does not cover" in verdict.reason


# --- 7. A bounded correction cannot complete the parent Issue --------------


def test_an_authorized_bounded_review_is_scoped_and_never_valid() -> None:
    """The whole point: it is admitted, and it is not Issue completion."""

    verdict = _verify(_document(coverage_scope=_scope()), coverage_scopes=_authorized_scopes())

    assert verdict.state == "scoped"
    assert verdict.ok is False
    assert "Issue completion" in verdict.reason


# --- 8. Path-derived defect families remain mandatory ----------------------


def test_a_bounded_review_missing_an_applicable_family_is_incomplete() -> None:
    """Scope relaxes coverage, never the applicable-family sweep."""

    verdict = _verify(
        _document(families=APPLICABLE[:1], coverage_scope=_scope()),
        coverage_scopes=_authorized_scopes(),
    )

    assert verdict.state == "incomplete"
    assert "DFF-013" in verdict.reason


# --- 9. Candidate findings remain mandatory --------------------------------


def test_a_bounded_review_with_an_unresolved_blocking_finding_is_unresolved() -> None:
    """A scoped review cannot carry an unresolved substantive finding."""

    finding = {"id": "F-1", "severity": "blocking", "resolution": "unresolved", "evidence": "still wrong"}
    verdict = _verify(_document(findings=(finding,), coverage_scope=_scope()), coverage_scopes=_authorized_scopes())

    assert verdict.state == "unresolved"
    assert "F-1" in verdict.reason


# --- 10. Regression evidence remains mandatory -----------------------------


def test_a_bounded_review_whose_regression_test_is_not_committed_is_incomplete() -> None:
    """The resolved-finding evidence rule is unchanged under a bounded scope."""

    finding = _resolved("F-2", regression_test="tests/elsewhere.py", correction=CORRECTION)
    verdict = _verify(_document(findings=(finding,), coverage_scope=_scope()), coverage_scopes=_authorized_scopes())

    assert verdict.state == "incomplete"
    assert "regression test" in verdict.reason


# --- 11. Correction commit/range evidence remains mandatory ----------------


def test_a_bounded_review_whose_correction_is_outside_the_range_is_stale() -> None:
    """A scoped review may not borrow a correction commit it did not carry."""

    finding = _resolved("F-3", regression_test="tests/test_hunter_reviewer_collector.py", correction=CORRECTION)
    verdict = _verify(
        _document(findings=(finding,), coverage_scope=_scope()),
        coverage_scopes=_authorized_scopes(),
        resolution_corrections=frozenset({"9" * 40}),
    )

    assert verdict.state == "stale"
    assert "commit range" in verdict.reason


# --- 12. Stale / replayed / mismatched authorization fails closed ----------


def test_a_replayed_authorization_for_another_head_is_refused() -> None:
    """An authorization pinned to one head cannot be replayed onto another."""

    verdict = _verify(
        _document(coverage_scope=_scope()),
        coverage_scopes=_authorized_scopes(_entry(head_sha=OTHER_HEAD)),
    )

    assert verdict.state == "incomplete"
    assert "head" in verdict.reason


def test_an_authorization_matching_this_exact_head_is_honoured() -> None:
    """The paired valid equivalent of the pinned-head guard."""

    verdict = _verify(
        _document(coverage_scope=_scope()),
        coverage_scopes=_authorized_scopes(_entry(head_sha=HEAD)),
    )

    assert verdict.state == "scoped"


def test_a_removed_authorization_fails_closed_on_replay() -> None:
    """Withdrawing the authorization withdraws the relaxation immediately."""

    verdict = _verify(_document(coverage_scope=_scope()), coverage_scopes=())

    assert verdict.state == "incomplete"
    assert "no owner authorization" in verdict.reason


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ("not-a-list", "must be a list"),
        ([{"mode": "bounded_correction", "criteria": ["a"]}], "must name one governing Issue"),
        ([{"issue": "abc", "mode": "bounded_correction", "criteria": ["a"]}], "must name one governing Issue"),
        ([{"issue": "412", "mode": "bounded_correction", "criteria": []}], "must authorize at least one"),
        ([{"issue": "412", "mode": "invented", "criteria": ["a"]}], "unrecognised"),
        ([_entry(), _entry(criteria=CRITERIA)], "more than once"),
    ],
)
def test_a_malformed_authorization_fails_closed_at_load(entries: Any, expected: str) -> None:
    """An unreadable or ambiguous authority is never partially honoured."""

    scopes, error = review.load_coverage_scopes(_policy(entries))

    assert scopes is None
    assert expected in error


def test_a_policy_without_coverage_scopes_loads_as_no_scopes() -> None:
    """Absence means the feature is off, which is the strict default."""

    scopes, error = review.load_coverage_scopes(_policy([]))

    assert error == ""
    assert scopes == ()


def test_a_policy_without_a_coverage_scopes_key_loads_as_no_scopes() -> None:
    """A default branch that never adopted this feature is not malformed."""

    policy = _policy([])
    del policy["review_progression"]["review_authority"]["coverage_scopes"]

    scopes, error = review.load_coverage_scopes(policy)

    assert error == ""
    assert scopes == ()


# --- 13. Scoped coverage survives the supported production paths ------------


def test_scoped_request_is_admissible_without_becoming_issue_completion() -> None:
    """Request validation may dispatch scoped work even though scoped is not valid."""
    document = _document(coverage_scope=_scope())
    document["review_request"] = {"schema": "hunter.review-request.v1", "claims_id": document["review_id"]}

    verdict = review.verify_review_request(
        document,
        base_sha=BASE,
        changes=CHANGES,
        families=FAMILIES,
        issue_criteria=CRITERIA,
        coverage_scopes=_authorized_scopes(),
        head_sha=HEAD,
    )

    assert verdict.state == review.SCOPED_STATE
    assert verdict.ok is False


def test_prepare_request_emits_coverage_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canonical --request path must carry the owner's scoped request."""
    monkeypatch.setattr(review, "local_changes", lambda *args, **kwargs: CHANGES)
    judgement = {
        "acceptance_criteria": tuple(_criterion(text, f"AC-{index}") for index, text in enumerate(AUTHORIZED, 1)),
        "adversarial_dimensions": review.REQUIRED_ADVERSARIAL_DIMENSIONS,
        "defect_families": tuple({"family": name, "outcome": "clear", "evidence": "swept"} for name in APPLICABLE),
        "findings": (),
        "coverage_scope": _scope(),
    }

    document = review.prepare_request(issue=ISSUE, base=BASE, head=HEAD, base_ref="main", judgement=judgement)

    assert document["coverage_scope"] == _scope()


def test_record_emits_coverage_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canonical --record path must not silently drop scoped coverage."""
    monkeypatch.setattr(review, "local_changes", lambda *args, **kwargs: CHANGES)
    monkeypatch.setattr(review, "_run_git", lambda *args, **kwargs: HEAD)
    judgement = {
        "acceptance_criteria": tuple(_criterion(text, f"AC-{index}") for index, text in enumerate(AUTHORIZED, 1)),
        "adversarial_dimensions": review.REQUIRED_ADVERSARIAL_DIMENSIONS,
        "authority": _authority(),
        "defect_families": tuple({"family": name, "outcome": "clear", "evidence": "swept"} for name in APPLICABLE),
        "findings": (),
        "coverage_scope": _scope(),
    }

    document = review.record(issue=ISSUE, base=BASE, head=HEAD, base_ref="main", judgement=judgement)

    assert document["coverage_scope"] == _scope()
