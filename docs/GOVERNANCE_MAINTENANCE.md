# Owner-Directed Governance Maintenance

Issue #405 adds a second capability to the governed connector write ingress: `governance-maintenance`.

Issue #403 gave the connected assistant an ordinary `feature-branch-write` capability and deliberately closed every governance-sensitive path — `docs/ADR/`, `scripts/`, `.github/`, the canonical policy and registry files. That boundary was correct, but it made owner-authorized governance work depend on a clone-capable external agent being available merely to write the change. This capability removes that dependency without opening the guardrails themselves.

Nothing here weakens any existing control. `.githooks/pre-push` is unchanged and remains the authoritative boundary for clone-capable writers. Hosted CI, `Hunter Governance Review`, substantive independent review, `Hunter Merge Readiness`, and owner merge approval all remain mandatory. There is no auto-ready and no auto-merge.

## Authority

| Concern | Authority |
| --- | --- |
| Grant | `docs/CODE_WRITE_POLICY.json` → `connector_write_ingress` |
| Root-of-trust floor | `docs/CODE_WRITE_POLICY.json` → `connector_write_ingress.root_of_trust_paths` |
| Capability scope | `connector_write_ingress.additional_capabilities` |
| Named scopes | `connector_write_ingress.governance_maintenance_scopes` |
| Per-Issue authorization | `connector_write_ingress.governance_maintenance_authorizations` |
| Write authorization | `scripts/hunter_connector_write_ingress.py` |
| Admission consequence | `scripts/hunter_governance_review_v2.py` → `verify_connector_ingress_authorization` |
| Grant validation | `scripts/hunter_defect_prevention_preflight.py` → `validate_governance_maintenance_capability` |
| Regression tests | `tests/test_governance_maintenance_ingress.py` |
| Defect class | `docs/DEFECT_REGISTRY.json` → `GMI-001` |

The grant is read by the trusted governance controller from the **default branch**, never from the candidate head. Everything below therefore describes authority a candidate can be measured against, not authority a candidate can assert.

## The three-layer scope model

A changed path is decided in this order:

1. **Root of trust.** `root_of_trust_paths` names the files that decide whether a candidate may be written and admitted at all: the push boundary, the hosted workflows, the canonical gate chain's own scripts, the connector authorizer, the governance/admission controllers, the merge-readiness controller, the shared scope and transport primitives, the packaging and tooling pins, and this policy. **No capability on this ingress may write them, and no named scope may unblock them.** This is checked first and applies to every capability, so it is a floor rather than a property of whichever capability remembered to declare it.

2. **Capability scope.** Each capability declares `allowed_paths` (its outer bound) and `prohibited_paths` (its closed default). `governance-maintenance` uses exactly the ordinary capability's prohibited list, so on its own it grants **nothing** beyond `feature-branch-write`. Its outer bound additionally contains `scripts/`, which is entirely prohibited by default; that bound exists so a future owner-authored scope has somewhere to open, not because anything under it is currently writable.

3. **Named scope, authorized per Issue.** `governance_maintenance_scopes` maps a scope name to the paths it unblocks. `governance_maintenance_authorizations` maps a governing Issue to the scope names the owner authorized for it. A prohibited path becomes writable only when a named scope unblocks it **and** the governing Issue holds that scope.

The scopes as shipped:

| Scope | Unblocks |
| --- | --- |
| `adr-lifecycle` | `docs/ADR/`, `docs/architecture-index.md`, `docs/architecture-records/` |
| `defect-registry` | `docs/DEFECT_REGISTRY.json`, `docs/DEFECT_PREVENTION_LIFECYCLE.json` |

The authorizations as shipped:

| Issue | Scopes |
| --- | --- |
| #402 | `adr-lifecycle` |

Two structural invariants make the model safe rather than merely tidy, and both are enforced by the loader and by the deterministic guard:

* no named scope may overlap a root-of-trust path — checked as **entry-to-entry overlap in both directions**, because `docs/` is not itself a root-of-trust entry but would cover `docs/CODE_WRITE_POLICY.json`;
* no named scope may reach outside its capability's `allowed_paths`, so scopes can only re-open what the closed default shut, never invent new territory.

The governing Issue is derived from the **branch name** through the grant's `branch_pattern_template`, exactly as under Issue #403. A receipt cannot claim a different Issue than the branch it sits on, so per-Issue authorization is bound to trusted pull-request evidence rather than to a caller's declaration. The authorized scope names are likewise derived from the trusted manifest, not declared by the request — the write request carries no scope field at all.

## Anti-self-escalation

The requirement is that a candidate must never expand its own capability and then rely on that expansion inside the same pull request. Three mechanisms carry it, and they are independent:

**The root of trust is unreachable through this ingress.** `docs/CODE_WRITE_POLICY.json`, the authorizer, the controllers, and the gate chain are prohibited to every capability and un-unblockable by every scope. A connector candidate simply cannot author a policy change.

**The grant version is pinned at authorization time.** An authorized decision records `grant_fingerprint`, a SHA-256 over every authorization-relevant field of the grant — writers, capabilities, scopes, authorizations, the path lists, and the root-of-trust floor. The trusted controller re-derives that fingerprint from the default branch and requires an exact match. A receipt minted under an older grant, or under a grant a candidate wrote at its own head, is stale and admits nothing.

**A widening head is refused outright.** The controller reads the candidate head's own copy of the policy, parses it with the same rules it applies to the trusted one, and compares. Any *additional* authority — a new capability, a new writer grant, a widened allowed path, a dropped prohibition, a dropped root-of-trust entry, a new or widened scope, a new or widened Issue authorization, or activating a disabled grant — blocks admission of a candidate that is itself writing through the ingress. Narrowing is deliberately not reported: tightening the grant cannot escalate the candidate proposing it.

So a wider grant is still proposed the way everything else is: as a governed pull request, written through the pre-push boundary by a clone-capable writer, independently reviewed and owner-merged. It becomes effective for the *next* candidate, never for its own. **This contribution is itself an instance of that shape** — it adds the `governance-maintenance` capability and authorizes Issue #402, and neither is in force for this pull request.

## Fail-closed conditions

Any one of these rejects the write or leaves the candidate unadmitted:

* missing, unreadable, or structurally invalid grant; a grant whose floor does not cover the gate chain; a scope that unblocks the root of trust or reaches outside its capability; an authorization naming an unknown scope or a non-numeric Issue;
* unauthorized writer; a writer that does not hold the presented capability; a capability the grant does not define;
* a governing Issue with no owner-authored authorization for the capability; a changed path outside the scopes that Issue holds;
* any root-of-trust path in the change set, under any capability;
* a target ref that is `main`, `HEAD`, or not a branch; a branch outside the connector namespace; a branch that does not bind the declared Issue;
* a base other than the authorized base, or a base commit that is not the exact tip resolved from trusted repository state (re-resolved again immediately before the write is applied);
* a receipt whose `authorization_id`, branch, base, fork point, writer, capability, governance scopes, or path set disagrees with trusted evidence;
* a receipt minted under any grant version other than the trusted default-branch one;
* a candidate head whose policy is missing, unparseable, or widens the trusted grant;
* a missing, failed, superseded, or wrong-PR trusted hosted exact-head canonical preflight status.

## Operating the capability

Authorizing a new Issue is an owner edit to the trusted default branch, made through the ordinary governed route:

```json
{ "issue": "412", "scopes": ["adr-lifecycle"], "authorized_by": "repository owner", "authorization": "Issue #412" }
```

Revoking is deleting that entry. Revoking the capability entirely is deleting the `governance-maintenance` writer entry — one grant entry carries one capability, so the two capabilities are revoked independently.

Running the authorizer is unchanged from Issue #403:

```text
python scripts/hunter_connector_write_ingress.py --request write-request.json --emit-receipt .hunter/connector-write-authorization.json
```

The request declares `capability: "governance-maintenance"`; the governing Issue and the branch must agree, and the scopes are filled in from the trusted manifest. Exit `0` authorizes and emits the receipt; exit `1` rejects with the reason. The candidate then stays Draft/unadmitted until the trusted hosted exact-head canonical preflight and the trusted re-derivation both prove it.
