# Resilient Reviewer Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact-head PR review start automatically within seconds, use the Mac-hosted reviewer when healthy, fail over immediately when it is unavailable, and reserve red status for confirmed defects/invalid evidence rather than normal reviewer delay.

**Architecture:** A trusted default-branch orchestrator owns one review cycle per exact HEAD. It queries a dedicated GitHub self-hosted Mac runner for local availability, dispatches a non-executing Ollama review job when available, otherwise advances to the next configured provider; Governance consumes explicit orchestration state and remains pending through waiting/failover. Provider results are exact-head-bound immutable artifacts/comments verified by trusted code before they can become review authority.

**Tech Stack:** Python 3.12, GitHub Actions/REST API, macOS GitHub self-hosted runner, Ollama HTTP API, `qwen2.5-coder:7b`, pytest, existing Hunter governance/pre-push gates.

**Spec:** `docs/superpowers/specs/2026-09-15-reviewer-orchestration-design.md`

## Global Constraints

- No merge or Ready transition without explicit owner approval.
- Exact-head review authority remains mandatory.
- Red/failure means a confirmed blocking finding or proven malformed/forged authority; waiting, quota, outage, pool exhaustion, stale/superseded HEAD, and reviewer unavailability remain non-red blocked/pending states.
- Root-of-trust workflow/script/policy changes must use the local clone + `.githooks/pre-push` path.
- Candidate code is never executed by the local reviewer; it reviews GitHub API diff/content only.
- No paid reviewer subscription is introduced.
- TDD is mandatory: failing regression first, then minimal production change.
- Current branch stays Draft and no merge occurs without the owner’s explicit approval.

---### Task 1: Make review availability an explicit non-red state

**Files:**
- Modify: `scripts/hunter_governance_review_v2.py`
- Modify: `scripts/hunter_merge_readiness_v2.py`
- Test: `tests/test_exact_head_review_authority.py`
- Test: `tests/test_issue_412_prevention_gate.py`

**Interfaces:**
- Produces: `ReviewLifecycleState` semantics represented as existing `(state, description)` tuples: `pending`, `success`, `failure`.
- Rule: missing current authority while a valid review cycle can still run returns `pending`; authenticated blocking findings or forged/malformed authority may return `failure`.

- [ ] **Step 1: Write failing tests for non-red missing/delayed authority**

```python
def test_missing_review_authority_with_available_orchestration_is_pending(monkeypatch):
    monkeypatch.setattr(core, "review_orchestration_state", lambda *_: ("WAITING_FOR_REVIEWER", "local selection"))
    state, message = readiness.review_authority_state(HEAD, PR_NUMBER)
    assert state == "pending"
    assert "WAITING_FOR_REVIEWER" in message


def test_pool_exhaustion_is_blocked_pending_not_red(monkeypatch):
    monkeypatch.setattr(core, "review_orchestration_state", lambda *_: ("POOL_EXHAUSTED", "no reviewer capacity"))
    state, _ = readiness.review_authority_state(HEAD, PR_NUMBER)
    assert state == "pending"
```

- [ ] **Step 2: Run those tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_exact_head_review_authority.py -k 'available_orchestration or pool_exhaustion'`
Expected: FAIL because no orchestration-aware pending path exists yet.- [ ] **Step 3: Implement minimal orchestration-aware state mapping**

```python
NON_RED_REVIEW_STATES = {
    "WAITING_FOR_REVIEWER",
    "REVIEW_IN_PROGRESS",
    "FAILOVER_IN_PROGRESS",
    "POOL_EXHAUSTED",
}


def review_wait_state(orchestration_state: str, detail: str) -> tuple[str, str] | None:
    if orchestration_state in NON_RED_REVIEW_STATES:
        return "pending", f"{orchestration_state}: {detail}"
    return None
```

Wire this before converting `MISSING_REVIEW_AUTHORITY`, `POOL_NOT_EXHAUSTED`, or unavailable exhaustion evidence into a red decision. Do not weaken `BLOCKING_FINDINGS` or authenticated malformed/forged evidence.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_exact_head_review_authority.py tests/test_issue_412_prevention_gate.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/hunter_governance_review_v2.py scripts/hunter_merge_readiness_v2.py tests/test_exact_head_review_authority.py tests/test_issue_412_prevention_gate.py
git commit -m "fix(review): keep reviewer availability states non-red"
```

### Task 2: Add one trusted exact-head orchestration record

**Files:**
- Create: `scripts/hunter_review_orchestrator.py`
- Create: `tests/test_hunter_review_orchestrator.py`
- Modify: `scripts/hunter_governance_review_v2.py`

**Interfaces:**
- Produces: `ReviewCycle(head_sha, pr_number, state, provider_id, trigger_id, started_at, config_digest)`.
- Produces: `read_cycle(repository, token, pr, head) -> tuple[str, dict | None, str | None]`.
- A cycle is valid only when PR remains open and current PR HEAD equals `head_sha`.

- [ ] **Step 1: Write RED tests for exact-head cycle identity and supersession**

```python
def test_cycle_for_old_head_is_superseded():
    cycle = make_cycle(head_sha="a" * 40)
    assert orchestrator.classify_cycle(cycle, current_head="b" * 40) == "SUPERSEDED"


def test_waiting_cycle_is_pending_not_failure():
    cycle = make_cycle(state="WAITING_FOR_REVIEWER")
    assert orchestrator.governance_projection(cycle) == ("pending", "WAITING_FOR_REVIEWER")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py`
Expected: FAIL because module/interfaces do not exist.- [ ] **Step 3: Implement the minimal cycle model and trusted read path**

```python
@dataclass(frozen=True)
class ReviewCycle:
    pr_number: int
    head_sha: str
    state: str
    provider_id: str
    trigger_id: int | None
    started_at: str
    config_digest: str


def classify_cycle(cycle: ReviewCycle, current_head: str) -> str:
    return "SUPERSEDED" if cycle.head_sha != current_head else cycle.state
```

Persist cycle evidence only through trusted default-branch workflow artifacts/statuses; candidate files are declarations only and never cycle authority.

- [ ] **Step 4: Integrate Governance projection and run focused tests**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py tests/test_exact_head_review_authority.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/hunter_review_orchestrator.py scripts/hunter_governance_review_v2.py tests/test_hunter_review_orchestrator.py tests/test_exact_head_review_authority.py
git commit -m "feat(review): add exact-head review lifecycle state"
```

### Task 3: Automatically dispatch the collector from trusted default-branch code

**Files:**
- Modify: `.github/workflows/hunter-governance-review.yml`
- Modify: `.github/workflows/hunter-governance-reconcile.yml`
- Modify: `scripts/hunter_review_orchestrator.py`
- Test: `tests/test_hunter_review_orchestrator.py`
- Test: `tests/test_issue_412_prevention_gate.py`

**Interfaces:**
- Produces: `dispatch_collector(repository, token, pr_number, head_sha) -> run identity`.
- Idempotency key: `(pr_number, head_sha, reviewer_pool_config_digest)`; an existing active/completed matching collector run prevents duplicate dispatch.

- [ ] **Step 1: Write RED tests for auto-dispatch and duplicate suppression**

```python
def test_ready_review_request_dispatches_collector_once(fake_github):
    first = orchestrator.ensure_collector(fake_github, 472, HEAD)
    second = orchestrator.ensure_collector(fake_github, 472, HEAD)
    assert first.run_id == second.run_id
    assert fake_github.dispatch_count == 1
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py -k dispatch`
Expected: FAIL.- [ ] **Step 3: Implement trusted workflow dispatch**

Use the GitHub Actions workflow-dispatch REST endpoint from trusted default-branch code and pass only `pr_number` and exact `head_sha`. The workflow itself remains checked out from the default branch.

```python
def dispatch_collector(repository: str, token: str, pr_number: int, head_sha: str) -> None:
    governance.request_json(
        repository,
        token,
        "POST",
        "actions/workflows/hunter-reviewer-collector.yml/dispatches",
        {"ref": "main", "inputs": {"pr_number": str(pr_number), "head_sha": head_sha}},
    )
```

Before dispatch, query recent workflow runs and refuse to duplicate an active/completed run for the same PR/head/config identity.

- [ ] **Step 4: Update workflow permissions**

`hunter-governance-review.yml` and reconcile need only the minimum permission that can dispatch Actions plus existing read/status permissions. Preserve trusted default-branch checkout and do not run candidate code.

- [ ] **Step 5: Run focused workflow/guard tests**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py tests/test_issue_412_prevention_gate.py tests/test_hunter_governance_cleanup.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/hunter-governance-review.yml .github/workflows/hunter-governance-reconcile.yml scripts/hunter_review_orchestrator.py tests/test_hunter_review_orchestrator.py tests/test_issue_412_prevention_gate.py
git commit -m "feat(review): auto-dispatch trusted reviewer collector"
```

### Task 4: Split acknowledgement timing from substantive review timing

**Files:**
- Modify: `docs/CODE_WRITE_POLICY.json`
- Modify: `scripts/hunter_pre_ready_review.py`
- Modify: `scripts/hunter_reviewer_collector.py`
- Modify: `tests/test_code_write_policy.py`
- Modify: `tests/test_hunter_reviewer_collector.py`

**Interfaces:**
- Provider config gains `ack_timeout_seconds` and `review_timeout_seconds`.
- `ack_timeout_seconds` decides availability/failover; `review_timeout_seconds` limits an acknowledged review without treating ordinary execution as provider unavailability.
- Initial values: local 30s ack; hosted 90s ack; provider-specific execution ceiling remains separately bounded.

- [ ] **Step 1: Write RED policy/parser tests**

```python
def test_reviewer_requires_distinct_ack_and_review_budgets():
    pool, error = review.load_reviewer_pool()
    assert not error
    for agent in review.enabled_pool_reviewers(pool):
        assert 1 <= agent["ack_timeout_seconds"] <= 90
        assert agent["review_timeout_seconds"] > agent["ack_timeout_seconds"]
```

- [ ] **Step 2: Write RED collector timing test**

```python
def test_no_ack_fails_over_after_short_budget():
    backend = Backend()
    results = collector.collect_attempts(POOL_WITH_30S_ACK, HEAD, backend)
    assert results[0]["ack_elapsed_seconds"] == 30
    assert results[0]["outcome"] == "unavailable"
```

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/pytest -q tests/test_code_write_policy.py tests/test_hunter_reviewer_collector.py`
Expected: FAIL on old 900-second single-budget model.- [ ] **Step 4: Implement two-stage timing**

```python
ack_deadline = backend.now() + agent["ack_timeout_seconds"]
while backend.now() < ack_deadline:
    if backend.acknowledged(agent, trigger):
        return wait_for_substantive_result(agent, trigger, backend)
    backend.sleep(min(5, ack_deadline - backend.now()))
return AttemptResult(outcome="unavailable", failure_class="transient")
```

Keep a response/reaction as proof of acknowledgement only. A clear review requires the existing structured exact-head parser; an acknowledgement alone never grants authority.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest -q tests/test_code_write_policy.py tests/test_hunter_reviewer_collector.py tests/test_exact_head_review_authority.py`
Expected: PASS.

```bash
git add docs/CODE_WRITE_POLICY.json scripts/hunter_pre_ready_review.py scripts/hunter_reviewer_collector.py tests/test_code_write_policy.py tests/test_hunter_reviewer_collector.py tests/test_exact_head_review_authority.py
git commit -m "fix(review): separate reviewer ack and execution budgets"
```

### Task 5: Add the Mac self-hosted reviewer as a trusted free provider

**Files:**
- Create: `.github/workflows/hunter-local-reviewer.yml`
- Create: `scripts/hunter_local_reviewer.py`
- Create: `tests/test_hunter_local_reviewer.py`
- Modify: `scripts/hunter_review_orchestrator.py`
- Modify: `docs/CODE_WRITE_POLICY.json`
- Modify: `tests/test_code_write_policy.py`

**Interfaces:**
- Runner label: `hunter-reviewer` on the dedicated Mac self-hosted runner.
- Local reviewer input: `repository`, `pr_number`, `head_sha`, `claims_id`.
- Local reviewer output artifact schema: `hunter.local-review.v1` with exact head, config/model digest, findings, verdict, started/completed timestamps.
- Model endpoint: local Ollama only; current installed baseline is `qwen2.5-coder:7b` on a 16-GB Intel Mac.

- [ ] **Step 1: Write RED tests that candidate code is never executed**

```python
def test_local_reviewer_uses_github_diff_not_candidate_checkout(monkeypatch):
    calls = []
    reviewer.review_pr(fetch_diff=lambda *_: calls.append("diff") or "patch", run_command=lambda *_: calls.append("exec"))
    assert calls == ["diff"]
```

- [ ] **Step 2: Write RED exact-head output test**

```python
def test_result_is_bound_to_requested_exact_head():
    result = reviewer.build_result(head_sha=HEAD, model="qwen2.5-coder:7b", findings=[])
    assert result["schema"] == "hunter.local-review.v1"
    assert result["head_sha"] == HEAD
    assert result["verdict"] == "clear"
```

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/pytest -q tests/test_hunter_local_reviewer.py`
Expected: FAIL because the reviewer does not exist.- [ ] **Step 4: Implement minimal Ollama reviewer adapter**

```python
def ollama_review(diff: str, model: str = "qwen2.5-coder:7b") -> dict:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "prompt": build_review_prompt(diff),
    }
    return post_json("http://127.0.0.1:11434/api/generate", payload)
```

The prompt requires machine JSON only: `verdict`, `summary`, and zero or more findings with severity/path/line/evidence. Reject malformed output rather than converting prose into authority.

- [ ] **Step 5: Add trusted local-review workflow**

The workflow runs only from the trusted default branch and targets `runs-on: [self-hosted, macOS, hunter-reviewer]`. It must not `checkout` the candidate. It fetches PR metadata/diff through GitHub APIs, verifies current HEAD equals the requested SHA before and after review, and uploads the structured result artifact.

- [ ] **Step 6: Add local provider to policy only after parser tests pass**

Use provider id `local-ollama`, priority 1 for routine changes, `ack_timeout_seconds: 30`, exact-head support true. Move Codex below it, while root-of-trust policy can still require hosted corroboration.

- [ ] **Step 7: Run focused tests and commit**

Run: `.venv/bin/pytest -q tests/test_hunter_local_reviewer.py tests/test_hunter_review_orchestrator.py tests/test_code_write_policy.py`
Expected: PASS.

```bash
git add .github/workflows/hunter-local-reviewer.yml scripts/hunter_local_reviewer.py scripts/hunter_review_orchestrator.py docs/CODE_WRITE_POLICY.json tests/test_hunter_local_reviewer.py tests/test_code_write_policy.py
git commit -m "feat(review): add trusted local Ollama reviewer"
```

### Task 6: Detect Mac offline/unresponsive state and fail over immediately

**Files:**
- Modify: `scripts/hunter_review_orchestrator.py`
- Modify: `.github/workflows/hunter-reviewer-collector.yml`
- Modify: `tests/test_hunter_review_orchestrator.py`
- Modify: `tests/test_hunter_reviewer_collector.py`

**Interfaces:**
- `runner_state(repository, token, label="hunter-reviewer") -> online|offline|busy|missing` from GitHub Actions runner API.
- `offline`/`missing` skip the local provider immediately.
- `online` but no job start/ack inside 30 seconds becomes `unresponsive` and advances to the next provider.

- [ ] **Step 1: Write RED offline and race tests**

```python
def test_offline_mac_skips_local_without_red(fake_github):
    fake_github.runner_status = "offline"
    decision = orchestrator.select_provider(fake_github, HEAD)
    assert decision.state == "FAILOVER_IN_PROGRESS"
    assert decision.next_provider == "codex"


def test_online_mac_that_never_starts_does_not_stall_pr(fake_github):
    fake_github.runner_status = "online"
    fake_github.local_job_started = False
    decision = orchestrator.wait_for_local_ack(fake_github, timeout=30)
    assert decision.reason == "unresponsive"
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py -k 'offline_mac or never_starts'`
Expected: FAIL.- [ ] **Step 3: Implement runner-state selection and bounded local ack**

Use GitHub's repository self-hosted runner listing as trusted availability evidence. Never infer Mac health from a stale local file or candidate-provided heartbeat.

```python
if runner_state(repo, token) not in {"online", "busy"}:
    return failover("local-ollama", reason="offline")

run_id = dispatch_local_review(...)
if not wait_for_job_start(run_id, seconds=30):
    cancel_run(run_id)
    return failover("local-ollama", reason="unresponsive")
```

- [ ] **Step 4: Preserve non-red status through fallback**

Assert every offline/unresponsive branch publishes `FAILOVER_IN_PROGRESS`/`pending`; only a parsed substantive finding can publish failure.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py tests/test_hunter_reviewer_collector.py tests/test_exact_head_review_authority.py`
Expected: PASS.

```bash
git add scripts/hunter_review_orchestrator.py .github/workflows/hunter-reviewer-collector.yml tests/test_hunter_review_orchestrator.py tests/test_hunter_reviewer_collector.py tests/test_exact_head_review_authority.py
git commit -m "fix(review): fail over immediately when Mac reviewer is unavailable"
```

### Task 7: Bootstrap the Mac reviewer service and prove it survives normal operation

**Files:**
- Create: `docs/operations/hunter-local-reviewer.md`
- Create: `scripts/bootstrap_hunter_review_runner.sh`
- Test: `tests/test_hunter_local_reviewer_bootstrap.py`

**Interfaces:**
- Dedicated GitHub runner label: `hunter-reviewer`.
- Runtime dependency: existing `/usr/local/bin/ollama`, model `qwen2.5-coder:7b`.
- Runner/service must auto-start with the macOS login session and be inspectable/restartable over the existing iPhone SSH path.

- [ ] **Step 1: Write RED bootstrap contract test**

```python
def test_bootstrap_requires_ollama_model_and_dedicated_runner_label():
    text = Path("scripts/bootstrap_hunter_review_runner.sh").read_text()
    assert "qwen2.5-coder:7b" in text
    assert "hunter-reviewer" in text
    assert "svc.sh install" in text or "LaunchAgent" in text
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest -q tests/test_hunter_local_reviewer_bootstrap.py`
Expected: FAIL because bootstrap artifacts do not exist.

- [ ] **Step 3: Implement idempotent bootstrap**

The script must refuse to print/store registration tokens, confirm Ollama is reachable, confirm the model exists, configure the dedicated runner label, install/start the runner service, and provide status-only commands. Registration token acquisition is one-time and must come from authenticated `gh api`, never a committed secret.

- [ ] **Step 4: Document iPhone SSH operations**

Document only operational commands: runner status, Ollama status, recent logs, restart, and a manual diagnostic review. Normal PR review remains automatic and must not depend on SSH.

- [ ] **Step 5: Run bootstrap tests and commit**

Run: `.venv/bin/pytest -q tests/test_hunter_local_reviewer_bootstrap.py tests/test_hunter_local_reviewer.py`
Expected: PASS.

```bash
git add docs/operations/hunter-local-reviewer.md scripts/bootstrap_hunter_review_runner.sh tests/test_hunter_local_reviewer_bootstrap.py
git commit -m "docs(review): add persistent Mac reviewer bootstrap"
```### Task 8: Benchmark local review quality before granting routine authority

**Files:**
- Create: `tests/fixtures/reviewer_benchmark_cases.json`
- Create: `scripts/hunter_reviewer_benchmark.py`
- Create: `tests/test_hunter_reviewer_benchmark.py`
- Modify: `docs/CODE_WRITE_POLICY.json`

**Interfaces:**
- Benchmark fixture contains historical confirmed defect snippets from existing DFF/backfill evidence plus clean controls.
- `benchmark(model) -> {recall, false_positive_rate, latency_seconds}`.
- Local reviewer becomes routine-authority eligible only after the configured minimum benchmark passes; root-of-trust/governance changes still require hosted corroboration.

- [ ] **Step 1: Write RED benchmark-policy test**

```python
def test_local_reviewer_cannot_gain_authority_without_benchmark_evidence():
    pool, error = review.load_reviewer_pool()
    assert not error
    local = next(a for a in pool["agents"] if a["id"] == "local-ollama")
    assert local["quality_gate"]["required"] is True
    assert local["quality_gate"]["benchmark_id"]
```

- [ ] **Step 2: Build fixtures from already-confirmed defects and clean controls**

Do not invent new vulnerabilities. Use small, deterministic excerpts representing known stale-head, missing-authority, retry/exhaustion, and workflow-permission failures already recorded by Hunter.

- [ ] **Step 3: Implement benchmark runner**

```python
def score(cases, results):
    positives = [c for c in cases if c["expected"] == "finding"]
    clean = [c for c in cases if c["expected"] == "clear"]
    recall = sum(results[c["id"]]["found"] for c in positives) / len(positives)
    fp = sum(results[c["id"]]["found"] for c in clean) / len(clean)
    return recall, fp
```

- [ ] **Step 4: Run benchmark on the installed model**

Run: `.venv/bin/python scripts/hunter_reviewer_benchmark.py --model qwen2.5-coder:7b`
Record latency and quality in a generated non-secret benchmark result. If threshold fails, keep local reviewer as triage/first-pass only and require fallback authority; do not pretend it is sufficient.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest -q tests/test_hunter_reviewer_benchmark.py tests/test_code_write_policy.py`
Expected: PASS.

```bash
git add tests/fixtures/reviewer_benchmark_cases.json scripts/hunter_reviewer_benchmark.py tests/test_hunter_reviewer_benchmark.py docs/CODE_WRITE_POLICY.json
git commit -m "test(review): gate local reviewer authority on benchmark evidence"
```

### Task 9: Register recurrence prevention and perform exact-head verification

**Files:**
- Modify: `docs/DEFECT_REGISTRY.json`
- Modify: `.hunter/pre-ready-hostile-review.json` only through the canonical review-request generator after final content HEAD is known
- Test: existing DFF/guard suites

**Interfaces:**
- Extend DFF-022 evidence for: waiting-not-red, auto-dispatch, ack-vs-execution timeout split, Mac-offline failover, stale-cycle supersession.
- Do not create a new Issue unless implementation proves a genuinely distinct defect family.

- [ ] **Step 1: Add the new deterministic regression references to DFF-022**

Register exact test selectors added in Tasks 1–8 and extend applicability to `scripts/hunter_review_orchestrator.py`, the local reviewer workflow/script, and bootstrap/runner orchestration boundaries as appropriate.

- [ ] **Step 2: Run focused reviewer/governance suite**

Run: `.venv/bin/pytest -q tests/test_hunter_review_orchestrator.py tests/test_hunter_reviewer_collector.py tests/test_hunter_local_reviewer.py tests/test_hunter_reviewer_benchmark.py tests/test_exact_head_review_authority.py tests/test_issue_412_prevention_gate.py tests/test_code_write_policy.py`
Expected: all PASS (platform-specific tests may skip only with an explicit reason).

- [ ] **Step 3: Run repository guards/static checks**

Run the canonical Architecture Index, Artifact Guard, Defect Prevention Guard, Ruff, Black check, and Mypy using the same commands enforced by `.githooks/pre-push`.
Expected: all PASS.

- [ ] **Step 4: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS with only previously-accepted explicit platform skips.

- [ ] **Step 5: Regenerate the exact-head review request after the final content commit**

Run the canonical `scripts/hunter_pre_ready_review.py` command against the current `main` base and Issue #461. Commit only the regenerated review request artifact after the content HEAD is final.

- [ ] **Step 6: Push through the real pre-push boundary and verify hosted exact-head state**

Confirm pre-push PASS, push, then verify Trusted Preflight, CI, governance pending/clear semantics, automatic collector dispatch, local/offline failover behavior, and no red state caused solely by waiting/unavailability.

- [ ] **Step 7: Request owner approval only after fresh exact-head review is clean**

Do not mark Ready and do not merge. Report exact HEAD, local/full verification counts, hosted statuses, active reviewer/provider used, and any remaining blockers to the owner for explicit approval.
