# Historical finding evidence reconciliation — PRs 579, 581, 582

This is a **read-only historical evidence inventory**, not a signed `finding_proven` receipt. Source: GitHub PR review comments, immutable commit diffs and exact-final-head checks, inspected 2026-10-21. The anchored signed knowledge ledger has all eight findings but none has a signed `finding_proven` or `thread_resolved` event. All eight review threads are resolved on GitHub. `historical-coverage --pr 579 --pr 581 --pr 582` reported `examined=8`, `proof_gap=8`.

| PR / original GitHub comment | Remediation diff (commit prefix) | Related regression evidence | Disposition |
|---|---|---|---|
| 579 / 4222875872 | `2fd21e432cc1` changes `src/hunter/automation/issue_agent_authorize.py` and `tests/test_issue_agent_authorize.py` to bind the configured checkout | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` | Historical fix/test association; **no signed finding proof** |
| 579 / 4222896715 | `2fd21e432cc1`, same code and test change | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` | Duplicate root cause, distinct review comment; **no signed finding proof** |
| 579 / 4222896794 | `2fd21e432cc1` adds the authorization-path test; not merely a direct compiler test | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` and `test_prepare_missing_configured_checkout_registry_fails_closed` | Historical test coverage observed; **no signed finding proof** |
| 581 / 4223707384 | `cfa0dd7ebd71` changes `tests/test_hunter_ci_impact.py` to exercise CLI subprocess and failure propagation | `test_cli_focused_executes_and_propagates_failure`, `test_cli_diff_failure_fails_closed`, `test_cli_full_required_never_runs_focused` | Historical regression coverage observed; **no signed finding proof** |
| 581 / 4224365171 | `1196534b30ee` changes `scripts/hunter_pr_preflight.py` expected-RED timing | New focused test `test_expected_red_lane_timing_includes_pytest_duration` added in PR #625; 1 passed | **Historical proof gap remains**: new test validates current behavior, not the original historical run |
| 582 / 4224886962 | `26d0ebe538a5` changes `scripts/hunter_preflight_reuse_decision.py` to guard exact-head race | `test_changed_pr_head_refuses_reuse`, `test_head_changes_during_adjudication_refuses_reuse` | Historical fix/test association; **no signed finding proof** |
| 582 / 4224887008 | `26d0ebe538a5` changes decision logic and `scripts/hunter_governance_review_v2.py` malformed-file handling | `test_malformed_pr_file_listing_refuses_reuse` | Historical fix/test association; **no signed finding proof** |
| 582 / 4225085464 | `8be7836a63e1` changes bootstrap protected-path handling in `scripts/hunter_preflight_reuse_decision.py` | `test_candidate_controlled_bootstrap_forces_trusted_full_gates` (includes `build_backend/project_hunter_build.py`) | Historical fix/test association; **no signed finding proof** |

The focused mapped tests were executed on PR #625's working tree on 2026-10-21: **11 passed** (the parameterized bootstrap test contributes five cases). These are *current regression runs*, not proof that the same tests passed at the historical heads. GitHub confirms final-head `Hunter Pre-PR Preflight`, `Trusted Candidate Preflight Validation`, and `Quality Gates` success on all three PRs, but that is not a finding-specific signed remediation receipt.

| PR | Final historical head | Merge commit |
|---|---|---|
| 579 | `3beac11ee17966e369e5875928c924ed3edae6ef` | `cbfa4280a840c6f289caed7f34df7cca6eae3cc0` |
| 581 | `1196534b30ee1ac3ace1ba3530b77b9691ffdaf8` | `207e28b8074dc3c783529da215d78a54b6d4d78d` |
| 582 | `382032bebcd4c5c04b4940be98c6bd9a9bbb18aa` | `d1130e539592a8e1fa2b2c5b220a2ce3b1ab4914` |

## Live prevention materialization

The authorized `hunter-state/v1/knowledge` ledger was read through the existing `scripts/hunter_issue_agent_lifecycle.py::_knowledge_view` trust path: **39 findings / 39 open prevention overlay entries**. `EngineeringContextAuthority(knowledge_overlay=...)` compiled `ENGINEERING_IMPLEMENT_TASK_KEY` for the scope `scripts/hunter_preflight_reuse_decision.py` and returned three real entries: `KF-21c00be7c3d9`, `KF-539ce65a8ec5`, `KF-8443f516e8ce` (the three findings on PR 582). This is real signed-ledger-to-DPM engineering-context consumption, **not** a hosted autonomous SPM agent run. No signed proof or thread resolution was written, and no family was canonically promoted.

## Outstanding gates

1. Historical `finding_proven` cannot be retroactively signed from a merged PR or resolved thread. Any new evidence-based historical disposition must use an owner-approved, separate typed record with original run/commit/test provenance and an explicit distinction from the exact-head Issue-agent proof contract.
2. PR 581 / 4224365171 now has a dedicated timing regression in PR #625; obtain authenticated historical execution evidence separately rather than backdating the new test.
3. Verify live SPM agent execution consumes the resulting context; confirm bounded coverage of the full eligible historical interval; pass PR #625's required exact-head CI and review gates before owner approval. Do not close Issues #551/#586 yet.

## Historical evidence assessment contract (PR #625 extension)

`historical_evidence_assessed` is a distinct insert-only K_STATE-signed knowledge record, bound to the authenticated original review comment, reviewed head, actual remediation commit, historical final head, merge SHA and successful exact-head hosted Pre-PR check job. The only authorized role is the existing `knowledge-ingest` writer under the existing anchored knowledge ledger CAS and GitHub Action run provenance. Its `historical-regression-gap` disposition explicitly distinguishes a newly introduced current regression from a test run at the historical head. It **never** sets `finding_proven`, `thread_resolved`, family promotion or merge readiness. DPM's original open-finding overlay remains active.

The bounded `historical-assess --pr 579|581|582` command checks GitHub's merged PR identities, live resolution, review inventory, remediation diffs and final-head check job identity before K_STATE signing. It is safe to invoke **only from the trusted default-branch `Hunter / Issue Agent Knowledge` workflow** with the state-signing secret and real Actions run provenance. Before this code is on trusted main, any use of locally supplied credentials or fabricated run identity would fail the signer provenance contract; therefore no production ledger backfill is claimed on this Draft PR. The mapping is an inventory, not permission to impersonate a past validation receipt.

The added `test_signed_historical_assessment_reaches_issue_agent_dpm_without_fabricating_proof` exercises the existing Issue-agent EngineeringContextAuthority using a validated historical assessment folded into its anchored knowledge view; it confirms the finding is still open, the exact-head proof is absent, and the prevention entry reaches the `engineering.implement` task context. This is a *model-free compilation boundary test*, not a hosted signed SPM agent run or executable handoff.

### Trusted GitHub Actions dispatch wiring

The existing `.github/workflows/hunter-issue-agent-knowledge.yml` now routes a manually dispatched `pr=579`, `581` or `582` to `historical-assess`, while the scheduled and `workflow_run` paths still perform ordinary `knowledge-ingest`. All paths retain trusted `main` checkout, the existing protected `hunter-issue-agent-control` environment, signer secret, anchored ledger CAS and run provenance; `checks: read` is explicitly granted for historical exact-head check metadata. Invalid PR input fails closed. Run one dispatch per PR **only after** the reviewed code is merged with explicit owner approval, and verify the signed ledger report afterward. No dispatch has been performed while this change is only on a Draft PR.
