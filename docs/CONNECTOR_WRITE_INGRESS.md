# Governed Connector Code-Write Ingress

Issue #403 authorizes one narrow repository-owned path that lets an explicitly authorized connector writer (the owner's connected ChatGPT assistant, or an equally narrow trusted connector capability) create code changes on a non-`main` feature branch without a local clone.

This is an **additional** ingress. `.githooks/pre-push` remains the authoritative boundary for clone-capable writers and is unchanged by this capability.

## Authority

| Concern | Authority |
| --- | --- |
| Grant | `docs/CODE_WRITE_POLICY.json` → `connector_write_ingress` |
| Write authorization | `scripts/hunter_connector_write_ingress.py` |
| Admission consequence | `scripts/hunter_governance_review_v2.py` → `verify_code_write_ingress_provenance` |
| Grant validation | `scripts/hunter_defect_prevention_preflight.py` → `validate_connector_write_ingress` |
| Regression tests | `tests/test_connector_write_ingress.py` |
| Defect class | `docs/DEFECT_REGISTRY.json` → `CWI-001` |

The governance controller checks out the default branch, so the grant is always read from trusted state. A candidate cannot grant itself ingress, widen its own writer allowlist, or move its own scope.

## Write authorization

A connector write is authorized only when every one of the following holds. Any missing, malformed, or unreadable input rejects the write.

1. The grant loads, declares `enabled: true`, and binds at least one writer identity.
2. The writer login is on the owner-bound allowlist and presents the exact granted capability (`feature-branch-write`).
3. The base ref is the authorized base (`main`).
4. The target is a branch ref that is neither the base branch nor a forbidden ref, so a direct `main` write is unrepresentable.
5. The request declares exactly one governing Issue number, and the target branch name binds that Issue (`branch_pattern_template`, which must contain `{issue}`).
6. The declared base commit is a full SHA and is exactly the observed base tip. A stale base is rejected at write time rather than surfacing later as a conflict.
7. Every changed path is repository-relative, outside `prohibited_paths`, and inside `allowed_paths`. The hooks, workflows, scripts, and policy files that bind the ingress are prohibited, so the ingress cannot be used to widen itself.

Path scope uses the same matcher as the workflow-state scope gate (`hunter_workflow_state.path_matches_scope_entry`); two implementations of "is this path in scope" would be two different answers.

Run it directly:

```text
python scripts/hunter_connector_write_ingress.py --request write-request.json
```

Exit `0` authorizes the write, exit `1` rejects it with the reason.

## Admission is separate, and stricter

Write authorization is never pre-push proof and never admission.

* The grant may not declare `local_pre_push_equivalent: true`; the loader and the deterministic guard both refuse such a policy.
* Connector writer logins must be disjoint from `ingress_provenance.authorized_signers`. A shared identity would let a connector write be counted as clone-capable pre-push proof, so it blocks admission outright.
* Every commit in the range still needs a verified signature. A connector-written commit that clears that check does **not** satisfy pre-push ingress on its own.
* A range containing connector-written commits is admitted only when the trusted hosted exact-head canonical preflight status (`Hunter Trusted Preflight Upgrade / PR #<n>`) succeeds for that exact head and that exact PR — **in addition to** the existing exact-head branch preflight. That status is published by `.github/workflows/hunter-trusted-preflight-upgrade.yml`, whose trusted controller runs the canonical gate chain against the exact candidate SHA.
* Until that proof exists, the candidate stays Draft/unadmitted. Proof published against a superseded head, or against a different PR, is not this head's proof.

Hosted CI, `Hunter Governance Review`, independent review, `Hunter Merge Readiness`, and owner merge approval all remain mandatory and unchanged. Nothing here enables automatic merge or automatic Ready promotion.

## Activation

The grant ships **declared but not activated**: `enabled` is `false` and the writer entry carries an empty `login`, so the ingress authorizes nothing and controller behaviour is unchanged.

Activation is one owner action, in trusted default-branch state:

1. Set `connector_write_ingress.authorized_writers[].login` to the verified GitHub account or app login the connector actually authenticates as. It must not be a login listed in `ingress_provenance.authorized_signers`.
2. Set `connector_write_ingress.enabled` to `true`.

The login is deliberately left unbound rather than guessed: binding an unverified name into a trusted allowlist would authorize whichever account happens to hold that name. `validate_connector_write_ingress` rejects an enabled grant that binds no writer identity, so a half-finished activation fails closed.
