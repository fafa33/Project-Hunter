# Historical finding evidence reconciliation — PRs 579, 581, 582

This is a **read-only historical evidence inventory**, not a signed `finding_proven` receipt. Source: GitHub PR review comments, immutable commit diffs and exact-final-head checks, inspected 2026-10-21. The anchored signed knowledge ledger has all eight findings but none has a signed `finding_proven` or `thread_resolved` event. All eight review threads are resolved on GitHub. `historical-coverage --pr 579 --pr 581 --pr 582` reported `examined=8`, `proof_gap=8`.

| PR / original GitHub comment | Remediation diff (commit prefix) | Related regression evidence | Disposition |
|---|---|---|---|
| 579 / 4222875872 | `2fd21e432cc1` changes `src/hunter/automation/issue_agent_authorize.py` and `tests/test_issue_agent_authorize.py` to bind the configured checkout | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` | Historical fix/test association; **no signed finding proof** |
| 579 / 4222896715 | `2fd21e432cc1`, same code and test change | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` | Duplicate root cause, distinct review comment; **no signed finding proof** |
| 579 / 4222896794 | `2fd21e432cc1` adds the authorization-path test; not merely a direct compiler test | `test_prepare_reads_configured_checkout_registry_not_ambient_cwd` and `test_prepare_missing_configured_checkout_registry_fails_closed` | Historical test coverage observed; **no signed finding proof** |
| 581 / 4223707384 | `cfa0dd7ebd71` changes `tests/test_hunter_ci_impact.py` to exercise CLI subprocess and failure propagation | `test_cli_focused_executes_and_propagates_failure`, `test_cli_diff_failure_fails_closed`, `test_cli_full_required_never_runs_focused` | Historical regression coverage observed; **no signed finding proof** |
| 581 / 4224365171 | `1196534b30ee` changes `scripts/hunter_pr_preflight.py` expected-RED timing | No dedicated expected-RED lane-timing test established by targeted search | **Evidence gap**: fix diff exists, focused regression proof missing |
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
2. PR 581 / 4224365171 requires a dedicated timing regression if no adequate existing one is located.
3. Verify live SPM agent execution consumes the resulting context; confirm bounded coverage of the full eligible historical interval; pass PR #625's required exact-head CI and review gates before owner approval. Do not close Issues #551/#586 yet.
