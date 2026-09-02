# Governed Connector Code-Write Ingress

Issue #403 authorizes one narrow repository-owned path that lets an explicitly authorized connector writer (the owner's connected ChatGPT assistant, or an equally narrow trusted connector capability) create code changes on a non-`main` feature branch without a local clone.

This is an **additional** ingress. `.githooks/pre-push` remains the authoritative boundary for clone-capable writers and is unchanged by this capability.

Issue #405 later added a **second capability on this same ingress**, `governance-maintenance`, for owner-authorized ADR lifecycle and explicitly scoped governance work. Everything on this page continues to describe the ordinary `feature-branch-write` capability unchanged; see `docs/GOVERNANCE_MAINTENANCE.md` for the added capability, the root-of-trust floor beneath both, and the anti-self-escalation rules.

## Authority

| Concern | Authority |
| --- | --- |
| Grant | `docs/CODE_WRITE_POLICY.json` → `connector_write_ingress` |
| Write authorization | `scripts/hunter_connector_write_ingress.py` |
| Admission consequence | `scripts/hunter_governance_review_v2.py` → `verify_code_write_ingress_provenance` |
| Grant validation | `scripts/hunter_defect_prevention_preflight.py` → `validate_connector_write_ingress` |
| Regression tests | `tests/test_connector_write_ingress.py` |
| Defect class | `docs/DEFECT_REGISTRY.json` → `CWI-001`, `CAC-001` |
| Added capability (#405) | `docs/GOVERNANCE_MAINTENANCE.md` |

The governance controller checks out the default branch, so the grant is always read from trusted state. A candidate cannot grant itself ingress, widen its own writer allowlist, or move its own scope.

## Write authorization

A connector write is authorized only when every one of the following holds. Any missing, malformed, or unreadable input rejects the write.

1. The grant loads, declares `enabled: true`, and binds at least one writer identity.
2. The writer login is on the owner-bound allowlist and presents the exact granted capability (`feature-branch-write`).
3. The base ref is the authorized base (`main`).
4. The target is a branch ref inside the connector namespace (`branch_namespace`, `connector/`) that is neither the base branch nor a forbidden ref, so a direct `main` write is unrepresentable.
5. The request declares exactly one governing Issue number, and the target branch name binds that Issue (`branch_pattern_template`, which must contain `{issue}` and live inside the namespace).
6. The declared base commit is a full SHA and equals the current base tip **resolved from trusted repository state** — see below.
7. Every changed path is repository-relative, outside `prohibited_paths`, and inside `allowed_paths`. The hooks, workflows, scripts, and policy files that bind the ingress are prohibited, so the ingress cannot be used to widen itself. The request may not declare `.hunter/` ingress state as content.

Path scope uses the same matcher as the workflow-state scope gate (`hunter_workflow_state.path_matches_scope_entry`); two implementations of "is this path in scope" would be two different answers, and admission re-uses the same `check_scope` the authorizer used.

### The base tip is never the caller's to assert

The request carries **no field describing the current base tip**. `git_base_tip` resolves it from the remote ref at authorization time, so a caller working from a stale checkout cannot submit a self-consistent stale pair and certify its own staleness. A request that still carries the old `observed_base_tip_sha` field is refused outright as an unknown field, rather than silently having its stale-base check skipped.

`confirm_base_unchanged` re-resolves the tip immediately before the write is applied and refuses an authorization minted against an older tip, closing the window between authorization and application.

Run it directly:

```text
python scripts/hunter_connector_write_ingress.py --request write-request.json --emit-receipt .hunter/connector-write-authorization.json
```

Exit `0` authorizes the write and emits the receipt, exit `1` rejects it with the reason.

## The authorization receipt binds admission to the actual decision

A verified signature from an allowlisted account proves *who* wrote a commit. It does **not** prove the write ever crossed this authorizer — the same credential could push a signed commit touching `pyproject.toml` or a workflow. So an authorized decision mints a `ConnectorWriteAuthorization`: the canonical claim set (writer, capability, Issue, base ref, base commit, target branch, changed paths) with an `authorization_id` that is a SHA-256 over exactly those claims. The writer commits it to the candidate head as `.hunter/connector-write-authorization.json`.

The receipt is candidate-authored content, so the trusted controller treats it as a **declaration to be checked, never as evidence**. `verify_connector_ingress_authorization` re-derives every claim from trusted repository and pull-request evidence:

| Claim | Checked against |
| --- | --- |
| target branch | the pull request's trusted `head.ref` |
| base ref | the pull request's trusted `base.ref`, which must be the granted base |
| governing Issue | derived from the trusted branch name via the grant's pattern, not from the receipt |
| writer / capability | the grant, plus the committer login of **every** attested commit in the range |
| base commit | the trusted merge base (fork point) of the candidate with the base branch |
| changed paths | the trusted pull-request file list, re-scoped with the grant's allowed/prohibited paths |
| self-consistency | `authorization_id` recomputed over the claims |

Consequences, all fail-closed:

* A branch inside the connector namespace with **no** receipt fails admission, however green the hosted statuses are.
* A receipt on a branch **outside** the namespace fails admission.
* A receipt that understates the files actually changed fails: scope is re-evaluated on the trusted file list, not the declared one.
* A receipt minted for another branch, another Issue, or another fork point fails.
* A stale receipt reused on a newer head fails, because the newer head's trusted file list no longer matches the authorized path set.

## Admission is separate, and stricter

Write authorization is never pre-push proof and never admission.

### Why the channels are separated by evidence, not identity

The connected assistant authenticates its GitHub session as the **repository owner account**, which also appears in `ingress_provenance.authorized_signers`. Committer login therefore cannot say which channel wrote a commit, and this is not a gap the ingress introduced: a Contents API write authenticated as an authorized signer already produced a verified signature under the pre-existing provenance model.

Requiring the connector identity to be disjoint from the clone-capable signer set would not fix that. It would only make the grant **unbindable** — there is no separate account for the connector to write as — while leaving the underlying indistinguishability untouched.

So the safety property is kept, and moved onto evidence that no writer can mint:

> While the grant is active, a verified signature from an authorized signer is **not sufficient on its own for any candidate**. Every code-changing range additionally requires the trusted hosted exact-head canonical preflight proof.

That proof (`Hunter Trusted Preflight Upgrade / PR #<n>`) is published by `.github/workflows/hunter-trusted-preflight-upgrade.yml`, whose trusted default-branch controller runs the canonical gate chain against the exact candidate SHA. Admission accepts it only when GitHub records `github-actions[bot]` as publisher and its canonical target run is that exact trusted workflow, PR, head, and successful conclusion; a push-capable user's same-context status is not proof. The requirement applies to clone-written ranges too, precisely because identity cannot be trusted to sort them.

This is strictly stronger than signature-only admission and costs no extra hosted work: that workflow already runs on every pull request to `main`. Overlapping logins are consequently allowed; an **active** grant that fails to declare `hosted_admission.require_for_all_candidates: true` is refused by both the loader and the deterministic guard, so the requirement cannot be quietly dropped.

### The channel decides which proof is required (Issue #409)

The connector API creates commits **server-side**: there is no local key and no pre-push boundary to sign at, so a connector-written commit can never carry a verified local signature. Requiring one made the authorized path unusable — Issue #402 / PR #408 was blocked as `unsigned` despite being a correctly authorized `connector/issue-402-*` candidate carrying its receipt.

So admission decides *which channel wrote the candidate* first, from trusted evidence, and only then requires that channel's proof:

| Channel | How it is established | Ingress proof required |
| --- | --- | --- |
| ordinary (clone-capable) | no receipt, outside the connector namespace | verified commit signature from an authorized signer, over the whole range — unchanged |
| connector-origin | the full trusted re-derivation below concludes it | the exact-head receipt re-derived from trusted evidence, one authorized writer across the whole range, and the trusted hosted exact-head canonical preflight proof |

The connector channel's evidence is bound two ways that the first form of this split lacked (both found by independent review of PR #410):

* **Writer identity is authenticated at every connector commit's push boundary.** On an unsigned API-created commit, `committer.login` is GitHub's resolution of caller-supplied committer metadata, so it cannot authenticate the credential that wrote the commit. PR authorship is also insufficient because another credential can later push the branch. The channel reads the trusted push-event `Hunter / Pre-PR Preflight` workflow run for every commit SHA in the connector range on the exact PR head branch and requires each GitHub-authenticated actor to equal the receipt writer. A commit without that evidence fails closed. The committer check remains only a narrowing consistency check; it is never identity proof.
* **The receipt binds the exact file transition, not just the path set.** It carries canonical `changes` records. Additions and modifications bind the resulting git blob SHA; deletions bind absence instead of reusing GitHub's base-blob SHA; renames bind both `previous_path` and destination plus the exact destination blob. Admission preserves GitHub's `status` and `previous_filename` evidence and compares the complete transition set at the exact head. Same-path mutation, deletion, rename, or rename-plus-modification therefore requires fresh authorization, and a protected source cannot be renamed into an allowed destination.

Three further properties keep this a split rather than a relaxation:

* **Connector origin is a conclusion, never a claim.** It is set only by `verify_connector_ingress_authorization` succeeding on every re-derived constraint. A signature cannot establish it and cannot substitute for any part of it, so a clone-capable writer cannot reach the connector regime by asserting it — and reaching it legitimately means accepting *more* constraints, not fewer.
* **Unsigned committer metadata can only narrow admission.** Every connector commit is authenticated by its trusted push-workflow actor first. GitHub's commit listing is then checked for consistency with that actor, so missing or foreign metadata still blocks but forged authorized metadata cannot conceal a foreign ancestor writer.
* **Connector evidence is never credited as local pre-push proof.** The verdict says so explicitly, the grant still declares `local_pre_push_equivalent: false`, and the trusted hosted exact-head proof — which no writer on either channel can mint — is required unconditionally for a connector-origin range.

An inactive grant, a writer not bound by the trusted grant, or a missing/failed/wrong-PR hosted proof all block a connector-origin candidate.

### The rest of the admission contract

* The grant may not declare `local_pre_push_equivalent: true`; the loader and the deterministic guard both refuse such a policy.
* Every ordinary clone-path commit in the range still needs a verified signature from an authorized ingress writer.
* A connector-namespace candidate additionally needs its exact-head authorization re-derived from trusted evidence, as above.
* The trusted hosted proof is required **in addition to** the existing exact-head branch preflight, not instead of it.
* Until that proof exists, the candidate stays Draft/unadmitted. Proof published against a superseded head, or against a different PR, is not this head's proof.
* Deactivating the grant (`enabled: false`) restores signature-only admission for clone-capable writers.

Hosted CI, `Hunter Governance Review`, independent review, `Hunter Merge Readiness`, and owner merge approval all remain mandatory and unchanged. Nothing here enables automatic merge or automatic Ready promotion.

## Activation

The grant ships **active and bound**: `enabled` is `true` and the writer entry binds the owner account the connector authenticates as. No follow-up policy edit is required, so the connector can perform a compliant write as soon as this change merges — a grant that shipped inert would have required another clone-capable bootstrap PR just to switch it on, which is the manual relay Issue #403 exists to remove.

The bound login is evidence-based, not guessed: the repository's collaborator listing shows exactly one account with write access (`fafa33`, admin). `chatgpt-codex-connector[bot]` is observed on this repository as a pull-request *review* identity only and is deliberately **not** granted write; if it is ever observed producing commits, bind it explicitly rather than inferring the grant. Binding is low-stakes by design — identity authorizes nothing on its own, since every connector candidate must still carry a re-derived exact-head authorization.

To deactivate, set `connector_write_ingress.enabled` to `false` in trusted default-branch state. `validate_connector_write_ingress` rejects an active grant that binds no writer identity, or that drops the hosted-proof requirement, so a half-finished edit fails closed rather than silently loosening admission.
