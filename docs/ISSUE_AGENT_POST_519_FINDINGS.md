# Issue Agent Post-#519 Findings

Status: evidence capture; not an implementation authorization.

## Purpose

This note preserves the production evidence and architectural questions discovered
after PR #519 so that PR-A and any future executor design start from repository-held
evidence rather than chat memory. Canonical authority remains with the existing
Issue Agent execution contract, Task Scope contract, governance documents, ADRs, and
defect registry. This note creates no new authority.

## Observed production evidence

- PR #519 merged as `ae0a67b0c3295713ece493645d4be0bff93cb224`.
- The Railway OpenCode startup self-check passed with the external Groq provider after
  the provider environment allowlist was corrected to valid JSON.
- Issue #520's fresh governed trigger reached `/issue-agent/provision` and
  `/issue-agent/authorize` successfully.
- Real execution then emitted `bwrap: Creating new namespace failed: Operation not
  permitted` and exhausted the provider pool. No Draft PR was produced.
- Code tracing reproduced a launcher-selection divergence: the startup self-check sees
  `RAILWAY_ENVIRONMENT`; the real provider child environment does not, so the same
  launcher-selection function can choose different backends.
- The exact source of the visible `bwrap` stderr line remains unresolved because the
  expected provider subprocess stderr is redirected to `DEVNULL`. This does not negate
  the reproduced backend divergence, but it must not be silently declared solved.

> **Live-trigger safety:** Until PR-A makes admission fail closed before claim/dispatch,
> no Issue carrying the live execution label may be triggered. This prohibition is
> path-wide, not specific to Issue #520. Issue #520 is only the canary to resume after
> rehearsal evidence and a fresh authorization.

## Security findings

1. A same-process/self-check result cannot prove a different execution path safe.
2. The Railway permission shim restricts OpenCode tools but is not OS isolation.
3. Publication must not execute candidate-controlled hooks or scripts while a write
   token/signing credential is present.
4. Candidate validation that executes candidate code belongs in an isolated execution
   boundary, not on a secret-bearing authority/ledger host.
5. Scope enforcement must derive from TaskScope (`allowed_paths` /
   `prohibited_paths`); a publisher must not create an independent path authority.
6. A future runner's trust cannot be self-attested by a probe running on that runner.
   External platform enforcement owns runner eligibility; in-job probes only diagnose
   configuration drift.

## Current direction, not yet implementation authority (UNIMPLEMENTED)

The preferred design direction is to keep Railway as authorization/ledger/ingress and
move untrusted agent execution to ephemeral GitHub-hosted execution, separating agent
and publisher privileges. The publisher would consume only validated untrusted data,
never execute candidate code, and downstream candidate tests would run in a separate
secret-free boundary. This direction requires the open evidence below before PR-B.

## Required read-only audit before PR-B

1. Trace exact prompt/handoff and terminal-result ledger semantics; compare GitHub OIDC
   reporting with Railway pull observation.
2. Resolve writer identity, verified commit provenance, token type, create-only push,
   and downstream workflow-trigger semantics against current Candidate Admission.
3. Because `fafa33/Project-Hunter` is public, classify exact prompt, Evidence/Source
   Handling data, logs, patch data, and Actions artifacts before placing any of them on
   GitHub-hosted surfaces.
4. Trace every configured `HUNTER_AGENT_*_COMMAND` and corresponding environment
   allowlist so PR-A disables the entire provider pool before claim, not only OpenCode.

## Defect-registry transition

Two systemic defect classes are identified by this evidence: readiness probes that exercise a different execution boundary, and candidate-controlled code executing inside a credential-bearing publication boundary. This evidence contribution records both classes in `DEFECT_REGISTRY` at lifecycle status `recorded`; it does not claim a guard exists. PR-A/PR-B must promote them to `guarded` only together with the real machine-enforced prevention boundary and regression tests.

## Bounded next steps

- PR-A: fail closed before authorization claim/dispatch for all Railway execution
  providers; record the contract transition and make #520 mechanically non-runnable.
- Read-only audit: answer the four evidence questions above without implementation.
- PR-B: only after those answers, implement the replacement executor and structurally
  non-publishing rehearsal.
- Canary: one fresh #520 authorization only after rehearsal and all relevant gates pass.

The Railway capability probe for Landlock/seccomp/UID isolation is intentionally
deferred while the GitHub-hosted executor direction is evaluated; no conclusion about
Railway kernel capability is asserted.
