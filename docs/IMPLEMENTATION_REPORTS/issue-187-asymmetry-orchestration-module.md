# Implementation Report: Issue #187 — Canonical Asymmetry Orchestration Module (Undispatched)

## Summary

Adds a manifest-driven orchestration module (`hunter.asymmetry.command`) exposing
the existing, already-implemented `CanonicalAsymmetryService`'s five write
operations (`persist_asymmetry_methodology`, `persist_scenario_set`,
`persist_scenario_probability`, `persist_scenario_payoff`, `assess`) plus a
read-only `status` query over all five record families. This mirrors the
structure, scope, and undispatched posture already established and merged for
`hunter.mispricing.command` (Issue #183 / PR #184) and
`hunter.comparative_valuation.command` (Issue #181).

**Production activation was NOT performed.** This module is not dispatched from
`hunter.__main__` and is not reachable through the `hunter` CLI. `src/hunter/__main__.py`
was not modified in any way.

## Branch and base

- Branch: `claude/asymmetry-orchestration-module`
- Base: `main` at merge commit `30e6688339b9ae696ff84d36184999e0bbc19fa9` (PR #184,
  the merged Mispricing orchestration module)

## Authorization proof (Phase 0)

Per the same governance test established and applied for Issue #183/PR #184:

- `docs/DEVELOPMENT_GOVERNANCE.md` Stage 1 routes to
  `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` only for "architecturally
  significant changes."
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s Scope section defines
  architectural significance as creating or materially changing canonical
  authority/ownership, persistence/correction/versioning/migration semantics,
  strict-known replay/historical reconstruction, evidence/provenance/sufficiency/
  calibration contracts, an engine/service/subsystem/cross-component boundary, or
  compatibility guarantees, or requiring a new/amended ADR.
- `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`'s Scope Control section: "A
  mechanically necessary change remains in scope when it is directly required to
  complete an already-authorized acceptance criterion and does not create new
  architecture."
- ADR 0021 "Implementation order after acceptance," item 6, already authorizes and
  records as implemented: "Implement scenario/probability/payoff records and
  `CanonicalAsymmetryService`, including tail, dependency, correlation, and
  duplicate-evidence controls." `docs/architecture-index.md`'s Epic/Issue Mapping
  confirms: "Issue #164 | not applicable | ADR 0021 | PR #165 | Canonical
  Asymmetry foundation implemented; merge `99c95f0`."
- `hunter.asymmetry.command` calls only this already-authorized, already-merged
  service's existing public methods. It adds no new canonical authority, no new
  persistence/replay/evidence contract, no calibration or normalization logic, and
  no cross-component/production boundary — it is not dispatched from
  `hunter.__main__`.
- Dispatching this module into `hunter.__main__` *would* create a new
  cross-component/production boundary under the Scope section's explicit trigger,
  and would require independent Architecture Review
  (`docs/AI_REVIEW_PROTOCOL.md`, Development Governance Stage 5) before
  activation. That dispatch is explicitly excluded from this issue's scope and was
  not performed.

Conclusion: this is an implementation detail of an already-authorized capability,
not an architecturally significant change. No ADPR, ADR, or new architecture
review was required or performed. No answer to Phase 0's authorization questions
was YES or UNCERTAIN.

## Existing implementation audit (Phase 1)

`src/hunter/asymmetry/` (unmodified except the `__init__.py` docstring, see
below) already implements, via PR #165 (merged, stabilized by Issue #166):

- `CanonicalAsymmetryService` (`service.py`) with five write methods
  (`persist_asymmetry_methodology`, `persist_scenario_set`,
  `persist_scenario_probability`, `persist_scenario_payoff`, `assess`), five
  read/get methods, five history methods, five strict-known methods
  (`strict_known_methodology`, `strict_known_scenario_set`,
  `strict_known_scenario_probability`, `strict_known_scenario_payoff`,
  `strict_known_assessment`), and five `unresolved_*_conflicts` methods.
- `AsymmetryRepository` (`repository.py`), purely mechanical reads with no
  write/apply method of its own (ADR 0009 repository purity).
- Five immutable record dataclasses (`models.py`): `AsymmetryMethodologySnapshot`,
  `ScenarioSetSnapshot`, `ScenarioProbabilityRecord`,
  `ScenarioPayoffEstimateRecord`, `AsymmetryAssessmentRecord`.

No new API was invented. `hunter.asymmetry.command` calls only the methods listed
above, with the exact keyword signatures already defined on
`CanonicalAsymmetryService`.

## Pattern extraction (Phase 2)

`hunter.asymmetry.command` follows the identical structure already established by
`hunter.valuation_authority.command`, `hunter.comparative_valuation.command`
(Issue #181), and `hunter.mispricing.command` (Issue #183/PR #184):

| Aspect | Pattern followed |
| --- | --- |
| Entry point | `main(argv: list[str]) -> int`, validates `argv == ["run", MANIFEST_PATH]` |
| Usage message (wrong argv) | Literal `python -c '...'` executable invocation (the corrected, non-misleading form established by PR #184's remediation `b26d91f`), not a nonexistent `hunter <verb>` CLI command |
| Manifest dispatch | JSON object with `operation` field, routed to `_persist_*`/`_assess`/`_status` helpers |
| Service construction | `_service(application_root)` builds `CanonicalAsymmetryService` from `AsymmetryRepository` + `ObservedMarketFactRepository`, both pointed at `_canonical_path(application_root, "data/data_ops.sqlite")` |
| Path safety | `_application_root()` requires `HUNTER_APPLICATION_ROOT` (absolute path); `_canonical_path()` rejects any resolved path escaping the root |
| Status operation | Read-only, bounded strictly by manifest-supplied `effective_as_of`/`known_by`/`logical_id`; no "latest" selection |
| Output | `json.dumps(output, sort_keys=True)`; `_json_safe()` recursively converts datetimes (ISO-8601 UTC) and tuples for JSON serialization |
| Identity construction | `_identity()` builds `EconomicClaimIdentity` from a payload dict |
| Timestamp parsing | `_datetime()` requires timezone-aware ISO-8601 values |
| Validation/business logic | None reimplemented; every guarantee (strict-known selection, missingness, compatibility, correction-lineage integrity, repository-bypass rejection) is enforced exclusively by the unmodified service |
| Dispatch | Not wired into `hunter.__main__`; not reachable through the `hunter` CLI |

Asymmetry-specific additions to the pattern (required because the underlying
service has five write operations instead of Mispricing's two): `scenario_set`
requires `_string_tuple()` (non-empty list-of-strings) and `_pair_tuple()`
(list of two-element `[str, str]` lists, for `dependency_pairs`) helpers not
needed by the simpler Mispricing/Comparative-Valuation manifests.

## Implementation (Phase 3)

Files created/modified, exactly as scoped by Issue #187:

- **`src/hunter/asymmetry/command.py`** (new, 352 lines): `main()` plus
  `_persist_methodology`, `_persist_scenario_set`, `_persist_scenario_probability`,
  `_persist_scenario_payoff`, `_assess`, `_status`, and shared helpers
  (`_service`, `_string_tuple`, `_pair_tuple`, `_json_safe`, `_identity`,
  `_optional_text`, `_application_root`, `_canonical_path`, `_datetime`).
- **`src/hunter/asymmetry/__init__.py`**: docstring updated only (two sentences
  in the opening paragraph adjusted; one new paragraph added describing the
  orchestration module's existence and undispatched posture). No import, export,
  or `__all__` change.

Files intentionally untouched (confirmed via `git diff --stat`, zero changes):

- `src/hunter/__main__.py` — never modified, per the absolute constraint.
- `src/hunter/cli.py` — never modified. (Its pre-existing
  `HISTORICAL_ACQUISITION_ENGINES` tuple already listed the string `"asymmetry"`
  before this change, for unrelated historical-acquisition-coverage reporting; no
  line in that file was changed by this work.)
- `src/hunter/asymmetry/models.py`, `repository.py`, `service.py` — no changes;
  all validation, formula, replay, correction, and persistence logic remains
  exactly as merged in PR #165.
- No other package's files (Mispricing, Comparative Valuation, Evidence
  Assembly, Valuation, Market Facts, Value Capture) were touched.
- No ADR, ADPR, or canonical governance document was modified.

## Testing (Phase 4)

**`tests/test_asymmetry_authority_v1.py`** (new, 23 tests, all passing), mirroring
`tests/test_mispricing_authority_v1.py`'s structure and strength:

- **A. Write-operation happy paths** — `methodology`, `scenario_set`, and
  `scenario_probability`/`scenario_payoff` each persist correctly through the
  orchestration module and are independently readable via the unmodified
  repository.
- **B. End-to-end chain** — all five operations driven through the orchestration
  module in sequence (methodology → scenario_set → 3 probabilities → 3 payoffs →
  assess) produce a complete assessment; confirms ADR 0021's fixed
  no-calibration-exists behavior (`availability_state ==
  "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"`, `normalization_status ==
  "unavailable"`, `raw_asymmetry_ratio` populated).
- **C. Insert-identical idempotency** — repeating an identical methodology
  manifest returns the same `record_id`.
- **D. Divergent-duplicate rejection** — a second methodology manifest with a
  changed field for the same logical identity raises
  `AsymmetryIntegrityError` ("root record already exists"), proving the
  orchestration module performs no silent overwrite and does not weaken the
  service's correction-lineage rules.
- **E. Independent-construction equivalence** — the critical test pattern
  established by PR #182's hostile-review remediation and reused for Mispricing:
  constructs the full five-record chain twice, from identical semantic inputs,
  into two *completely separate* databases — Path A entirely through
  `asymmetry_authority_command.main()`, Path B entirely through direct
  `CanonicalAsymmetryService` construction, never touching the orchestration
  module. Asserts full `dataclasses.asdict(...)` equality (not read-back
  equality) for all five record families, covering `record_id`/`logical_id`,
  the deterministic `content_hash`, methodology/formula-version fields, and
  provenance. This is the equivalence proof the earlier read-back-only pattern
  (rejected in PR #182's hostile review) could not provide.
- **F. Read-only status query** — parametrized "unavailable before any record
  exists" across all five status targets; "available" cases for `methodology`,
  `scenario_set`, and `assessment` matching the direct repository read; unknown
  status-target rejection; and a repeated-query regression test proving `status`
  never mutates the underlying repository row count.
- **G. Malformed-input rejection** — unknown `operation`, missing
  `HUNTER_APPLICATION_ROOT`, wrong `argv` shape (asserts the exact corrected
  `python -c '...'` usage string), malformed (non-object) top-level manifest.
- **H. Repository-bypass-impossible** — `AsymmetryRepository` exposes no
  `save`/`apply`/`write`/`persist`/`assess`/`replay` method of its own.
- **I. Non-dispatch regression** — `hunter_main.main(["asymmetry-authority", "run",
  ...])` raises `SystemExit` with code `2` (argparse's "invalid choice" for an
  unregistered verb), and the canonical database file is never created as a side
  effect of the attempt.

## Hostile self-review (Phase 6)

Reviewed as an independent hostile reviewer attempting to reject the PR. Checked
for and found none of the following:

- **Hidden production activation or CLI exposure**: `git diff --stat` against
  `src/hunter/__main__.py` and `src/hunter/cli.py` shows zero changes to either
  file. `grep` across `src/` for `asymmetry.command`/`asymmetry_authority` finds
  references only inside the two files this issue authorizes changing
  (`command.py` itself, and the `__init__.py` docstring). The pre-existing
  `"asymmetry"` string in `cli.py`'s `HISTORICAL_ACQUISITION_ENGINES` tuple
  predates this change and is unrelated (historical-acquisition-coverage
  reporting, not CLI dispatch).
- **Replay/persistence/evidence-contract/normalization/ownership changes**:
  `models.py`, `repository.py`, `service.py` are byte-identical to the merged
  PR #165 state (`git diff --stat` shows no changes).
- **ADR/governance violations**: no ADR, ADPR, or canonical governance document
  was modified; the authorization proof above is grounded entirely in already-
  accepted documents.
- **Documentation inconsistencies or stale references**: the `__init__.py`
  docstring, `command.py`'s module docstring, and the test file's module
  docstring cross-reference Issue #187, PR #165 (Asymmetry foundation), Issue
  #181/PR #182 (Comparative Valuation), and Issue #183/PR #184 (Mispricing)
  consistently; all four numbers were independently verified against
  `docs/architecture-index.md` and the actual GitHub issue/PR records rather than
  assumed.
- **Incorrect or misleading usage message**: the wrong-argv usage string uses the
  corrected, directly executable `python -c '...'` form (matching PR #184's
  remediation commit `b26d91f`), not a nonexistent `hunter <verb>` invocation —
  and is asserted verbatim by
  `test_wrong_argv_shape_prints_usage_and_returns_nonzero`.
- **Weakened guarantees**: every write/read/status operation delegates 100% of
  validation, formula, replay, and persistence logic to the unmodified service;
  no new business logic, defaulting, or silent coercion was introduced beyond
  JSON-to-Python type parsing (`str()`/`_datetime()`/`_string_tuple()`/
  `_pair_tuple()`), which mirrors the identical, already-reviewed pattern in
  `hunter.mispricing.command`.

No issues were found requiring correction before this report.

## Governance proof

- Implementer (this session) has not approved this work; the PR will be opened
  as Draft and left in Draft state.
- Per `docs/AI_REVIEW_PROTOCOL.md`'s Rule 22 hostile-review gate (established at
  PR #180) and this repository's session-level operating protocol, independent
  review is required before "Ready for Review" and remains outstanding.

## Validation (Phase 7)

Run against the branch tip immediately prior to this report's own commit (code
and tests only; see "Files changed" above for the complete list):

- `ruff check .` — **All checks passed!**
- `black --check .` — the three new/modified files in scope for this issue
  (`src/hunter/asymmetry/command.py`, `src/hunter/asymmetry/__init__.py`,
  `tests/test_asymmetry_authority_v1.py`) are clean. Six pre-existing files
  outside this issue's scope (`src/hunter/committee/repository.py`,
  `src/hunter/committee/engine.py`, `src/hunter/evidence_assembly/repository.py`,
  `src/hunter/discovery/repository.py`, `tests/test_dashboard_api.py`,
  `tests/test_operational_status.py`) would be reformatted; none were touched by
  this change and correcting them is out of this issue's scope.
- `mypy` — no new errors attributable to this change. The only errors touching
  `tests/test_asymmetry_authority_v1.py` are a single `import-not-found` on
  `pytest`, which is a pre-existing, repository-wide baseline condition
  reproduced identically on the already-merged `tests/test_mispricing_authority_v1.py`
  (confirmed by direct comparison) and on 68 other test files; it is not
  introduced by this change.
- `pytest tests/test_asymmetry_authority_v1.py -q` — **23 passed**.
- `pytest -q` (full suite) — **1496 passed**, 0 failed, 0 errors.

## Architecture impact

None. No canonical authority, ownership, persistence/correction/versioning
semantics, strict-known replay behavior, evidence/provenance/sufficiency/
calibration contract, engine/service/subsystem boundary, or compatibility
guarantee was created or changed. No ADR was created, amended, or required.

## Evidence impact

None. This module produces no new evidence; it orchestrates calls to the
existing, unmodified `CanonicalAsymmetryService`, which itself produces no
calibrated `[0,1]` normalized Market Validation input (ADR 0021's calibration
gate remains unimplemented, unchanged by this work).

## Remaining work / blockers

- Independent Architecture Review and code review, per Rule 22 — not yet
  performed; required before "Ready for Review."
- Production dispatch (wiring an `asymmetry-authority` verb into
  `hunter.__main__`) is explicitly out of scope for this issue and was not
  performed. It would require a separate, independently-reviewed future change.
- Calibrated `[0,1]` normalization for Asymmetry is unimplemented (ADR 0021
  calibration gate), unchanged by and out of scope for this issue.

## Explicit confirmation

Production activation was **NOT** performed. `hunter.__main__` was **NOT**
modified. The module remains implemented, fully tested, and directly
constructible, but is unreachable through the `hunter` CLI.
