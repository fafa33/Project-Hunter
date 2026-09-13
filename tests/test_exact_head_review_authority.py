from __future__ import annotations

import hashlib
import json
from typing import Any

import hunter_connector_write_ingress as ingress
import hunter_defect_prevention_preflight as prevention
import hunter_governance_review_v2 as core
import hunter_merge_readiness_v2 as readiness
import hunter_pre_ready_review as review
import pytest

_REAL_READ_REVIEWS = core.read_pr_pool_review_comments

HEAD = "a" * 40
BASE = "0" * 40
PR_NUMBER = 469
COPILOT = "chatgpt-codex-connector[bot]"


def _change(path: str, blob: str = "1" * 40, status: str = "modified") -> ingress.ConnectorFileChange:
    return ingress.ConnectorFileChange(status, path, "", blob)


CANDIDATE_CHANGES = (
    _change("scripts/hunter_writer_provenance.py", "1" * 40, "added"),
    _change("docs/DEFECT_REGISTRY.json", "2" * 40),
)


def _attempt(
    agent_id: str = "codex",
    *,
    status: str = "exhausted",
    reason: str = "rate-limited at the configured timeout",
    timeout_seconds: int = 900,
    failure_class: str = "transient",
    attempt_count: int = 2,
    invocation_reference: str = "actions/runs/6372342596",
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "status": status,
        "reason": reason,
        "timeout_seconds": timeout_seconds,
        "failure_class": failure_class,
        "attempt_count": attempt_count,
        "invocation_reference": invocation_reference,
    }


def _agent(
    agent_id: str = "codex", *, priority: int = 1, enabled: bool = True, timeout_seconds: int = 900
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "priority": priority,
        "enabled": enabled,
        "exact_head_support": True,
        "timeout_seconds": timeout_seconds,
        "retryable": True,
    }


CODEX_AGENT = _agent()
ALTERNATE = _agent("alternate-agent-1", priority=2)


def _pool(*, agents: tuple = (), last_resort: str = "opencode", retries_per_agent: int = 1) -> dict[str, Any]:
    return {
        "last_resort": last_resort,
        "timeout_policy": {
            "bounded": True,
            "default_seconds": 900,
            "max_seconds": 1800,
            "retries_per_agent": retries_per_agent,
        },
        "agents": (CODEX_AGENT,) + tuple(agents),
    }


def _use_pool(monkeypatch, pool: dict[str, Any]) -> None:
    monkeypatch.setattr(review, "load_reviewer_pool", lambda *_args, **_kwargs: (pool, ""))


def _authority(authority_type: str = "codex", head_sha: str = HEAD, attempts=None, **overrides: Any) -> dict[str, Any]:
    authority: dict[str, Any] = {
        "type": authority_type,
        "tool": "codex-cli" if authority_type == "codex" else "opencode-hunter-review",
        "head_sha": head_sha,
        "reviewed_at": "2026-09-13T00:00:00Z",
        "artifact": review.REVIEW_RELATIVE_PATH,
    }
    if authority_type == "opencode":
        authority.update(
            {
                "fallback_reason": "Codex unavailable (rate-limited)",
                "unresolved_thread_count": 0,
                "governance_state": "success",
                "trusted_preflight_state": "success",
                "structured_evidence_status": "complete",
            }
        )
    if authority_type != "codex":
        authority["reviewer_attempts"] = list(attempts) if attempts is not None else [dict(_attempt())]
    authority.update(overrides)
    return authority


FAMILIES = (
    {"id": "DFF-010", "applicability": {"changed_paths": ["scripts/"]}},
    {"id": "DFF-013", "applicability": {"changed_paths": ["docs/DEFECT_REGISTRY.json"]}},
)


def _review_document(
    *, changes=CANDIDATE_CHANGES, base=BASE, authority=None, findings=(), families=("DFF-010", "DFF-013")
) -> dict:
    claims = review.build_claims(
        issue="467",
        base_ref="main",
        base_sha=base,
        changes=changes,
        acceptance_criteria=(
            {"id": "AC-1", "criterion": "the gate blocks Ready", "verdict": "satisfied", "evidence": "this suite"},
        ),
        defect_families=tuple({"family": name, "outcome": "clear", "evidence": "swept"} for name in families),
        findings=tuple(findings),
        adversarial_dimensions=tuple(review.REQUIRED_ADVERSARIAL_DIMENSIONS),
    )
    return review.document_for(claims, authority=authority if authority is not None else _authority())


def _verify(
    document: Any,
    *,
    changes=CANDIDATE_CHANGES,
    base=BASE,
    head_sha=HEAD,
    resolution_corrections=None,
) -> review.ReviewVerdict:
    return review.verify_claims(
        document,
        base_sha=base,
        changes=changes,
        families=FAMILIES,
        resolution_corrections=resolution_corrections,
        head_sha=head_sha,
    )


def _install_governance(
    monkeypatch,
    *,
    document: Any,
    state: str = "present",
    comments: tuple[Any, ...] = (),
) -> None:
    monkeypatch.setattr(core, "read_pr_refs", lambda *_args: (True, "issue-467-reviewer-pool-failover", "main", None))
    monkeypatch.setattr(core, "read_merge_base", lambda *_args: (True, BASE, None))
    monkeypatch.setattr(
        core,
        "read_pr_changed_files",
        lambda *_args: (
            True,
            (
                core.PullRequestFile("added", "scripts/hunter_writer_provenance.py", "", "1" * 40),
                core.PullRequestFile("modified", "docs/DEFECT_REGISTRY.json", "", "2" * 40),
            ),
            None,
        ),
    )
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_args: (state, document, None))
    monkeypatch.setattr(core.pre_ready, "load_families", lambda *_args, **_kwargs: (FAMILIES, ""))
    monkeypatch.setattr(core, "read_issue_acceptance_criteria", lambda *_args: ("present", (), ""))
    monkeypatch.setattr(core, "read_pr_commits", lambda *_args: (True, ({"sha": HEAD},), None))
    monkeypatch.setattr(core, "read_pr_pool_review_comments", lambda *_args: (comments, None))


# ---------------------------------------------------------------------------
# Strict exact-head binding: an ancestor-recorded head is stale, never valid.
# ---------------------------------------------------------------------------


def test_an_ancestor_recorded_head_is_stale_not_valid() -> None:
    """An ancestor artifact must never become the authority for a later head."""
    recorded = "c" * 40
    evaluated = "e" * 40
    document = _review_document(authority=_authority(authority_type="opencode", head_sha=recorded))

    verdict = _verify(document, head_sha=evaluated)

    assert verdict.state == "stale"
    assert "recorded for exact head" in verdict.reason
    assert "not the evaluated exact head" in verdict.reason


def test_the_exact_head_recorded_artifact_is_still_valid() -> None:
    verdict = _verify(_review_document())

    assert verdict.state == "valid"
    assert verdict.ok is True, verdict.reason


def test_hosted_admission_rejects_an_artifact_recorded_for_an_ancestor_head(monkeypatch) -> None:
    recorded = "d" * 40
    document = _review_document(authority=_authority(authority_type="opencode", head_sha=recorded))
    _install_governance(monkeypatch, document=document, state="present")
    monkeypatch.setattr(core, "read_pr_commits", lambda *_args: (True, ({"sha": recorded}, {"sha": HEAD}), None))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "not the evaluated exact head" in description


def test_an_exact_head_finding_does_not_replace_required_structured_evidence(monkeypatch) -> None:
    comments = (
        {"login": COPILOT, "commit_id": HEAD, "body": "P1 finding with a long substantive adversarial explanation"},
    )
    _install_governance(monkeypatch, document=None, state="absent", comments=comments)

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "hostile review" in description


def test_zero_exact_head_reviews_fail_closed_without_an_artifact(monkeypatch) -> None:
    _install_governance(monkeypatch, document=None, state="absent", comments=())
    monkeypatch.setattr(core, "read_pr_pool_review_comments", lambda *_args: ((), None))

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "hostile review" in description


def test_an_exact_head_comment_does_not_rescue_a_stale_artifact(monkeypatch) -> None:
    stale = _review_document(authority=_authority(authority_type="opencode", head_sha="d" * 40))
    comments = (
        {"login": COPILOT, "commit_id": HEAD, "body": "deep substantive adversarial review body with evidence"},
    )
    _install_governance(monkeypatch, document=stale, state="present", comments=comments)

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"


def test_an_agent_comment_on_an_older_commit_is_not_exact_head_authority(monkeypatch) -> None:
    comments = (
        {"login": COPILOT, "commit_id": "9" * 40, "body": "finding against older content with full explanation"},
    )
    _install_governance(monkeypatch, document=None, state="absent", comments=comments)

    state, description = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)

    assert state == "failure"
    assert "hostile review" in description


# ---------------------------------------------------------------------------
# Trusted exhaustion evidence: the guard relies on machine evidence, not prose.
# ---------------------------------------------------------------------------


def test_a_fake_timeout_attempt_is_unproven_exhaustion(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(authority=_authority("opencode", attempts=[_attempt(timeout_seconds=300)]))

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert "timeout_seconds" in verdict.reason
    assert "900" in verdict.reason


def test_a_retryable_transient_single_attempt_is_not_exhausted(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(
        authority=_authority("opencode", attempts=[_attempt(failure_class="transient", attempt_count=1)])
    )

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert "attempt" in verdict.reason


def test_a_missing_invocation_reference_is_unproven_exhaustion(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(authority=_authority("opencode", attempts=[_attempt(invocation_reference="")]))

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert "invocation_reference" in verdict.reason


def test_a_missing_failure_class_is_unproven_exhaustion(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(authority=_authority("opencode", attempts=[_attempt(failure_class="unexpected")]))

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert "failure_class" in verdict.reason


def test_a_permanent_failure_single_attempt_is_trustworthy(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(
        authority=_authority("opencode", attempts=[_attempt(failure_class="permanent", attempt_count=1)])
    )

    verdict = _verify(document)

    assert verdict.state == "valid"
    assert verdict.ok is True, verdict.reason


def test_two_retryable_transient_attempts_with_the_exact_timeout_are_trustworthy(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    document = _review_document(authority=_authority("opencode"))

    verdict = _verify(document)

    assert verdict.state == "valid"
    assert verdict.ok is True, verdict.reason


def test_a_guard_that_skips_an_enabled_alternate_is_pool_not_exhausted(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool(agents=(ALTERNATE,)))
    document = _review_document(authority=_authority("opencode", attempts=[_attempt()]))

    verdict = _verify(document)

    assert verdict.state == "incomplete"
    assert ALTERNATE["id"] in verdict.reason


def test_exhaustion_failure_kind_distinguishes_unproven_from_unattempted(monkeypatch) -> None:
    pool = _pool()
    _use_pool(monkeypatch, pool)

    fake_timeout = _authority("opencode", attempts=[_attempt(timeout_seconds=300)])
    assert review.exhaustion_failure_kind(pool, fake_timeout, "opencode") == "EXHAUSTION_UNPROVEN"

    skipped_pool = _pool(agents=(ALTERNATE,))
    _use_pool(monkeypatch, skipped_pool)
    skipped = _authority("opencode", attempts=[_attempt()])
    assert review.exhaustion_failure_kind(skipped_pool, skipped, "opencode") == "POOL_NOT_EXHAUSTED"

    trustworthy = _authority("opencode")
    assert review.exhaustion_failure_kind(pool, trustworthy, "opencode") is None


# ---------------------------------------------------------------------------
# Merge-readiness review-authority states are explicit and fail closed.
# ---------------------------------------------------------------------------


def _comment(login: str = COPILOT, commit_id: str = HEAD, body: str = "substantive") -> dict[str, str]:
    return {"login": login, "commit_id": commit_id, "body": body}


def _state(
    *,
    comments=(),
    guard=("absent", None, None),
    threads=0,
    has_unresolved=False,
    guard_trusted=("success", "authenticated exhaustion fixture"),
    pool=None,
):
    """Exercise the production verifier with authenticated transport fixtures."""
    with pytest.MonkeyPatch.context() as patch:
        state, document, error = guard
        snapshots = list(comments)
        if document is not None and not snapshots:
            snapshots = [
                {
                    "id": 1,
                    "agent_id": document["authority"]["type"],
                    "login": "guard[bot]",
                    "state": "COMMENTED",
                    "commit_id": HEAD,
                    "body": json.dumps(document),
                }
            ]
        else:
            snapshots = [
                {**c, "id": 1, "agent_id": "codex", "state": "COMMENTED", "body": json.dumps(_review_document())}
                for c in snapshots
            ]
        _install_governance(patch, document=document, state=state, comments=tuple(snapshots))
        _use_pool(patch, pool or _pool())
        patch.setattr(core, "read_unresolved_review_threads", lambda *a: (tuple(str(n) for n in range(threads)), None))
        patch.setattr(core, "check_reviewer_dispositions", lambda: (not has_unresolved, "unresolved finding"))
        patch.setattr(
            core, "verify_trusted_exhaustion", lambda *a: (guard_trusted[0], "EXHAUSTION_UNPROVEN: " + guard_trusted[1])
        )
        return readiness.resolve_review_authority(
            core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)
        )


def test_authority_state_missing_with_no_agent_review_and_no_artifact() -> None:
    assert _state().state == "MISSING_REVIEW_AUTHORITY"


def test_authority_state_valid_agent_review_at_the_exact_head() -> None:
    verdict = _state(comments=(_comment(),))
    assert verdict.state == "VALID_AGENT_REVIEW"


def test_authority_state_blocking_findings_with_unresolved_threads() -> None:
    verdict = _state(comments=(_comment(),), threads=1)
    assert verdict.state == "BLOCKING_FINDINGS"


def test_authority_state_blocking_findings_with_validated_unresolved_disposition() -> None:
    verdict = _state(comments=(_comment(),), has_unresolved=True)
    assert verdict.state == "BLOCKING_FINDINGS"


def test_authority_state_missing_when_only_older_commit_review_exists() -> None:
    verdict = _state(comments=(_comment(commit_id="9" * 40),))
    assert verdict.state == "MISSING_REVIEW_AUTHORITY"


def test_authority_state_malformed_when_the_artifact_is_unreadable() -> None:
    verdict = _state(guard=("invalid", None, "not readable JSON"))
    assert verdict.state == "MALFORMED_REVIEW"


def test_authority_state_pool_not_exhausted_for_a_guard_that_skips_an_alternate() -> None:
    doc = _review_document(authority=_authority("opencode", attempts=[_attempt()]))
    guard = ("present", doc, None)
    verdict = _state(
        pool=_pool(agents=(ALTERNATE,)),
        guard=guard,
        guard_trusted=("success", ""),
    )
    assert verdict.state == "POOL_NOT_EXHAUSTED"


def test_authority_state_exhaustion_unproven_for_fake_timeout_evidence() -> None:
    doc = _review_document(authority=_authority("opencode", attempts=[_attempt(timeout_seconds=300)]))
    guard = ("present", doc, None)
    verdict = _state(
        pool=_pool(),
        guard=guard,
        guard_trusted=("success", ""),
    )
    assert verdict.state == "EXHAUSTION_UNPROVEN"


def test_authority_state_values_the_last_resort_guard_only_with_trusted_evidence() -> None:
    doc = _review_document(authority=_authority("opencode", head_sha=HEAD))
    guard = ("present", doc, None)
    verdict = _state(
        pool=_pool(),
        guard=guard,
        guard_trusted=("success", ""),
    )
    assert verdict.state == "VALID_LAST_RESORT_GUARD"


def test_authority_state_rejects_a_guard_whose_live_trust_reverification_fails() -> None:
    doc = _review_document(authority=_authority("opencode", head_sha=HEAD))
    guard = ("present", doc, None)
    verdict = _state(
        pool=_pool(),
        guard=guard,
        guard_trusted=("failure", "run evidence is missing"),
    )
    assert verdict.state == "EXHAUSTION_UNPROVEN"


def test_review_authority_state_surfaces_the_state_name_and_blocks(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(core, "read_pr_pool_review_comments", lambda *_args: ((), None))
    monkeypatch.setattr(readiness, "unresolved_review_threads", lambda _number: ())
    monkeypatch.setattr(readiness, "changes_requested_reviewers", lambda _number: ())
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    _install_governance(monkeypatch, document=None, state="absent")

    state, message = readiness.review_authority_state(HEAD, PR_NUMBER)

    assert state == "failure"
    assert "MISSING_REVIEW_AUTHORITY" in message


def test_a_stale_authority_state_is_surfaced_by_merge_readiness(monkeypatch) -> None:
    _use_pool(monkeypatch, _pool())
    monkeypatch.setattr(core, "read_head_pre_ready_review", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(
        core,
        "read_pr_pool_review_comments",
        lambda *_args: ((_comment(commit_id="9" * 40),), None),
    )
    monkeypatch.setattr(readiness, "unresolved_review_threads", lambda _number: ())
    monkeypatch.setattr(readiness, "changes_requested_reviewers", lambda _number: ())
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    _install_governance(monkeypatch, document=None, state="absent")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "dummy")

    state, message = readiness.review_authority_state(HEAD, PR_NUMBER)

    assert state == "failure"
    assert "MISSING_REVIEW_AUTHORITY" in message


def test_merge_ready_success_describes_the_positive_exact_head_authority() -> None:
    decision = readiness.evaluate(
        readiness.StaticReadinessObservation(
            review_authority=("success", "VALID_AGENT_REVIEW: exact-head review verified"),
            governance_status={"id": 99, "state": "success"},
            check_runs=tuple(
                {"id": i, "name": name, "status": "completed", "conclusion": "success"}
                for i, name in enumerate(readiness.REQUIRED_CHECKS, start=1)
            ),
        )
    )
    assert decision.state == "success"
    assert "exact-head review" in decision.description


def test_required_authority_states_are_all_declared() -> None:
    declared = set(readiness.REVIEW_AUTHORITY_STATES)
    assert declared == {
        "MISSING_REVIEW_AUTHORITY",
        "VALID_AGENT_REVIEW",
        "VALID_LAST_RESORT_GUARD",
        "STALE_REVIEW",
        "MALFORMED_REVIEW",
        "BLOCKING_FINDINGS",
        "POOL_NOT_EXHAUSTED",
        "EXHAUSTION_UNPROVEN",
    }


# ---------------------------------------------------------------------------
# Historical backfill manifest: the curated record set is pinned against drift.
# ---------------------------------------------------------------------------


def _family(family_id: str, *, boundary: str = "review", lifecycle: str = "regression-tested") -> dict[str, Any]:
    return {
        "id": family_id,
        "title": "fixture family",
        "invariant": "fixture invariant",
        "lifecycle": lifecycle,
        "applicability": {"changed_paths": ["scripts/"], "rationale": "fixture"},
        "prevention": {
            "mechanism": "fixture mechanism",
            "boundary": boundary,
            "guard_reference": "scripts/hunter_defect_prevention_preflight.py::validate_historical_defect_backfill",
        },
        "regression_evidence": [
            "tests/test_historical_defect_backfill.py::test_the_canonical_historical_defect_backfill_is_valid"
        ],
        "sources": ["fixture"],
    }


def _record(**overrides: object) -> dict[str, Any]:
    record: dict[str, object] = {
        "id": "HBF-999-001",
        "source_pr": 999,
        "source_reference": "reviewer inline comment 9999999999",
        "reviewer": "chatgpt-codex-connector[bot]",
        "severity": "P1",
        "classification": "confirmed",
        "original_defect": "the fixture defect",
        "canonical_family": "DFF-099",
        "dff_id": "DFF-099",
        "fix_reference": "PR #999",
        "regression_test_reference": "tests/test_historical_defect_backfill.py::test_the_canonical_historical_defect_backfill_is_valid",
        "selector_reference": "DFF-099 scope declares scripts/",
        "gate_reference": "scripts/hunter_defect_prevention_preflight.py::validate_historical_defect_backfill",
        "status": "guarded",
    }
    record.update(overrides)
    return record


def _pin(record: dict[str, object]) -> dict[str, Any]:
    return {
        "source_pr": record["source_pr"],
        "dff_id": record["dff_id"],
        "classification": record["classification"],
        "severity": record["severity"],
        "status": record["status"],
        "defect_digest": prevention.historical_defect_digest(record),
    }


def _write(tmp_path, *, records=None, manifest=None) -> object:
    records = list(records) if records is not None else [_record()]
    if manifest is None:
        manifest = {
            "version": 1,
            "record_ids": sorted(str(r["id"]) for r in records),
            "confirmed_ids": sorted(str(r["id"]) for r in records if r["classification"] == "confirmed"),
            "family_ids": sorted(
                str(r["dff_id"]) for r in records if r["classification"] == "confirmed" and str(r["dff_id"])
            ),
            "included_pull_requests": sorted(int(r["source_pr"]) for r in records),
            "pins": {str(r["id"]): _pin(r) for r in records},
        }
    manifest["expected_count"] = len(manifest["record_ids"])
    path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "purpose": "test",
                "window": {"pull_requests": [999]},
                "manifest": manifest,
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _patch(monkeypatch, tmp_path, *, records=None, manifest=None) -> None:
    if manifest == "missing":
        records = list(records) if records is not None else [_record()]
        path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
        path.write_text(
            json.dumps(
                {"version": 1, "purpose": "test", "window": {"pull_requests": [999]}, "records": records},
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        path = _write(tmp_path, records=records, manifest=manifest)
    monkeypatch.setattr(prevention, "BACKFILL_PATH", path)
    if manifest != "missing":
        pinned = json.loads(path.read_text())["manifest"]
        monkeypatch.setattr(
            prevention,
            "TRUSTED_BACKFILL_MANIFEST_DIGEST",
            hashlib.sha256(json.dumps(pinned, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        )


def _governing_registry(monkeypatch, tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"version": 1, "defects": [], "families": [_family("DFF-099")]}, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(prevention, "REGISTRY_PATH", path)


def test_the_canonical_backfill_gains_a_matching_manifest() -> None:
    assert prevention.validate_historical_defect_backfill() == []
    backfill = prevention._load_object(prevention.BACKFILL_PATH)
    manifest = backfill["manifest"]
    assert manifest["version"] == 1
    assert manifest["record_ids"] == sorted(str(r["id"]) for r in backfill["records"])
    assert manifest["confirmed_ids"] == sorted(
        str(r["id"]) for r in backfill["records"] if r["classification"] == "confirmed"
    )


def test_backfill_without_a_manifest_is_refused(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, manifest="missing")
    _governing_registry(monkeypatch, tmp_path)
    errors = prevention.validate_historical_defect_backfill()
    assert any("manifest" in error for error in errors)


def test_a_deleted_pinned_record_is_refused(monkeypatch, tmp_path) -> None:
    manifest = {
        "version": 1,
        "record_ids": ["HBF-999-001", "HBF-999-002"],
        "confirmed_ids": ["HBF-999-001"],
        "family_ids": ["DFF-099"],
        "included_pull_requests": [999],
        "pins": {str(r["id"]): _pin(r) for r in (_record(), _record(id="HBF-999-002"))},
    }
    _patch(monkeypatch, tmp_path, records=[_record(), _record(id="HBF-999-002")], manifest=manifest)
    _governing_registry(monkeypatch, tmp_path)

    # HBF-999-002 is silently deleted from records while the manifest still pins it.
    backfill = prevention._load_object(prevention.BACKFILL_PATH)
    backfill["records"] = [r for r in backfill["records"] if r["id"] != "HBF-999-002"]
    prevention.BACKFILL_PATH.write_text(json.dumps(backfill, indent=2), encoding="utf-8")

    errors = prevention.validate_historical_defect_backfill()
    assert any("HBF-999-002" in error for error in errors)


def test_an_unsigned_new_record_is_refused(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, records=[_record()])
    _governing_registry(monkeypatch, tmp_path)
    backfill = prevention._load_object(prevention.BACKFILL_PATH)
    backfill["records"].append(_record(id="HBF-999-009"))
    prevention.BACKFILL_PATH.write_text(json.dumps(backfill, indent=2), encoding="utf-8")

    errors = prevention.validate_historical_defect_backfill()
    assert any("HBF-999-009" in error for error in errors)


def test_a_pinned_record_whose_family_changed_is_refused(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, records=[_record()])
    _governing_registry(monkeypatch, tmp_path)
    backfill = prevention._load_object(prevention.BACKFILL_PATH)
    backfill["records"][0]["dff_id"] = "DFF-777"
    backfill["records"][0]["canonical_family"] = "DFF-777"
    prevention.BACKFILL_PATH.write_text(json.dumps(backfill, indent=2), encoding="utf-8")

    errors = prevention.validate_historical_defect_backfill()
    assert any("DFF-777" in error for error in errors)


def test_a_pinned_record_whose_defect_text_changed_is_refused(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, records=[_record()])
    _governing_registry(monkeypatch, tmp_path)
    backfill = prevention._load_object(prevention.BACKFILL_PATH)
    backfill["records"][0]["original_defect"] = "silently rewritten defect text"
    prevention.BACKFILL_PATH.write_text(json.dumps(backfill, indent=2), encoding="utf-8")

    errors = prevention.validate_historical_defect_backfill()
    assert any("digest" in error for error in errors)


def test_a_matching_manifests_backfill_is_valid(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, records=[_record()])
    _governing_registry(monkeypatch, tmp_path)
    assert prevention.validate_historical_defect_backfill() == []


# ---------------------------------------------------------------------------
# Machine-gate accounting: only executing machine boundaries count as gated.
# ---------------------------------------------------------------------------


def test_only_machine_boundary_families_count_as_gated(monkeypatch, tmp_path) -> None:
    families = [
        _family("DFF-091", boundary="hosted-gate", lifecycle="hosted-enforced"),
        _family("DFF-092", boundary="local-pre-push", lifecycle="locally-enforced"),
        _family("DFF-093", boundary="review", lifecycle="regression-tested"),
    ]
    records = [
        _record(id="HBF-091-001", dff_id="DFF-091", canonical_family="DFF-091"),
        _record(id="HBF-092-001", dff_id="DFF-092", canonical_family="DFF-092"),
        _record(id="HBF-093-001", dff_id="DFF-093", canonical_family="DFF-093"),
    ]
    path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "purpose": "test",
                "window": {"pull_requests": [999]},
                "manifest": {
                    "version": 1,
                    "record_ids": sorted(r["id"] for r in records),
                    "confirmed_ids": sorted(r["id"] for r in records),
                    "family_ids": ["DFF-091", "DFF-092", "DFF-093"],
                    "included_pull_requests": [999],
                    "pins": {r["id"]: _pin(r) for r in records},
                },
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"version": 1, "defects": [], "families": families}, indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(prevention, "BACKFILL_PATH", path)
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)

    monkeypatch.setattr(
        prevention,
        "MACHINE_FAMILY_BINDINGS",
        {
            (
                f["id"],
                f["prevention"]["boundary"],
                f["prevention"]["guard_reference"],
            ): "scripts/hunter_defect_prevention_preflight.py::validate_defect_prevention_lifecycle"
            for f in families[:2]
        },
    )
    coverage = prevention.historical_backfill_coverage()
    assert coverage["families_with_gate"] == 2
    assert coverage["families_without_gate"] == 1
    assert coverage["confirmed_families"] == 3


def test_a_nonempty_gate_reference_on_a_review_boundary_is_not_a_machine_gate(monkeypatch, tmp_path) -> None:
    families = [_family("DFF-094", boundary="review", lifecycle="regression-tested")]
    records = [_record(id="HBF-094-001", dff_id="DFF-094", canonical_family="DFF-094")]
    path = tmp_path / "HISTORICAL_DEFECT_BACKFILL.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "purpose": "test",
                "window": {"pull_requests": [999]},
                "manifest": {
                    "version": 1,
                    "record_ids": ["HBF-094-001"],
                    "confirmed_ids": ["HBF-094-001"],
                    "family_ids": ["DFF-094"],
                    "included_pull_requests": [999],
                    "pins": {r["id"]: _pin(r) for r in records},
                },
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"version": 1, "defects": [], "families": families}, indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(prevention, "BACKFILL_PATH", path)
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)

    coverage = prevention.historical_backfill_coverage()
    assert coverage["families_with_gate"] == 0


def test_the_canonical_backfill_reports_review_boundary_families_as_without_a_gate() -> None:
    coverage = prevention.historical_backfill_coverage()
    assert coverage["families_without_gate"] > 0
    assert coverage["families_with_gate"] < coverage["confirmed_families"]


def test_families_without_a_machine_gate_do_not_fail_the_guard() -> None:
    assert prevention.validate_historical_defect_backfill() == []


def test_zero_reviews_cannot_admit_a_candidate_authored_exact_head_artifact(monkeypatch):
    _install_governance(monkeypatch, document=_review_document())
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_zero_reviews_cannot_admit_when_no_defect_family_applies(monkeypatch):
    _install_governance(monkeypatch, document=None, state="absent")
    monkeypatch.setattr(core.pre_ready, "load_families", lambda: ((), ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_guard_with_unresolved_threads_is_never_valid():
    assert (
        _state(guard=("present", _review_document(authority=_authority("opencode")), None), threads=1).state
        == "BLOCKING_FINDINGS"
    )


def test_correct_looking_exhaustion_without_trusted_result_is_blocked(monkeypatch):
    _use_pool(monkeypatch, _pool())
    _install_governance(monkeypatch, document=_review_document(authority=_authority("opencode")))
    monkeypatch.setattr(core, "request_json", lambda *a, **kw: {})
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def _trusted_review(
    agent_id="codex", body="Completed adversarial review of this exact change with no blocking findings."
):
    return {"id": 31, "login": COPILOT, "agent_id": agent_id, "commit_id": HEAD, "body": body, "state": "COMMENTED"}


def test_positive_exact_head_review_and_complete_evidence_admit(monkeypatch):
    _install_governance(monkeypatch, document=_review_document(), comments=(_trusted_review(),))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "success"


def test_external_exact_head_structured_review_avoids_artifact_commit(monkeypatch):
    document = _review_document()
    _install_governance(
        monkeypatch, document=None, state="absent", comments=(_trusted_review(body=json.dumps(document)),)
    )
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "success"


def test_exact_head_review_cannot_admit_unresolved_threads(monkeypatch):
    _install_governance(monkeypatch, document=_review_document(), comments=(_trusted_review(),))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: (("thread",), None))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_review_fetch_uses_submitted_reviews_and_rejects_spoofed_identity(monkeypatch):
    rows = [
        dict(
            id=1,
            user={"login": COPILOT},
            commit_id=HEAD,
            state="COMMENTED",
            body="Completed adversarial review with detailed evidence for all changed paths.",
        ),
        dict(
            id=2,
            user={"login": "author"},
            commit_id=HEAD,
            state="APPROVED",
            body="Completed adversarial review with detailed evidence for all changed paths.",
        ),
    ]
    monkeypatch.setattr(core, "request_json", lambda *a: rows)
    reviews, error = core.read_pr_pool_review_comments("repo", "token", PR_NUMBER, _pool(), HEAD)
    assert error is None
    assert [r["id"] for r in reviews if r["agent_id"]] == [1]
    assert reviews[0]["agent_id"] == "codex"


def _trusted_exhaustion(monkeypatch, *, outcome="timed_out", **overrides):
    agent = {**CODEX_AGENT, "trigger_method": "configured trigger", "evidence_parser": "configured parser"}
    pool = {**_pool(), "agents": (agent,)}
    attempt = _attempt(invocation_reference=f"pulls/{PR_NUMBER}/reviews/31")
    evidence = dict(
        schema="hunter.reviewer-attempt.v1",
        head_sha=HEAD,
        agent_id="codex",
        priority=1,
        timeout_seconds=900,
        trigger_method="configured trigger",
        retryable=True,
        evidence_parser="configured parser",
        retries_per_agent=1,
        status="exhausted",
        attempt_count=2,
        failure_class="transient",
        outcome=outcome,
    )
    evidence.update(overrides)
    result = dict(id=31, commit_id=HEAD, state="COMMENTED", user={"login": COPILOT}, body=json.dumps(evidence))
    monkeypatch.setattr(core, "request_json", lambda *a: result)
    return core.verify_trusted_exhaustion(
        "repo", "token", PR_NUMBER, HEAD, pool, _authority("opencode", attempts=[attempt])
    )


def test_actual_trusted_configured_exhaustion_allows_guard_eligibility(monkeypatch):
    assert _trusted_exhaustion(monkeypatch)[0] == "success"


def test_trusted_result_cannot_substitute_a_different_timeout(monkeypatch):
    assert _trusted_exhaustion(monkeypatch, timeout_seconds=1)[0] == "failure"


def test_trusted_result_cannot_substitute_a_different_trigger(monkeypatch):
    assert _trusted_exhaustion(monkeypatch, trigger_method="unconfigured trigger")[0] == "failure"


def test_available_reviewer_is_not_exhausted(monkeypatch):
    assert _trusted_exhaustion(monkeypatch, outcome="available")[0] == "failure"


def test_rewriting_records_and_manifest_together_cannot_remove_accepted_history(monkeypatch, tmp_path):
    original = prevention._load_object(prevention.BACKFILL_PATH)
    original["records"] = [r for r in original["records"] if r["id"] != "HBF-450-001"]
    original["manifest"] = prevention.historical_manifest(original["records"])
    path = tmp_path / "backfill.json"
    path.write_text(json.dumps(original))
    monkeypatch.setattr(prevention, "BACKFILL_PATH", path)
    assert any("trusted canonical manifest" in e for e in prevention.validate_historical_defect_backfill())


def test_a_declared_machine_boundary_without_an_executing_binding_is_not_gated():
    family = _family("DFF-099", boundary="hosted-gate", lifecycle="hosted-enforced")
    assert not prevention._family_has_machine_gate(family)


def test_audited_canonical_machine_boundary_is_gated():
    registry = prevention._load_object(prevention.REGISTRY_PATH)
    family = next(f for f in registry["families"] if f["id"] == "DFF-013")
    assert prevention._family_has_machine_gate(family)


def test_pr469_zero_reviews_all_checks_green_is_not_merge_ready(monkeypatch):
    _install_governance(monkeypatch, document=_review_document())
    authority = core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)
    observation = readiness.StaticReadinessObservation(
        review_authority=authority,
        governance_status={"id": 99, "state": "success"},
        check_runs=tuple(
            {"id": n, "name": name, "status": "completed", "conclusion": "success"}
            for n, name in enumerate(readiness.REQUIRED_CHECKS, 1)
        ),
    )
    assert readiness.evaluate(observation).state == "failure"


def test_pr469_zero_review_ready_transition_is_returned_to_draft(monkeypatch):
    import hunter_candidate_admission as admission

    _install_governance(monkeypatch, document=_review_document())
    pr = {"state": "open", "draft": False, "head": {"sha": HEAD}, "base": {"ref": "main"}, "node_id": "PR469"}
    monkeypatch.setattr(core, "read_mergeability", lambda *a: pr)
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *a: ("normal", None))
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *a: (True, (), None))
    monkeypatch.setattr(core, "verify_code_write_ingress_provenance", lambda *a: ("success", "green"))
    transitions = []
    monkeypatch.setattr(admission, "convert_to_draft", lambda token, node: transitions.append(node) or True)
    assert admission.enforce_candidate_admission("repo", "token", PR_NUMBER) == 1
    assert transitions == ["PR469"]


def test_pending_admission_cannot_leave_a_pr_ready(monkeypatch):
    import hunter_candidate_admission as admission

    pr = {"state": "open", "draft": False, "head": {"sha": HEAD}, "base": {"ref": "main"}, "node_id": "PR469"}
    monkeypatch.setattr(core, "read_mergeability", lambda *a: pr)
    monkeypatch.setattr(core, "candidate_admission", lambda *a: ("pending", "review authority is unproven"))
    transitions = []
    monkeypatch.setattr(admission, "convert_to_draft", lambda token, node: transitions.append(node) or True)
    assert admission.enforce_candidate_admission("repo", "token", PR_NUMBER) == 1
    assert transitions == ["PR469"]


def test_reviewed_candidate_with_missing_security_checks_returns_to_draft(monkeypatch):
    import hunter_candidate_admission as admission

    pr = {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
        "node_id": "PR469",
        "mergeable": True,
    }
    monkeypatch.setattr(core, "read_mergeability", lambda *a: pr)
    monkeypatch.setattr(core, "candidate_admission", lambda *a: ("success", "valid exact-head review"))
    monkeypatch.setattr(core, "request_json", lambda *a: {"check_runs": []} if "check-runs" in a[-1] else [])
    transitions = []
    monkeypatch.setattr(admission, "convert_to_draft", lambda token, node: transitions.append(node) or True)
    assert admission.enforce_candidate_admission("repo", "token", PR_NUMBER) == 1
    assert transitions == ["PR469"]


def test_external_structured_review_cannot_claim_another_reviewers_identity(monkeypatch):
    document = _review_document()
    forged = {
        **_trusted_review(agent_id="alternate-agent-1", body=json.dumps(document)),
        "login": "alternate[bot]",
        "id": 32,
    }
    _install_governance(monkeypatch, document=None, state="absent", comments=(_trusted_review(), forged))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_human_changes_requested_blocks_even_with_exact_codex_review(monkeypatch):
    document = _review_document()
    _install_governance(monkeypatch, document=document)
    rows = [
        dict(id=1, user={"login": COPILOT}, commit_id=HEAD, state="COMMENTED", body=json.dumps(document)),
        dict(id=2, user={"login": "human-reviewer"}, commit_id=HEAD, state="CHANGES_REQUESTED", body=""),
    ]
    monkeypatch.setattr(core, "read_pr_pool_review_comments", _REAL_READ_REVIEWS)
    monkeypatch.setattr(core, "request_json", lambda *a: rows)
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_ready_workflow_can_read_required_check_evidence():
    import yaml

    workflow = yaml.safe_load((prevention.ROOT / ".github/workflows/hunter-candidate-admission.yml").read_text())
    assert workflow["permissions"].get("checks") == "read"
    assert workflow["permissions"].get("statuses") == "read"


def test_every_hosted_review_consumer_can_read_collector_and_guard_evidence():
    import yaml

    workflows = prevention.ROOT / ".github/workflows"
    expected = {
        "hunter-candidate-admission.yml": ("actions", "checks"),
        "hunter-governance-review.yml": ("actions", "checks"),
        "hunter-governance-reconcile.yml": ("actions", "checks"),
        "hunter-merge-readiness.yml": ("actions", "checks"),
    }
    for filename, permissions in expected.items():
        workflow = yaml.safe_load((workflows / filename).read_text())
        for permission in permissions:
            assert workflow["permissions"].get(permission) == "read"


def _request_and_ack():
    document = _review_document(authority=_authority(head_sha="c" * 40))
    document["review_request"] = {"schema": "hunter.review-request.v1", "claims_id": document["review_id"]}
    ack = {
        "schema": "hunter.review-ack.v1",
        "head_sha": HEAD,
        "claims_id": document["review_id"],
        "verdict": "clear",
        "summary": "Completed the adversarial review of all requested criteria and changed surfaces; no blocking findings remain.",
    }
    return document, ack


def test_a_current_authenticated_acknowledgement_establishes_new_exact_head_authority(monkeypatch):
    document, ack = _request_and_ack()
    snapshot = {**_trusted_review(body=json.dumps(ack)), "submitted_at": "2026-09-13T23:00:00Z"}
    _install_governance(monkeypatch, document=document, comments=(snapshot,))
    monkeypatch.setattr(core, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "success"
    assert document["authority"]["head_sha"] == "c" * 40  # Historical evidence is not rebound.


def test_a_request_without_explicit_reviewer_adoption_is_not_authority(monkeypatch):
    document, ack = _request_and_ack()
    _install_governance(monkeypatch, document=document, comments=(_trusted_review(),))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_review_request_adoption_of_another_claims_digest_is_blocked(monkeypatch):
    document, ack = _request_and_ack()
    ack["claims_id"] = "0" * 64
    _install_governance(monkeypatch, document=document, comments=(_trusted_review(body=json.dumps(ack)),))
    assert core.verify_pre_ready_hostile_review("repo", "token", HEAD, PR_NUMBER)[0] == "failure"


def test_review_acknowledgement_rejects_contradictory_extra_fields():
    _, ack = _request_and_ack()
    ack["findings"] = [{"severity": "P1", "summary": "A blocking finding remains."}]
    assert core.review_acknowledgement(json.dumps(ack)) is None


def test_a_review_request_cannot_be_verified_locally_as_a_completed_review():
    document, _ = _request_and_ack()
    document["authority"]["head_sha"] = HEAD
    assert not _verify(document).ok


def test_review_request_is_content_bound_without_committed_authority(monkeypatch):
    judgement = {
        "acceptance_criteria": [
            {"id": "AC-1", "criterion": "the gate blocks Ready", "verdict": "satisfied", "evidence": "suite"}
        ],
        "adversarial_dimensions": list(review.REQUIRED_ADVERSARIAL_DIMENSIONS),
        "defect_families": [
            {"family": "DFF-010", "outcome": "clear", "evidence": "swept"},
            {"family": "DFF-013", "outcome": "clear", "evidence": "swept"},
        ],
        "findings": [],
    }
    monkeypatch.setattr(review, "local_changes", lambda *_a, **_k: CANDIDATE_CHANGES)
    document = review.prepare_request(issue="467", base=BASE, head=HEAD, base_ref="main", judgement=judgement)

    assert "authority" not in document
    assert document["review_request"] == {
        "schema": "hunter.review-request.v1",
        "claims_id": document["review_id"],
    }
    assert document["claims"]["review_target"] == [change.document() for change in CANDIDATE_CHANGES]
