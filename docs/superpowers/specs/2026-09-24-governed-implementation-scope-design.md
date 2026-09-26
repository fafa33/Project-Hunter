# Governed Implementation Scope for DPM → SPM

Issue #506 closes the scope-authority gap identified while verifying parent #496.

The canonical `TaskScopeContract` is repository-owned and shared by workflow-state enforcement and engineering prevention selection. Owner-authored Issue scope is accepted only from one explicit `hunter-task-scope-v1` JSON metadata block; free-form Issue/task prose is never parsed to select prevention rules. The trusted Issue trigger validates the contract, binds `task_id` to the derived authorization identity, and signs authorization plus scope in the outer v2 transport while preserving the accepted inner authorization v1 unchanged.

Production execution and issuer edges verify that signature before using the scope. The Smart Prompt Machine additionally requires scope `task_id` to equal the request execution-owner identity. Missing, malformed, tampered, or cross-task scope fails closed before dispatch.

`EngineeringContextAuthority` selects canonical DFF families by intersection between family applicability and the signed allowed scope, excluding prohibited scope. The selected context includes scope coordinates and therefore enters the existing compiled prompt/build/envelope identity; no parallel renderer or identity exists. Caller task prose remains a separate untrusted field and cannot add, remove, or choose DFF families.

Historical replay proof uses DFF-018 for `src/hunter/evidence_intelligence/`; DFF-019 (Railway/scripts) is excluded for that scope. Existing `engineering.review-fix` behavior is unchanged.
