# Governed engineering Issue queue (#437)

Read-only queue selection is not authorization. The existing signed Issue-agent admission and #436 GovernedEngineeringTaskIngress remain the execution authorities.

An authenticated repository owner can author exactly one GitHub Issue comment with this exact prefix and JSON payload:

```text
hunter-engineering-queue-v1:{"engineering":true,"owner_approved":true,"ready":true,"priority":"P1","order":10,"blocked_by":[436]}
```

All fields are mandatory. Missing policy is ineligible. Multiple owner policies, malformed metadata, unknown priorities, unresolved/missing blockers and dependency cycles fail closed. To update policy, edit the existing owner comment. Other authors' comments and Issue body text do not grant authority.

Ordering: P0, P1, P2, P3; then ascending owner order; then ascending Issue number. An active Issue-agent workflow or open issue-numbered PR blocks another selection. A closed blocker is fetched and verified. Owner override only changes ordering, not eligibility.

Read-only live evaluation:

```sh
python3 scripts/hunter_engineering_issue_queue.py --repository fafa33/Project-Hunter
```

Optional --owner-override NUMBER is auditable. Output includes policy version, digest, and dispatch=false. No-selection is a valid outcome. The pure handoff_selected_issue adapter revalidates the trusted snapshot and delegates the verified Issue identity to the existing canonical ingress; it does not compile or dispatch itself.

## Existing signed lifecycle handoff (Issue #437 follow-up)

After the repository owner explicitly approves an Issue via its owner-authored queue policy comment and the Issue has a valid `hunter-task-scope-v1`, the owner can invoke:

```sh
python3 scripts/hunter_engineering_issue_queue.py --repository fafa33/Project-Hunter --execute
```

This reselects against fresh GitHub evidence, checks authenticated owner identity, validates canonical Issue scope, refuses duplicate execution labels, and reselects once more before emitting the **existing** `hunter-agent-execute` label. The existing `issues:labeled` workflow, not the queue, remains the only signed authorization, atomic admission, SPM compilation and execution authority. This CLI never directly dispatches an Actions workflow or creates a PR. No selection returns `NO_SELECTION` without mutation. A concurrent change between the final selection and label event is still independently guarded by the existing signed issuer and lifecycle admission; queue checks alone are not atomic authorization.

This handoff is an owner-invoked execution seam, not an unattended scheduler. End-to-end hosted execution requires a separately owner-approved eligible Issue and successful exact-head workflow evidence. Do not infer autonomous readiness from unit tests or label emission alone.
