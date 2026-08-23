# Architecture Audit Report: Evidence Assembly Production Constructibility (Issue #197)

## Metadata

- Reviewed artifact: `src/hunter/evidence_assembly/` (`service.py`, `composition.py`, `repository.py`, `registry.py`, `semantics.py`, `models.py`)
- Governing ADR: [ADR 0028](../ADR/0028-evidence-assembly-supporting-authorities.md), Evidence Assembly Supporting Authorities (Accepted, Revision 9)
- Triggering issue: [Issue #197](https://github.com/fafa33/Project-Hunter/issues/197)
- Target issue: [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190), Canonical Evidence Assembly Orchestration Module
- Audit date: `2026-08-23`
- Role: Implementer / Auditor

## Objective

Verify that `CanonicalEvidenceAssemblyService` is production-constructible using real production-backed collaborators delivered by Issues #194, #195, and #196, without fakes, stubs, mocks, or placeholders, and determine whether Issue #190 can now proceed.

## Summary Finding

Issues #194, #195, and #196 have delivered all required production-backed authorities:

1. **Methodology Contract Authority:** `CanonicalValuationMethodologyAuthority` in `hunter.valuation_methodology.service` backed by `ValuationMethodologyRepository`.
2. **Evidence Shape Registry Authority:** `CanonicalEvidenceShapeRegistryAuthority` in `hunter.evidence_assembly.registry` backed by `EvidenceShapeRegistryRepository`.
3. **Canonical Evidence Semantic Input Authority:** `CanonicalEvidenceSemanticInputAuthority` in `hunter.evidence_semantic_inputs` backed by `EvidenceSemanticInputRepository`.
4. **Evidence Semantics Authority:** `CanonicalEvidenceSemanticsAuthority` in `hunter.evidence_assembly.semantics` backed by `EvidenceSemanticsRepository`.
5. **Native Evidence Query:** `SupplyAndValueCaptureService` in `hunter.value_capture.service` backed by `SupplyAndValueCaptureRepository`.
6. **Assembled Evidence Repository:** `AssembledEvidenceRepository` in `hunter.evidence_assembly.repository`.

Issue #197 has established `build_production_evidence_assembly_service` in `hunter.evidence_assembly.composition` to compose `CanonicalEvidenceAssemblyService` using exclusively production-backed collaborators constructed internally from `HUNTER_APPLICATION_ROOT` and the established Value Capture producer signing-key contract (`HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID` and hex-encoded `HUNTER_VALUE_CAPTURE_SIGNING_KEY`), with zero collaborator override parameters.

## Required Proofs Verification

| Proof ID | Criterion | Audit Result | Verification Evidence |
|---|---|---|---|
| P-01 | Production-backed construction | PASS | `build_production_evidence_assembly_service` constructs `CanonicalEvidenceAssemblyService` with 100% production classes constructed internally (`test_proof_1_and_13_production_construction`). |
| P-02 | Methodology strict-known availability | PASS | Fails closed with `CanonicalEvidenceAssemblyError` when contract is missing or `accepts_assembled_evidence==False` (`test_proof_2_methodology_strict_known_availability`, `test_proof_2_methodology_contract_rejects_assembled_evidence`). |
| P-03 | Shape registry strict-known availability | PASS | Fails closed with `CanonicalEvidenceAssemblyError` when shape registry version is missing at cutoff (`test_proof_3_shape_registry_strict_known_availability`). |
| P-04 | Semantics strict-known availability | PASS | Fails closed with `CanonicalEvidenceAssemblyError` when authoritative semantics record is missing or metadata diverges (`test_proof_4_semantics_strict_known_availability`). |
| P-05 | Assembly write and read | PASS | `assemble()` creates and persists `AssembledFundamentalEvidenceRecord`; `strict_known()` retrieves exact record (`test_proof_5_assembly_write_and_read`). |
| P-06 | Correction behavior | PASS | Successor assembly carries `supersedes_record_id` and `correction_reason`; branching correction is rejected (`test_proof_6_correction_behavior_and_branching_prohibition`). |
| P-07 | Conflict behavior | PASS | Full-interval qualifying native evidence takes precedence and persists assembly conflict record (`test_proof_7_conflict_behavior_qualifying_native_precedence`). |
| P-08 | Provenance continuity | PASS | Preserves exact constituent record IDs, logical IDs, content hashes, and shape IDs (`test_proof_8_provenance_continuity`). |
| P-09 | Historical / strict-known replay | PASS | Pre-recording replay yields `None`; exact cutoff replay yields historical record (`test_proof_9_historical_strict_known_replay`). |
| P-10 | Separate production repositories isolation | PASS | Distinct SQLite paths operate independently without cross-database leakage (`test_proof_10_separate_repositories_isolation`). |
| P-11 | Read-only operations create no records | PASS | `strict_known()`, `is_superseded()`, and `unresolved_assembly_conflicts()` create no database records (`test_proof_11_12_readonly_operations_no_writes_and_upstream_immutability`). |
| P-12 | Read-only operations mutate no persistence | PASS | Read-only operations leave database contents, snapshot counts, and upstream repositories byte-identical (`test_proof_11_12_readonly_operations_no_writes_and_upstream_immutability`). |
| P-13 | No fake/stub/placeholder in production composition | PASS | Inspected composition contains zero fakes or placeholders; established producer signing-key ID/hex contract is reused and fails closed on missing, malformed, or short decoded keys (`tests/test_evidence_assembly_production_key_contract.py`, `test_production_composition_fails_closed_when_key_unconfigured`, `test_production_composition_fails_closed_when_key_secret_too_short`, `test_hostile_fake_authority_substitution`). |
| P-14 | Dependency graph remains acyclic | PASS | AST analysis of all upstream packages (`hunter.value_capture`, `hunter.valuation_methodology`, `hunter.evidence_semantic_inputs`) confirms zero imports of `hunter.evidence_assembly` (`test_proof_14_dependency_graph_acyclic_ast_analysis`). |
| P-15 | Authority ownership remains singular | PASS | Evidence Assembly service owns only assembly records and conflict records, with no write path into methodology or semantics authorities (`test_proof_15_authority_ownership_singular`). |

## Hostile Test Verification

Hostile tests verified fail-closed behavior across:
- Unconfigured / missing verification keys (`test_production_composition_fails_closed_when_key_unconfigured`)
- Malformed or insufficient established producer signing keys (`tests/test_evidence_assembly_production_key_contract.py`)
- Insufficient legacy compatibility key secret length (`test_production_composition_fails_closed_when_key_secret_too_short`)
- Fake authority substitution (`test_hostile_fake_authority_substitution`)
- Constituent metadata caller override (`test_hostile_caller_override_of_canonical_authority`)
- Provenance content-hash tampering (`test_hostile_inconsistent_provenance_hash_tampering`)

## Final Repository Audit Question

**Can Issue #190 now be implemented using production-backed CanonicalEvidenceAssemblyService without architectural or production-authority blockers?**

Verdict:
`IMPLEMENTABLE`

Issue #190 is unblocked and may now be implemented.