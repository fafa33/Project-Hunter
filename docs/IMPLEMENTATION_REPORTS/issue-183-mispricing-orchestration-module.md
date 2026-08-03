# Issue #183 — Canonical Mispricing Orchestration Module (Undispatched) — Implementation Report

## Status

This report documents a manifest-driven orchestration module
(`src/hunter/mispricing/command.py`) for the already-implemented Canonical
Mispricing foundation (ADR 0021; `src/hunter/mispricing/`, merged via PR #163,
stabilized by Issue #166), structurally mirroring `hunter.valuation_authority.command`
and `hunter.comparative_valuation.command` (Issue #181/PR #182, corrected form).

**This implementation intentionally creates NO production entry point and NO
`hunter.__main__` dispatch.** `hunter.mispricing.command` is fully implemented and
exercised only by direct construction in `tests/test_mispricing_authority_v1.py`;
`src/hunter/__main__.py` is byte-for-byte unchanged from `main`'s baseline.

## Authorization proof (Phase 0)

Before implementation, the following was proven from repository evidence, not
assumed:

1. **Building an undispatched orchestration module is not an architecturally
   significant change.** `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s Scope
   section defines architectural significance as creating or materially changing
   canonical authority/ownership, persistence/replay/evidence contracts, or a
   cross-component/production boundary. `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`'s
   Scope Control section states: "A mechanically necessary change remains in scope
   when it is directly required to complete an already-authorized acceptance
   criterion and does not create new architecture."
2. The implementation creates: no new authority (calls only existing
   `CanonicalMispricingService.persist_mispricing_methodology`/`assess`/
   `strict_known_*` methods, unmodified); no new ownership; no new persistence
   semantics; no new replay semantics; no new evidence contract; no new production
   surface (not dispatched); no new runtime activation; no new CLI command
   (`hunter.__main__` untouched).
3. ADR 0021's "Implementation order after acceptance," item 5 ("Implement
   `CanonicalMispricingService` only after compatible fair-value and observed-market
   records exist") already authorizes the underlying service, implemented by PR
   #163. This module is a mechanical exposure of that already-authorized capability's
   existing public API through a manifest-driven caller, not a new capability.
4. Therefore: no ADPR, no ADR, no architecture redesign is required. Dispatching this
   module into `hunter.__main__` *would* create a new cross-component/production
   boundary (the Scope section's explicit trigger) and *would* require independent
   Architecture Review before activation — which is exactly why this issue excludes
   dispatch from its scope rather than attempting to satisfy that requirement here.

## What this change adds

`hunter.mispricing.command.main(argv)` — manifest-driven orchestration routing an
`operation` field to `persist_mispricing_methodology`, `assess`, and a read-only
`status` query over both record families:

```json
{"operation": "methodology", "payload": {...}}
{"operation": "assess", "identity": {...}, "methodology_record_id": "...", "fair_value_logical_id": "...", ...}
{"operation": "status", "target": "methodology"|"assessment", "effective_as_of": "...", "known_by": "...", "logical_id": "..."}
```

### Architecture impact

None. No ADR is touched. No existing validation, formula, replay, calibration, or
persistence logic is added, modified, or duplicated. Every `_persist_methodology`/
`_assess` helper calls exactly the corresponding, unmodified
`CanonicalMispricingService` method; `_status` calls exactly the same
`strict_known_*` service read methods already exercised directly by
`tests/test_mispricing_v1.py`. `src/hunter/mispricing/{models,repository,service}.py`
are byte-for-byte unchanged. `src/hunter/__main__.py` is byte-for-byte unchanged. No
Market Validation adapter, weighting, composition, or activation is introduced; ADR
0027 remains `Proposed` and unimplemented, unaffected by this work.

### Evidence impact

None new. Tests reuse the same deterministic native-evidence and fair-value-chain
fixtures `tests/test_mispricing_v1.py` already uses (circulating-supply/spot-price
market facts, value-capture evidence/supply/rule, valuation methodology, fair-value
estimate), seeded at the orchestration module's canonical persistence path. No live
network access was used or required.

### Authority boundaries preserved

- `MispricingRepository` still exposes no public write method (verified by
  `test_repository_bypass_remains_impossible`).
- No new validation logic: every methodology/formula-version compatibility,
  missingness, conflict, and correction-lineage rule is enforced exclusively by
  `CanonicalMispricingService`, unchanged.
- No caller-supplied value can select "latest"/"current" state: `status` requires
  the same explicit `effective_as_of`/`known_by`/`logical_id` the service's
  `strict_known_*` methods already require.
- The module is not reachable through `hunter.__main__`/the `hunter` CLI.

## Files changed

- `src/hunter/mispricing/command.py` (new — manifest-driven `main(argv)`
  orchestration module; not dispatched from `hunter.__main__`)
- `src/hunter/mispricing/__init__.py` (modified — docstring only, records that the
  orchestration module exists and remains undispatched, citing the Scope-section
  rationale)
- `tests/test_mispricing_authority_v1.py` (new — 16 tests)
- `docs/IMPLEMENTATION_REPORTS/issue-183-mispricing-orchestration-module.md` (this
  report)

No file under `docs/ADR/`, `docs/architecture-records/`, `configs/`, `data/`,
`src/hunter/__main__.py`, or `src/hunter/mispricing/{models,repository,service}.py`
is touched.

## Test coverage

`tests/test_mispricing_authority_v1.py` (16 tests), reusing the identity/fair-value
seeding primitives from `tests/test_mispricing_v1.py`:

- Methodology persists through the orchestration module and matches a direct
  repository read.
- End-to-end: `methodology` -> `assess`, reproducing the same
  `UNAVAILABLE_UNCALIBRATED_NORMALIZATION` raw-values state ADR 0021 requires for
  this foundation (no calibrated normalization exists).
- Insert-identical idempotency and divergent-duplicate rejection for `methodology`.
- **True field-equivalence** between independent orchestration-module construction
  and independent direct-service construction of both record families, into two
  separate databases, asserting full `dataclasses.asdict` equality per record
  (`test_orchestration_construction_is_field_equivalent_to_direct_service_construction`)
  — built correctly the first time, learning directly from Issue #181/PR #182's
  hostile-review Finding 2 (a prior read-back-only equivalence test was
  insufficient).
- `status` reports unavailable before any record exists, and reports persisted
  `methodology` and `assessment` records matching a direct repository read
  (content-hash/raw-ratio cross-checked).
- Unknown `status` target is rejected; `status`, run repeatedly, never itself
  writes a record.
- Unknown `operation` value, malformed top-level manifest, missing
  `HUNTER_APPLICATION_ROOT`, and wrong argv shape are all rejected.
- Repository bypass remains impossible.
- **`hunter.__main__` does NOT dispatch this verb** — regression test proving the
  entry point remains disabled at the `hunter` CLI level, and that no database write
  occurs as a side effect of the attempt.

## Verification Results (Phase 5)

- `ruff check .` — all checks passed.
- `black --check .` — all 619 files unchanged (no reformatting needed).
- `mypy` — success, no issues in 618 source files.
- `pytest tests/test_mispricing_v1.py tests/test_valuation_authority_v1.py tests/test_comparative_valuation_authority_v1.py tests/test_mispricing_authority_v1.py tests/test_valuation_family_repository_purity.py tests/test_valuation_family_integration.py` — 90 passed.
- Full `pytest` suite on this exact HEAD — **1473 passed, 0 failed** (521.95s).
- `git status --porcelain --untracked-files=all` before and after the full suite —
  only the intentional new/modified files listed above; no incidental write to
  `data/` or any other tracked/untracked file.

## Hostile self-review (Phase 6)

Explicitly verified:

- **No production activation** — `hunter.mispricing.command` is never invoked
  outside test code; no manifest is executed against a real `HUNTER_APPLICATION_ROOT`
  in this implementation.
- **No CLI exposure** — `src/hunter/__main__.py` diff against `main` is empty
  (verified via `git diff main --stat -- src/hunter/__main__.py`, zero output).
  `test_hunter_main_does_not_dispatch_mispricing_authority` proves the `hunter`
  CLI rejects the verb (`SystemExit(2)`, argparse "invalid choice") and that no
  database file is created as a side effect.
- **No scheduler exposure** — no automation/scheduler file is touched.
- **No architecture drift** — `git status --porcelain` confirms only the three files
  listed above changed; `models.py`/`repository.py`/`service.py` are unmodified.
- **No undocumented behavior** — the module's own docstring and the package
  `__init__.py` docstring both state the undispatched status and cite the exact
  governance-framework reasoning.

One legitimate finding was identified and fixed during implementation, before this
report was finalized: the first draft of the test file's `_methodology_manifest`
helper reused `test_mispricing_v1.methodology_payload` — which builds the
**valuation** methodology payload for `CanonicalValuationMethodologyAuthority`, not
the **mispricing** methodology payload `CanonicalMispricingService.
persist_mispricing_methodology` requires. No equivalent mispricing-methodology
payload builder existed in `test_mispricing_v1.py` to import (it is inlined in that
file's `Fixture` class). Fixed by defining a local
`mispricing_methodology_payload()` builder in the new test file, matching
`persist_mispricing_methodology`'s actual parameters, and renaming the imported
valuation-methodology builder to `valuation_methodology_payload` to prevent the two
from being confused again. All 16 tests pass after the fix; the full suite was
re-verified green.

## Synchronization gate (Phase 7)

PR title, PR description, acceptance matrix, this implementation report, Issue #183
text, test names, docstrings, comments, Architecture Impact, and Evidence Impact
were all written against and verified against this exact head before the Draft PR
was opened.

## Remaining limitations and risks

- Asymmetry and Evidence Assembly have the identical orchestration-module gap;
  explicitly out of scope here, and — per the same Phase 0 authorization proof
  applied to those authorities — remain candidates for separate, equally narrow,
  equally undispatched follow-up issues.
- Dispatching `hunter.mispricing.command` into `hunter.__main__` remains a separate,
  future, independently-reviewed change, not authorized by this issue.
- No calibrated normalization exists (ADR 0021); every `assess` path this module
  can reach ends in `UNAVAILABLE_UNCALIBRATED_NORMALIZATION`, by design.

## Governance

This implementation intentionally creates NO production entry point and NO
`hunter.__main__` dispatch. This PR is opened as Draft and must not be marked Ready
for Review or merged by the implementer. Per `docs/AI_REVIEW_PROTOCOL.md` and the
Rule 22 hostile-review gate (PR #180), independent review is required before this
PR may leave Draft status.
