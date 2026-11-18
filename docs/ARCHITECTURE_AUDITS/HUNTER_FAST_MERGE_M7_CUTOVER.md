# Hunter Fast Merge M7 Cutover Evidence

Issue: #588

M7 cutover is owner-approved and live on the `Protect main` repository ruleset.

Post-cutover required merge checks are:
- `Quality Gates`
- `dependency-review`
- `CodeQL`
- `Hunter Merge Readiness`

`Hunter Pre-PR Preflight` remains an internal exact-head proof producer but is no longer a direct merge gate. `Hunter Governance Review` remains compatibility/advisory evidence and is not a required status check. No bypass actors are configured.

The cutover is based on the real PR #757 shadow observation for exact HEAD `9a143b35c9226235778d6cbd9feeed13e0f6e760`, which recorded `legacy_state=success`, `successor_state=success`, `parity=true`, `false_ready=false`, and `missed_blocker=false`. Rollback remains fail-closed: restore the pre-cutover ruleset snapshot if post-cutover verification detects a regression.
