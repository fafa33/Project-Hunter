# Governance Production Cutover

This installs successor governance in non-authoritative shadow mode only. It grants no publication, admission, orchestration, merge-readiness, or merge authority.

## Authority boundary
The active generation is identified by generation, implementation SHA, and policy SHA in a durable transfer record. Candidate code cannot authorize its own transfer. Human production-entry approval and independently authenticated transfer evidence are separate inputs after shadow/replay acceptance.

## Required progression
LAB_VALIDATED -> SHADOW_INSTALLED -> HISTORICAL_REPLAY_VERIFIED -> SHADOW_RUNTIME_VERIFIED -> CUTOVER_CANDIDATE -> LEGACY_AUTHORITY_DISABLED -> NEW_AUTHORITY_ENABLED -> POST_CUTOVER_VERIFIED -> LEGACY_CODE_REMOVABLE -> LEGACY_CODE_REMOVED -> CUTOVER_COMPLETE.

Before NEW_AUTHORITY_ENABLED, successor publication is forbidden. At LEGACY_AUTHORITY_DISABLED neither owner may publish. After enablement, legacy publication is forbidden. Consumers bind the active generation.

## Acceptance
Cutover may be proposed only after Lab campaigns and historical replay pass; runtime shadow parity has no undispositioned divergence; legacy workflows, triggers, writers and consumers have verified fences; restart/replay passes at every seam; stale and delayed publications fail closed; DFF-025 is enforced; rollback proof is current; and explicit human production-entry approval exists.

## Rollback
Fence the successor first. Rollback returns to CUTOVER_CANDIDATE with both owners unable to publish until a newly authorized transfer sequence completes.

## Legacy retirement
scripts/hunter_governance_review/bootstrap_external_review_469.py and other PR-specific authority bridges are retirement targets, never successor dependencies. Remove them only after post-cutover verification.
