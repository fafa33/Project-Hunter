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
