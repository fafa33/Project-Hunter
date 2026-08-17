"""Unit tests for the Hunter Governance Review merge gate.

Covers the Deterministic Governance Engine, the Authoritative Context
Resolver's integration point, the Decision Engine, and the end-to-end
orchestration (``run_review``) with a fake GitHub runner, including the
post-audit... freshness re-check. This gate is entirely deterministic and
repository-native: no LLM or other external provider is ever contacted, so
no test in this module uses network access or any fake LLM/provider
double.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from subprocess import CompletedProcess

import hunter_governance_review.github_api as github_api_module
import pytest
import yaml
from hunter_governance_review.__main__ import _write_summary, run_review
from hunter_governance_review.contracts import (
    CHECK_CONTEXT,
    ChangedFile,
    ContextManifest,
    DeterministicResult,
    Finding,
    Outcome,
    PullRequest,
    ReviewPair,
    Severity,
    outcome_to_check_state,
)
from hunter_governance_review.decision import Decision, decide
from hunter_governance_review.deterministic import ValidationContext, run_deterministic_engine
from hunter_governance_review.github_api import GhCliRunner, GitHubError, GitHubUnavailable

GOOD_BODY = """## Summary

Implements the canonical mispricing orchestration module for Issue #183.

## Scope and architecture

- [x] The governing Issue and acceptance criteria are linked.
- [x] Architecture Impact Check is recorded.
- [x] Evidence Impact is recorded.
- [x] No unapproved scope expansion is present.
- [x] Relevant ADR authority, provenance, missingness, replay, and persistence boundaries remain intact.

## Acceptance-criteria matrix

| Acceptance criterion | Status | Evidence |
|---|---|---|
| Orchestration wiring exists | PASS | src/hunter/mispricing/service.py |
| Deterministic replay preserved | PASS | tests/test_mispricing_replay.py |

- [x] No criterion is omitted or inferred from green CI.
- [x] No FAIL or BLOCKED criterion remains.

## Verification

- [x] `ruff check .`
- [x] `black --check .`
- [x] `mypy`
- [x] Full `pytest` suite

```text
ruff: passed; black: passed; mypy: passed; pytest: 214 passed.
```

## Operational validation

- [x] All required runbooks executed in a suitable environment.

Operational evidence:

```text
hunter mispricing --run produced record mispricing:issue-183:1; replayed via hunter replay --strict.
```

## Remaining limitations and risks

None.

## Implementer readiness declaration

- [x] `READY FOR REVIEW` — all acceptance criteria and required operational validations pass.
"""

DOCS_ONLY_BODY = """## Summary

Documents the observed market facts for Issue #88.

## Acceptance-criteria matrix

| Acceptance criterion | Status | Evidence |
|---|---|---|
| Documented | PASS | docs/OBSERVED_MARKET_FACTS.md |

## Implementer readiness declaration

- [x] `READY FOR REVIEW` — documentation-only change.
"""

CODE_FILE = ChangedFile("src/hunter/mispricing/service.py", "modified", 10, 2)
DOCS_FILE = ChangedFile("docs/OBSERVED_MARKET_FACTS.md", "added", 5, 0)
GATE_FILE = ChangedFile(".github/workflows/hunter-governance-review.yml", "modified", 20, 4)

DEFAULT_MAP_TEXT = """# Canonical Architecture Map

## Canonical Document Authority Hierarchy

1. `docs/PROJECT_CONSTITUTION.md`
2. `docs/PROJECT_PRINCIPLES.md`
3. `docs/CANONICAL_ARCHITECTURE_MAP.md`
4. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
7. Accepted ADRs in `docs/ADR/`
10. `docs/DEVELOPMENT_GOVERNANCE.md`
12. `docs/AI_REVIEW_PROTOCOL.md`
13. Versioned sprint specifications in `docs/SPRINTS/`
"""

DEFAULT_CANONICAL_DOCS: dict[str, str] = {
    "docs/CANONICAL_ARCHITECTURE_MAP.md": DEFAULT_MAP_TEXT,
    "docs/PROJECT_CONSTITUTION.md": "constitution text",
    "docs/PROJECT_PRINCIPLES.md": "principles text",
    "docs/HUNTER_ARCHITECTURE_MANIFEST.md": "manifest text",
    "docs/DEVELOPMENT_GOVERNANCE.md": "development governance text",
    "docs/AI_REVIEW_PROTOCOL.md": "ai review protocol text",
}


# --- Fixtures ----------------------------------------------------------------------


def _pr(**overrides: object) -> PullRequest:
    defaults: dict[str, object] = dict(
        number=7,
        title="feat: implement canonical mispricing orchestration",
        body=GOOD_BODY,
        state="open",
        draft=False,
        head_ref_name="feat/issue-183",
        head_oid="a" * 40,
        base_ref_name="main",
        base_oid="b" * 40,
        mergeable="MERGEABLE",
        changed_files=2,
        url="https://github.com/fafa33/Project-Hunter/pull/7",
    )
    defaults.update(overrides)
    return PullRequest(**defaults)  # type: ignore[arg-type]


def _args(pr: int = 7, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        pr=pr,
        repository=None,
        root=None,
        protected_branches=None,
        dry_run=dry_run,
    )


def _env(**overrides: object) -> dict[str, str]:
    env: dict[str, str] = {
        "GITHUB_REPOSITORY": "fafa33/Project-Hunter",
        "GITHUB_TOKEN": "token",
        "GITHUB_RUN_ID": "123",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    for key, value in overrides.items():
        env[key] = str(value)
    return env


def _pair(pr: PullRequest | None = None, run_id: str = "run-1") -> ReviewPair:
    pr = pr or _pr()
    return ReviewPair(
        repository="fafa33/Project-Hunter",
        pull_request_number=pr.number,
        source_branch=pr.head_ref_name,
        source_head_sha=pr.head_oid,
        target_branch=pr.base_ref_name,
        target_base_sha=pr.base_oid,
        workflow_run_id=run_id,
        review_timestamp="2026-08-05T00:00:00+00:00",
    )


class FakeGhRunner:
    """Test double for the GitHubRunner protocol."""

    def __init__(
        self,
        *,
        pr: PullRequest | None = None,
        current: PullRequest | None = None,
        pr_sequence: list[PullRequest] | None = None,
        files: list[ChangedFile] | None = None,
        statuses: list[dict[str, str]] | None = None,
        canonical_docs: dict[str, str] | None = None,
        directories: dict[str, list[str]] | None = None,
        canonical_docs_by_ref: dict[str, dict[str, str]] | None = None,
        directories_by_ref: dict[str, dict[str, list[str]]] | None = None,
        fail_pr: bool = False,
        fail_reresolve: bool = False,
        fail_files: bool = False,
        publish_fail: bool = False,
        publish_unavailable: bool = False,
        pr_unavailable: bool = False,
    ) -> None:
        self.repository = "fafa33/Project-Hunter"
        self.pr = pr or _pr()
        self.current = current
        self.pr_sequence = pr_sequence
        self.files = files if files is not None else [CODE_FILE]
        self.statuses = statuses if statuses is not None else []
        self.canonical_docs = canonical_docs if canonical_docs is not None else dict(DEFAULT_CANONICAL_DOCS)
        self.directories = directories if directories is not None else {}
        # ref-specific overrides -- e.g. content that exists at the PR's own
        # head SHA but not at base, simulating a PR that genuinely
        # introduces a new canonical record (see the bootstrap-fix tests).
        self.canonical_docs_by_ref = canonical_docs_by_ref or {}
        self.directories_by_ref = directories_by_ref or {}
        self.fail_pr = fail_pr
        self.fail_reresolve = fail_reresolve
        self.fail_files = fail_files
        self.publish_fail = publish_fail
        self.publish_unavailable = publish_unavailable
        self.pr_unavailable = pr_unavailable
        self.pr_views = 0

    def get_pull_request(self, number: int) -> PullRequest:
        self.pr_views += 1
        if self.pr_unavailable:
            raise GitHubUnavailable(
                "gh pr view",
                attempts=3,
                last=GitHubError("HTTP 503: no server currently available to process the request"),
            )
        if self.fail_pr:
            raise GitHubError("cannot resolve pull request")
        if self.pr_sequence is not None:
            index = min(self.pr_views - 1, len(self.pr_sequence) - 1)
            return self.pr_sequence[index]
        if self.fail_reresolve and self.pr_views >= 2:
            raise GitHubError("cannot re-resolve pull request")
        # The initial resolve returns the PR as first observed; subsequent
        # re-resolves may return an advanced PR (stale pair).
        if self.pr_views >= 2 and self.current is not None:
            return self.current
        return self.pr

    def get_pull_files(self, number: int) -> list[ChangedFile]:
        if self.fail_files:
            raise GitHubError("cannot list pull request files")
        return list(self.files)

    def get_file_content(self, path: str, ref: str) -> str | None:
        if ref in self.canonical_docs_by_ref and path in self.canonical_docs_by_ref[ref]:
            return self.canonical_docs_by_ref[ref][path]
        return self.canonical_docs.get(path)

    def list_directory(self, path: str, ref: str) -> list[str] | None:
        if ref in self.directories_by_ref and path in self.directories_by_ref[ref]:
            return self.directories_by_ref[ref][path]
        return self.directories.get(path)

    def post_commit_status(
        self,
        *,
        sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None:
        if self.publish_unavailable:
            raise GitHubUnavailable(
                "gh api statuses",
                attempts=3,
                last=GitHubError("HTTP 503: no server currently available to process the request"),
            )
        if self.publish_fail:
            raise GitHubError("cannot publish status")
        self.statuses.append(
            {
                "sha": sha,
                "state": state,
                "context": context,
                "description": description,
                "target_url": target_url,
            }
        )


def _deterministic(
    body: str | None = None,
    pr: PullRequest | None = None,
    missing_references: tuple[str, ...] = (),
) -> DeterministicResult:
    pr = pr or _pr(body=body if body is not None else GOOD_BODY)
    ctx = ValidationContext(pr=pr, files=[CODE_FILE], missing_references=missing_references)
    return run_deterministic_engine(ctx)


# --- Deterministic Governance Engine -------------------------------------------------


def test_outcome_mapping_only_approved_is_success() -> None:
    assert outcome_to_check_state(Outcome.APPROVED).value == "success"
    assert outcome_to_check_state(Outcome.CHANGES_REQUIRED).value == "failure"
    assert outcome_to_check_state(Outcome.REVIEW_FAILED).value == "failure"


def test_good_code_pr_has_no_blocking_findings() -> None:
    assert not _deterministic().blocking


def test_empty_body_blocks() -> None:
    assert _deterministic(body="").blocking


def test_placeholder_body_blocks() -> None:
    assert _deterministic(body=GOOD_BODY.replace("Implements", "TODO: Replace with real summary")).blocking


def test_missing_template_sections_block_code_pr() -> None:
    body = "## Summary\n\nshort\n\n## Implementer readiness declaration\n\n- [x] `READY FOR REVIEW`"
    assert _deterministic(body=body).blocking


def test_fail_criterion_blocks_merge_ready_pr() -> None:
    body = GOOD_BODY.replace("PASS | src/hunter/mispricing/service.py", "FAIL | not implemented")
    assert _deterministic(body=body).blocking


def test_fail_criterion_is_not_blocking_for_draft_pr() -> None:
    body = GOOD_BODY.replace("PASS | src/hunter/mispricing/service.py", "FAIL | not implemented")
    result = _deterministic(body=body, pr=_pr(body=body, draft=True))
    assert not any(f.validator_id == "V-040" and f.severity is Severity.BLOCKING for f in result.findings)


def test_pass_criterion_with_negative_evidence_blocks_merge_ready_pr() -> None:
    body = GOOD_BODY.replace("PASS | src/hunter/mispricing/service.py", "PASS | tests failed")
    result = _deterministic(body=body)
    assert result.blocking
    assert any(f.validator_id == "V-040" and f.severity is Severity.BLOCKING for f in result.findings)


def test_pass_criterion_with_negative_evidence_is_not_blocking_for_draft_pr() -> None:
    body = GOOD_BODY.replace("PASS | src/hunter/mispricing/service.py", "PASS | tests failed")
    result = _deterministic(body=body, pr=_pr(body=body, draft=True))
    assert not any(f.validator_id == "V-040" and f.severity is Severity.BLOCKING for f in result.findings)
    assert any(f.validator_id == "V-040" for f in result.findings)


def test_pass_evidence_guard_is_bound_to_shared_substantive_boundary(monkeypatch) -> None:
    body = GOOD_BODY.replace("PASS | src/hunter/mispricing/service.py", "PASS | tests failed")

    def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("hunter_governance_review.deterministic._require_substantive_evidence", noop)
    result = _deterministic(body=body)

    assert not any(f.validator_id == "V-040" for f in result.findings)


def test_missing_readiness_declaration_blocks() -> None:
    body = GOOD_BODY.replace("- [x] `READY FOR REVIEW`", "- [ ] `READY FOR REVIEW`")
    assert _deterministic(body=body).blocking


def test_ambiguous_readiness_declaration_blocks() -> None:
    body = GOOD_BODY + "\n- [x] `CHANGES REQUIRED` — also this one"
    assert _deterministic(body=body).blocking


def test_changes_required_declaration_blocks_non_draft() -> None:
    body = GOOD_BODY.replace("`READY FOR REVIEW`", "`CHANGES REQUIRED`")
    assert _deterministic(body=body, pr=_pr(body=body, draft=False)).blocking


def test_missing_adr_reference_blocks() -> None:
    body = GOOD_BODY + "\n\nSee ADR-9999 for context."
    result = _deterministic(body=body, missing_references=("ADR 9999",))
    assert result.blocking
    assert any(f.validator_id == "V-070" for f in result.findings)


def test_existing_adr_reference_passes() -> None:
    body = GOOD_BODY + "\n\nSee ADR-0001 for context."
    result = _deterministic(body=body, missing_references=())
    assert not any(f.validator_id == "V-070" for f in result.findings)


def test_mergeable_conflicting_blocks() -> None:
    result = _deterministic(pr=_pr(mergeable="CONFLICTING"))
    assert any(f.validator_id == "V-080" for f in result.findings)


def test_docs_only_pr_uses_minimal_sections() -> None:
    pr = _pr(body=DOCS_ONLY_BODY)
    ctx = ValidationContext(pr=pr, files=[DOCS_FILE], missing_references=())
    result = run_deterministic_engine(ctx)
    assert not result.blocking


def test_gate_self_modification_is_informational_only() -> None:
    pr = _pr()
    ctx = ValidationContext(pr=pr, files=[CODE_FILE, GATE_FILE], missing_references=())
    result = run_deterministic_engine(ctx)
    finding = next(f for f in result.findings if f.validator_id == "V-090")
    assert finding.severity is Severity.INFO


# --- Decision Engine -------------------------------------------------------------------


def test_decision_approved_when_all_pass() -> None:
    decision = decide(deterministic=DeterministicResult(), pair_fresh=True)
    assert decision.outcome is Outcome.APPROVED


def test_decision_stale_pair_is_review_failed() -> None:
    decision = decide(deterministic=DeterministicResult(), pair_fresh=False, pair_fresh_error="stale")
    assert decision.outcome is Outcome.REVIEW_FAILED
    assert decision.reason == "stale"


def test_decision_deterministic_blocking_is_changes_required() -> None:
    result = DeterministicResult(findings=[Finding("V-020", "empty body", Severity.BLOCKING, "detail")])
    decision = decide(deterministic=result, pair_fresh=True)
    assert decision.outcome is Outcome.CHANGES_REQUIRED


def test_decision_non_blocking_findings_still_approve() -> None:
    result = DeterministicResult(findings=[Finding("V-090", "gate self-modification", Severity.INFO, "detail")])
    decision = decide(deterministic=result, pair_fresh=True)
    assert decision.outcome is Outcome.APPROVED


# --- Orchestration (run_review) ---------------------------------------------------------


def test_run_review_happy_path_publishes_success() -> None:
    gh = FakeGhRunner()
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "success"
    assert gh.statuses[0]["context"] == CHECK_CONTEXT
    assert gh.statuses[0]["sha"] == "a" * 40


def test_run_review_deterministic_failure_is_changes_required() -> None:
    gh = FakeGhRunner(pr=_pr(body=""))
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Changes required" in gh.statuses[0]["description"]


def test_run_review_context_resolution_failure_is_review_failed() -> None:
    gh = FakeGhRunner(canonical_docs={})  # even the canonical map itself is unresolvable
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Review failed" in gh.statuses[0]["description"]


def test_run_review_evidence_failure_is_review_failed() -> None:
    gh = FakeGhRunner(fail_files=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_missing_adr_reference_end_to_end_is_changes_required() -> None:
    gh = FakeGhRunner(pr=_pr(body=GOOD_BODY + "\n\nSee ADR-9999 for context."))
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Changes required" in gh.statuses[0]["description"]


def _valid_new_adr_text(number: str, title: str) -> str:
    return (
        f"# ADR {number}: {title}\n\n## Status\n\nProposed.\n\n## Context\n\nc\n\n"
        "## Decision\n\nd\n\n## Consequences\n\n- c\n\n## Alternatives Considered\n\n- a\n"
    )


def test_run_review_bootstrap_pr_introducing_a_new_adr_is_approved_end_to_end() -> None:
    """Direct regression test for the post-PR-#200 bootstrap defect: a PR
    that legitimately introduces a brand-new ADR referenced from its own
    body must not deadlock on "the record can't exist yet because this is
    the PR that adds it" -- it must resolve as a proposed record and the
    review must be able to reach APPROVED."""
    pr = _pr(body=GOOD_BODY + "\n\nThis PR proposes ADR-0029.")
    adr_path = "docs/ADR/0029-something.md"
    gh = FakeGhRunner(
        pr=pr,
        directories_by_ref={pr.head_oid: {"docs/ADR": ["0029-something.md"]}},
        canonical_docs_by_ref={pr.head_oid: {adr_path: _valid_new_adr_text("0029", "Something")}},
        files=[CODE_FILE, ChangedFile(adr_path, "added", 20, 0)],
    )
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "success"


def test_run_review_bootstrap_pr_referencing_but_not_adding_the_adr_still_fails() -> None:
    """The bootstrap fix must not become a general bypass: a PR that merely
    mentions a nonexistent ADR number, without actually adding it, is
    still rejected exactly as before."""
    pr = _pr(body=GOOD_BODY + "\n\nSee ADR-0029 for context.")
    gh = FakeGhRunner(pr=pr)  # no ADR-0029 anywhere, at base or head
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Changes required" in gh.statuses[0]["description"]


def test_run_review_non_protected_base_skips_gate() -> None:
    gh = FakeGhRunner(pr=_pr(base_ref_name="staging", base_oid="d" * 40))
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses == []


def test_run_review_unresolvable_pr_returns_2() -> None:
    gh = FakeGhRunner(fail_pr=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 2


def test_run_review_publish_failure_returns_3() -> None:
    gh = FakeGhRunner(publish_fail=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 3


def test_run_review_dry_run_does_not_publish() -> None:
    gh = FakeGhRunner()
    code = run_review(args=_args(dry_run=True), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses == []


def test_run_review_writes_step_summary_with_context_manifest(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    code = run_review(args=_args(), env=_env(GITHUB_STEP_SUMMARY=str(summary)), gh=gh)
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "Hunter Governance Review" in text
    assert "APPROVED" in text
    assert "a" * 40 in text
    assert "Authoritative context coverage manifest" in text
    assert "docs/CANONICAL_ARCHITECTURE_MAP.md" in text


def test_run_review_step_summary_includes_deterministic_findings(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner(pr=_pr(body=""))
    code = run_review(args=_args(), env=_env(GITHUB_STEP_SUMMARY=str(summary)), gh=gh)
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "Deterministic findings" in text
    assert "V-020" in text


def test_run_review_stale_pair_before_publish_is_review_failed() -> None:
    original = _pr(head_oid="a" * 40)
    advanced = _pr(head_oid="c" * 40)
    gh = FakeGhRunner(pr_sequence=[original, advanced])
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "stale" in gh.statuses[0]["description"].lower()
    assert gh.statuses[0]["sha"] == "c" * 40  # published to the CURRENT head, not the stale one


def test_run_review_re_resolve_failure_before_publish_is_review_failed() -> None:
    gh = FakeGhRunner(fail_reresolve=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_uses_repository_argument_over_environment() -> None:
    gh = FakeGhRunner()
    args = _args()
    args.repository = "override/repo"
    code = run_review(args=args, env=_env(), gh=gh)
    assert code == 0
    assert gh.statuses[0]["target_url"].startswith("https://github.com/override/repo/")


# --- Job configuration -------------------------------------------------------------------


def test_hunter_governance_review_workflow_has_no_llm_provider_configuration() -> None:
    """This gate is entirely deterministic and repository-native: the
    workflow that runs it must never reference an LLM provider secret,
    key, base URL, or model variable."""
    workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    text = workflow_path.read_text(encoding="utf-8")
    assert "HUNTER_LLM" not in text
    assert "GROQ_API_KEY" not in text
    assert "OPENAI_API_KEY" not in text
    assert "actions/cache" not in text


def test_hunter_governance_review_workflow_timeout_is_small() -> None:
    """No external LLM call remains in this path, so the job timeout should
    be small -- a regression back toward a large value would indicate a
    slow, network/provider-bound step crept back in."""
    workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    timeout_minutes = workflow["jobs"]["governance-review"]["timeout-minutes"]
    assert timeout_minutes <= 30


# --- _write_summary ------------------------------------------------------------------------


def test_write_summary_no_op_when_step_summary_env_unset() -> None:
    decision = Decision(Outcome.APPROVED, "ok")
    pair = _pair()
    # No exception, and nothing written anywhere -- env lacks GITHUB_STEP_SUMMARY.
    _write_summary(
        {},
        decision=decision,
        pair=pair,
        deterministic=DeterministicResult(),
        context=None,
        published_state="success",
    )


def test_write_summary_includes_missing_references(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    decision = Decision(Outcome.CHANGES_REQUIRED, "missing evidence")
    pair = _pair()
    context = ContextManifest(entries=(), missing_references=("ADR 9999",))
    _write_summary(
        {"GITHUB_STEP_SUMMARY": str(summary)},
        decision=decision,
        pair=pair,
        deterministic=DeterministicResult(),
        context=context,
        published_state="failure",
    )
    text = summary.read_text(encoding="utf-8")
    assert "ADR 9999" in text


# --- Automated/Machine-Generated Dependency PR Validation Tests -------------------------------


def test_trusted_dependency_pr_classification() -> None:
    from hunter_governance_review.deterministic import is_trusted_dependency_pr

    pr = _pr(author_login="dependabot[bot]")
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]
    ctx = ValidationContext(pr=pr, files=files)

    assert is_trusted_dependency_pr(ctx) is True


def test_non_bot_pr_fails_classification() -> None:
    from hunter_governance_review.deterministic import is_trusted_dependency_pr

    # Author is human, trying to spoof dependency change path
    pr = _pr(author_login="human_attacker")
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]
    ctx = ValidationContext(pr=pr, files=files)

    assert is_trusted_dependency_pr(ctx) is False


def test_bot_pr_with_src_changes_fails_classification() -> None:
    from hunter_governance_review.deterministic import is_trusted_dependency_pr

    # Author is bot, but tries to change files in src/ (hostile or compromised)
    pr = _pr(author_login="dependabot[bot]")
    files = [
        ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1),
        ChangedFile("src/hunter/tokenomics/identity.py", "modified", 1, 1),
    ]
    ctx = ValidationContext(pr=pr, files=files)

    assert is_trusted_dependency_pr(ctx) is False


def test_bot_pr_with_workflow_changes_fails_classification() -> None:
    from hunter_governance_review.deterministic import is_trusted_dependency_pr

    # Author is bot, tries to modify CI workflow
    pr = _pr(author_login="dependabot[bot]")
    files = [
        ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1),
        ChangedFile(".github/workflows/ci.yml", "modified", 1, 1),
    ]
    ctx = ValidationContext(pr=pr, files=files)

    assert is_trusted_dependency_pr(ctx) is False


def test_trusted_dependency_pr_skips_template_and_readiness_checks() -> None:
    pr = _pr(
        author_login="dependabot[bot]",
        body="Bumps dependency releases...",  # Lacks required sections and template sections
    )
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]

    ctx = ValidationContext(pr=pr, files=files)
    result = run_deterministic_engine(ctx)

    # Since it is a trusted dependency PR, sections, matrix, and readiness validators are skipped.
    # It should have exactly 0 findings and be approved.
    assert len(result.findings) == 0
    assert result.blocking is False


def test_attacker_dependency_pr_fails_governance_checks() -> None:
    pr = _pr(
        author_login="attacker_user",
        body="Bumps dependency releases...",  # Lacks required sections
    )
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]

    ctx = ValidationContext(pr=pr, files=files)
    result = run_deterministic_engine(ctx)

    # Since the author is not a trusted bot, it is routed to the Manual Path and fails V-030/040/050
    assert len(result.findings) > 0
    assert result.blocking is True

    validator_ids = {f.validator_id for f in result.findings}
    assert "V-030" in validator_ids
    assert "V-040" in validator_ids
    assert "V-050" in validator_ids


# --- Systemic Structural PR Contract & Lifecycle Regression Tests ---------------------------


def test_contract_missing_summary() -> None:
    # 2. Missing Summary -> rejected.
    body = GOOD_BODY.replace("## Summary", "## Unrelated")
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-030" for f in result.findings)


def test_contract_missing_scope() -> None:
    # 3. Missing Scope/Architecture -> rejected.
    body = GOOD_BODY.replace("## Scope and architecture", "## Unrelated")
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[ChangedFile("src/hunter/core.py", "modified", 1, 1)])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-030" for f in result.findings)


def test_contract_missing_matrix() -> None:
    # 4. Missing acceptance matrix -> rejected.
    body = (
        GOOD_BODY.replace("## Acceptance-criteria matrix", "## Unrelated")
        .replace("| Orchestration wiring exists | PASS | src/hunter/mispricing/service.py |", "")
        .replace("| Deterministic replay preserved | PASS | tests/test_mispricing_replay.py |", "")
    )
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    # Both V-030 (missing section) and V-040 (missing matrix) should fail
    validator_ids = {f.validator_id for f in result.findings}
    assert "V-030" in validator_ids
    assert "V-040" in validator_ids


def test_contract_invalid_acceptance_status() -> None:
    # 5. Invalid acceptance status -> rejected.
    body = GOOD_BODY.replace("PASS", "INVALID_STATUS")
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    # V-040 (missing matrix because of invalid status row)
    assert any(f.validator_id == "V-040" for f in result.findings)


def test_contract_missing_validation() -> None:
    # 6. Missing Validation (Operational validation / Verification) -> rejected.
    body = (
        GOOD_BODY.replace("## Operational validation", "## Unrelated")
        .replace("operational validation", "unrelated")
        .replace("operational-validation", "unrelated")
    )
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[ChangedFile("src/hunter/core.py", "modified", 1, 1)])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-030" for f in result.findings)


def test_contract_missing_limitations() -> None:
    # 7. Missing limitations/risks -> rejected.
    body = GOOD_BODY.replace("## Remaining limitations and risks", "## Unrelated")
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[ChangedFile("src/hunter/core.py", "modified", 1, 1)])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-030" for f in result.findings)


def test_contract_missing_readiness_declaration() -> None:
    # 8. Missing readiness declaration -> rejected.
    body = GOOD_BODY.replace("- [x] `READY FOR REVIEW`", "- [ ] `READY FOR REVIEW`")
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-050" for f in result.findings)


def test_contract_placeholder_content() -> None:
    # 9. Placeholder content -> rejected.
    body = GOOD_BODY + "\ntodo: fix this later"
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-021" for f in result.findings)


def test_contract_ambiguous_readiness() -> None:
    # 10. Ambiguous readiness -> rejected.
    body = GOOD_BODY + "\n- [x] `CHANGES REQUIRED` — implementation remains incomplete."
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert any(f.validator_id == "V-050" for f in result.findings)


def test_correct_hunter_controlled_pr_passes() -> None:
    # 11. Correctly constructed Hunter-controlled PR -> passes structural contract.
    # 19. PR #204-shaped regression fixture succeeds after canonical construction.
    with open("docs/PR_204_BODY.md", encoding="utf-8") as f:
        pr_body = f.read()
    pr = _pr(body=pr_body, draft=False)
    files = [
        ChangedFile("scripts/hunter_governance_review/contracts.py", "modified", 5, 5),
        ChangedFile("scripts/hunter_governance_review/deterministic.py", "modified", 5, 5),
    ]
    ctx = ValidationContext(pr=pr, files=files)
    result = run_deterministic_engine(ctx)
    # Should have no blocking findings (only INFO V-090 for self-modification)
    assert not result.blocking


def test_draft_ready_for_review_lifecycle() -> None:
    # 16. Draft -> ready_for_review lifecycle behaves correctly.
    # When in Draft state, structural template errors are non-blocking INFO severity
    pr = _pr(body="completely empty body", draft=True)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert result.blocking is False  # All findings (V-020, V-030, etc.) are INFO severity on draft
    assert any(f.severity is Severity.INFO for f in result.findings)

    # When draft is turned off, they escalate to BLOCKING immediately
    pr_ready = _pr(body="completely empty body", draft=False)
    ctx_ready = ValidationContext(pr=pr_ready, files=[])
    result_ready = run_deterministic_engine(ctx_ready)
    assert result_ready.blocking is True
    assert any(f.severity is Severity.BLOCKING for f in result_ready.findings)


def test_pr_body_mutation_invalidates_governance() -> None:
    # 17. PR body mutation invalidates/re-evaluates governance appropriately.
    # Initially passes
    pr = _pr(body=GOOD_BODY)
    ctx = ValidationContext(pr=pr, files=[])
    result = run_deterministic_engine(ctx)
    assert len(result.findings) == 0

    # Body is mutated to be invalid (empty)
    pr_mutated = _pr(body="")
    ctx_mutated = ValidationContext(pr=pr_mutated, files=[])
    result_mutated = run_deterministic_engine(ctx_mutated)
    assert len(result_mutated.findings) > 0
    assert result_mutated.blocking is True


def test_pr_head_mutation_stale_auth() -> None:
    # 18. PR head mutation cannot reuse stale authorization.
    from unittest.mock import MagicMock

    from hunter_governance_review.__main__ import _resolve_pair_freshness

    pair = _pair()
    gh = MagicMock()
    # Mock PR returning a different head SHA (mutation)
    gh.get_pull_request.return_value = _pr(head_oid="different_sha")

    current, pair_fresh, error = _resolve_pair_freshness(gh, 7, pair)
    assert pair_fresh is False
    assert "stale source" in error


# --- GitHub infrastructure unavailability ------------------------------------


def test_run_review_publication_unavailable_returns_4_not_semantic() -> None:
    """Exhausted publication outage is a distinct outcome, never CHANGES_REQUIRED."""
    gh = FakeGhRunner(publish_unavailable=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 4
    assert gh.statuses == []


def test_run_review_publication_unavailable_summary_preserves_approved(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner(publish_unavailable=True)
    code = run_review(args=_args(), env=_env(GITHUB_STEP_SUMMARY=str(summary)), gh=gh)
    assert code == 4
    text = summary.read_text(encoding="utf-8")
    assert "**Outcome**: `APPROVED`" in text
    assert "-> `unavailable`" in text
    assert "Changes required" not in text


def test_run_review_pr_acquisition_unavailable_returns_4() -> None:
    gh = FakeGhRunner(pr_unavailable=True)
    code = run_review(args=_args(), env=_env(), gh=gh)
    assert code == 4


def test_gh_cli_runner_retries_transient_503_then_succeeds(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            return CompletedProcess(args, 1, stderr="HTTP 503\nNo server is currently available")
        return CompletedProcess(args, 0, stdout="{}")

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    assert runner._run(["api", "repos/x"]) == "{}"
    assert len(calls) == 3


def test_gh_cli_runner_transient_503_exhaustion_raises_unavailable(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 1, stderr="HTTP 503\nNo server is currently available")

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    with pytest.raises(GitHubUnavailable) as raised:
        runner._run(["api", "repos/x"])
    assert raised.value.attempts == 3
    assert len(calls) == 3


def test_gh_cli_runner_node_resolution_404_exhaustion_raises_unavailable(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        return CompletedProcess(
            args,
            1,
            stderr="HTTP 404\nCould not resolve to a node with the global id of 'PR_kwDOTRDHr87_tH6X'",
        )

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    with pytest.raises(GitHubUnavailable) as raised:
        runner._run(["api", "repos/x/pulls/1/reviews"])
    assert raised.value.attempts == 3


def test_gh_cli_runner_plain_404_never_retried(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 1, stderr="HTTP 404: Not Found")

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    with pytest.raises(GitHubError):
        runner._run(["api", "repos/x"])
    assert len(calls) == 1


def test_gh_cli_runner_node_resolution_404_never_reads_as_missing_file(monkeypatch) -> None:
    """A node-resolution 404 is infrastructure failure, never 'file not found'."""

    def fake_run(args, **kwargs):
        return CompletedProcess(
            args,
            1,
            stderr="HTTP 404\nCould not resolve to a node with the global id of 'PR_kwDOTRDHr87_tH6X'",
        )

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    with pytest.raises(GitHubUnavailable):
        runner.get_file_content("docs/x.md", "a" * 40)


def test_gh_cli_runner_plain_404_reads_as_missing_file(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        return CompletedProcess(args, 1, stderr="HTTP 404: Not Found")

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    assert runner.get_file_content("docs/x.md", "a" * 40) is None


def test_gh_cli_runner_retries_transient_503_for_file_content(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            return CompletedProcess(args, 1, stderr="HTTP 503\nNo server is currently available")
        return CompletedProcess(args, 0, stdout="content")

    monkeypatch.setattr(github_api_module.subprocess, "run", fake_run)
    monkeypatch.setattr(github_api_module, "_sleeper", lambda _: None)
    runner = GhCliRunner("fafa33/Project-Hunter", token="t")
    assert runner.get_file_content("docs/x.md", "a" * 40) == "content"
    assert len(calls) == 3
