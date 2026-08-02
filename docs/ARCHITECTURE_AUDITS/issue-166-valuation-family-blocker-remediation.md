# Issue #166 Valuation-Family Blocker Remediation

Date: 2026-08-02

Baseline: `origin/main` at `04d12fcd01455eef4dc1419cc1a9555f9fec0e94`

Scope: the three blockers validated by the independent architecture audit

## Architecture Impact Report

### Repository authority

The valuation, valuation-methodology, comparative-valuation, mispricing, and
asymmetry repositories now expose deterministic mechanical history or typed
record retrieval only. Strict-known selection, supersession resolution,
unresolved-conflict classification, eligibility checks, and current-lineage
selection are owned by their respective services.

All callers were updated to enter through service authority. In particular,
mispricing resolves its exact valuation input through
`CanonicalValuationService`, and valuation-authority status queries resolve
methodology and valuation replay through their owning services.

`tests/test_valuation_family_repository_purity.py` provides an AST-based
regression boundary across all five repositories. It rejects replay,
authorization, normalization, current-selection, or assessment methods and
rejects repository access to quality, conflict, and supersession domain fields.

No accepted ADR or ADPR was modified. No Market Validation, Opportunity,
ranking, or portfolio authority was introduced.

## Evidence Impact Report

`tests/test_valuation_family_integration.py` exercises the complete canonical
valuation-family flow:

```text
Valuation -> Comparative Valuation -> Mispricing -> Asymmetry
```

The suite proves exact-version lineage, immutable evidence references,
correction propagation, logical-identity preservation, deterministic replay,
input-permutation invariance, replay before and after corrections,
anti-double-counting, valuation/mispricing isolation, and
asymmetry/scenario isolation. It also asserts the absence of Market Validation,
Opportunity, ranking, and portfolio dependencies.

Peer-universe candidate ordering is canonicalized before both fingerprinting
and persistence. Equivalent candidate sets therefore produce one immutable
record and replay identically regardless of input order.

Existing valuation-authority tests now verify replay through service authority.
All immutable records and correction links remain append-only; no accepted
history was rewritten.

## Repository Cleanliness Report

Runtime-writing tests now use injected or temporary roots:

- data-operations persistence derives its database from the injected
  application root or an absolute configuration root;
- pipeline operational-corpus defaults honor injected application and test
  runtime roots;
- competitive, sufficiency, historical-acquisition, and pipeline-persistence
  tests use temporary databases and corpus paths.

`tests/conftest.py` snapshots `git status --porcelain --untracked-files=all` at
test-session start and compares it at session end. Any tracked or untracked
mutation fails the full suite.

Final quality-gate evidence:

- `ruff check .`: passed
- `black --check .`: passed
- `mypy`: passed for 613 source files
- `pytest -q`: 1,433 passed in 939.80 seconds
- repository cleanliness comparison: passed

The working tree after the full suite contained only the intentional source,
test, and report changes listed in this remediation.
