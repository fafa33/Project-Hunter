"""Issue #412 regression suite.

Three defect families this repository already understood recurred inside PR #411
and its predecessors, and each recurrence is treated here as a prevention-system
defect rather than a one-off finding:

* a correct change was permanently non-admissible because its commits carried an
  implementation agent's Git identity instead of the authorization-bound writer;
* a valid two-commit candidate stranded its intermediate commit, because the
  evidence model demanded that every commit have been a pushed head in its own
  right, and the only escape was a manual rewind and force-push;
* the first complete hostile pass over a candidate happened *after* it was marked
  Ready, so predictable defect families were rediscovered one review round at a
  time.

The tests below are paired throughout: every guard is shown to reject the
defect *and* to admit the canonically valid equivalent, because a guard that
blocks valid work is itself a defect.
"""

from __future__ import annotations

import json
from pathlib import Path

import hunter_connector_write_ingress as ingress
import hunter_defect_prevention_preflight as prevention
import hunter_governance_review_v2 as core
import hunter_pre_push
import hunter_pre_ready_review as review
import hunter_writer_provenance as provenance
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = "b" * 40
HEAD = "c" * 40
ANCESTOR = "d" * 40
PR_NUMBER = 413
BRANCH = "issue-412-pre-ready-hostile-review-gate"
WRITER = "claude"


# --------------------------------------------------------------------------
# Writer identity binding (DFF-010)
# --------------------------------------------------------------------------


def _policy(**overrides) -> dict:
    binding = {
        "match": provenance.EXACT_MATCH_MODE,
        "require_author_and_committer_independently": True,
        "require_single_writer_per_range": True,
        "identities": [
            {
                "login": "claude",
                "git_names": ["Claude"],
                "git_emails": ["noreply@anthropic.com"],
                "canonical_git_name": "Claude",
                "canonical_git_email": "noreply@anthropic.com",
            },
            {
                "login": "fafa33",
                "git_names": ["Farhad5778"],
                "git_emails": ["34549283+fafa33@users.noreply.github.com"],
                "canonical_git_name": "Farhad5778",
                "canonical_git_email": "34549283+fafa33@users.noreply.github.com",
            },
        ],
    }
    binding.update(overrides)
    return {provenance.BINDING_FIELD: binding}


def _binding(**overrides) -> provenance.WriterIdentityBinding:
    parsed, error = provenance.parse_binding(_policy(**overrides))
    assert parsed is not None, error
    return parsed


def _commit(
    sha: str = HEAD,
    *,
    author: tuple[str, str] = ("Claude", "noreply@anthropic.com"),
    committer: tuple[str, str] | None = None,
) -> provenance.CommitProvenance:
    committer = committer if committer is not None else author
    return provenance.CommitProvenance(sha, author[0], author[1], committer[0], committer[1])


def test_agent_committer_with_a_correct_tree_is_rejected_before_any_push() -> None:
    """The PR #411 trap: correct content, unauthorized committer identity."""
    verdict = provenance.evaluate_commit(
        _binding(),
        _commit(committer=("google-labs-jules[bot]", "161369871+google-labs-jules[bot]@users.noreply.github.com")),
    )

    assert verdict.ok is False
    assert "committer" in verdict.reason
    assert "google-labs-jules[bot]" in verdict.reason


def test_agent_attribution_trailer_cannot_rescue_an_unbound_identity() -> None:
    """Attribution lives in trailers; identity is read only from commit headers.

    The commit body below names a bound writer in a co-author trailer, which is
    exactly how an implementation agent preserves attribution. It must not make
    an unbound author admissible -- identity is never derived from message text.
    """
    binding = _binding()
    unbound = _commit(author=("google-labs-jules[bot]", "jules@example.invalid"))

    assert provenance.evaluate_commit(binding, unbound).ok is False

    # The same commit with the identity in the headers and the agent in a
    # trailer is the canonically valid form, and it is admitted.
    attributed = _commit(author=("Claude", "noreply@anthropic.com"))
    assert provenance.evaluate_commit(binding, attributed).ok is True


def test_author_and_committer_must_each_resolve_to_the_same_bound_writer() -> None:
    """'One of the two matched' is not a match: both resolve, to one identity."""
    binding = _binding()

    split = _commit(
        author=("Claude", "noreply@anthropic.com"),
        committer=("Farhad5778", "34549283+fafa33@users.noreply.github.com"),
    )
    verdict = provenance.evaluate_commit(binding, split)
    assert verdict.ok is False
    assert "splits its provenance" in verdict.reason


def test_a_bound_name_with_an_unbound_email_is_not_a_partial_match() -> None:
    """Identity is the pair, so no other entry may complete a half match."""
    verdict = provenance.evaluate_commit(_binding(), _commit(author=("Claude", "attacker@example.invalid")))

    assert verdict.ok is False


@pytest.mark.parametrize(
    "email",
    [
        "noreply@anthropic.com.attacker.example",
        "prefix-noreply@anthropic.com",
        "noreply@anthropic.como",
        " noreply@anthropic.com.evil ",
    ],
)
def test_substring_and_affix_lookalikes_are_refused(email: str) -> None:
    """The reintroduced historical defect: matching by prefix/suffix/domain.

    An earlier attempt at this guard accepted any email starting with the login
    or ending in a matching no-reply suffix. Each address below satisfies that
    weaker rule and none of them is the bound identity.
    """
    assert provenance.evaluate_commit(_binding(), _commit(author=("Claude", email))).ok is False


def test_canonically_equivalent_spelling_is_still_admitted() -> None:
    """Normalisation must not turn a valid identity into a false positive block."""
    equivalent = _commit(author=("  claude  ", "NoReply@Anthropic.com"))

    assert provenance.evaluate_commit(_binding(), equivalent).ok is True


def test_a_range_mixing_bound_writers_is_refused() -> None:
    verdict = provenance.evaluate_range(
        _binding(),
        (
            _commit(ANCESTOR, author=("Claude", "noreply@anthropic.com")),
            _commit(HEAD, author=("Farhad5778", "34549283+fafa33@users.noreply.github.com")),
        ),
    )

    assert verdict.ok is False
    assert "mixes authorization-bound writers" in verdict.reason


def test_a_multi_commit_range_under_one_writer_is_admitted() -> None:
    verdict = provenance.evaluate_range(_binding(), (_commit(ANCESTOR), _commit(HEAD)))

    assert verdict.ok is True
    assert verdict.writer_login == WRITER


def test_an_empty_range_fails_closed() -> None:
    """No evidence is not clean evidence."""
    assert provenance.evaluate_range(_binding(), ()).ok is False


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {provenance.BINDING_FIELD: {}},
        _policy(match="substring"),
        _policy(require_author_and_committer_independently=False),
        _policy(identities=[]),
        _policy(
            identities=[
                {
                    "login": "claude",
                    "git_names": ["Claude"],
                    "git_emails": ["noreply@anthropic.com"],
                    "canonical_git_name": "Someone Else",
                    "canonical_git_email": "noreply@anthropic.com",
                }
            ]
        ),
    ],
)
def test_missing_or_malformed_binding_fails_closed(policy: dict) -> None:
    parsed, error = provenance.parse_binding(policy)

    assert parsed is None
    assert error


def test_unreadable_commit_metadata_fails_closed() -> None:
    with pytest.raises(provenance.GitEvidenceUnavailable):
        provenance.parse_commit_records("deadbeef\x1fonly\x1fthree\x1e")


def test_the_repository_binding_names_the_identity_an_agent_must_configure() -> None:
    """The identity is discoverable from policy before the first commit exists."""
    binding, error = provenance.load_binding()

    assert binding is not None, error
    identity = binding.identity_for(WRITER)
    assert identity is not None
    assert identity.canonical_name and identity.canonical_email
    assert provenance.remediation(binding).count("scripts/hunter_writer_provenance.py") == 1


# --------------------------------------------------------------------------
# Range-level publication evidence (DFF-011)
# --------------------------------------------------------------------------


def _push_evidence(monkeypatch, actors: dict[str, str], *, branch: str = BRANCH) -> None:
    """Stub authenticated push-run evidence per commit SHA.

    A SHA absent from ``actors`` has no push-event workflow run at all -- exactly
    the state of an intermediate commit published inside a multi-commit push.
    """

    def reader(_repo, _token, sha, ref):
        if ref != branch or sha not in actors:
            return False, "", "trusted workflow carries no run for the exact-head push"
        return True, actors[sha], None

    monkeypatch.setattr(core, "read_exact_head_push_actor", reader)


def _range(*shas: str) -> tuple[dict, ...]:
    return tuple({"sha": sha} for sha in shas)


def test_two_commits_pushed_together_cannot_strand_the_first_commit(monkeypatch) -> None:
    """The Issue #412 requirement, stated as the behaviour that replaces the trap.

    Only the head of a push receives a push-event workflow run, so the earlier
    commit has no run of its own. It is published by the same authenticated push
    and must be admitted without any rewind or force-push.
    """
    _push_evidence(monkeypatch, {HEAD: WRITER})

    ok, message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range(ANCESTOR, HEAD))

    assert ok is True, message
    assert "publishes all 2 commit(s)" in message


def test_a_foreign_authenticated_ancestor_push_still_blocks_the_range(monkeypatch) -> None:
    """Range-level evidence is not a relaxation: a foreign push is still refused."""
    _push_evidence(monkeypatch, {ANCESTOR: "other-writer", HEAD: WRITER})

    ok, message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range(ANCESTOR, HEAD))

    assert ok is False
    assert f"commit {ANCESTOR[:10]} was pushed by authenticated actor 'other-writer'" in message


def test_the_exact_head_still_needs_its_own_authenticated_push(monkeypatch) -> None:
    _push_evidence(monkeypatch, {ANCESTOR: WRITER})

    ok, message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range(ANCESTOR, HEAD))

    assert ok is False
    assert f"authenticated push actor evidence for commit {HEAD[:10]} is unavailable" in message


def test_a_head_push_on_another_branch_publishes_nothing_here(monkeypatch) -> None:
    _push_evidence(monkeypatch, {HEAD: WRITER}, branch="some/other-branch")

    ok, _message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range(HEAD))

    assert ok is False


def test_the_head_push_actor_must_be_the_bound_writer(monkeypatch) -> None:
    _push_evidence(monkeypatch, {HEAD: "impersonator"})

    ok, message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range(HEAD))

    assert ok is False
    assert "was pushed by authenticated actor 'impersonator'" in message


def test_a_malformed_range_sha_fails_closed(monkeypatch) -> None:
    _push_evidence(monkeypatch, {HEAD: WRITER})

    ok, message = core.verify_range_push_provenance("repo", "token", HEAD, BRANCH, WRITER, _range("not-a-sha", HEAD))

    assert ok is False
    assert "malformed commit SHA" in message


# --------------------------------------------------------------------------
# Receipt lifecycle (DFF-012)
# --------------------------------------------------------------------------


def _change(path: str, blob: str = "1" * 40, status: str = "modified") -> ingress.ConnectorFileChange:
    return ingress.ConnectorFileChange(status, path, "", blob)


def _receipt(changes: tuple[ingress.ConnectorFileChange, ...]) -> dict:
    authorization = ingress.ConnectorWriteAuthorization(
        writer=WRITER,
        capability="feature-branch-write",
        issue="412",
        base_ref="main",
        base_sha=BASE,
        target_ref="connector/issue-412-x",
        paths=tuple(sorted({path for change in changes for path in change.affected_paths()})),
        changes=changes,
    )
    return authorization.document()


def _stage_receipt(monkeypatch, tmp_path: Path, receipt_changes, actual_changes) -> None:
    monkeypatch.chdir(tmp_path)
    receipt_path = tmp_path / ingress.AUTHORIZATION_RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(_receipt(receipt_changes)), encoding="utf-8")
    monkeypatch.setattr(hunter_pre_push.provenance, "resolve_governed_base", lambda *_a, **_k: BASE)
    monkeypatch.setattr(hunter_pre_push.review, "local_changes", lambda *_a, **_k: actual_changes)


def test_content_changed_after_the_receipt_blocks_the_push(monkeypatch, tmp_path: Path) -> None:
    """The receipt is the final mutation; a later edit invalidates it."""
    minted = (_change("src/hunter/a.py", "a" * 40),)
    mutated = (_change("src/hunter/a.py", "e" * 40), _change(ingress.AUTHORIZATION_RECEIPT_PATH, "f" * 40))
    _stage_receipt(monkeypatch, tmp_path, minted, mutated)

    with pytest.raises(RuntimeError, match="is stale"):
        hunter_pre_push._validate_receipt_freshness(HEAD)


def test_a_receipt_that_still_binds_the_exact_range_does_not_block_the_push(monkeypatch, tmp_path: Path) -> None:
    """The paired positive: an exact receipt is not churn to regenerate."""
    minted = (_change("src/hunter/a.py", "a" * 40),)
    actual = minted + (_change(ingress.AUTHORIZATION_RECEIPT_PATH, "f" * 40),)
    _stage_receipt(monkeypatch, tmp_path, minted, actual)

    hunter_pre_push._validate_receipt_freshness(HEAD)


def test_a_candidate_without_a_receipt_is_unaffected(monkeypatch, tmp_path: Path) -> None:
    """Receipt freshness is conditional: the clone path carries no receipt."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hunter_pre_push.provenance, "resolve_governed_base", lambda *_a, **_k: BASE)
    monkeypatch.setattr(hunter_pre_push.review, "local_changes", lambda *_a, **_k: (_change("src/hunter/a.py"),))

    hunter_pre_push._validate_receipt_freshness(HEAD)


def test_a_receipt_inherited_from_the_base_branch_does_not_block_the_push(monkeypatch, tmp_path: Path) -> None:
    """The Issue #407 receipt merged to main says nothing about a later candidate.

    From PR #411 onward every candidate inherits that file at its head. Treating
    its mere presence as this candidate's claim refused every ordinary push over
    a receipt describing already-merged work -- a false-positive block, and the
    exact way stale receipt claims would be carried into unrelated Issues.
    """
    monkeypatch.chdir(tmp_path)
    receipt_path = tmp_path / ingress.AUTHORIZATION_RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(_receipt((_change("src/hunter/other.py", "9" * 40),))), encoding="utf-8")
    monkeypatch.setattr(hunter_pre_push.provenance, "resolve_governed_base", lambda *_a, **_k: BASE)
    monkeypatch.setattr(hunter_pre_push.review, "local_changes", lambda *_a, **_k: (_change("scripts/x.py"),))

    hunter_pre_push._validate_receipt_freshness(HEAD)


def test_an_inherited_receipt_does_not_make_an_ordinary_branch_a_connector_candidate(monkeypatch) -> None:
    """The same rule on the trusted boundary, where the block actually lands."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_head_authorization_receipt", lambda *_a: ("present", {"schema": "x"}, None))

    verdict = core.verify_connector_ingress_authorization(
        "repo",
        "token",
        HEAD,
        PR_NUMBER,
        (core.PullRequestFile("modified", "scripts/x.py", "", "a" * 40),),
        _range(HEAD),
    )

    assert verdict.ok is True
    assert verdict.origin is False


def test_an_ordinary_branch_that_writes_a_receipt_is_still_refused(monkeypatch) -> None:
    """The relaxation covers inherited state only, never a receipt written here."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_head_authorization_receipt", lambda *_a: ("present", {"schema": "x"}, None))

    verdict = core.verify_connector_ingress_authorization(
        "repo",
        "token",
        HEAD,
        PR_NUMBER,
        (core.PullRequestFile("modified", ingress.AUTHORIZATION_RECEIPT_PATH, "", "a" * 40),),
        _range(HEAD),
    )

    assert verdict.ok is False
    assert "outside the connector namespace" in verdict.message


def test_retiring_an_inherited_receipt_is_not_a_claim_about_this_range(monkeypatch, tmp_path: Path) -> None:
    """Removing a stale receipt is a retirement, not a receipt this candidate wrote.

    The Issue #407 receipt merged onto the default branch can never again be a
    valid claim for any candidate -- it is bound to a deleted connector branch
    and an old fork point. A candidate that removes it must not be held to it,
    and must not be blocked for a file it deliberately deleted.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hunter_pre_push.provenance, "resolve_governed_base", lambda *_a, **_k: BASE)
    monkeypatch.setattr(
        hunter_pre_push.review,
        "local_changes",
        lambda *_a, **_k: (
            _change("scripts/x.py"),
            ingress.ConnectorFileChange("removed", ingress.AUTHORIZATION_RECEIPT_PATH, "", ""),
        ),
    )

    hunter_pre_push._validate_receipt_freshness(HEAD)


def test_the_stale_issue_407_receipt_is_not_carried_into_issue_412() -> None:
    """Acceptance: no merged connector receipt remains at this candidate's head."""
    assert not (ROOT / ingress.AUTHORIZATION_RECEIPT_PATH).exists()


def test_an_unparseable_receipt_fails_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    receipt_path = tmp_path / ingress.AUTHORIZATION_RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(hunter_pre_push.provenance, "resolve_governed_base", lambda *_a, **_k: BASE)
    monkeypatch.setattr(
        hunter_pre_push.review,
        "local_changes",
        lambda *_a, **_k: (_change(ingress.AUTHORIZATION_RECEIPT_PATH, "f" * 40),),
    )

    with pytest.raises(RuntimeError, match="unreadable"):
        hunter_pre_push._validate_receipt_freshness(HEAD)


# --------------------------------------------------------------------------
# Pre-ready hostile review (DFF-013) and its tamper binding (DFF-008)
# --------------------------------------------------------------------------

CANDIDATE_CHANGES = (
    _change("scripts/hunter_writer_provenance.py", "a" * 40, "added"),
    _change("docs/DEFECT_REGISTRY.json", "b" * 40),
)

FAMILIES = (
    {
        "id": "DFF-010",
        "applicability": {"changed_paths": ["scripts/"]},
    },
    {
        "id": "DFF-013",
        "applicability": {"changed_paths": ["docs/DEFECT_REGISTRY.json"]},
    },
    {
        "id": "DFF-001",
        "applicability": {"changed_paths": ["src/hunter/"]},
    },
)


def _judgement(*, families=("DFF-010", "DFF-013"), findings=()) -> dict:
    return {
        "acceptance_criteria": [
            {"id": "AC-1", "criterion": "the gate blocks Ready", "verdict": "satisfied", "evidence": "this suite"}
        ],
        "adversarial_dimensions": list(review.REQUIRED_ADVERSARIAL_DIMENSIONS),
        "defect_families": [{"family": name, "outcome": "clear", "evidence": "swept"} for name in families],
        "findings": list(findings),
    }


def _review_document(*, changes=CANDIDATE_CHANGES, base=BASE, **judgement_kwargs) -> dict:
    judgement = _judgement(**judgement_kwargs)
    claims = review.build_claims(
        issue="412",
        base_ref="main",
        base_sha=base,
        changes=changes,
        acceptance_criteria=tuple(judgement["acceptance_criteria"]),
        defect_families=tuple(judgement["defect_families"]),
        findings=tuple(judgement["findings"]),
        adversarial_dimensions=tuple(judgement["adversarial_dimensions"]),
    )
    return review.document_for(claims)


def _verify(document, *, changes=CANDIDATE_CHANGES, base=BASE) -> review.ReviewVerdict:
    return review.verify_claims(document, base_sha=base, changes=changes, families=FAMILIES)


def test_a_complete_review_of_this_exact_content_is_valid() -> None:
    verdict = _verify(_review_document())

    assert verdict.ok is True, verdict.reason


def test_ready_is_blocked_without_a_pre_ready_hostile_review() -> None:
    verdict = _verify(None)

    assert verdict.state == "missing"


def test_a_review_of_earlier_content_is_stale_for_this_head() -> None:
    """The binding is content, so any later mutation invalidates the review."""
    reviewed = _review_document()
    mutated = (CANDIDATE_CHANGES[0], _change("docs/DEFECT_REGISTRY.json", "9" * 40))

    verdict = _verify(reviewed, changes=mutated)

    assert verdict.state == "stale"
    assert "mutated after it was reviewed" in verdict.reason


def test_a_review_taken_against_another_base_is_stale() -> None:
    verdict = _verify(_review_document(base="0" * 40))

    assert verdict.state == "stale"


def test_an_unresolved_blocking_finding_blocks_ready() -> None:
    finding = {"id": "F-1", "severity": "blocking", "resolution": "unresolved", "evidence": "open"}
    verdict = _verify(_review_document(findings=(finding,)))

    assert verdict.state == "unresolved"
    assert "F-1" in verdict.reason


def test_an_unresolved_non_blocking_finding_does_not_block_ready() -> None:
    """Non-blocking findings are not made blocking to reach a zero comment count."""
    finding = {"id": "F-2", "severity": "non-blocking", "resolution": "unresolved", "evidence": "optional refactor"}

    assert _verify(_review_document(findings=(finding,))).ok is True


def test_an_applicable_family_left_unchecked_blocks_ready() -> None:
    verdict = _verify(_review_document(families=("DFF-010",)))

    assert verdict.state == "incomplete"
    assert "DFF-013" in verdict.reason


def test_a_review_cannot_narrow_its_own_applicability() -> None:
    """Applicability is re-derived from the trusted change set, never declared."""
    changed_paths = tuple(
        sorted({path for change in review.target_changes(CANDIDATE_CHANGES) for path in change.affected_paths()})
    )

    assert review.applicable_family_ids(FAMILIES, changed_paths) == ("DFF-010", "DFF-013")


def test_a_family_outside_the_changed_scope_is_not_demanded() -> None:
    """A guard that demanded inapplicable families would block valid work."""
    assert "DFF-001" not in review.applicable_family_ids(
        FAMILIES, ("scripts/hunter_writer_provenance.py", "docs/DEFECT_REGISTRY.json")
    )


def test_a_review_may_name_a_family_the_trusted_catalog_does_not_yet_know() -> None:
    """The candidate that *adds* a family must be able to review that family.

    The trusted controller reads the catalog from the default branch, so a family
    introduced by the candidate is unknown there. Refusing the review for naming
    it would block exactly the contribution that strengthens prevention. It is
    safe because what a review must cover is derived from the trusted catalog and
    the trusted changed paths, never from what the review lists -- an extra name
    cannot make an incomplete review look complete.
    """
    assert _verify(_review_document(families=("DFF-010", "DFF-013", "DFF-999"))).ok is True
    assert _verify(_review_document(families=("DFF-010", "DFF-999"))).state == "incomplete"


def test_an_incomplete_adversarial_batch_is_refused() -> None:
    document = _review_document()
    document["claims"]["adversarial_dimensions"] = ["authorization"]
    document["review_id"] = review.review_id(document["claims"])

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert "adversarial batch is incomplete" in verdict.reason


def test_a_hand_edited_review_is_detected_by_its_own_digest() -> None:
    """Tamper resistance: the review binds a digest over exactly its own claims."""
    document = _review_document(
        findings=({"id": "F-1", "severity": "blocking", "resolution": "unresolved", "evidence": "open"},)
    )
    document["claims"]["findings"] = []

    verdict = _verify(document)

    assert verdict.state == "stale"
    assert "does not match its own claims" in verdict.reason


def test_the_review_artifact_and_the_receipt_are_excluded_from_the_reviewed_set() -> None:
    """Otherwise recording the review would immediately invalidate the review."""
    with_artifacts = CANDIDATE_CHANGES + (
        _change(review.REVIEW_RELATIVE_PATH, "7" * 40),
        _change(ingress.AUTHORIZATION_RECEIPT_PATH, "8" * 40),
    )

    assert review.target_digest(with_artifacts) == review.target_digest(CANDIDATE_CHANGES)
    assert _verify(_review_document(), changes=with_artifacts).ok is True


def test_a_review_of_the_wrong_schema_is_not_a_review() -> None:
    assert _verify({"schema": "something.else", "claims": {}, "review_id": ""}).state == "missing"


def test_a_review_with_no_satisfied_acceptance_criterion_is_incomplete() -> None:
    document = _review_document()
    document["claims"]["acceptance_criteria"] = [
        {"id": "AC-1", "criterion": "x", "verdict": "not-applicable", "evidence": "n/a"}
    ]
    document["review_id"] = review.review_id(document["claims"])

    assert _verify(document).state == "incomplete"


def test_ready_admission_refuses_a_candidate_without_review_state(monkeypatch) -> None:
    """End to end: the missing review reaches candidate admission as a refusal."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "b" * 40),), None),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("absent", None, None))
    monkeypatch.setattr(review, "load_families", lambda *_a, **_k: (FAMILIES, ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "no pre-ready hostile review exists" in description


def test_ready_admission_fails_closed_when_review_evidence_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "b" * 40),), None),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("unavailable", None, "HTTP 502"))
    monkeypatch.setattr(review, "load_families", lambda *_a, **_k: (FAMILIES, ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "unavailable" in description


def test_review_gate_fails_closed_without_a_family_catalog(monkeypatch) -> None:
    monkeypatch.setattr(review, "load_families", lambda *_a, **_k: ((), "catalog missing"))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "catalog missing" in description


# --------------------------------------------------------------------------
# Local change-set derivation feeding the review binding
# --------------------------------------------------------------------------


def test_local_raw_diff_maps_onto_the_canonical_change_vocabulary() -> None:
    raw = "\0".join(
        [
            ":000000 100644 " + "0" * 40 + " " + "a" * 40 + " A",
            "scripts/new.py",
            ":100644 100644 " + "1" * 40 + " " + "2" * 40 + " M",
            "docs/x.json",
            ":100644 000000 " + "3" * 40 + " " + "0" * 40 + " D",
            "docs/gone.json",
            ":100644 100644 " + "4" * 40 + " " + "5" * 40 + " R100",
            "docs/old.md",
            "docs/new.md",
            "",
        ]
    )

    changes = review.parse_raw_diff(raw)

    assert {change.status for change in changes} == {"added", "modified", "removed", "renamed"}
    removed = next(change for change in changes if change.status == "removed")
    assert removed.blob_sha == "" and removed.previous_path == ""
    renamed = next(change for change in changes if change.status == "renamed")
    assert renamed.previous_path == "docs/old.md" and renamed.path == "docs/new.md"


def test_an_unrecognised_raw_diff_record_fails_closed() -> None:
    with pytest.raises(review.GitEvidenceUnavailable):
        review.parse_raw_diff("not-a-record\0")


# --------------------------------------------------------------------------
# Pending versus failure for hosted prerequisites (DFF-014)
# --------------------------------------------------------------------------


def _ingress_verdict(monkeypatch, proof: tuple[str, str]) -> tuple[str, str]:
    monkeypatch.setattr(core, "load_ingress_provenance_policy", lambda: (frozenset({WRITER}), "", None))
    monkeypatch.setattr(core, "load_connector_write_ingress_policy", lambda: (True, frozenset({"fafa33"}), None))
    monkeypatch.setattr(core, "read_pr_commits", lambda *_a: (True, (_signed_commit(),), None))
    monkeypatch.setattr(core, "read_pr_changed_files", lambda *_a: (True, (), None))
    monkeypatch.setattr(
        core,
        "verify_connector_ingress_authorization",
        lambda *_a: core.ConnectorAdmission(ok=True, origin=False, message=""),
    )
    monkeypatch.setattr(core, "read_trusted_upgrade_status", lambda *_a: proof)
    return core.verify_code_write_ingress_provenance("repo", "token", HEAD, PR_NUMBER)


def _signed_commit() -> dict:
    return {
        "sha": HEAD,
        "commit": {"verification": {"verified": True, "reason": "valid"}},
        "committer": {"login": WRITER},
    }


def test_a_running_hosted_proof_is_pending_not_failure(monkeypatch) -> None:
    """A proof that has not finished is not a defect in the candidate."""
    state, description = _ingress_verdict(monkeypatch, ("pending", "Waiting for exact-head validation."))

    assert state == "pending"
    assert "Candidate admission is waiting" in description


def test_an_invalid_hosted_proof_remains_failure(monkeypatch) -> None:
    state, description = _ingress_verdict(monkeypatch, ("failure", "trusted candidate preflight validation=failure"))

    assert state == "failure"
    assert "Candidate admission blocked" in description


def test_an_absent_hosted_proof_remains_failure(monkeypatch) -> None:
    """Missing is not running: absence of proof still blocks."""
    state, _description = _ingress_verdict(monkeypatch, ("missing", "status is missing"))

    assert state == "failure"


def test_a_successful_hosted_proof_admits_the_range(monkeypatch) -> None:
    state, _description = _ingress_verdict(monkeypatch, ("success", "validated"))

    assert state == "success"


def test_candidate_admission_propagates_pending_instead_of_publishing_failure(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_a: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_a: ("normal", None))
    monkeypatch.setattr(
        core,
        "verify_code_write_ingress_provenance",
        lambda *_a: ("pending", "Candidate admission is waiting: hosted proof is running"),
    )

    state, description = core.candidate_admission("repo", "token", HEAD, PR_NUMBER)

    assert state == "pending"
    assert "waiting" in description


# --------------------------------------------------------------------------
# The catalog may not overclaim its own enforcement (DFF-015)
# --------------------------------------------------------------------------


def _family(**overrides) -> dict:
    family = {
        "id": "DFF-900",
        "title": "example",
        "invariant": "an invariant",
        "applicability": {"changed_paths": ["scripts/"], "rationale": "because"},
        "prevention": {
            "mechanism": "a mechanism",
            "boundary": "local-pre-push",
            "guard_reference": "scripts/hunter_writer_provenance.py::evaluate_commit",
        },
        "regression_evidence": [f"tests/{Path(__file__).name}::test_a_multi_commit_range_under_one_writer_is_admitted"],
        "lifecycle": "locally-enforced",
        "sources": ["Issue #412"],
    }
    family.update(overrides)
    return family


def _validate(family: dict, lifecycle: dict | None = None) -> list[str]:
    return prevention.validate_recurring_defect_families({"families": [family]}, lifecycle or {})


def test_a_well_formed_family_validates() -> None:
    assert _validate(_family()) == []


def test_a_family_cannot_claim_enforcement_without_a_resolvable_guard() -> None:
    """An old test alone is detection, not prevention."""
    prevention_block = {"mechanism": "a mechanism", "boundary": "review"}
    errors = _validate(_family(prevention=prevention_block))

    assert any("requires a resolvable guard_reference" in error for error in errors)


def test_a_family_cannot_claim_prevented_without_merge_enforcement_evidence() -> None:
    errors = _validate(_family(lifecycle="prevented"))

    assert any("prevented requires a merge-gate boundary" in error for error in errors)


def test_a_family_cannot_claim_regression_tested_without_a_resolvable_test() -> None:
    errors = _validate(
        _family(regression_evidence=["tests/test_does_not_exist.py::test_nothing"], lifecycle="regression-tested")
    )

    assert any("only recorded" in error for error in errors)


def test_a_guard_reference_that_no_longer_resolves_is_refused() -> None:
    prevention_block = {
        "mechanism": "a mechanism",
        "boundary": "local-pre-push",
        "guard_reference": "scripts/hunter_writer_provenance.py::renamed_away",
    }
    errors = _validate(_family(prevention=prevention_block))

    assert any("invalid guard_reference" in error for error in errors)


def test_a_guard_reference_may_not_point_at_the_tests_tree() -> None:
    prevention_block = {
        "mechanism": "a mechanism",
        "boundary": "local-pre-push",
        "guard_reference": f"tests/{Path(__file__).name}::test_a_well_formed_family_validates",
    }
    errors = _validate(_family(prevention=prevention_block))

    assert any("must not target the tests tree" in error for error in errors)


def test_a_family_without_a_stable_identity_is_refused() -> None:
    assert _validate(_family(id="not-a-family-id"))


def test_duplicate_family_identities_are_refused() -> None:
    errors = prevention.validate_recurring_defect_families({"families": [_family(), _family()]}, {})

    assert any("duplicate defect family id" in error for error in errors)


def test_an_empty_catalog_fails_closed() -> None:
    assert prevention.validate_recurring_defect_families({"families": []}, {})
    assert prevention.validate_recurring_defect_families({}, {})


def test_lifecycle_disagreement_with_explicit_enforcement_is_refused() -> None:
    errors = _validate(_family(), {"explicit_enforcement": {"DFF-900": {"state": "prevented"}}})

    assert any("disagrees with its explicit enforcement state" in error for error in errors)


# --------------------------------------------------------------------------
# The repository's own artifacts satisfy the guards they introduce
# --------------------------------------------------------------------------


def test_the_repository_catalog_and_policy_pass_their_own_guards() -> None:
    assert prevention.validate_defect_prevention_lifecycle() == []


def test_every_declared_family_is_reachable_by_some_changed_path() -> None:
    """A family no candidate can ever trigger is documentation, not prevention."""
    families, error = review.load_families()
    assert not error
    for family in families:
        scope = family["applicability"]["changed_paths"]
        assert review.applicable_family_ids(families, tuple(scope))


def test_the_issue_412_families_are_registered_with_honest_stages() -> None:
    families, error = review.load_families()
    assert not error
    stages = {family["id"]: family["lifecycle"] for family in families}
    for family_id in ("DFF-010", "DFF-011", "DFF-012", "DFF-013", "DFF-014", "DFF-015"):
        assert family_id in stages
        assert stages[family_id] in {"locally-enforced", "hosted-enforced"}


# --------------------------------------------------------------------------
# The push boundary refuses before any remote mutation
# --------------------------------------------------------------------------


def _pre_push_update(sha: str) -> list[str]:
    return [f"refs/heads/{BRANCH} {sha} refs/heads/{BRANCH} {hunter_pre_push.ZERO_SHA}\n"]


def _pre_push_git(tmp_path: Path, head: str):
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    return fake_git


def test_pre_push_refuses_an_unbound_writer_before_any_network_push(monkeypatch, tmp_path: Path) -> None:
    """The whole point of Issue #412: the refusal happens locally, not after a push."""
    pushed: list[object] = []
    monkeypatch.setattr(hunter_pre_push, "_run_git", _pre_push_git(tmp_path, HEAD))
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    monkeypatch.setattr(hunter_pre_push, "_validate_receipt_freshness", lambda _head: None)
    monkeypatch.setattr(
        hunter_pre_push.provenance,
        "check_range",
        lambda *_a, **_k: "commit cccccccccc committer Jules <jules@example.invalid> is not an "
        "authorization-bound writer identity",
    )

    def _record(*args, **_kwargs):
        pushed.append(args)
        raise AssertionError("the canonical preflight must not run for an inadmissible range")

    monkeypatch.setattr(hunter_pre_push.subprocess, "run", _record)

    with pytest.raises(RuntimeError, match="authorization-bound writer identity"):
        hunter_pre_push.enforce_pre_push(_pre_push_update(HEAD))

    assert pushed == [], "the canonical preflight must not even start for an inadmissible range"


def test_pre_push_admits_a_bound_multi_commit_range(monkeypatch, tmp_path: Path) -> None:
    """The paired positive: a bound range reaches the canonical preflight."""
    monkeypatch.setattr(hunter_pre_push, "_run_git", _pre_push_git(tmp_path, HEAD))
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    monkeypatch.setattr(hunter_pre_push, "_validate_receipt_freshness", lambda _head: None)
    monkeypatch.setattr(hunter_pre_push, "report_pre_ready_review_state", lambda _head: None)
    monkeypatch.setattr(hunter_pre_push.provenance, "check_range", lambda *_a, **_k: None)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)

    class _Completed:
        returncode = 0

    monkeypatch.setattr(hunter_pre_push.subprocess, "run", lambda *a, **k: _Completed())

    assert hunter_pre_push.enforce_pre_push(_pre_push_update(HEAD)) == 0


def test_pre_push_fails_closed_when_the_fork_point_is_unavailable(monkeypatch) -> None:
    """An unknown governed range is not an empty one."""
    monkeypatch.setattr(
        provenance,
        "resolve_governed_base",
        lambda *_a, **_k: (_ for _ in ()).throw(provenance.GitEvidenceUnavailable("no fork point")),
    )

    problem = provenance.check_range(HEAD)

    assert problem is not None
    assert "unavailable" in problem


def test_pre_push_fails_closed_without_repository_policy(monkeypatch) -> None:
    monkeypatch.setattr(provenance, "load_binding", lambda *_a, **_k: (None, "policy is missing"))

    problem = provenance.check_range(HEAD)

    assert problem is not None
    assert "policy is missing" in problem


# --------------------------------------------------------------------------
# Findings from the single adversarial batch over this candidate
# --------------------------------------------------------------------------


def test_rename_representation_does_not_change_the_reviewed_subject() -> None:
    """Local git and GitHub need not agree that a pair of paths is a rename.

    Rename detection is a heuristic on both sides. If the binding depended on it,
    a locally valid review would look stale to the trusted controller whenever the
    two heuristics disagreed -- a false-positive Ready block with no defect behind
    it. Both representations must therefore reduce to the same reviewed subject.
    """
    as_rename = (ingress.ConnectorFileChange("renamed", "docs/new.md", "docs/old.md", "5" * 40),)
    as_delete_add = (
        ingress.ConnectorFileChange("removed", "docs/old.md", "", ""),
        ingress.ConnectorFileChange("added", "docs/new.md", "", "5" * 40),
    )

    assert review.target_digest(as_rename) == review.target_digest(as_delete_add)
    assert _verify(_review_document(changes=as_rename), changes=as_delete_add).ok is True


def test_different_content_still_invalidates_an_expanded_rename() -> None:
    """The expansion must not lose the content binding it exists to preserve."""
    reviewed = (ingress.ConnectorFileChange("renamed", "docs/new.md", "docs/old.md", "5" * 40),)
    mutated = (ingress.ConnectorFileChange("renamed", "docs/new.md", "docs/old.md", "6" * 40),)

    assert _verify(_review_document(changes=reviewed), changes=mutated).state == "stale"


@pytest.mark.parametrize(
    ("github_status", "canonical"),
    [
        ("added", "added"),
        ("modified", "modified"),
        ("removed", "removed"),
        ("renamed", "renamed"),
        ("changed", "modified"),
        ("copied", "added"),
        ("Modified", "modified"),
    ],
)
def test_github_change_vocabulary_maps_onto_the_canonical_one(github_status: str, canonical: str) -> None:
    assert review.canonical_status(github_status) == canonical


def test_an_unrecognised_github_status_fails_closed(monkeypatch) -> None:
    """An unmapped status is unrecognised evidence, never a change to drop."""
    assert review.canonical_status("teleported") is None

    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("teleported", "scripts/x.py", "", "a" * 40),), None),
    )
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "unrecognised status" in description


def test_a_candidate_triggering_no_family_needs_no_review(monkeypatch) -> None:
    """A guard that demanded a review with nothing applicable would block valid work.

    A dependency-only candidate touches no path any recurring-defect family
    claims, and its author cannot produce a review artifact at all. Requiring one
    would be the very defect family DFF-009 exists to prevent: granting a channel
    an obligation it cannot satisfy.
    """
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, "dependabot/pip/x", "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("modified", "requirements/ci-constraints.txt", "", "a" * 40),), None),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("absent", None, None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "success"
    assert "No recurring-defect family applies" in description


def test_a_present_review_is_verified_even_when_no_family_applies(monkeypatch) -> None:
    """The exemption covers absence only; a stale artifact is never carried."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, "dependabot/pip/x", "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("modified", "requirements/ci-constraints.txt", "", "a" * 40),), None),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("present", _review_document(), None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))
    monkeypatch.setattr(core, "read_issue_acceptance_criteria", lambda *_a: ("present", (), ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "different content" in description


def test_the_review_artifact_is_governance_evidence_not_authorized_content() -> None:
    """Otherwise the connector channel would be handed an unsatisfiable obligation.

    The review artifact lives outside the connector's allowed paths, so a receipt
    can never authorize it. If admission bound it as content, a connector
    candidate carrying a review would fail the path check while a connector
    candidate without one would fail the review gate -- inadmissible either way.
    """
    policy, error = ingress.load_policy()
    assert policy is not None and error == ""
    scope = policy.capability_scope(policy.required_capability)
    assert scope is not None
    assert not any(
        review.REVIEW_RELATIVE_PATH.startswith(entry.rstrip("/") + "/") or review.REVIEW_RELATIVE_PATH == entry
        for entry in scope.allowed_paths
    )
    source = (ROOT / "scripts" / "hunter_governance_review_v2.py").read_text(encoding="utf-8")
    assert "EVIDENCE_PATHS = {ingress.AUTHORIZATION_RECEIPT_PATH, pre_ready.REVIEW_RELATIVE_PATH}" in source


def test_the_receipt_does_not_have_to_bind_the_review_artifact(monkeypatch, tmp_path: Path) -> None:
    """The pre-push receipt scope matches what the trusted controller re-derives."""
    minted = (_change("src/hunter/a.py", "a" * 40),)
    actual = minted + (
        _change(ingress.AUTHORIZATION_RECEIPT_PATH, "f" * 40),
        _change(review.REVIEW_RELATIVE_PATH, "e" * 40),
    )
    _stage_receipt(monkeypatch, tmp_path, minted, actual)

    hunter_pre_push._validate_receipt_freshness(HEAD)


def test_a_review_must_name_the_governing_issue_number() -> None:
    document = _review_document()
    document["claims"]["issue"] = "not-a-number"
    document["review_id"] = review.review_id(document["claims"])

    assert _verify(document).state == "incomplete"


def test_dependency_manifests_are_deliberately_outside_the_review_scope() -> None:
    """Pinned in a test so widening it back is a deliberate, reviewed decision."""
    families, error = review.load_families()
    assert not error
    for path in ("requirements/ci-constraints.txt", "poetry.lock"):
        assert review.applicable_family_ids(families, (path,)) == ()
    assert "DFF-013" in review.applicable_family_ids(families, ("src/hunter/cli.py",))


def test_a_rename_onto_an_excluded_artifact_cannot_hide_the_source_deletion() -> None:
    """The exclusion applies to expanded facts, not to whole rename records.

    Renaming a governed file onto an evidence-artifact path would otherwise drop
    the entire record -- and with it the fact that the governed file is gone --
    out of the reviewed change set.
    """
    hidden = (
        ingress.ConnectorFileChange("renamed", review.REVIEW_RELATIVE_PATH, "scripts/hunter_pre_push.py", "7" * 40),
    )
    reviewed = review.target_changes(hidden)

    assert [(change.status, change.path) for change in reviewed] == [("removed", "scripts/hunter_pre_push.py")]
    assert review.target_digest(hidden) != review.target_digest(())


def test_pre_push_refuses_a_rename_onto_a_governance_evidence_path(monkeypatch, tmp_path: Path) -> None:
    minted = (_change("src/hunter/a.py", "a" * 40),)
    actual = minted + (
        ingress.ConnectorFileChange("renamed", review.REVIEW_RELATIVE_PATH, "scripts/hunter_pre_push.py", "7" * 40),
    )
    _stage_receipt(monkeypatch, tmp_path, minted, actual)

    with pytest.raises(RuntimeError, match="rename destination"):
        hunter_pre_push._validate_receipt_freshness(HEAD)


def test_a_review_taken_against_another_base_branch_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (
            True,
            (
                core.PullRequestFile("added", "scripts/hunter_writer_provenance.py", "", "a" * 40),
                core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "b" * 40),
            ),
            None,
        ),
    )
    document = _review_document()
    document["claims"]["base_ref"] = "release/1.x"
    document["review_id"] = review.review_id(document["claims"])
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("present", document, None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))
    monkeypatch.setattr(core, "read_issue_acceptance_criteria", lambda *_a: ("present", (), ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "base branch" in description


def test_a_copied_file_is_not_rejected_as_a_malformed_addition(monkeypatch) -> None:
    """GitHub reports a source path for a copy; an addition may not carry one."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, BRANCH, "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (
            True,
            (core.PullRequestFile("copied", "scripts/copy.py", "scripts/original.py", "a" * 40),),
            None,
        ),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("absent", None, None))
    monkeypatch.setattr(
        core.pre_ready,
        "load_families",
        lambda *_a, **_k: (({"id": "DFF-X", "applicability": {"changed_paths": ["docs/"]}},), ""),
    )

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "success", description


def test_a_stale_local_base_branch_cannot_widen_the_governed_range(monkeypatch) -> None:
    """Only the remote-tracking base decides the fork point."""
    consulted: list[str] = []

    def fake_git(*args: str, cwd=None) -> str:
        consulted.append(args[-1])
        raise provenance.GitEvidenceUnavailable("no such ref")

    monkeypatch.setattr(provenance, "_run_git", fake_git)

    with pytest.raises(provenance.GitEvidenceUnavailable, match="git fetch"):
        provenance.resolve_governed_base(HEAD)

    assert consulted == ["origin/main", "refs/remotes/origin/main"]


def test_local_blob_digests_are_full_length_and_comparable_with_github() -> None:
    """`git diff --raw` abbreviates by default; an abbreviated digest binds nothing.

    The review is verified locally against git and remotely against GitHub's
    changed-file listing, which reports full 40-character blob SHAs. An
    abbreviated local digest would make every review look stale to the trusted
    controller -- and, before that, fail canonical normalisation outright.
    """
    changes = review.local_changes("HEAD~1", "HEAD") if _has_parent() else ()
    for change in changes:
        if change.status != "removed":
            assert len(change.blob_sha) == 40, change


def _has_parent() -> bool:
    try:
        review._run_git("rev-parse", "HEAD~1")
    except review.GitEvidenceUnavailable:
        return False
    return True


# --------------------------------------------------------------------------
# Independent review findings (Codex, PR #413 on commit 00245cd827)
# --------------------------------------------------------------------------

ISSUE_BODY = """## Required behavior

- something that is not an acceptance criterion

## Acceptance criteria

- First criterion that must be covered.
- Second criterion that must be covered.
- `Third` **criterion** that must be covered.

## Regression scenarios must include:

- another bullet that is not an acceptance criterion
"""


def test_issue_acceptance_criteria_are_scoped_to_their_own_section() -> None:
    """Other bullet lists in the Issue are not acceptance criteria."""
    derived = review.parse_issue_acceptance_criteria(ISSUE_BODY)

    assert derived == (
        "first criterion that must be covered",
        "second criterion that must be covered",
        "third criterion that must be covered",
    )


def test_an_issue_with_no_criteria_section_imposes_no_coverage() -> None:
    """Nothing to cover is the truthful reading, not a bypass."""
    assert review.parse_issue_acceptance_criteria("## Motivation\n\n- a bullet\n") == ()


def test_a_wrapped_criterion_is_one_criterion() -> None:
    body = "## Acceptance criteria\n\n- A criterion that wraps\n  onto a second line.\n"

    assert review.parse_issue_acceptance_criteria(body) == ("a criterion that wraps onto a second line",)


def test_indented_sub_bullets_are_detail_not_criteria() -> None:
    body = "## Acceptance criteria\n\n- A criterion.\n  - supporting detail\n"

    assert review.parse_issue_acceptance_criteria(body) == ("a criterion",)


def _criteria_review(*criteria: str) -> dict:
    judgement = _judgement()
    claims = review.build_claims(
        issue="412",
        base_ref="main",
        base_sha=BASE,
        changes=CANDIDATE_CHANGES,
        acceptance_criteria=tuple(
            {"id": f"AC-{index}", "criterion": text, "verdict": "satisfied", "evidence": "covered"}
            for index, text in enumerate(criteria, start=1)
        ),
        defect_families=tuple(judgement["defect_families"]),
        findings=(),
        adversarial_dimensions=tuple(judgement["adversarial_dimensions"]),
    )
    return review.document_for(claims)


def test_a_review_covering_one_self_authored_criterion_is_refused() -> None:
    """The Codex P1: an arbitrary non-empty criteria list is not completeness.

    Before this, a single invented criterion marked satisfied made the review
    structurally complete, so the acceptance-criteria leg of the gate proved
    nothing about the Issue it named.
    """
    derived = review.parse_issue_acceptance_criteria(ISSUE_BODY)
    document = _criteria_review("Something I made up entirely.")

    verdict = review.verify_claims(
        document, base_sha=BASE, changes=CANDIDATE_CHANGES, families=FAMILIES, issue_criteria=derived
    )

    assert verdict.state == "incomplete"
    assert "3 acceptance criteria the governing Issue defines" in verdict.reason


def test_a_review_omitting_all_but_one_criterion_is_refused() -> None:
    derived = review.parse_issue_acceptance_criteria(ISSUE_BODY)
    document = _criteria_review("First criterion that must be covered.")

    verdict = review.verify_claims(
        document, base_sha=BASE, changes=CANDIDATE_CHANGES, families=FAMILIES, issue_criteria=derived
    )

    assert verdict.state == "incomplete"


def test_a_review_covering_every_issue_criterion_is_admitted() -> None:
    """The paired positive: full coverage passes, and markdown noise does not block."""
    derived = review.parse_issue_acceptance_criteria(ISSUE_BODY)
    document = _criteria_review(
        "First criterion that must be covered.",
        "  second   criterion that must be covered  ",
        "`Third` **criterion** that must be covered",
    )

    verdict = review.verify_claims(
        document, base_sha=BASE, changes=CANDIDATE_CHANGES, families=FAMILIES, issue_criteria=derived
    )

    assert verdict.ok is True, verdict.reason
    assert "including all 3 the Issue defines" in verdict.reason


def test_extra_criteria_beyond_the_issue_are_allowed() -> None:
    """A reviewer may record more than the Issue asks; coverage is what is measured."""
    derived = review.parse_issue_acceptance_criteria(ISSUE_BODY)
    document = _criteria_review(
        "First criterion that must be covered.",
        "Second criterion that must be covered.",
        "Third criterion that must be covered.",
        "An extra criterion the reviewer added.",
    )

    assert review.verify_claims(
        document, base_sha=BASE, changes=CANDIDATE_CHANGES, families=FAMILIES, issue_criteria=derived
    ).ok


@pytest.mark.parametrize(
    ("branch", "issue"),
    [
        ("issue-412-pre-ready-hostile-review-gate-123", "412"),
        ("claude/issue-409-rebuilt-clean", "409"),
        ("connector/issue-402-adr", "402"),
        ("fix/trusted-black-upgrade-bootstrap", None),
        ("main", None),
    ],
)
def test_branch_issue_binding(branch: str, issue: str | None) -> None:
    assert core.issue_for_branch(branch) == issue


def test_a_review_naming_another_issue_than_the_branch_is_refused(monkeypatch) -> None:
    """A review cannot be measured against a conveniently chosen Issue."""
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, "issue-412-x", "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (True, (core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "b" * 40),), None),
    )
    document = _review_document()
    document["claims"]["issue"] = "999"
    document["review_id"] = review.review_id(document["claims"])
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("present", document, None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "binds Issue #412" in description


def test_unavailable_issue_criteria_evidence_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_refs", lambda *_a: (True, "issue-412-x", "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_a: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_a: (
            True,
            (
                core.PullRequestFile("added", "scripts/hunter_writer_provenance.py", "", "a" * 40),
                core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "b" * 40),
            ),
            None,
        ),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_a: ("present", _review_document(), None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_a, **_k: (FAMILIES, ""))
    monkeypatch.setattr(core, "read_issue_acceptance_criteria", lambda *_a: ("unavailable", (), "HTTP 502"))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "acceptance-criteria evidence is unavailable" in description


def test_a_pull_request_cannot_stand_in_for_the_governing_issue(monkeypatch) -> None:
    """Otherwise a candidate could point the gate at its own PR description."""
    monkeypatch.setattr(
        core, "request_json", lambda *_a, **_k: {"body": "## Acceptance criteria\n\n- x\n", "pull_request": {}}
    )

    state, criteria, error = core.read_issue_acceptance_criteria("repo", "token", "413")

    assert state == "unavailable" and criteria == ()
    assert "is a pull request" in error


def test_candidate_admission_reconciles_after_the_trusted_upgrade_completes() -> None:
    """The Codex P1: a pending head must not stay Ready with nothing re-checking it.

    Admission is deliberately not returned to Draft while a trusted proof is
    still running. That is only safe if something re-evaluates admission once the
    proof lands -- otherwise a head marked Ready mid-proof stays Ready while
    unadmitted, contrary to unadmitted_head_state=draft. Trusted-upgrade
    completion previously triggered only the status-only Governance Review.
    """
    workflow = yaml.safe_load((ROOT / ".github/workflows/hunter-candidate-admission.yml").read_text(encoding="utf-8"))
    triggers = workflow[True]["workflow_run"]["workflows"]

    assert "Hunter / Trusted Preflight Upgrade" in triggers
    assert "Hunter / Pre-PR Preflight" in triggers

    condition = workflow["jobs"]["candidate-admission"]["if"]
    assert "pull_request_target" in condition and "push" in condition


def test_the_draft_controller_still_leaves_a_pending_head_alone() -> None:
    """Reconciling later is the fix, not turning pending back into a failure."""
    source = (ROOT / "scripts/hunter_candidate_admission.py").read_text(encoding="utf-8")

    assert 'if admission_state == "pending":' in source
