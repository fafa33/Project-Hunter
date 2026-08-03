# Issue #181 — `hunter.comparative_valuation.command` orchestration module — Implementation Report

## Status

This report documents a narrow, manifest-driven orchestration module
(`src/hunter/comparative_valuation/command.py`) for the already-implemented
Canonical Comparative Valuation foundation (ADR 0026;
`src/hunter/comparative_valuation/`, merged via PR #161 and stabilized by Issue
#166/PRs #167-169), in the same structural shape as `hunter valuation-authority`
(`src/hunter/valuation_authority/command.py`, Issue #107).

**Following independent hostile review of Draft PR #182 (BLOCKER finding), this
module is implemented and fully tested by direct construction but is NOT dispatched
from `hunter.__main__` and is NOT reachable through the `hunter` CLI.** ADR 0026
Implementation Prerequisite 9 ("disabled-entry-point plans") and Prerequisite 10
("independent implementation review and post-merge audit ... before any production
activation") are not yet satisfied, so wiring this module into the live `hunter` CLI
is deferred to a separate, future, independently-reviewed change. This mirrors the
identical, pre-existing precedent for `hunter.evidence_assembly` (see
`docs/CANONICAL_RUNTIME_ARCHITECTURE.md`'s Evidence Assembly Authority
classification: "implemented ... but not part of the executable production runtime
flow ... no CLI, scheduler, or production entry point wires it in").

This change does not implement Mispricing or Asymmetry entry points, does not
activate Market Validation composition, and does not close Issue #173/ADR 0027
(which remains `Proposed`).

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
  and models but no equivalent `command.py` — they were only reachable through
  direct Python construction in tests.
- ADR 0026's own Status section states implementation of the Comparative Valuation
  foundation is authorized under a separately governed implementation Issue; Issue
  #181 is that issue.

Comparative Valuation was selected over Mispricing/Asymmetry because it has a
complete, dedicated, accepted methodology ADR (0026) with a fully specified
persistence/replay/confidence contract, matching the exact precedent already set for
`valuation-authority`. The same operability addition for Mispricing/Asymmetry
remains available as separate, equally narrow, later issues.

## Hostile review findings and remediation (PR #182)

Independent hostile review of the original submission returned two findings, both
addressed in this revision.

### Finding 1 — BLOCKER: unauthorized production activation

**Claim:** wiring `comparative-valuation-authority` into `hunter.__main__` exposed
Comparative Valuation as a live, callable production capability before ADR 0026's
own activation prerequisites were satisfied.

**Investigation:** ADR 0026's Implementation Prerequisites section states:

> 9. additive migration, transactional write, compatibility, preflight,
>    observability, rollback, and **disabled-entry-point plans**; and
> 10. independent implementation review and post-merge audit **before any
>     production activation**.

This is a textual requirement specific to ADR 0026 — the sibling ADR governing
`hunter valuation-authority` (ADR 0022) has no equivalent "disabled-entry-point"
or "production activation" gate in its Implementation Prerequisites; its "Current
availability decision" section instead governs *Market-Validation* availability
only. ADR 0026's Prerequisite 9/10 language is broader: it gates the entry point
itself, not only Market Validation composition. The original submission wired
`comparative-valuation-authority` into `hunter.__main__` unconditionally, with no
disabled-by-default state and without the independent review/post-merge audit
Prerequisite 10 requires before *any* production activation — a genuine ADR 0026
compliance gap, not a false positive.

**Verdict: BLOCKER was valid.**

**Resolution:** `src/hunter/__main__.py`'s dispatch entry for
`comparative-valuation-authority` was removed (see git history: PR #182's second
revision). `src/hunter/comparative_valuation/command.py` itself is unchanged in
substance — it remains fully implemented — but is now reachable only by direct
Python import/construction (exactly as every test in
`tests/test_comparative_valuation_authority_v1.py` already exercised it), never
through the `hunter` CLI a live operator would invoke. This is not an ADR change:
it implements ADR 0026's own already-stated Prerequisite 9/10, and mirrors the
`hunter.evidence_assembly` precedent already established elsewhere in this
repository for the identical "implemented, not yet activated" state. No code was
deleted; only the `hunter.__main__` dispatch entry was removed, with an inline
comment explaining why and citing the exact ADR 0026 clauses.

### Finding 2 — CHANGES REQUIRED: claimed direct-construction equivalence was not tested

**Claim:** the original `test_strict_known_replay_through_cli_reproduces_direct_construction`
only proved `CLI -> persist -> read the same record back` (a replay/read proof),
never independently constructing the same logical chain through the service and
comparing the two constructions — so it could not detect a CLI argument-mapping bug
(a dropped field, a field mapped to the wrong keyword, a type-coercion difference).

**Verdict: valid.** The finding accurately describes what that test did and did not
prove.

**Resolution:** replaced with
`test_cli_construction_is_field_equivalent_to_direct_service_construction`, which
independently constructs the complete five-record-family chain twice, from the same
semantic inputs, into two entirely separate databases — Path A through the CLI
(`comparative_valuation_authority_command.main`), Path B through direct
`CanonicalComparativeValuationService` construction that never touches the CLI/
command module — then asserts complete `dataclasses.asdict` structural equality for
every record family, including canonical identity (`record_id`/`logical_id`), the
deterministic `content_hash`, methodology/policy version fields, provenance, and
every other ADR 0026 field. Because `content_hash`/`record_id` are computed
deterministically from record content, any CLI argument-mapping divergence changes
the computed hash and fails the test immediately.

## What this change adds

`hunter.comparative_valuation.command.main(argv)` — a manifest-driven orchestration
module, structurally identical to `hunter.valuation_authority.command`, but **not**
dispatched from `hunter.__main__`. The manifest's `operation` field routes to the
five existing `CanonicalComparativeValuationService` write methods, plus a read-only
`status` query over the same five record families:

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
`tests/test_comparative_valuation_v1.py`. No Market Validation adapter, weighting,
composition, or activation is introduced; ADR 0027 remains `Proposed` and
unimplemented, unchanged by this work. The module is **not** wired into the `hunter`
CLI (see "Hostile review findings" above), so it grants no live operational
capability beyond what already existed (direct Python construction in tests).

### Authority boundaries preserved

- `ComparativeValuationRepository` still exposes no public write method (unchanged;
  verified by `test_repository_bypass_remains_impossible`, mirroring the identical
  assertion in `tests/test_valuation_authority_v1.py`).
- No new validation logic: every eligibility, coverage, missingness, conflict, and
  correction-lineage rule is enforced exclusively by
  `CanonicalComparativeValuationService`, unchanged.
- No caller-supplied value can select "latest"/"current" state: `status` requires
  the same explicit `effective_as_of`/`known_by`/`logical_id` the service's
  `strict_known_*` methods already require.
- `comparative_valuation.models`, `.repository`, and `.service` are byte-for-byte
  unchanged by this issue.
- The module is not reachable through `hunter.__main__`/the `hunter` CLI, per ADR
  0026 Implementation Prerequisites 9 and 10.

## Files changed

- `src/hunter/comparative_valuation/command.py` (new — manifest-driven `main(argv)`
  orchestration module; not dispatched from `hunter.__main__`)
- `src/hunter/__main__.py` (unchanged from `main`'s baseline — the
  `comparative-valuation-authority` dispatch entry added in the first revision of
  this PR was removed following the hostile-review BLOCKER finding; an explanatory
  comment was added in its place)
- `src/hunter/comparative_valuation/__init__.py` (modified — module docstring
  records that the orchestration module exists but is deliberately not dispatched
  from `hunter.__main__`, citing ADR 0026 Prerequisites 9/10)
- `tests/test_comparative_valuation_authority_v1.py` (new — 16 tests, all exercising
  `hunter.comparative_valuation.command.main` by direct construction, never through
  `hunter.__main__`, except one regression test proving `hunter.__main__`
  deliberately does *not* dispatch this verb)
- `docs/IMPLEMENTATION_REPORTS/issue-181-comparative-valuation-authority.md` (this
  report)

No file under `docs/ADR/`, `docs/architecture-records/`, `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`,
`configs/`, `data/`, or any other `src/hunter/` module is touched.

## Test coverage

`tests/test_comparative_valuation_authority_v1.py` (16 tests), reusing the identity/
candidate/policy-payload builders and native-evidence seeding primitives from
`tests/test_comparative_valuation_v1.py` so the two suites cannot drift apart on
what a valid payload looks like:

- Peer policy persists through `command.main` and matches a direct repository read.
- End-to-end: `peer_policy` -> `peer_universe` -> `eligibility_decision` (x3) ->
  `metric_observation` (x4) -> `assess`, reproducing the same
  `UNAVAILABLE_UNCALIBRATED_NORMALIZATION` raw-values state ADR 0026 requires for
  this foundation (no calibrated normalization exists).
- Insert-identical idempotency and divergent-duplicate rejection for `peer_policy`.
- **True field-equivalence** between independent CLI construction and independent
  direct-service construction of the complete five-record-family chain, into two
  separate databases, asserting full `dataclasses.asdict` equality per record
  (`test_cli_construction_is_field_equivalent_to_direct_service_construction`) —
  added to remediate hostile-review Finding 2.
- `status` reports unavailable before any record exists, and reports a persisted
  `peer_policy`, `peer_universe`, `eligibility_decision`, `metric_observation`, and
  `assessment` matching a direct repository read (content-hash/residual
  cross-checked) — all five `_STATUS_TARGETS` are exercised.
- Unknown `status` target is rejected.
- `status`, run repeatedly, never itself writes a record (row count before/after is
  compared across three repeated queries).
- Unknown `operation` value is rejected; missing `HUNTER_APPLICATION_ROOT` is
  rejected; malformed argv prints usage and returns a non-zero exit code.
- Repository bypass remains impossible (no `save`/`apply`/`write`/`persist`/`assess`/
  `replay` method exists on `ComparativeValuationRepository`).
- **`hunter.__main__` does NOT dispatch this verb** — added to remediate
  hostile-review Finding 1; proves the entry point remains disabled at the `hunter`
  CLI level and fails if a future change silently re-wires it without the required
  authorization.

## Verification Results

- `ruff check .` — all checks passed.
- `black --check .` — all 617 files unchanged (no reformatting needed).
- `mypy` — success, no issues in 616 source files.
- `pytest tests/test_comparative_valuation_v1.py tests/test_valuation_authority_v1.py tests/test_comparative_valuation_authority_v1.py tests/test_valuation_family_repository_purity.py tests/test_valuation_family_integration.py` — 78 passed.
- Full `pytest` suite on this exact HEAD — **1457 passed, 0 failed** (478.89s).
- `git status --porcelain --untracked-files=all` before and after the full suite —
  only the intentional files listed above; no incidental write to any tracked or
  untracked file, including `data/`.

## Scope Boundary

This change does not implement Mispricing or Asymmetry entry points. It does not
add calibration or normalization (ADR 0026's `normalization_status` remains
`"unavailable"` in every path this module exercises). It does not wire any Market
Validation adapter, weighting, composition, or activation (ADR 0027 remains
`Proposed`, unimplemented). It does not modify any existing validation, formula,
replay, or persistence logic. It does not acquire, fabricate, or commit any real or
synthetic evidence beyond the same deterministic test fixtures
`tests/test_comparative_valuation_v1.py` already uses. It does not change any
accepted ADR, ADPR, or canonical architecture document. **It does not expose any new
live capability through the `hunter` CLI** — following the hostile-review BLOCKER
finding, the orchestration module is implemented and tested but not dispatched.

## Governance

This PR is opened as Draft. A hostile self-review was performed by the implementer
after independent hostile review returned findings on the first revision; both
findings were investigated, confirmed valid, and remediated as documented above. Per
`docs/AI_REVIEW_PROTOCOL.md` and the Rule 22 hostile-review gate (PR #180), the
implementer must not approve its own work; independent review is required before
this PR may leave Draft status.
