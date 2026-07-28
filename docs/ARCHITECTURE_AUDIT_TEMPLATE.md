# Architecture Audit Report Template

## Metadata

- Reviewed artifact:
- Reviewed revision:
- Audit type: `FULL` or `TARGETED`
- Auditor:
- Audit date:
- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`

## Audit Scope

Describe the exact scope of the audit.

For a targeted re-audit, list:

- prior blocking findings;
- sections changed;
- regression surface examined.

## Evidence Sources Examined

List the exact repository files, accepted ADRs, implementation evidence, tests, issue records, and external primary sources used.

## Dimension Results

Use one result for each applicable dimension:

- `PASS`
- `PASS_WITH_FINDINGS`
- `FAIL`
- `NOT_APPLICABLE`

A dimension result does not determine the final verdict by itself.

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | | | |
| Scope completeness | | | |
| Canonical consistency | | | |
| Evidence integrity | | | |
| Assumption discipline | | | |
| Option completeness | | | |
| Option normalization | | | |
| Comparative fairness | | | |
| Falsifiability | | | |
| Authority and ownership | | | |
| Persistence and replay | | | |
| Evidence and provenance | | | |
| Implementation impact | | | |
| Migration impact | | | |
| Operational impact | | | |
| Testability and validation | | | |
| Maintainability and extensibility | | | |
| Governance compatibility | | | |
| Traceability | | | |
| Risks and unresolved uncertainty | | | |

## Findings

Repeat the following record for every substantiated finding.

### F-000 — Finding title

- **Evidence:**
- **Location:**
- **Category:**
- **Severity:** `A`, `B`, `C`, or `D`
- **Decision impact:**
- **Consequence if ignored:**
- **Required action:**
- **Blocks ADR:** `YES` or `NO`

For Class C or D findings, explicitly answer:

> What incorrect, incomplete, or unsupported architectural decision could result if this finding is ignored?

A Class C or D classification is invalid without this answer.

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| | | | | | |

## Verdict Derivation

- Highest unresolved severity:
- Cumulative Class B materiality, if any:
- Blocking findings:
- Conditions required before ADR approval, if any:

Explain how the verdict follows from `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`. Do not derive the verdict from PASS/FAIL counts.

## Final Verdict

Choose exactly one:

- `READY_FOR_ADR`
- `READY_FOR_ADR_WITH_MINOR_FINDINGS`
- `CONDITIONAL_ADR_READY`
- `ADPR_REVISION_REQUIRED`
- `ARCHITECTURE_NOT_READY`

## Required Corrections or Conditions

List only corrections required by the selected verdict. Keep non-blocking improvements separate.

## Non-Blocking Follow-Up

Record optional editorial or documentation-quality improvements that do not block ADR readiness.

## Audit Completion Check

- [ ] Exact artifact and revision identified
- [ ] Audit scope identified
- [ ] Evidence sources listed
- [ ] Applicable dimensions assessed
- [ ] Every finding includes all mandatory fields
- [ ] Every Class C or D finding demonstrates decision consequence
- [ ] Findings matrix completed
- [ ] Verdict derived from severity and materiality
- [ ] Targeted re-audit rule followed where applicable
- [ ] Auditor did not recommend or rank options unless explicitly authorized
