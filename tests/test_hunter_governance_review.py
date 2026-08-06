"""Unit tests for the Hunter Governance Review merge gate.

Covers the Deterministic Governance Engine, the LLM Architecture Audit parsing
and fail-closed behavior, the Decision Engine mapping, and the end-to-end
orchestration (``run_review``) with fake GitHub and LLM runners. No live
network access is used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from hunter_governance_review.__main__ import run_review
from hunter_governance_review.contracts import (
    CHECK_CONTEXT,
    ChangedFile,
    DeterministicResult,
    Finding,
    Outcome,
    PullRequest,
    ReviewPair,
    Severity,
    outcome_to_check_state,
)
from hunter_governance_review.decision import decide
from hunter_governance_review.deterministic import ValidationContext, run_deterministic_engine
from hunter_governance_review.github_api import GitHubError
from hunter_governance_review.llm_audit import (
    MAX_CHANGED_FILES_LISTED,
    MAX_COMPLETION_TOKENS,
    PR_BODY_CHAR_LIMIT,
    PROMPT_CHAR_BUDGET,
    SYSTEM_PROMPT,
    AuditVerdict,
    LLMAuditError,
    build_audit_prompt,
    parse_audit_response,
    run_llm_audit,
)

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
        "HUNTER_LLM_API_KEY": "secret",
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
        files: list[ChangedFile] | None = None,
        diff: str = "",
        statuses: list[dict[str, str]] | None = None,
        fail_pr: bool = False,
        fail_reresolve: bool = False,
        fail_files: bool = False,
        fail_diff: bool = False,
        publish_fail: bool = False,
    ) -> None:
        self.repository = "fafa33/Project-Hunter"
        self.pr = pr or _pr()
        self.current = current
        self.files = files if files is not None else [CODE_FILE]
        self.diff = (
            diff
            or "diff --git a/src/hunter/mispricing/service.py b/src/hunter/mispricing/service.py\n+def orchestrate(): ...\n"
        )
        self.statuses = statuses if statuses is not None else []
        self.fail_pr = fail_pr
        self.fail_reresolve = fail_reresolve
        self.fail_files = fail_files
        self.fail_diff = fail_diff
        self.publish_fail = publish_fail
        self.pr_views = 0

    def get_pull_request(self, number: int) -> PullRequest:
        self.pr_views += 1
        if self.fail_pr:
            raise GitHubError("cannot resolve pull request")
        if self.fail_reresolve and self.pr_views >= 2:
            raise GitHubError("cannot re-resolve pull request")
        # The initial resolve returns the PR as first observed; the
        # decision-time re-resolve may return an advanced PR (stale pair).
        if self.pr_views >= 2 and self.current is not None:
            return self.current
        return self.pr

    def get_pull_files(self, number: int) -> list[ChangedFile]:
        if self.fail_files:
            raise GitHubError("cannot list pull request files")
        return list(self.files)

    def get_pull_diff(self, number: int) -> str:
        if self.fail_diff:
            raise GitHubError("cannot fetch pull request diff")
        return self.diff

    def post_commit_status(
        self,
        *,
        sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None:
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


class FakeLlmRunner:
    """Test double for the LLMRunner protocol."""

    def __init__(
        self,
        verdict: str = "APPROVED",
        error: LLMAuditError | None = None,
        summary: str = "audit summary",
        findings: list[dict[str, str]] | None = None,
        rationale: str = "ok",
    ) -> None:
        self.verdict = verdict
        self.error = error
        self.summary = summary
        self.findings = findings if findings is not None else []
        self.rationale = rationale
        self.calls: list[tuple[ReviewPair, PullRequest, list[ChangedFile], str, list[Finding]]] = []

    def __call__(
        self,
        env: dict[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        files: list[ChangedFile],
        diff: str,
        deterministic_findings: list[Finding],
        timeout: int = 120,
    ) -> AuditVerdict:
        self.calls.append((pair, pr, files, diff, deterministic_findings))
        if self.error is not None:
            raise self.error
        return AuditVerdict(
            verdict=self.verdict, summary=self.summary, findings=self.findings, rationale=self.rationale
        )


def _deterministic(body: str | None = None, pr: PullRequest | None = None) -> DeterministicResult:
    pr = pr or _pr(body=body if body is not None else GOOD_BODY)
    ctx = ValidationContext(
        pr=pr,
        files=[CODE_FILE],
        repository_root=Path("/nonexistent-repository-root"),
    )
    return run_deterministic_engine(ctx)


# --- Outcome mapping ----------------------------------------------------------------


def test_outcome_mapping_only_approved_is_success() -> None:
    assert outcome_to_check_state(Outcome.APPROVED).value == "success"
    assert outcome_to_check_state(Outcome.CHANGES_REQUIRED).value == "failure"
    assert outcome_to_check_state(Outcome.REVIEW_FAILED).value == "failure"


# --- Deterministic Governance Engine -------------------------------------------------


def test_good_code_pr_has_no_blocking_findings() -> None:
    result = _deterministic()
    assert result.blocking is False


def test_empty_body_blocks() -> None:
    result = _deterministic(body="")
    assert result.blocking is True
    assert any(f.validator_id == "V-020" for f in result.findings)


def test_placeholder_body_blocks() -> None:
    result = _deterministic(body=GOOD_BODY + "\nTODO: fill this in later.\n")
    assert result.blocking is True
    assert any(f.validator_id == "V-021" for f in result.findings)


def test_missing_template_sections_block_code_pr() -> None:
    body = GOOD_BODY.replace("## Verification", "## Verification and everything after is omitted")
    body = body[: body.index("## Verification and everything after is omitted")]
    result = _deterministic(body=body)
    assert result.blocking is True
    assert any(f.validator_id == "V-030" for f in result.findings)


def test_fail_criterion_blocks_merge_ready_pr() -> None:
    body = GOOD_BODY.replace("| Orchestration wiring exists | PASS |", "| Orchestration wiring exists | FAIL |")
    result = _deterministic(body=body)
    assert result.blocking is True
    assert any(f.validator_id == "V-040" for f in result.findings)


def test_fail_criterion_is_not_blocking_for_draft_pr() -> None:
    body = GOOD_BODY.replace("| Orchestration wiring exists | PASS |", "| Orchestration wiring exists | BLOCKED |")
    result = _deterministic(body=body, pr=_pr(draft=True))
    assert result.blocking is False
    assert any(f.validator_id == "V-100" and f.severity is Severity.INFO for f in result.findings)


def test_missing_readiness_declaration_blocks() -> None:
    body = GOOD_BODY.replace("- [x] `READY FOR REVIEW`", "- [ ] `READY FOR REVIEW`")
    result = _deterministic(body=body)
    assert result.blocking is True
    assert any(f.validator_id == "V-050" for f in result.findings)


def test_ambiguous_readiness_declaration_blocks() -> None:
    body = GOOD_BODY + "\n- [x] `CHANGES REQUIRED` — incomplete\n"
    result = _deterministic(body=body)
    assert result.blocking is True
    assert any(f.validator_id == "V-050" for f in result.findings)


def test_changes_required_declaration_blocks_non_draft() -> None:
    body = GOOD_BODY.replace("READY FOR REVIEW", "CHANGES REQUIRED")
    result = _deterministic(body=body)
    assert result.blocking is True
    assert any(f.validator_id == "V-050" for f in result.findings)


def test_missing_adr_reference_blocks(tmp_path: Path) -> None:
    body = GOOD_BODY + "\nPer ADR 9999 this is required.\n"
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[CODE_FILE], repository_root=tmp_path)
    result = run_deterministic_engine(ctx)
    assert result.blocking is True
    assert any(f.validator_id == "V-070" for f in result.findings)


def test_existing_adr_reference_passes(tmp_path: Path) -> None:
    (tmp_path / "docs" / "ADR").mkdir(parents=True)
    (tmp_path / "docs" / "ADR" / "0022-canonical-valuation-methodology.md").write_text("accepted", encoding="utf-8")
    body = GOOD_BODY + "\nPer ADR 0022 the methodology applies.\n"
    pr = _pr(body=body)
    ctx = ValidationContext(pr=pr, files=[CODE_FILE], repository_root=tmp_path)
    result = run_deterministic_engine(ctx)
    assert not any(f.validator_id == "V-070" for f in result.findings)


def test_mergeable_conflicting_blocks() -> None:
    result = _deterministic(pr=_pr(mergeable="CONFLICTING"))
    assert result.blocking is True
    assert any(f.validator_id == "V-080" for f in result.findings)


def test_docs_only_pr_uses_minimal_sections() -> None:
    pr = _pr(body=DOCS_ONLY_BODY)
    ctx = ValidationContext(pr=pr, files=[DOCS_FILE], repository_root=Path("/nonexistent-repository-root"))
    result = run_deterministic_engine(ctx)
    assert result.blocking is False


def test_gate_self_modification_is_informational_only() -> None:
    pr = _pr()
    ctx = ValidationContext(pr=pr, files=[CODE_FILE, GATE_FILE], repository_root=Path("/nonexistent-repository-root"))
    result = run_deterministic_engine(ctx)
    assert result.blocking is False
    assert any(f.validator_id == "V-090" and f.severity is Severity.INFO for f in result.findings)


# --- LLM Architecture Audit ---------------------------------------------------------


def test_parse_fenced_json_approved() -> None:
    raw = '```json\n{"verdict": "APPROVED", "summary": "clean", "findings": [], "rationale": "No blocking findings were identified."}\n```'
    verdict = parse_audit_response(raw)
    assert verdict.verdict == "APPROVED"
    assert verdict.summary == "clean"


def test_parse_plain_json_changes_required() -> None:
    raw = '{"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [{"id": "F-001", "severity": "blocking", "location": "x", "description": "d", "decision_impact": "i"}], "rationale": "r"}'
    verdict = parse_audit_response(raw)
    assert verdict.verdict == "CHANGES_REQUIRED"
    assert verdict.findings[0]["id"] == "F-001"


def test_parse_rejects_unsupported_verdict() -> None:
    with pytest.raises(LLMAuditError, match="unsupported response schema"):
        parse_audit_response('{"verdict": "PENDING"}')


def test_parse_rejects_non_json() -> None:
    with pytest.raises(LLMAuditError, match="malformed model output"):
        parse_audit_response("I approve this change, no JSON here")


def test_missing_api_secret_fails_closed() -> None:
    with pytest.raises(LLMAuditError, match="missing API secret"):
        run_llm_audit(
            {"GITHUB_REPOSITORY": "fafa33/Project-Hunter"},
            pair=_pair(),
            pr=_pr(),
            files=[CODE_FILE],
            diff="@@ -1 +1 @@",
            deterministic_findings=[],
        )


def test_audit_prompt_contains_exact_pair_and_diff() -> None:
    prompt = build_audit_prompt(
        pair=_pair(),
        pr=_pr(),
        files=[CODE_FILE],
        diff="@@ -1 +1 @@\n+def orchestrate(): ...",
        deterministic_findings=[],
    )
    assert "a" * 40 in prompt  # source head SHA
    assert "b" * 40 in prompt  # target base SHA
    assert "GOVERNANCE BRIEF" in prompt
    assert "orchestrate" in prompt


def test_audit_prompt_bounds_huge_diff_to_token_budget() -> None:
    huge_diff = "+line of diff content\n" * 100_000
    prompt = build_audit_prompt(
        pair=_pair(),
        pr=_pr(),
        files=[CODE_FILE],
        diff=huge_diff,
        deterministic_findings=[],
    )
    assert len(SYSTEM_PROMPT) + len(prompt) <= PROMPT_CHAR_BUDGET
    assert "DIFF TRUNCATED" in prompt
    assert huge_diff not in prompt


def test_audit_prompt_bounds_pr_body_to_limit() -> None:
    huge_body = "x" * (PR_BODY_CHAR_LIMIT * 5)
    prompt = build_audit_prompt(
        pair=_pair(),
        pr=_pr(body=huge_body),
        files=[CODE_FILE],
        diff="@@ -1 +1 @@",
        deterministic_findings=[],
    )
    assert "PR BODY TRUNCATED" in prompt
    assert huge_body not in prompt


def test_audit_prompt_bounds_changed_files_list() -> None:
    many_files = [ChangedFile(f"src/module_{i}.py", "modified", 1, 0) for i in range(MAX_CHANGED_FILES_LISTED + 20)]
    prompt = build_audit_prompt(
        pair=_pair(),
        pr=_pr(),
        files=many_files,
        diff="@@ -1 +1 @@",
        deterministic_findings=[],
    )
    assert "omitted to satisfy the audit prompt token budget" in prompt
    assert "src/module_0.py" in prompt
    assert f"src/module_{MAX_CHANGED_FILES_LISTED + 19}.py" not in prompt


def test_run_llm_audit_caps_completion_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeHTTPResponse:
        def __enter__(self) -> _FakeHTTPResponse:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def read(self) -> bytes:
            body = {"choices": [{"message": {"content": '{"verdict": "APPROVED", "summary": "ok"}'}}]}
            return json.dumps(body).encode("utf-8")

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        return _FakeHTTPResponse()

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    verdict = run_llm_audit(
        _env(),
        pair=_pair(),
        pr=_pr(),
        files=[CODE_FILE],
        diff="@@ -1 +1 @@",
        deterministic_findings=[],
    )

    assert verdict.verdict == "APPROVED"
    assert captured["payload"]["max_tokens"] == MAX_COMPLETION_TOKENS  # type: ignore[index]


# --- Decision Engine ----------------------------------------------------------------


def test_decision_approved_when_all_pass() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="APPROVED", summary=""),
        audit_error=None,
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.APPROVED


def test_decision_stale_pair_is_review_failed() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="APPROVED", summary=""),
        audit_error=None,
        pair_fresh=False,
    )
    assert decision.outcome is Outcome.REVIEW_FAILED


def test_decision_deterministic_blocking_wins_over_llm() -> None:
    blocking = DeterministicResult(findings=[Finding("V-040", "x", Severity.BLOCKING, "y")])
    decision = decide(
        deterministic=blocking,
        audit=AuditVerdict(verdict="APPROVED", summary=""),
        audit_error=None,
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.CHANGES_REQUIRED


def test_decision_llm_changes_required() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="CHANGES_REQUIRED", summary=""),
        audit_error=None,
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.CHANGES_REQUIRED
    assert decision.reason == "the hostile architecture audit returned CHANGES_REQUIRED with blocking findings."


def test_decision_llm_changes_required_includes_audit_summary() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="CHANGES_REQUIRED", summary="the migration drops evidence provenance"),
        audit_error=None,
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.CHANGES_REQUIRED
    assert "the migration drops evidence provenance" in decision.reason


def test_decision_audit_error_is_review_failed() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=None,
        audit_error="LLM API returned HTTP 500",
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.REVIEW_FAILED


# --- Orchestration (run_review) -------------------------------------------------------


def test_run_review_happy_path_publishes_success() -> None:
    gh = FakeGhRunner()
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert len(llm.calls) == 1
    assert len(gh.statuses) == 1
    status = gh.statuses[0]
    assert status["context"] == CHECK_CONTEXT
    assert status["state"] == "success"
    assert status["sha"] == "a" * 40
    assert gh.pr_views == 2  # initial resolve + decision-time re-resolve


def test_run_review_deterministic_failure_skips_llm() -> None:
    gh = FakeGhRunner(pr=_pr(body=""))
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert llm.calls == []
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_missing_secret_is_review_failed() -> None:
    gh = FakeGhRunner()
    llm = FakeLlmRunner(error=LLMAuditError("missing API secret: HUNTER_LLM_API_KEY"))
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Review failed" in gh.statuses[0]["description"]


def test_run_review_stale_pair_is_review_failed_and_published_to_current_head() -> None:
    advanced = _pr(head_oid="c" * 40)
    gh = FakeGhRunner(current=advanced)
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert llm.calls == []  # LLM is not consulted for a pair that is already stale
    assert gh.statuses[0]["state"] == "failure"
    assert gh.statuses[0]["sha"] == "c" * 40
    assert "Review failed" in gh.statuses[0]["description"]


def test_run_review_evidence_failure_is_review_failed() -> None:
    gh = FakeGhRunner(fail_files=True)
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert llm.calls == []
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_non_protected_base_skips_gate() -> None:
    gh = FakeGhRunner(pr=_pr(base_ref_name="staging", base_oid="d" * 40))
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=llm)
    assert code == 0
    assert gh.statuses == []
    assert llm.calls == []


def test_run_review_unresolvable_pr_returns_2() -> None:
    gh = FakeGhRunner(fail_pr=True)
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=FakeLlmRunner())
    assert code == 2


def test_run_review_publish_failure_returns_3() -> None:
    gh = FakeGhRunner(publish_fail=True)
    code = run_review(args=_args(), env=_env(), gh=gh, llm_runner=FakeLlmRunner(verdict="APPROVED"))
    assert code == 3


def test_run_review_dry_run_does_not_publish() -> None:
    gh = FakeGhRunner()
    code = run_review(args=_args(dry_run=True), env=_env(), gh=gh, llm_runner=FakeLlmRunner(verdict="APPROVED"))
    assert code == 0
    assert gh.statuses == []


def test_run_review_writes_step_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
    )
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "Hunter Governance Review" in text
    assert "APPROVED" in text
    assert "a" * 40 in text


def test_run_review_step_summary_includes_audit_findings(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeLlmRunner(
        verdict="CHANGES_REQUIRED",
        summary="the migration drops evidence provenance",
        findings=[
            {
                "id": "F-001",
                "severity": "blocking",
                "location": "src/hunter/mispricing/service.py",
                "description": "provenance field is dropped during migration",
                "decision_impact": "silently loses evidence lineage",
            }
        ],
        rationale="the change removes a required provenance field without a migration path",
    )
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
    )
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "CHANGES_REQUIRED" in text
    assert "the migration drops evidence provenance" in text
    assert "F-001" in text
    assert "provenance field is dropped during migration" in text
    assert "silently loses evidence lineage" in text
    assert "the change removes a required provenance field without a migration path" in text
