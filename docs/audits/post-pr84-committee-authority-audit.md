# Post-PR84 Committee Authority Audit

## Verdict

**BLOCKED**

The original `APPROVED` verdict merged in PR #85 was incorrect and is superseded by this correction.

## Remaining blocker

The final contract requires the installed-CLI success-path E2E test to load the resulting pipeline run, committee votes, assessments, and champion from generic SQL.

At the audited PR #84 state, the success-path test loaded assessments, champion snapshots, and pipeline runs, but did not load or assert persisted committee votes. A regression in successful CLI vote persistence could therefore pass the audit.

## Audited repository state

- Canonical PR #84 merge commit: `3db1052a1daf7aec320b30f6f48658c2cf61c45c`
- Merged audit PR #85 commit: `4651b57882106e8901bfad1f188967d24897540f`
- Audited implementation HEAD: `f969924b4a55ea333caa820be1ddacc57ed7e981`
- Final-head CI run for PR #84: #285 (`29996266163`)

## Verified findings retained

### Installed CLI and canonical persistence

Verified.

The E2E test resolves the installed `hunter` console script from `PATH`, executes `hunter committee-authority` from an unrelated working directory, verifies the canonical database path, and proves that no standalone committee database or wrong-working-directory database is created.

### Persisted ranking and exact Dashboard projection

Verified.

The two-candidate cycle persists contiguous ranks `(1, 2)`. Dashboard rows match exact persisted assessment identity, project ID, decision, confidence, rank, and source-record IDs. Record counts remain unchanged across Dashboard construction.

### Post-evaluation persistence failure and atomic rollback

Verified.

The forced failure occurs after real output staging and proves rollback of votes, assessments, champion, and success state while preserving one deterministic failed run with exact fingerprints and the original error.

### Cross-candidate vote identity

Verified.

Committee vote identity includes `project_id`, preventing collisions between candidates with otherwise identical vote payloads.

### Quality gates

Verified on implementation HEAD `f969924b4a55ea333caa820be1ddacc57ed7e981` in CI run #285:

- `ruff check .` — passed
- `black --check .` — passed
- `mypy` — passed
- `pytest` — passed

## Required closure sequence

1. Add an installed-CLI success-path E2E assertion that loads persisted committee votes from generic SQL.
2. Prove vote IDs are unique and every vote is linked to the correct persisted assessment and project.
3. Merge the implementation only after Ruff, Black, mypy, and pytest pass on the same exact HEAD.
4. Run a new independent post-merge audit.
5. Close Issue #61 only if that later audit returns `APPROVED`.
