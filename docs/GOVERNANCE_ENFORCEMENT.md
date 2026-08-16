# Governance Enforcement

## Purpose

This document explains how agents and repository automation execute the governance already owned by Project Hunter's canonical documents. It is an operational guide only. It does not define lifecycle, review, architecture-audit, implementation, approval, or merge semantics.

Canonical ownership remains:

| Governance domain | Canonical owner |
|---|---|
| Development lifecycle and merge-readiness lifecycle semantics | `docs/DEVELOPMENT_GOVERNANCE.md` |
| Contribution review roles, blocking-finding classification/reporting, review outcomes, approval | `docs/AI_REVIEW_PROTOCOL.md` |
| Architecture-preparation audit materiality, verdicts, and re-audit scope | `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` |
| Implementation obligations and durable root-cause hardening | `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` |
| Agent behavior requiring use of the governed path | `AGENT_RULES.md` |

`scripts/hunter_governance_preflight.py` is an executable consumer of those authorities. If the executable projection and canonical prose diverge, preflight must fail closed; the prose owners remain authoritative.

## Mandatory agent entry point

Before a governed repository or GitHub mutation, agents run the preflight action matching the intended mutation. The supported action surface is:

- `branch`
- `commit`
- `push`
- `pr-create`
- `pr-update`
- `ready`
- `resolve-finding`
- `merge-readiness`

The preflight verifies the live governing Issue rather than accepting a remembered, guessed, or sequence-inferred Issue number.

Examples:

```bash
python scripts/hunter_governance_preflight.py self-check

python scripts/hunter_governance_preflight.py branch \
  --repo fafa33/Project-Hunter \
  --issue 276 \
  --objective "Governance enforcement: mandatory agent preflight and PR generator" \
  --branch governance/issue-276-agent-preflight

python scripts/hunter_governance_preflight.py push \
  --repo fafa33/Project-Hunter \
  --issue 276 \
  --objective "Governance enforcement: mandatory agent preflight and PR generator" \
  --allow-governance-diff-check
```

Normal repository quality verification remains a separate command:

```bash
python scripts/hunter_pr_preflight.py
```

The governance preflight does not replace Ruff, Black, mypy, pytest, CI, independent review, or human merge approval.

## Canonical PR-body generation

Agents do not hand-invent governed PR metadata. The generator consumes:

- the live verified governing Issue;
- `.github/pull_request_template.md`;
- the actual changed-file scope;
- the exact source-head and target revisions;
- explicit criterion-specific evidence supplied by the implementer.

Example:

```bash
python scripts/hunter_governance_preflight.py generate-pr-body \
  --repo fafa33/Project-Hunter \
  --issue 276 \
  --objective "Governance enforcement: mandatory agent preflight and PR generator" \
  --head-sha "$HEAD_SHA" \
  --base-sha "$BASE_SHA" \
  --evidence-json /tmp/hunter-pr-evidence.json \
  --output /tmp/hunter-pr-body.md
```

Every governing Issue acceptance criterion is represented exactly once. Unproven criteria default to `BLOCKED`; the generator never infers `PASS` from green CI. A generated body carries exactly one implementer readiness declaration and an exact-pair trace marker:

```text
<!-- hunter-governance-preflight:v1 issue=<N> head=<HEAD_SHA> base=<BASE_SHA> -->
```

Changing the source head or target revision makes that evidence stale until the body is regenerated or re-synchronized with current evidence.

## Ready-for-review preflight

Before an agent promotes a Draft PR, run:

```bash
python scripts/hunter_governance_preflight.py ready \
  --repo fafa33/Project-Hunter \
  --issue <ISSUE> \
  --objective "<EXACT VERIFIED ISSUE TITLE>" \
  --pr <PR_NUMBER>
```

The command re-reads current GitHub state. It requires the canonical Issue identity, complete matrix, current exact-pair trace, `READY FOR REVIEW`, no `FAIL`/`BLOCKED` criterion, current exact-head required checks, current Governance Review evidence, and no live review-feedback blocker. Event payloads are hints; current GitHub state is authority.

## Blocking-finding resolution

`docs/AI_REVIEW_PROTOCOL.md` owns `isolated` / `systemic` classification. The preflight consumes that result; it does not create or override it.

Before a blocking finding is treated as resolved, a machine-readable resolution record can be checked with:

```bash
python scripts/hunter_governance_preflight.py resolve-finding \
  --finding-json /tmp/finding-resolution.json
```

A systemic resolved finding requires classification evidence, the reusable boundary, durable guard evidence, and verifier evidence. This is the executable enforcement of the implementation obligation already owned by `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

## Canonical ownership guard

When governance documents change, preflight scans added semantics at the canonical document boundaries. It rejects known lifecycle, contribution-review, architecture-audit, or implementation semantics added to a non-owning canonical document unless the added text explicitly consumes the owning document rather than redefining it.

This guard is deliberately conservative and deterministic. It is not a natural-language policy engine and does not transfer authority to itself.

## Enforcement layers and platform limits

Some actions can be prevented before mutation only when the contributing agent cooperates with the repository command. GitHub does not allow repository code to intercept every manual owner action before GitHub accepts it. Hunter therefore uses two layers:

1. **Pre-action prevention** — agents run the deterministic preflight before branch/commit/push/PR/body/Ready/finding-resolution operations.
2. **Repository rejection** — trusted default-branch workflows re-read live GitHub state and reject a bypass before merge readiness. `Hunter Governance Review` and `Hunter Merge Readiness` remain the canonical merge-control path; the enforcement layer cannot approve itself or grant merge authority.

No agent-generated metadata is approval. No automation may merge merely because preflight passes. Human approval remains required.

## Bootstrap of Issue #276

GitHub Actions workflow definitions and scheduled enforcement become trusted repository behavior only after they exist on the default branch. Therefore the Issue #276 installation PR distinguishes:

- **pre-merge verified enforcement** — deterministic unit/integration/counterfactual tests, the existing trusted Governance Review, existing Merge Readiness, and independent hostile review verify the new enforcement code without executing PR-controlled enforcement code as authority;
- **post-merge active enforcement** — future PRs execute the preflight from the trusted default branch. The bootstrap path must never treat the installing PR's own preflight implementation as already-trusted enforcement.

This bootstrap distinction is a platform constraint, not a waiver of governance.

## Non-authority boundary

The enforcement mechanism has no authority over Hunter analytical behavior, evidence scoring, provider routing, replay, domain persistence, trading, portfolio logic, dashboard behavior, or any domain engine. It validates contribution governance only.
