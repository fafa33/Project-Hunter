# Issue #181 — `hunter comparative-valuation-authority` production entry point — Implementation Report

## Status

This report documents a narrow, operator-facing production entry point for the
already-implemented Canonical Comparative Valuation foundation (ADR 0026;
`src/hunter/comparative_valuation/`, merged via PR #161 and stabilized by Issue
#166/PRs #167-169). It mirrors `hunter valuation-authority`
(`src/hunter/valuation_authority/command.py`, Issue #107) exactly. It does not
implement Mispricing or Asymmetry entry points, does not activate Market Validation
composition, and does not close Issue #173/ADR 0027 (which remains `Proposed`).

## Why this change

Repository audit of `main` at `13d52d1` (post-Rule-22 hostile-review-gate merge, PR
#180) found:

- The valuation-family chain (`Valuation -> Comparative Valuation -> Mispricing ->
  Asymmetry`) is fully implemented and stabilized (Issue #166, closed).
- ADR 0027 (Canonical Market Validation Composition Authority) remains `Proposed`;
  implementation of Market Validation composition/activation is not authorized and
  is out of scope for any Runtime work right now.
- `src/hunter/comparative_valuation/__init__.py`'s own module docstring stated the
  foundation "does not wire any CLI, scheduler, Dashboard field, Market Validation
  adapter, or production entry point" — an accurate, self-declared gap.
- `hunter.__main__` already dispatches `committee-authority`, `valuation-evidence`,
  and `valuation-authority` to their own `command.py` orchestration modules.
  `comparative_valuation`, `mispricing`, and `asymmetry` had services, repositories,
  and models but no equivalent `command.py` and no dispatch entry — they were only
  reachable through direct Python construction in tests.
- ADR 0026's own Status section states implementation of the Comparative Valuation
  foundation is authorized under a separately governed implementation Issue; Issue
  #181 is that issue, closing the remaining operability gap (ADR 0026 Implementation
  Prerequisite 1).

Comparative Valuation was selected over Mispricing/Asymmetry because it has a
complete, dedicated, accepted methodology ADR (0026) with a fully specified
persistence/replay/confidence contract, matching the exact precedent already set for
`valuation-authority`. The same operability addition for Mispricing/Asymmetry
remains available as separate, equally narrow, later issues.

## What this change adds

`hunter comparative-valuation-authority run MANIFEST.json`
(`src/hunter/comparative_valuation/command.py`), dispatched from
`src/hunter/__main__.py` exactly like the three existing production verbs. The
manifest's `operation` field routes to the five existing
`CanonicalComparativeValuationService` write methods, plus a read-only `status`
query over the same five record families:

```json
{"operation": "peer_policy", "payload": {...}}
{"operation": "peer_universe", "identity": {...}, "policy_record_id": "...", "cutoff": "...", "candidates": [...], ...}
{"operation": "eligibility_decision", "target_identity": {...}, "candidate_identity": {...}, ...}
{"operation": "metric_observation", "identity": {...}, "policy_record_id": "...", ...}
{"operation": "assess", "identity": {...}, "policy_record_id": "...", ...}
{"operation": "status", "target": "peer_policy"|"peer_universe"|"eligibility_decision"|"metric_observation"|"assessment", "effective_as_of": "...", "known_by": "...", "logical_id": "..."}
```

### Architecture impact

None. No ADR is touched. No existing validation, formula, replay, calibration, or
persistence logic is added, modified, or duplicated. Every `_persist_*`/`_assess`
helper calls exactly the corresponding, unmodified
`CanonicalComparativeValuationService` method with parsed manifest arguments and
returns its result (JSON-serialized); `_status` calls exactly the same
`strict_known_*` service read methods already exercised directly by
`tests/test_comparative_valuation_v1.py`. This mirrors
`hunter.valuation_authority.command` (Issue #107) in substance and structure. No
Market Validation adapter, weighting, composition, or activation is introduced; ADR
0027 remains `Proposed` and unimplemented, unchanged by this work.

### Authority boundaries preserved

- `ComparativeValuationRepository` still exposes no public write method (unchanged;
  verified by the new `test_repository_bypass_remains_impossible`, mirroring the
  identical assertion in `tests/test_valuation_authority_v1.py`).
- No new validation logic: every eligibility, coverage, missingness, conflict, and
  correction-lineage rule is enforced exclusively by
  `CanonicalComparativeValuationService`, unchanged.
- No caller-supplied value can select "latest"/"current" state: `status` requires
  the same explicit `effective_as_of`/`known_by`/`logical_id` the service's
  `strict_known_*` methods already require.
- `comparative_valuation.models`, `.repository`, and `.service` are byte-for-byte
  unchanged by this issue.

## Files changed

- `src/hunter/comparative_valuation/command.py` (new — manifest-driven `main(argv)`
  orchestration layer)
- `src/hunter/__main__.py` (modified — dispatches `comparative-valuation-authority`
  to the new command module, alongside the three existing production verbs)
- `src/hunter/comparative_valuation/__init__.py` (modified — module docstring
  updated to record that a CLI entry point now exists, while explicitly preserving
  the still-true statement that no scheduler, Dashboard field, or Market Validation
  adapter exists)
- `tests/test_comparative_valuation_authority_v1.py` (new — 16 tests)
- `docs/IMPLEMENTATION_REPORTS/issue-181-comparative-valuation-authority.md` (this
  report)

No file under `docs/ADR/`, `docs/architecture-records/`, `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`,
`configs/`, `data/`, or any other `src/hunter/` module is touched.

## Test coverage

`tests/test_comparative_valuation_authority_v1.py` (16 tests), reusing the identity/
candidate/policy-payload builders and native-evidence seeding primitives from
`tests/test_comparative_valuation_v1.py` so the two suites cannot drift apart on
what a valid payload looks like:

- Peer policy persists through the production entry point and matches a direct
  repository read.
- End-to-end: `peer_policy` -> `peer_universe` -> `eligibility_decision` (x3) ->
  `metric_observation` (x4) -> `assess` all driven through the CLI, reproducing the
  same `UNAVAILABLE_UNCALIBRATED_NORMALIZATION` raw-values state ADR 0026 requires
  for this foundation (no calibrated normalization exists).
- Insert-identical idempotency and divergent-duplicate rejection for `peer_policy`.
- Strict-known replay through the CLI reproduces an identical assessment to direct
  service construction.
- `status` reports unavailable before any record exists, reports a persisted
  `peer_policy` matching a direct repository read (content-hash cross-checked), and
  reports a persisted `assessment` (residual cross-checked).
- `status` reports a persisted `peer_universe` (unavailable-before-creation and
  available-after cases), `eligibility_decision`, and `metric_observation` -- all
  five `_STATUS_TARGETS` are exercised, matching Issue #181's acceptance criterion.
- Unknown `status` target is rejected.
- `status`, run repeatedly, never itself writes a record (row count before/after is
  compared across three repeated queries).
- Unknown `operation` value is rejected; missing `HUNTER_APPLICATION_ROOT` is
  rejected; malformed argv prints usage and returns a non-zero exit code.
- Repository bypass remains impossible (no `save`/`apply`/`write`/`persist`/`assess`/
  `replay` method exists on `ComparativeValuationRepository`).
- `hunter.__main__` dispatch reaches the new command module.

## Verification Results

- `ruff check .` — all checks passed.
- `black --check .` — all 617 files unchanged (no reformatting needed).
- `mypy` — success, no issues in 617 source files.
- `pytest tests/test_comparative_valuation_v1.py tests/test_valuation_authority_v1.py tests/test_comparative_valuation_authority_v1.py tests/test_valuation_family_repository_purity.py tests/test_valuation_family_integration.py` — 77 passed.
- Full `pytest` suite on this exact HEAD — **1456 passed, 0 failed** (558.66s).
- `git status --porcelain --untracked-files=all` before and after the full suite —
  only the intentional files listed above; no incidental write to any tracked or
  untracked file, including `data/`.

## Scope Boundary

This change does not implement Mispricing or Asymmetry entry points. It does not
add calibration or normalization (ADR 0026's `normalization_status` remains
`"unavailable"` in every path this CLI exercises). It does not wire any Market
Validation adapter, weighting, composition, or activation (ADR 0027 remains
`Proposed`, unimplemented). It does not modify any existing validation, formula,
replay, or persistence logic. It does not acquire, fabricate, or commit any real or
synthetic evidence beyond the same deterministic test fixtures
`tests/test_comparative_valuation_v1.py` already uses. It does not change any
accepted ADR, ADPR, or canonical architecture document.

## Governance

This PR is opened as Draft. A hostile self-review was performed by the implementer
and its findings, if any, are recorded in the PR description. Per
`docs/AI_REVIEW_PROTOCOL.md` and the Rule 22 hostile-review gate (PR #180), the
implementer must not approve its own work; independent review is required before
this PR may leave Draft status.
