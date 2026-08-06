"""Unit tests for the Hunter Governance Review merge gate.

Covers the Authoritative Context Resolver's integration point, the
Deterministic Governance Engine, the LLM Architecture Audit's chunk-prompt
building and strict schema validation, the Decision Engine mapping, and the
end-to-end orchestration (``run_review``) with fake GitHub and LLM runners,
including multi-chunk aggregation, incomplete coverage, and the post-audit
freshness re-check. No live network access is used.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
from pathlib import Path

import pytest
from hunter_governance_review.__main__ import run_review
from hunter_governance_review.chunking import DiffChunk
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
    DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    MAX_COMPLETION_TOKENS_CAP,
    MAX_RATE_LIMIT_RETRIES,
    MIN_COMPLETION_TOKENS,
    MIN_DIFF_CHAR_BUDGET,
    PR_BODY_CHAR_LIMIT,
    AuditVerdict,
    LLMAuditError,
    _parse_retry_after_seconds,
    build_chunk_audit_prompt,
    estimate_chunk_diff_budget,
    parse_audit_response,
    run_llm_audit,
    validate_audit_payload,
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


def _multi_chunk_diff() -> str:
    return (
        "diff --git a/src/hunter/mispricing/service.py b/src/hunter/mispricing/service.py\n"
        "@@ -1 +1 @@\n-old\n+def orchestrate(): ...\n"
        "diff --git a/src/b.py b/src/b.py\n@@ -1 +1 @@\n-old2\n+new2\n"
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
        diff: str = "",
        statuses: list[dict[str, str]] | None = None,
        canonical_docs: dict[str, str] | None = None,
        directories: dict[str, list[str]] | None = None,
        fail_pr: bool = False,
        fail_reresolve: bool = False,
        fail_files: bool = False,
        fail_diff: bool = False,
        publish_fail: bool = False,
    ) -> None:
        self.repository = "fafa33/Project-Hunter"
        self.pr = pr or _pr()
        self.current = current
        self.pr_sequence = pr_sequence
        self.files = files if files is not None else [CODE_FILE]
        self.diff = (
            diff
            or "diff --git a/src/hunter/mispricing/service.py b/src/hunter/mispricing/service.py\n+def orchestrate(): ...\n"
        )
        self.statuses = statuses if statuses is not None else []
        self.canonical_docs = canonical_docs if canonical_docs is not None else dict(DEFAULT_CANONICAL_DOCS)
        self.directories = directories if directories is not None else {}
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

    def get_pull_diff(self, number: int) -> str:
        if self.fail_diff:
            raise GitHubError("cannot fetch pull request diff")
        return self.diff

    def get_file_content(self, path: str, ref: str) -> str | None:
        return self.canonical_docs.get(path)

    def list_directory(self, path: str, ref: str) -> list[str] | None:
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


class FakeChunkLlmRunner:
    """Test double for the chunk-based LLMRunner protocol.

    ``architectural_evidence_by_chunk`` lets a test give distinct chunks
    distinct structured evidence (e.g. chunk 1 declares an ownership change
    that chunk 2's authority_changes conflicts with) while every chunk's
    free-text ``summary`` stays identically bland -- this is what proves a
    contradiction is caught via the structured evidence, not the prose.
    """

    def __init__(
        self,
        verdict: str = "APPROVED",
        error: LLMAuditError | None = None,
        findings: list[dict[str, str]] | None = None,
        architectural_evidence_by_chunk: dict[int, dict[str, list[str]]] | None = None,
    ) -> None:
        self.verdict = verdict
        self.error = error
        self.findings = findings if findings is not None else []
        self.architectural_evidence_by_chunk = architectural_evidence_by_chunk or {}
        self.calls: list[DiffChunk] = []

    def __call__(
        self,
        env: dict[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        chunk: DiffChunk,
        context_brief: str,
        deterministic_findings: list[Finding],
        timeout: int = 120,
    ) -> AuditVerdict:
        self.calls.append(chunk)
        if self.error is not None:
            raise self.error
        return AuditVerdict(
            verdict=self.verdict,
            summary=f"chunk {chunk.index} summary",
            findings=self.findings,
            rationale="ok",
            architectural_evidence=self.architectural_evidence_by_chunk.get(chunk.index, {}),
        )


class FakeSynthesisRunner:
    """Test double for the SynthesisRunner protocol."""

    def __init__(
        self,
        verdict: str = "APPROVED",
        error: LLMAuditError | None = None,
        findings: list[dict[str, str]] | None = None,
    ) -> None:
        self.verdict = verdict
        self.error = error
        self.findings = findings if findings is not None else []
        self.calls: list[str] = []

    def __call__(
        self,
        env: dict[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        synthesis_input: str,
        timeout: int = 120,
    ) -> AuditVerdict:
        self.calls.append(synthesis_input)
        if self.error is not None:
            raise self.error
        return AuditVerdict(
            verdict=self.verdict,
            summary="synthesis summary",
            findings=self.findings,
            rationale="synthesis ok",
        )


class FakeDocumentReviewRunner:
    """Test double for the DocumentReviewRunner protocol.

    ``fail_on_call`` fails only the Nth section review (1-indexed across
    every mandatory document's sections, in review order) while every other
    call succeeds -- this exercises the fail-closed aggregation path with a
    genuinely mixed (some succeed, one fails) outcome, rather than either
    "all succeed" or "all fail."
    """

    def __init__(
        self,
        verdict: str = "APPROVED",
        error: LLMAuditError | None = None,
        findings: list[dict[str, str]] | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self.verdict = verdict
        self.error = error
        self.findings = findings if findings is not None else []
        self.fail_on_call = fail_on_call
        self.calls: list[DiffChunk] = []
        self.call_count = 0

    def __call__(
        self,
        env: dict[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        document_chunk: DiffChunk,
        architectural_evidence_summary: str,
        timeout: int = 120,
    ) -> AuditVerdict:
        self.call_count += 1
        self.calls.append(document_chunk)
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise LLMAuditError(f"simulated document section failure on call {self.call_count}")
        if self.error is not None:
            raise self.error
        document_path = document_chunk.files[0] if document_chunk.files else "?"
        return AuditVerdict(
            verdict=self.verdict,
            summary=f"document {document_path} section {document_chunk.index} summary",
            findings=self.findings,
            rationale="document review ok",
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


# --- LLM Architecture Audit: strict schema validation --------------------------------


def test_parse_fenced_json_approved() -> None:
    raw = (
        '```json\n{"verdict": "APPROVED", "summary": "ok", "findings": [], "rationale": "r", '
        '"architectural_evidence": {}}\n```'
    )
    verdict = parse_audit_response(raw)
    assert verdict.verdict == "APPROVED"


def test_parse_plain_json_changes_required() -> None:
    raw = (
        '{"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": '
        '[{"id": "F-001", "severity": "blocking", "location": "x", "description": "d", "decision_impact": "i"}], '
        '"rationale": "r", "architectural_evidence": {}}'
    )
    verdict = parse_audit_response(raw)
    assert verdict.verdict == "CHANGES_REQUIRED"


def test_parse_rejects_unsupported_verdict() -> None:
    raw = '{"verdict": "MAYBE", "summary": "s", "findings": [], "rationale": ""}'
    with pytest.raises(LLMAuditError):
        parse_audit_response(raw)


def test_parse_rejects_non_json() -> None:
    with pytest.raises(LLMAuditError):
        parse_audit_response("not json at all")


def test_validate_audit_payload_accepts_full_valid_payload() -> None:
    payload = {
        "verdict": "CHANGES_REQUIRED",
        "summary": "found an issue",
        "findings": [
            {"id": "F-001", "severity": "blocking", "location": "a.py", "description": "d", "decision_impact": "i"}
        ],
        "rationale": "r",
        "architectural_evidence": {"entities_introduced": ["Foo"]},
    }
    verdict = validate_audit_payload(payload)
    assert verdict.verdict == "CHANGES_REQUIRED"
    assert verdict.findings[0]["id"] == "F-001"
    assert verdict.architectural_evidence["entities_introduced"] == ["Foo"]
    # Every other category defaults to an empty list when omitted.
    assert verdict.architectural_evidence["ownership_declarations"] == []


def test_validate_audit_payload_rejects_non_dict() -> None:
    with pytest.raises(LLMAuditError, match="not a JSON object"):
        validate_audit_payload(["not", "a", "dict"])


def test_validate_audit_payload_rejects_unknown_verdict() -> None:
    with pytest.raises(LLMAuditError, match="verdict must be one of"):
        validate_audit_payload({"verdict": "MAYBE", "summary": "s", "findings": [], "rationale": ""})


def test_validate_audit_payload_rejects_empty_summary() -> None:
    with pytest.raises(LLMAuditError, match="summary must be a non-empty string"):
        validate_audit_payload({"verdict": "APPROVED", "summary": "", "findings": [], "rationale": ""})


def test_validate_audit_payload_rejects_findings_not_a_list() -> None:
    with pytest.raises(LLMAuditError, match="findings must be a list"):
        validate_audit_payload({"verdict": "APPROVED", "summary": "s", "findings": "nope", "rationale": ""})


def test_validate_audit_payload_rejects_finding_missing_field() -> None:
    bad_finding = {"id": "F-001", "severity": "blocking", "location": "a.py"}
    with pytest.raises(LLMAuditError, match="missing required field"):
        validate_audit_payload(
            {"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [bad_finding], "rationale": ""}
        )


def test_validate_audit_payload_rejects_finding_with_wrong_type() -> None:
    bad_finding = {
        "id": "F-001",
        "severity": "blocking",
        "location": "a.py",
        "description": "d",
        "decision_impact": 123,
    }
    with pytest.raises(LLMAuditError, match="must be a non-empty string"):
        validate_audit_payload(
            {"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [bad_finding], "rationale": ""}
        )


def test_validate_audit_payload_rejects_unknown_severity() -> None:
    bad_finding = {
        "id": "F-001",
        "severity": "urgent",
        "location": "a.py",
        "description": "d",
        "decision_impact": "i",
    }
    with pytest.raises(LLMAuditError, match="severity must be one of"):
        validate_audit_payload(
            {"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [bad_finding], "rationale": ""}
        )


def test_validate_audit_payload_rejects_duplicate_finding_ids() -> None:
    f1 = {"id": "F-001", "severity": "blocking", "location": "a.py", "description": "d1", "decision_impact": "i1"}
    f2 = {
        "id": "F-001",
        "severity": "non-blocking",
        "location": "b.py",
        "description": "d2",
        "decision_impact": "i2",
    }
    with pytest.raises(LLMAuditError, match="duplicate finding id"):
        validate_audit_payload({"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [f1, f2], "rationale": ""})


def test_validate_audit_payload_rejects_approved_with_blocking_finding() -> None:
    blocking = {"id": "F-001", "severity": "blocking", "location": "a.py", "description": "d", "decision_impact": "i"}
    with pytest.raises(LLMAuditError, match="contradictory"):
        validate_audit_payload(
            {
                "verdict": "APPROVED",
                "summary": "s",
                "findings": [blocking],
                "rationale": "",
                "architectural_evidence": {},
            }
        )


def test_validate_audit_payload_rejects_changes_required_with_no_findings() -> None:
    with pytest.raises(LLMAuditError, match="contradictory"):
        validate_audit_payload(
            {
                "verdict": "CHANGES_REQUIRED",
                "summary": "s",
                "findings": [],
                "rationale": "",
                "architectural_evidence": {},
            }
        )


def test_validate_audit_payload_accepts_approved_with_non_blocking_finding() -> None:
    non_blocking = {
        "id": "F-001",
        "severity": "non-blocking",
        "location": "a.py",
        "description": "d",
        "decision_impact": "i",
    }
    verdict = validate_audit_payload(
        {
            "verdict": "APPROVED",
            "summary": "s",
            "findings": [non_blocking],
            "rationale": "",
            "architectural_evidence": {},
        }
    )
    assert verdict.verdict == "APPROVED"


def test_validate_audit_payload_rejects_non_string_rationale() -> None:
    with pytest.raises(LLMAuditError, match="rationale must be a string"):
        validate_audit_payload({"verdict": "APPROVED", "summary": "s", "findings": [], "rationale": 123})


def test_validate_audit_payload_rejects_unknown_top_level_field() -> None:
    with pytest.raises(LLMAuditError, match="unknown top-level field"):
        validate_audit_payload(
            {"verdict": "APPROVED", "summary": "s", "findings": [], "rationale": "", "confidence": 0.9}
        )


def test_validate_audit_payload_rejects_unknown_finding_field() -> None:
    finding_with_extra = {
        "id": "F-001",
        "severity": "blocking",
        "location": "a.py",
        "description": "d",
        "decision_impact": "i",
        "confidence": 0.9,
    }
    with pytest.raises(LLMAuditError, match="unknown field"):
        validate_audit_payload(
            {"verdict": "CHANGES_REQUIRED", "summary": "s", "findings": [finding_with_extra], "rationale": ""}
        )


def test_validate_audit_payload_rejects_changes_required_with_only_non_blocking_findings() -> None:
    non_blocking = {
        "id": "F-001",
        "severity": "non-blocking",
        "location": "a.py",
        "description": "d",
        "decision_impact": "i",
    }
    with pytest.raises(LLMAuditError, match="contradictory"):
        validate_audit_payload(
            {
                "verdict": "CHANGES_REQUIRED",
                "summary": "s",
                "findings": [non_blocking],
                "rationale": "",
                "architectural_evidence": {},
            }
        )


def test_validate_audit_payload_rejects_missing_architectural_evidence() -> None:
    """F-001 regression: architectural_evidence is mandatory, not optional --
    a chunk review that omits it must fail closed rather than silently
    proceeding with no structured evidence for synthesis to reason over."""
    with pytest.raises(LLMAuditError, match="architectural_evidence is required"):
        validate_audit_payload({"verdict": "APPROVED", "summary": "s", "findings": [], "rationale": ""})


def test_validate_audit_payload_rejects_unknown_architectural_evidence_category() -> None:
    with pytest.raises(LLMAuditError, match="unknown category"):
        validate_audit_payload(
            {
                "verdict": "APPROVED",
                "summary": "s",
                "findings": [],
                "rationale": "",
                "architectural_evidence": {"made_up_category": ["x"]},
            }
        )


def test_validate_audit_payload_rejects_architectural_evidence_category_not_a_list() -> None:
    with pytest.raises(LLMAuditError, match="entities_introduced must be a list"):
        validate_audit_payload(
            {
                "verdict": "APPROVED",
                "summary": "s",
                "findings": [],
                "rationale": "",
                "architectural_evidence": {"entities_introduced": "Foo"},
            }
        )


def test_validate_audit_payload_rejects_architectural_evidence_non_string_item() -> None:
    with pytest.raises(LLMAuditError, match="must be a non-empty string"):
        validate_audit_payload(
            {
                "verdict": "APPROVED",
                "summary": "s",
                "findings": [],
                "rationale": "",
                "architectural_evidence": {"entities_introduced": [123]},
            }
        )


def test_validate_audit_payload_rejects_architectural_evidence_empty_string_item() -> None:
    with pytest.raises(LLMAuditError, match="must be a non-empty string"):
        validate_audit_payload(
            {
                "verdict": "APPROVED",
                "summary": "s",
                "findings": [],
                "rationale": "",
                "architectural_evidence": {"entities_introduced": ["  "]},
            }
        )


def test_validate_audit_payload_rejects_architectural_evidence_not_an_object() -> None:
    with pytest.raises(LLMAuditError, match="architectural_evidence must be an object"):
        validate_audit_payload(
            {"verdict": "APPROVED", "summary": "s", "findings": [], "rationale": "", "architectural_evidence": []}
        )


def test_parse_audit_response_rejects_embedded_json_surrounded_by_prose() -> None:
    """F-004 regression: embedded JSON must be rejected outright, not
    extracted from surrounding prose (a previous, more permissive fallback
    would have accepted this)."""
    raw = (
        "Sure, here is my analysis of the change. "
        '{"verdict": "APPROVED", "summary": "ok", "findings": [], "rationale": "r"}'
        " Let me know if you have any questions!"
    )
    with pytest.raises(LLMAuditError, match="not a single clean JSON object"):
        parse_audit_response(raw)


# --- LLM Architecture Audit: chunk prompt building ------------------------------------


def test_chunk_audit_prompt_contains_exact_pair_and_diff() -> None:
    chunk = DiffChunk(index=1, total=2, files=("src/hunter/mispricing/service.py",), text="@@ -1 +1 @@\n+orchestrate()")
    prompt = build_chunk_audit_prompt(
        pair=_pair(), pr=_pr(), chunk=chunk, context_brief="GOVERNANCE BRIEF TEXT", deterministic_findings=[]
    )
    assert "a" * 40 in prompt
    assert "b" * 40 in prompt
    assert "orchestrate" in prompt
    assert "chunk 1 of 2" in prompt
    assert "GOVERNANCE BRIEF TEXT" in prompt


def test_chunk_audit_prompt_bounds_pr_body_to_limit() -> None:
    huge_body = "x" * (PR_BODY_CHAR_LIMIT * 5)
    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    prompt = build_chunk_audit_prompt(
        pair=_pair(), pr=_pr(body=huge_body), chunk=chunk, context_brief="", deterministic_findings=[]
    )
    assert "PR BODY TRUNCATED" in prompt
    assert huge_body not in prompt


def test_chunk_audit_prompt_raises_when_chunk_too_large_for_budget() -> None:
    huge_chunk = DiffChunk(1, 1, ("a.py",), "x" * 1_000_000)
    with pytest.raises(LLMAuditError, match="does not fit"):
        build_chunk_audit_prompt(pair=_pair(), pr=_pr(), chunk=huge_chunk, context_brief="", deterministic_findings=[])


def test_estimate_chunk_diff_budget_leaves_room_for_a_real_chunk() -> None:
    budget = estimate_chunk_diff_budget(pr=_pr(), context_brief="short context", deterministic_findings=[])
    assert budget > MIN_DIFF_CHAR_BUDGET
    chunk = DiffChunk(1, 1, ("a.py", "b.py", "c.py"), "x" * budget)
    prompt = build_chunk_audit_prompt(
        pair=_pair(), pr=_pr(), chunk=chunk, context_brief="short context", deterministic_findings=[]
    )
    assert len(prompt) > 0  # did not raise for a chunk sized at the estimated budget


# --- LLM Architecture Audit: HTTP layer -----------------------------------------------


def test_missing_api_secret_fails_closed() -> None:
    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="missing API secret"):
        run_llm_audit(
            {"GITHUB_REPOSITORY": "fafa33/Project-Hunter"},
            pair=_pair(),
            pr=_pr(),
            chunk=chunk,
            context_brief="",
            deterministic_findings=[],
        )


class _FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_run_llm_audit_sets_adaptive_max_tokens_and_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        captured["payload"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"verdict": "APPROVED", "summary": "ok", "findings": [], "rationale": "r", '
                            '"architectural_evidence": {}}'
                        )
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@\n+ok")
    verdict = run_llm_audit(_env(), pair=_pair(), pr=_pr(), chunk=chunk, context_brief="ctx", deterministic_findings=[])

    assert verdict.verdict == "APPROVED"
    payload = captured["payload"]
    assert payload["response_format"] == {"type": "json_object"}  # type: ignore[index]
    assert MIN_COMPLETION_TOKENS <= payload["max_tokens"] <= MAX_COMPLETION_TOKENS_CAP  # type: ignore[index]


def test_run_llm_audit_fails_closed_on_truncated_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        body = {"choices": [{"message": {"content": '{"verdict": "APPROVED"'}, "finish_reason": "length"}]}
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="truncated"):
        run_llm_audit(_env(), pair=_pair(), pr=_pr(), chunk=chunk, context_brief="ctx", deterministic_findings=[])


def test_run_llm_audit_rejects_unsupported_finish_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-004 regression: only finish_reason == 'stop' is accepted; any other
    value (missing, 'content_filter', 'tool_calls', etc.) fails closed."""

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        body = {
            "choices": [
                {
                    "message": {
                        "content": '{"verdict": "APPROVED", "summary": "ok", "findings": [], "rationale": "r"}'
                    },
                    "finish_reason": "content_filter",
                }
            ]
        }
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="unsupported finish_reason"):
        run_llm_audit(_env(), pair=_pair(), pr=_pr(), chunk=chunk, context_brief="ctx", deterministic_findings=[])


def test_run_llm_audit_large_legitimate_findings_set_does_not_truncate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the independent reviewer's max_tokens concern.

    Simulates a provider that itself truncates the completion once it
    exceeds the requested ``max_tokens`` (reporting finish_reason=length) --
    the adaptive budget must be generous enough for a legitimately large
    findings list (15 findings here) on a small chunk not to hit that.
    """
    many_findings = [
        {
            "id": f"F-{i:03d}",
            "severity": "non-blocking",
            "location": f"file_{i}.py",
            "description": "issue " * 20,
            "decision_impact": "impact " * 10,
        }
        for i in range(15)
    ]
    response_content = {
        "verdict": "APPROVED",
        "summary": "many findings",
        "findings": many_findings,
        "rationale": "r" * 200,
        "architectural_evidence": {},
    }
    raw_json = json.dumps(response_content)

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        allowed_chars = payload["max_tokens"] * 4  # a plausible provider-side chars/token ratio
        if len(raw_json) > allowed_chars:
            body = {"choices": [{"message": {"content": raw_json[:allowed_chars]}, "finish_reason": "length"}]}
        else:
            body = {"choices": [{"message": {"content": raw_json}, "finish_reason": "stop"}]}
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@\n+small diff")
    verdict = run_llm_audit(
        _env(), pair=_pair(), pr=_pr(), chunk=chunk, context_brief="short", deterministic_findings=[]
    )
    assert verdict.verdict == "APPROVED"
    assert len(verdict.findings) == 15


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="http://x", code=code, msg="", hdrs=None, fp=io.BytesIO(body))  # type: ignore[arg-type]


def test_run_llm_audit_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the live 116-chunk rate-limit failure on PR #200 itself."""
    calls = {"count": 0}
    sleeps: list[float] = []

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            body = (
                b'{"error":{"message":"Rate limit reached for model llama-3.3-70b-versatile '
                b"... tokens per minute (TPM): Limit 12000, Used 10929, Requested 5467. "
                b'Please try again in 5.5s.","type":"tokens","code":"rate_limit_exceeded"}}'
            )
            raise _http_error(429, body)
        response_body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"verdict": "APPROVED", "summary": "ok", "findings": [], "rationale": "r", '
                            '"architectural_evidence": {}}'
                        )
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        return _FakeHTTPResponse(json.dumps(response_body).encode("utf-8"))

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@\n+ok")
    verdict = run_llm_audit(
        _env(),
        pair=_pair(),
        pr=_pr(),
        chunk=chunk,
        context_brief="ctx",
        deterministic_findings=[],
        sleep=sleeps.append,
    )
    assert verdict.verdict == "APPROVED"
    assert calls["count"] == 2
    assert sleeps == [5.5]


def test_run_llm_audit_gives_up_after_max_rate_limit_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        raise _http_error(429, b'{"error":{"message":"Rate limit reached"}}')

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="HTTP 429"):
        run_llm_audit(
            _env(),
            pair=_pair(),
            pr=_pr(),
            chunk=chunk,
            context_brief="ctx",
            deterministic_findings=[],
            sleep=sleeps.append,
        )
    assert len(sleeps) == MAX_RATE_LIMIT_RETRIES


def test_run_llm_audit_does_not_retry_non_429_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        raise _http_error(500, b"internal server error")

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="HTTP 500"):
        run_llm_audit(
            _env(),
            pair=_pair(),
            pr=_pr(),
            chunk=chunk,
            context_brief="ctx",
            deterministic_findings=[],
            sleep=sleeps.append,
        )
    assert sleeps == []


def test_parse_retry_after_seconds_extracts_groq_message_format() -> None:
    msg = "Rate limit reached ... Please try again in 21.98s. Need more tokens?"
    assert _parse_retry_after_seconds(msg) == 21.98


def test_parse_retry_after_seconds_falls_back_to_default() -> None:
    assert _parse_retry_after_seconds("no timing info in this message") == DEFAULT_RATE_LIMIT_BACKOFF_SECONDS


def test_parse_retry_after_seconds_handles_minutes_and_seconds_format() -> None:
    """Regression test: the live TPD failure on PR #200 used '44m34.944s',
    which the prior seconds-only regex mis-parsed as 34.944 seconds."""
    msg = "Rate limit reached ... Please try again in 44m34.944s. Need more tokens?"
    assert _parse_retry_after_seconds(msg) == pytest.approx(44 * 60 + 34.944)


def test_parse_retry_after_seconds_handles_hours_minutes_seconds_format() -> None:
    msg = "Please try again in 1h2m3.5s."
    assert _parse_retry_after_seconds(msg) == pytest.approx(3600 + 120 + 3.5)


def test_run_llm_audit_does_not_retry_daily_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the live TPD failure on PR #200 (run 31066459333):

    a daily quota 429 must fail immediately, not burn through
    MAX_RATE_LIMIT_RETRIES waiting on a window that won't reopen for
    tens of minutes -- far longer than any bounded CI job's timeout.
    """
    sleeps: list[float] = []

    def _fake_urlopen(request: object, timeout: int) -> _FakeHTTPResponse:
        body = (
            b'{"error":{"message":"Rate limit reached for model llama-3.3-70b-versatile '
            b"... on tokens per day (TPD): Limit 100000, Used 95514, Requested 7582. "
            b'Please try again in 44m34.944s.","type":"tokens","code":"rate_limit_exceeded"}}'
        )
        raise _http_error(429, body)

    monkeypatch.setattr("hunter_governance_review.llm_audit.urllib.request.urlopen", _fake_urlopen)

    chunk = DiffChunk(1, 1, ("a.py",), "@@ -1 +1 @@")
    with pytest.raises(LLMAuditError, match="tokens per day"):
        run_llm_audit(
            _env(),
            pair=_pair(),
            pr=_pr(),
            chunk=chunk,
            context_brief="ctx",
            deterministic_findings=[],
            sleep=sleeps.append,
        )
    assert sleeps == []  # not a single retry attempted


# --- Decision Engine -------------------------------------------------------------------


def test_decision_approved_when_all_pass() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="APPROVED", summary="ok"),
        audit_error=None,
        pair_fresh=True,
    )
    assert decision.outcome is Outcome.APPROVED


def test_decision_stale_pair_is_review_failed() -> None:
    decision = decide(
        deterministic=DeterministicResult(),
        audit=AuditVerdict(verdict="APPROVED", summary="ok"),
        audit_error=None,
        pair_fresh=False,
        pair_fresh_error="stale",
    )
    assert decision.outcome is Outcome.REVIEW_FAILED
    assert decision.reason == "stale"


def test_decision_deterministic_blocking_wins_over_llm() -> None:
    result = DeterministicResult(findings=[Finding("V-020", "empty body", Severity.BLOCKING, "detail")])
    decision = decide(
        deterministic=result,
        audit=AuditVerdict(verdict="APPROVED", summary="ok"),
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


# --- Orchestration (run_review) ---------------------------------------------------------


def test_run_review_happy_path_publishes_success() -> None:
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert len(llm.calls) == 1
    assert len(gh.statuses) == 1
    status = gh.statuses[0]
    assert status["context"] == CHECK_CONTEXT
    assert status["state"] == "success"
    assert status["sha"] == "a" * 40


def test_run_review_multi_chunk_happy_path_reviews_every_chunk() -> None:
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert len(llm.calls) >= 1
    covered = {f for chunk in llm.calls for f in chunk.files}
    assert covered == {"src/hunter/mispricing/service.py", "src/b.py"}
    assert gh.statuses[0]["state"] == "success"


def test_run_review_deterministic_failure_skips_llm() -> None:
    gh = FakeGhRunner(pr=_pr(body=""))
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert llm.calls == []
    assert synthesis.calls == []  # never reached: deterministic blocking skips the audit entirely
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_missing_secret_is_review_failed() -> None:
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(error=LLMAuditError("missing API secret: HUNTER_LLM_API_KEY"))
    synthesis = FakeSynthesisRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "Review failed" in gh.statuses[0]["description"]
    assert synthesis.calls == []  # incomplete coverage skips synthesis


def test_run_review_one_chunk_failure_makes_coverage_incomplete_and_review_failed() -> None:
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    llm = FakeChunkLlmRunner(error=LLMAuditError("simulated chunk failure"))
    synthesis = FakeSynthesisRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert synthesis.calls == []  # incomplete coverage skips synthesis


def test_run_review_diff_exceeding_sanity_bound_fails_closed_never_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-001 regression: a diff that cannot be fully retrieved/represented must
    never be silently truncated and treated as complete evidence."""
    monkeypatch.setattr("hunter_governance_review.__main__.DIFF_LIMIT", 10)
    gh = FakeGhRunner(diff="diff --git a/x.py b/x.py\n+way more than ten characters of real content")
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert gh.statuses[0]["state"] != "success"
    assert llm.calls == []  # no chunk was ever built from the oversized diff
    assert synthesis.calls == []


def test_run_review_stale_pair_before_audit_is_review_failed_and_skips_llm() -> None:
    advanced = _pr(head_oid="c" * 40)
    gh = FakeGhRunner(current=advanced)
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert llm.calls == []  # the cheap early check short-circuits before any audit call
    assert gh.statuses[0]["state"] == "failure"
    assert gh.statuses[0]["sha"] == "c" * 40
    assert "Review failed" in gh.statuses[0]["description"]


def test_run_review_pair_advances_during_audit_is_review_failed_despite_approval() -> None:
    """The post-audit freshness regression test.

    The PR is fresh for the initial resolve AND the early check, but has
    advanced by the time of the final, post-audit check -- simulating the
    target/source branch moving while the (possibly multi-chunk, slow) LLM
    audit was running. No approval may be published for a pair that no
    longer exists, even though the audit itself approved it.
    """
    original = _pr()
    advanced = _pr(head_oid="c" * 40)
    gh = FakeGhRunner(pr_sequence=[original, original, advanced])
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert len(llm.calls) >= 1  # the audit DID run, against the (now stale) original pair
    assert gh.statuses[0]["state"] == "failure"
    assert "stale" in gh.statuses[0]["description"].lower()
    assert gh.statuses[0]["sha"] == "c" * 40  # published to the CURRENT head, not the stale one


def test_run_review_context_resolution_failure_is_review_failed() -> None:
    gh = FakeGhRunner(canonical_docs={})  # even the canonical map itself is unresolvable
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert llm.calls == []
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_evidence_failure_is_review_failed() -> None:
    gh = FakeGhRunner(fail_files=True)
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert llm.calls == []
    assert gh.statuses[0]["state"] == "failure"


def test_run_review_non_protected_base_skips_gate() -> None:
    gh = FakeGhRunner(pr=_pr(base_ref_name="staging", base_oid="d" * 40))
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses == []
    assert llm.calls == []


def test_run_review_unresolvable_pr_returns_2() -> None:
    gh = FakeGhRunner(fail_pr=True)
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=FakeChunkLlmRunner(),
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 2


def test_run_review_publish_failure_returns_3() -> None:
    gh = FakeGhRunner(publish_fail=True)
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=FakeChunkLlmRunner(verdict="APPROVED"),
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 3


def test_run_review_dry_run_does_not_publish() -> None:
    gh = FakeGhRunner()
    code = run_review(
        args=_args(dry_run=True),
        env=_env(),
        gh=gh,
        llm_runner=FakeChunkLlmRunner(verdict="APPROVED"),
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses == []


def test_run_review_writes_step_summary_with_coverage_and_context_manifests(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "Hunter Governance Review" in text
    assert "APPROVED" in text
    assert "a" * 40 in text
    assert "Diff coverage manifest" in text
    assert "Authoritative context coverage manifest" in text
    assert "docs/CANONICAL_ARCHITECTURE_MAP.md" in text


def test_run_review_step_summary_includes_audit_findings(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(
        verdict="CHANGES_REQUIRED",
        findings=[
            {
                "id": "F-001",
                "severity": "blocking",
                "location": "src/hunter/mispricing/service.py",
                "description": "provenance field is dropped during migration",
                "decision_impact": "silently loses evidence lineage",
            }
        ],
    )
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=FakeSynthesisRunner(),
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert "CHANGES_REQUIRED" in text
    assert "C1-F-001" in text
    assert "provenance field is dropped during migration" in text
    assert "silently loses evidence lineage" in text


def test_run_review_cross_chunk_contradiction_blocks_approval_even_if_every_chunk_approved(
    tmp_path: Path,
) -> None:
    """F-002 regression: synthesis finding a contradiction must block approval
    even though every individual chunk was independently APPROVED."""
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(
        verdict="CHANGES_REQUIRED",
        findings=[
            {
                "id": "001",
                "severity": "blocking",
                "location": "chunks 1, 2",
                "description": "chunk 1 claims a function was removed; chunk 2's summary still calls it",
                "decision_impact": "merged understanding of the change would be incorrect",
            }
        ],
    )
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert len(synthesis.calls) == 1
    assert gh.statuses[0]["state"] == "failure"
    text = summary.read_text(encoding="utf-8")
    assert "SYN-001" in text
    assert "chunk 1 claims a function was removed; chunk 2's summary still calls it" in text


def test_run_review_synthesis_failure_fails_closed() -> None:
    """A synthesis call that itself fails must not silently fall back to the
    per-chunk aggregate -- coverage that cannot be verified consistent is
    not complete."""
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(error=LLMAuditError("simulated synthesis network failure"))
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert "synthesis" in gh.statuses[0]["description"].lower()


def test_run_review_synthesis_skipped_when_diff_is_empty() -> None:
    """A zero-chunk (empty diff) review has nothing to synthesize over."""
    # diff=" " is whitespace-only: truthy (bypasses FakeGhRunner's `diff or
    # default`), but chunking.split_diff_into_chunks treats it as empty via
    # .strip(). files=[] keeps the changed-files list consistent with the
    # empty diff (a non-empty files list here would separately -- and
    # correctly -- trip the "file missing from every chunk" coverage check).
    gh = FakeGhRunner(diff=" ", files=[])
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert synthesis.calls == []
    assert gh.statuses[0]["state"] == "success"


# --- F-001 regression: synthesis must reason over structured evidence, not summaries ---


class _EvidenceAwareSynthesisRunner:
    """A synthesis double that only flags a contradiction when both marker
    strings are present in its input -- proving whatever detection occurs
    depends on the STRUCTURED evidence being visible to synthesis, not on
    the (deliberately identical, bland) per-chunk prose summaries."""

    def __init__(self, marker_a: str, marker_b: str) -> None:
        self.marker_a = marker_a
        self.marker_b = marker_b
        self.calls: list[str] = []

    def __call__(
        self,
        env: dict[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        synthesis_input: str,
        timeout: int = 120,
    ) -> AuditVerdict:
        self.calls.append(synthesis_input)
        if self.marker_a in synthesis_input and self.marker_b in synthesis_input:
            return AuditVerdict(
                verdict="CHANGES_REQUIRED",
                summary="cross-chunk ownership/authority contradiction",
                findings=[
                    {
                        "id": "001",
                        "severity": "blocking",
                        "location": "chunks 1, 2",
                        "description": f"{self.marker_a} conflicts with {self.marker_b}",
                        "decision_impact": "ownership and authority evidence disagree across chunks",
                    }
                ],
                rationale="structured evidence conflict",
            )
        return AuditVerdict(verdict="APPROVED", summary="no contradiction visible", findings=[], rationale="ok")


def test_run_review_cross_chunk_contradiction_detected_via_structured_evidence_not_prose_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-001 regression: the cross-chunk synthesis stage must reason over
    each chunk's STRUCTURED architectural evidence, not merely its one-line
    prose summary. Both chunks here have an identically bland summary
    ("chunk N summary") -- the only way a contradiction is visible at all is
    through chunk 1's ownership_declarations conflicting with chunk 2's
    authority_changes. If synthesis only saw prose summaries (the pre-F-001
    behavior), this contradiction would be invisible and the PR would be
    falsely approved."""
    # Force the two files into genuinely separate chunks (the default
    # budget is large enough to pack both into one chunk, which would not
    # exercise the CROSS-chunk case this test is about).
    monkeypatch.setattr("hunter_governance_review.__main__.estimate_chunk_diff_budget", lambda **kwargs: 150)
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    ownership_evidence = "PaymentGateway owned exclusively by TeamA"
    authority_evidence = "TeamB granted call authority over PaymentGateway"
    llm = FakeChunkLlmRunner(
        verdict="APPROVED",
        architectural_evidence_by_chunk={
            1: {"ownership_declarations": [ownership_evidence]},
            2: {"authority_changes": [authority_evidence]},
        },
    )
    synthesis = _EvidenceAwareSynthesisRunner(ownership_evidence, authority_evidence)
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert len(synthesis.calls) == 1
    assert ownership_evidence in synthesis.calls[0]
    assert authority_evidence in synthesis.calls[0]
    assert gh.statuses[0]["state"] == "failure"
    text = summary.read_text(encoding="utf-8")
    assert "SYN-001" in text
    assert f"{ownership_evidence} conflicts with {authority_evidence}" in text


def test_run_review_baseline_no_structured_evidence_means_no_contradiction_to_find() -> None:
    """Contrast case for the test above, using the exact same evidence-aware
    synthesis double and marker strings: when neither chunk actually
    produces the structured evidence (as if only prose summaries existed),
    the double correctly finds nothing to flag -- confirming the detection
    above depended on the structured evidence being present, not some
    unrelated side effect of the double's construction."""
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    ownership_evidence = "PaymentGateway owned exclusively by TeamA"
    authority_evidence = "TeamB granted call authority over PaymentGateway"
    llm = FakeChunkLlmRunner(verdict="APPROVED")  # no architectural_evidence_by_chunk supplied
    synthesis = _EvidenceAwareSynthesisRunner(ownership_evidence, authority_evidence)
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=FakeDocumentReviewRunner(),
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "success"


# --- F-002 regression: full, lossless authoritative-document review -------------------


def test_run_review_document_review_violation_blocks_approval(tmp_path: Path) -> None:
    """F-002 regression: a violation found during full authoritative-document
    review must block approval even though the diff-chunk audit and the
    cross-chunk synthesis both approved -- a resolved document is not the
    same as a reviewed one, and reviewing it in full can surface a rule an
    excerpt-only review would have missed."""
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(verdict="APPROVED")
    document_review = FakeDocumentReviewRunner(
        verdict="CHANGES_REQUIRED",
        findings=[
            {
                "id": "001",
                "severity": "blocking",
                "location": "docs/DEVELOPMENT_GOVERNANCE.md#section 1",
                "description": "the evidence shows an authority boundary this section requires was crossed",
                "decision_impact": "violates a documented authority boundary",
            }
        ],
    )
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=document_review,
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    # Every mandatory document in DEFAULT_CANONICAL_DOCS is far smaller than
    # one section's budget, so each produced exactly one reviewed section.
    assert len(document_review.calls) == len(DEFAULT_CANONICAL_DOCS)
    text = summary.read_text(encoding="utf-8")
    assert "CTX-DOC1-001" in text
    assert "authority boundary this section requires was crossed" in text


def test_run_review_document_review_one_section_failure_fails_closed_never_approves() -> None:
    """F-002 regression: if even one section of one mandatory document
    fails to be reviewed, the whole authoritative-document review must fail
    closed -- there is no partial credit toward "the evidence was reviewed"
    just because most sections succeeded."""
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(verdict="APPROVED")
    # DEFAULT_CANONICAL_DOCS has multiple mandatory documents; fail exactly
    # one section while every other section succeeds.
    document_review = FakeDocumentReviewRunner(fail_on_call=3)
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=document_review,
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert gh.statuses[0]["state"] != "success"
    # Every section was still attempted -- a later section's success does
    # not get skipped just because an earlier one failed.
    assert len(document_review.calls) == len(DEFAULT_CANONICAL_DOCS)


def test_run_review_document_review_bytes_reviewed_matches_full_document_bytes_on_success(
    tmp_path: Path,
) -> None:
    """F-002 regression: the document-review coverage manifest's
    ``bytes_reviewed`` must reflect bytes that were ACTUALLY reviewed,
    section by section -- never bytes that were merely retrieved or
    excerpted. When every section succeeds this must equal the full
    combined byte length of every mandatory document's complete text."""
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(verdict="APPROVED")
    document_review = FakeDocumentReviewRunner(verdict="APPROVED")
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=document_review,
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "success"
    expected_bytes = sum(len(text) for text in DEFAULT_CANONICAL_DOCS.values())
    summary_text = summary.read_text(encoding="utf-8")
    assert f"Bytes actually reviewed**: {expected_bytes}/{expected_bytes}" in summary_text
    assert f"Documents**: {len(DEFAULT_CANONICAL_DOCS)}" in summary_text


def test_run_review_document_review_bytes_reviewed_is_zero_not_partial_when_incomplete(
    tmp_path: Path,
) -> None:
    """F-002 regression: ``bytes_reviewed`` must never report a partial byte
    count reflecting only the sections that happened to succeed before one
    failed -- when document-review coverage is incomplete, bytes_reviewed
    is exactly 0, not some fraction of bytes_total, so the manifest can
    never be misread as "mostly reviewed."."""
    summary = tmp_path / "summary.md"
    gh = FakeGhRunner()
    llm = FakeChunkLlmRunner(verdict="APPROVED")
    synthesis = FakeSynthesisRunner(verdict="APPROVED")
    document_review = FakeDocumentReviewRunner(fail_on_call=1)
    code = run_review(
        args=_args(),
        env=_env(GITHUB_STEP_SUMMARY=str(summary)),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=document_review,
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    expected_bytes = sum(len(text) for text in DEFAULT_CANONICAL_DOCS.values())
    summary_text = summary.read_text(encoding="utf-8")
    assert f"Bytes actually reviewed**: 0/{expected_bytes}" in summary_text
    assert "**Reason**: authoritative-document review incomplete" in summary_text


def test_run_review_document_review_skipped_when_diff_coverage_already_incomplete() -> None:
    """A diff chunk that itself failed already fails the review closed --
    authoritative-document review is not even attempted in that case, since
    there is nothing trustworthy yet to check documents against."""
    gh = FakeGhRunner(
        files=[CODE_FILE, ChangedFile("src/b.py", "modified", 1, 1)],
        diff=_multi_chunk_diff(),
    )
    llm = FakeChunkLlmRunner(error=LLMAuditError("simulated chunk failure"))
    synthesis = FakeSynthesisRunner()
    document_review = FakeDocumentReviewRunner()
    code = run_review(
        args=_args(),
        env=_env(),
        gh=gh,
        llm_runner=llm,
        synthesis_runner=synthesis,
        document_review_runner=document_review,
    )
    assert code == 0
    assert gh.statuses[0]["state"] == "failure"
    assert document_review.calls == []
