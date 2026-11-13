# Issue #586: standing pre-deployment review policy proposal

Status: **PROPOSED; NOT DEPLOYED; OWNER APPROVAL REQUIRED.** This document and
the adjacent patch grant no authority. They are a reviewable deployment package,
not a temporary PR #738 exception or a claim of Issue acceptance.

## Verified authority and deployment boundary

The inspected trusted main is `08f565fd14e9ded796a63c9fc45dce6f8997aeff`.
Its `docs/CODE_WRITE_POLICY.json` blob is
`9a9e4425769fe471289d39047457598b043f92d7`; `coverage_scopes` is empty and
`automatic_predeployment_review` is absent. Main also lacks the request-only
pending-deployment verifier implemented in PR #738. Installing the policy field
alone cannot enable that verifier.

`load_coverage_scopes` implements `bounded_correction`: a trusted owner grant
names one governing Issue and its allowed criterion subset, optionally one head.
It is useful for an explicitly bounded correction but does not itself define a
reusable rule for honest pending deployment evidence. This proposal leaves it
unchanged and uses the request-only rule already implemented in the same
verifier. Full governing-Issue criterion coverage remains required.

The current Protect main ruleset (20523851) requires PRs, signed commits,
required checks, thread resolution and CodeQL; it has no bypass actors. The
code-write policy classifies the verifier and policy as root-of-trust paths.
Its declared write channel for those paths is clone-capable signed contribution,
repository pre-push enforcement, independent review and owner-controlled merge.
No inspected workflow can deploy arbitrary source policy directly to main.
Automatic Canonicalization writes knowledge candidates through a Draft PR; it
does not install source policy or merge. State-ledger append authority cannot
write source policy either.

## Exact proposed policy change

[`ISSUE_586_PERMANENT_REVIEW_POLICY.patch`](ISSUE_586_PERMANENT_REVIEW_POLICY.patch)
applies to the inspected trusted-main policy and adds only
`review_progression.review_authority.automatic_predeployment_review`.
It leaves `coverage_scopes`, reviewer ordering, timeouts, acceptance requirements,
code-write grants and final proof/outcome authority unchanged. The `authorized_by`
and `authorization` fields describe the proposed standing grant; they do not
assert that its deployment has been approved.

The grant authorizes **review only**, for any current or future same-repository
PR satisfying a registered deployment dependency. It contains no PR number,
branch name or one-head exception. Normal complete requests keep their existing
automatic path. The initial dependency catalog names Issue #586's three pending
criteria and three literal affected paths; it does not authorize unrelated
Issues or arbitrary pending acceptance. Future Issues with a different proven
deployment dependency require a reviewed catalog entry, rather than a per-PR
approval. The engine is generic; the initial catalog is deliberately bounded.

The producer is the existing main-only knowledge workflow. Its inspected
SHA-256 is `d4a5ff2e2cc3ae19f5c7c31b8f94407470562289a132eee504438f72e40b7b10`.
A producer change invalidates this grant until its dependency binding is reviewed
and updated; never silently recompute that digest to admit a candidate. This
includes the eventual merge of PR #738, which changes that workflow.

## Security and lifecycle contract

- Only the trusted default-branch checkout supplies policy and producer bytes.
  Candidate `review_eligibility` is a request; candidate policy fields are ignored.
- Live evidence binds repository ID and name, same-repository head, PR number,
  governing Issue and its criteria, exact head, diff merge-base and default base
  ref. HEAD and base-tip races are checked again before dispatch eligibility.
- Existing exact-head trusted preflight and writer-ingress provenance must pass.
  Listed security STOP-WORK Issues must be closed by the policy's owner authority;
  Issue #603 was verified closed by `fafa33`. Open or unverifiable closure denies.
- The closed policy schema allows no command, credential, runner, endpoint,
  wildcard path, candidate execution privilege or reviewer implementation.
- Every pending criterion must be genuine governing-Issue text and explicitly
  registered as deployment-dependent. Invalid or stale request content and
  unresolved blocking findings remain rejected.
- `predeployment-review-eligible` has `ok == false`: it permits only the existing
  read-only independent review dispatch. Final `verify_claims` still rejects
  pending deployment acceptance. No production, security, merge or Issue-closure
  approval is created.
- The existing orchestrator owns correlation and duplicate suppression. Its
  existing ordered reviewer pool and finite budgets own actual invocation,
  authenticated response and exhaustion. No unavailability is inferred without
  an eligible invocation. No second orchestrator or Railway dependency is added.
- Canonical Proof Outcome Authority, code/security requirements and owner merge
  approval remain unchanged. A successful workflow or this proposal is not proof
  of deployed authority or a completed review.

## Verification and security finding

The existing eligibility regressions cover ordinary requests, deployment-pending
requests, another Issue/PR identity, invalid criteria, missing Issue, content
drift, HEAD/base races, foreign repositories, candidate-policy substitution,
missing trusted policy and open STOP-WORK. Existing orchestrator/collector tests
cover one invocation, duplicate correlation, stale requests, authenticated
unavailability and terminal exhaustion. No live reviewer invocation is claimed.

The proposal tests apply the real policy patch to an isolated copy of the pinned
main policy and producer. One standing grant admits simulated PRs #738 and #9001;
the candidate-side copy alone cannot admit either, missing STOP-WORK closure
denies both, and pending acceptance never becomes a valid final review.

CodeQL alert #14 is `py/clear-text-logging-sensitive-data`, High, at
`scripts/hunter_github_transport.py:231-232`. Exact-head analysis 2157836010 for
`68500e4` traces the new STOP-WORK Issue ID through the REST path into retry logs.
Its source is a public policy value assigned to `trusted_review_policy`;
CodeQL's sensitive-name heuristic includes `trusted`. The value is checked to be
an exact positive integer. Main analysis 2154801703 instead traces public commit
metadata through `trusted_tip`/`trusted_revision`. Neither trace establishes an
Authorization-header or token leak, and the new policy is absent on main, so
that lookup is currently inactive. The alert is nevertheless open on `68500e4`.

The PR correction keeps policy-derived endpoint values out of retry diagnostics
by passing a static operation label through the existing transport. A focused
regression exercises a real retry and confirms unchanged endpoint delivery,
bounded retry behavior, visible operation context and absence of endpoint/token
values in output. No query suppression, alert dismissal, sensitive-variable
rename, shared transport refactor or security threshold change is used. Only a
fresh exact-head CodeQL result can establish its hosted disposition. General raw
transport-error logging is owned by the shared GitHub governance transport and
is not falsely reported as a credential leak proved by this alert.

## Exact approval needed and activation procedure

**Approve one permanent trusted-main bootstrap contribution through the normal
protected-branch process**, containing the generic request-only verifier in
`scripts/hunter_pre_ready_review.py`, its trusted consumer and static diagnostic
label in `scripts/hunter_governance_review_v2.py`, their focused eligibility tests,
and the adjacent exact policy patch. These are isolated from PR #738's knowledge
classification/replay implementation. The contribution needs independent review,
signed provenance and the normal gates before a separate explicit owner merge
approval. This is authorization to prepare that minimal bootstrap PR, not to
merge PR #738 or bypass main protection. No such new PR or merge is authorized
or performed by this document.

There is no compliant in-place deployment under the current no-new-PR/no-merge
instruction: a policy-only content update would both violate protected-main
authority and leave the missing trusted verifier uninstalled. Do not execute a
candidate-ref privileged collector to work around that boundary.

After authorized deployment, verify the actual main SHA, deployed verifier/policy
blobs and producer digest, owner closure, and all required security/proof facts.
Then use the existing trusted-main `hunter-governance-reconcile.yml` recovery path
once; let it bind the current PR head and dispatch the existing collector.
Record the authenticated run/attempt, reviewer request and response. Do not
dispatch the collector directly or repeat review while the same valid cycle is
active. Remediate actual findings in PR #738 and refresh its exact-content request
through the authorized signed writer if content changes.

Issue #586 remains open. Actual production #737 replay, signed receipts and
classifications, truthful F1/F3 dispositions and hosted DPM/SPM consumption remain
outstanding. This policy does not authorize replay, duplicate the F4 reply,
manufacture corrections or declare those acceptance requirements satisfied.
