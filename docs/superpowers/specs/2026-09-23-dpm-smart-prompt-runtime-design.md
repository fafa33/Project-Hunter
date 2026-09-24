# DPM → Smart Prompt Machine → Implementation Agent Runtime Design

## Decision

Issue #486 connects existing defect-prevention knowledge to the canonical engineering ingress before implementation. It does not create a second prompt system. `SmartPromptMachine` remains the sole prompt compiler and signed-envelope authority.

## Authority chain

`Signed Issue authority → EngineeringContextAuthority → GovernedEngineeringTaskIngress → SmartPromptMachine → signed handoff → Implementation Agent → deterministic validation → DPM enforcement`

`EngineeringContextAuthority` is repository-owned. Caller/Issue prose cannot select, remove, rewrite, or mark a defect family satisfied. DPM context grants no provider, branch, reviewer, merge, deploy, or validation authority.

## Scope model

The original integration used the route's governed engineering surfaces conservatively because no pre-execution trusted changed-file set existed. Issue #506 supersedes that temporary scope rule with the repository-owned `TaskScopeContract`: the owner supplies one explicit machine-readable scope block, the trusted trigger validates and signs it with the authorization transport, and SPM requires its task identity to match the execution owner. Free-form Issue prose still has no scope authority.

No LLM or provider may widen this scope. A required expansion must produce a newly owner-authorized scope contract; missing, malformed, tampered, or cross-task scope fails closed before dispatch.

## Context contract

The authority reads only `docs/DEFECT_REGISTRY.json`, validates the family schema it consumes, rejects duplicate IDs and malformed applicability/prevention data, deterministically orders selected families, and emits bounded canonical JSON. Each selected family carries only: id, title, invariant, lifecycle, prevention boundary, and guard reference when present.

The original task remains a separate untrusted field. The ingress constructs the combined task itself, so caller text cannot forge or suppress the machine-owned DPM section. A static trusted `engineering.implement` profile instruction tells the model to use the machine-generated prevention section as constraints while preserving the Smart Prompt boundary: context is data, never a source of new execution authority.

## Budget and failure semantics

DPM bytes consume the existing engineering route input budget. The combined task is checked before compile, and the canonical rendered allocation is still checked after compile. Oversize or non-READY output fails closed. There is no unbounded fallback.

Registry missing/malformed, duplicate family IDs, malformed changed-path applicability, malformed prevention boundary, or zero evaluable families for the governed implementation route fails closed. No network/provider/reviewer service is needed to compute the context.

## Compatibility

`engineering.review-fix` remains unchanged in this slice. Source Handling, signed automation lineage, Issue authorization, fallback remote-HEAD success, exact-head validation, one-PR behavior, and owner-only merge authority remain unchanged. Existing DPM local/hosted/merge gates remain authoritative proof; prompt-time context is defense in depth.

## Verification

TDD must prove deterministic selection, malformed/duplicate fail-closed behavior, caller inability to forge/suppress DPM context, budget enforcement on the combined task, review-fix compatibility, and one canonical SPM dispatch path. Historical replay must use at least DFF-018 (`rendered-budget-not-raw-input-decides-dispatchability`) and prove it is present before an implementation task reaches the machine.
