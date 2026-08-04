# ADR 0028: Canonical Evidence Assembly Supporting Authorities

## Status

Proposed. Revision 5. Not accepted and not implementation authority.

Governing preparation record: [ADPR-0005](../architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md). Related issues: [#190](https://github.com/fafa33/Project-Hunter/issues/190) and [#191](https://github.com/fafa33/Project-Hunter/issues/191). Draft PR: [#192](https://github.com/fafa33/Project-Hunter/pull/192).

Issue #190 remains blocked. This ADR does not authorize production activation, CLI dispatch, implementation, or changes to `src/` or `tests/`.

## Context

ADR 0025 and `CanonicalEvidenceAssemblyService` require three strict-known collaborators before `assemble()` may persist an `AssembledFundamentalEvidenceRecord`:

1. a methodology evidence-input contract;
2. an Evidence Shape Registry snapshot;
3. authoritative semantics for every constituent.

No production implementation currently supplies those collaborators. Earlier revisions attempted to make Evidence Semantics derive classification dimensions directly from the observation-only `FundamentalEvidenceRecord`. That design was invalid: the native record does not authoritatively carry every dimension required to determine shape, accounting meaning, supply basis, pathway, representation continuity, or future semantic dimensions. Adding those fields to `FundamentalEvidenceRecord` would collapse observation and semantic interpretation into one authority and violate ADR 0021's evidence boundary.

This revision replaces that assumption with one new canonical upstream authority.

## Decision

Hunter establishes the following supporting authorities:

1. **Methodology Contract Authority**, owned exclusively by `CanonicalValuationMethodologyAuthority` and `ValuationMethodologyRepository` in `hunter.valuation_methodology`.
2. **Evidence Shape Registry Authority**, owned exclusively by `hunter.evidence_assembly`, as ADR 0025 already requires.
3. **Canonical Evidence Semantic Input Authority**, owned exclusively by a new upstream package, `hunter.evidence_semantic_inputs`.
4. **Evidence Semantics Authority**, owned exclusively by `hunter.evidence_assembly`, consuming only strict-known output from the Canonical Evidence Semantic Input Authority.

`FundamentalEvidenceRecord` remains observation-only and unchanged. It is referenced by exact ID and version; it is never extended with pathway, supply-basis, shape, accounting-meaning, continuity, or semantic-classification fields.

## Canonical Evidence Semantic Input Authority

### Ownership

The sole canonical owner is `CanonicalEvidenceSemanticInputAuthority` in `hunter.evidence_semantic_inputs`. Its mechanical persistence owner is `EvidenceSemanticInputRepository` in the same package.

No service in `hunter.value_capture`, `hunter.valuation_methodology`, or `hunter.evidence_assembly` may construct, amend, infer, override, or persist this authority's records.

### Authority boundary

The authority owns only the production of immutable semantic-classification inputs for an exact native evidence record version. It does not:

- acquire or parse observations;
- modify or validate `FundamentalEvidenceRecord` as native evidence;
- classify assembled evidence;
- determine assembly eligibility;
- select methodology inputs;
- perform valuation or downstream composition.

### Governing policy

Production is governed by an immutable `EvidenceSemanticInputPolicySnapshot`. Policy content is governance-authored reference data and may change only through an accepted ADR amendment. A code-only mapping, caller-supplied classification, per-record exception, manifest override, or fallback is prohibited.

Each policy snapshot contains deterministic rules whose match inputs are declared explicitly by that policy. Rules may use exact references already available to the upstream authority, including native evidence identity/version, canonical economic identity, source methodology, attribution-rule identity, evidence type, source identity/version, and unit. The policy itself supplies the semantic outputs; those outputs are never inferred from incomplete observation fields.

For one exact native evidence record and one exact policy version, evaluation has exactly one of three outcomes:

- one unique semantic-input result;
- explicit unavailable when no rule matches;
- explicit conflict when more than one non-equivalent rule matches.

There is no priority fallback unless the accepted policy version itself declares a deterministic, total ordering. Unknown, missing, or conflicting inputs never become accepted records.

### `EvidenceSemanticInputPolicySnapshot`

Every policy snapshot carries the full ADR 0021 envelope:

- `record_id` and stable `logical_id`;
- `schema_version` and `semantic_version`;
- `effective_at`, `recorded_at`, and `known_at`;
- `quality_state` and `conflict_state`;
- confidence state, explicit missing-evidence references, and conflict references;
- canonical `content_hash`;
- exact source/configuration references, authorizing ADR reference, methodology fingerprint, and `authorized_by`;
- immutable ordered rule definitions;
- `supersedes_record_id` and `correction_reason`.

Persistence is append-only. One predecessor may have at most one successor. A second independent root for the same `logical_id`, a branching successor, or divergent content under the same `record_id` is rejected.

The policy family has one fixed canonical logical identity, `canonical-evidence-semantic-input-policy`. Every accepted policy revision is a successor in that single lineage; competing policy lineages are prohibited.

`strict_known_policy(*, policy_logical_id, effective_as_of, known_by)` selects the unique non-superseded accepted tip satisfying:

- `effective_at <= effective_as_of`;
- `recorded_at <= known_by`;
- `known_at <= known_by`;
- `quality_state == "accepted"`;
- `conflict_state in {"none", "resolved"}`.

No current/latest fallback is permitted.

### `EvidenceSemanticInputRecord`

This is the single authoritative source for every input Evidence Semantics may use. Every record carries:

- the full ADR 0021 immutable envelope and correction lineage;
- exact `FundamentalEvidenceRecord` ID, logical ID, semantic version, and content hash;
- exact governing policy record ID, logical ID, semantic version, and content hash;
- `shape_id`;
- `accounting_meaning`;
- `supply_basis_id`;
- `pathway_id`;
- representation-continuity state and exact continuity-proof reference when continuity is asserted;
- currency and raw unit;
- an immutable, versioned `semantic_dimensions` mapping for future classification dimensions;
- deterministic rule ID and evaluation fingerprint;
- complete provenance and authorizing authority ID.

The full envelope includes schema/semantic versions, effective/recorded/known times, quality/conflict/confidence state, explicit missing-evidence and conflict references, canonical hash, source/configuration references, methodology fingerprint where applicable, and authorization metadata.

`semantic_dimensions` may add a future dimension only when an accepted ADR amendment defines its name, type, meaning, provenance requirement, and policy-production rule. It is not an ungoverned extension bag.

The logical identity is the deterministic hash of `(native_evidence_record_id, native_evidence_record_version, policy_logical_id)`. A policy correction or corrected evaluation creates an append-only successor within that logical lineage. Evaluation under a different policy logical identity creates a distinct lineage.

`persist_semantic_input(...)` accepts an exact native record reference and an exact strict-known policy reference. The service loads both records, verifies their hashes and temporal eligibility, applies the policy deterministically, constructs the output itself, authorizes correction lineage, and delegates only mechanical insertion to the repository. Callers cannot supply any semantic output field.

`strict_known_input(*, evidence_record_id, evidence_record_version, effective_as_of, known_by)` returns the unique accepted, non-conflicted, non-superseded semantic-input record whose native reference matches exactly and whose record and governing policy are both strict-known at the same coordinates. Because policy has one fixed logical lineage, selection cannot drift between competing policy families. Divergent roots or branches are rejected; latest-write and lexical-version ordering are prohibited.

### Determinism, provenance and replay

The deterministic evaluation fingerprint includes the exact native record identity/hash, exact policy identity/hash, matched rule ID, and every output field. Re-evaluating the same exact inputs produces the same record content and ID.

Historical replay uses persisted exact identities and hashes. It never reinterprets an old record under a newer policy. A missing referenced record, hash disagreement, unavailable policy, or unresolved correction/conflict fails closed.

### Dependency direction

The dependency graph is acyclic:

```text
hunter.value_capture (observation-only records)
        ↓ exact immutable reference
hunter.evidence_semantic_inputs
        ↓ strict-known EvidenceSemanticInputRecord
hunter.evidence_assembly Evidence Semantics Authority
        ↓ AuthoritativeEvidenceSemantics
CanonicalEvidenceAssemblyService
```

`hunter.evidence_semantic_inputs` may import observation-only model types from `hunter.value_capture`. It must not import `hunter.evidence_assembly`, `hunter.valuation_methodology`, valuation services, or downstream composition packages. `hunter.evidence_assembly` consumes its public protocol and record type and has no write path into it.

## Evidence Semantics Authority

`CanonicalEvidenceSemanticsAuthority` remains owned by `hunter.evidence_assembly`, but its input boundary changes materially:

- it consumes exactly one strict-known `EvidenceSemanticInputRecord` for the requested native record/version and cutoff;
- it does not inspect `FundamentalEvidenceRecord` fields to derive classification dimensions;
- it does not own a classification ruleset;
- it does not accept caller-supplied semantic dimensions;
- it copies and validates the upstream authority's exact semantic inputs into `AuthoritativeEvidenceSemantics`, preserving the upstream record ID/version/hash as mandatory provenance.

`AuthoritativeEvidenceSemantics` gains the full ADR 0021 envelope, correction lineage, and exact `evidence_semantic_input_record_id`, version, and content hash. Its deterministic content identity includes that upstream provenance.

`strict_known_semantics(...)` returns unavailable unless the referenced upstream semantic-input record is independently strict-known at the same cutoff. Evidence Semantics may reject an upstream record for incompatibility with its own contract, but may never replace, weaken, recalculate, or override any upstream semantic dimension.

## Methodology Contract Authority

The accepted activation/declaration split is retained:

- `ValuationMethodologySnapshot.accepts_assembled_evidence` is the sole methodology-level activation flag and defaults to `False`.
- A per-target `MethodologyEvidenceInputContract` states the exact entity, representation, `value_capture_pathway_id`, currency, unit, accounting window, accepted shapes, assembly rules, continuity requirements, provenance minimums, conflict policy, quality minimums, missingness and strict-known behavior.

The sole owner is `CanonicalValuationMethodologyAuthority` with mechanical persistence in `ValuationMethodologyRepository`.

`strict_known_contract(*, contract_id, contract_version, effective_as_of, known_by)` requires both the exact contract and its exact governing methodology snapshot to satisfy `effective_at <= effective_as_of`, `recorded_at <= known_by`, and `known_at <= known_by`. It returns unavailable unless the governing snapshot is the exact snapshot in force at those coordinates and has `accepts_assembled_evidence == True`.

The contract uses its existing `(contract_id, contract_version)` identity. `contract_id` is pathway-scoped. Corrections name `supersedes_contract_version` within one `contract_id`; branching and divergent duplicates are rejected.

## Evidence Shape Registry Authority

The Registry remains governed, immutable reference data owned by `hunter.evidence_assembly` under ADR 0025. Exact version lookup is strict-known. A changed shape is a new ADR-authorized version, never an in-place mutation or code-only change.

## Assembly semantic lineage

`AssembledFundamentalEvidenceRecord` gains required `constituent_semantic_lineage`, ordered index-for-index with its deterministic constituent order. Each entry persists:

- exact `AuthoritativeEvidenceSemantics` record ID/version/hash;
- exact upstream `EvidenceSemanticInputRecord` ID/version/hash;
- exact policy snapshot ID/version/hash;
- semantic effective and known times.

Every lineage field participates in `assembly_content_hash` and record `content_hash`. Replay uses these exact persisted references and never reruns policy selection or semantic derivation. A missing reference or hash mismatch fails closed. Corrections create a successor assembly and never mutate earlier lineage.

## Exact amendments to accepted ADRs

### ADR 0021

The following paragraph is appended to ADR 0021's record-family section:

> *(As amended by ADR 0028: `FundamentalEvidenceRecord` remains observation-only and gains no semantic-classification field. A distinct upstream `EvidenceSemanticInputPolicySnapshot` and `EvidenceSemanticInputRecord` family, produced exclusively by `CanonicalEvidenceSemanticInputAuthority` in `hunter.evidence_semantic_inputs`, supplies immutable, strict-known, provenance-complete semantic-classification inputs. These records never relabel or mutate native evidence. `ValuationMethodologySnapshot` additionally carries `accepts_assembled_evidence`, and the distinct `MethodologyEvidenceInputContract` family carries exact per-target assembly terms, as defined by ADR 0028.)*

This amendment recognizes:

- `ValuationMethodologySnapshot.accepts_assembled_evidence`;
- `MethodologyEvidenceInputContract`;
- `EvidenceSemanticInputPolicySnapshot`;
- `EvidenceSemanticInputRecord`.

The amendment explicitly reaffirms that `FundamentalEvidenceRecord` remains observation-only and gains none of the semantic fields owned by the new authority.

### ADR 0025

The following subsection is added immediately after ADR 0025's Methodology Contract section:

> ### Supporting authority ownership *(added by ADR 0028)*
>
> Methodology Contract production and persistence belong exclusively to `CanonicalValuationMethodologyAuthority` and `ValuationMethodologyRepository`. Evidence Shape Registry governance and persistence remain with `hunter.evidence_assembly`. Canonical semantic-classification inputs belong exclusively to `CanonicalEvidenceSemanticInputAuthority` and `EvidenceSemanticInputRepository` in upstream `hunter.evidence_semantic_inputs`; `FundamentalEvidenceRecord` remains observation-only. Evidence Semantics belongs to `hunter.evidence_assembly` and may consume semantic-classification dimensions only from an exact strict-known `EvidenceSemanticInputRecord`; it may not infer, replace, weaken, or override them.

ADR 0025's Assembled Fundamental Evidence Lineage table gains this exact row:

> | constituent semantic lineage | Ordered, index-aligned exact references and content hashes for each constituent's `AuthoritativeEvidenceSemantics`, upstream `EvidenceSemanticInputRecord`, and governing `EvidenceSemanticInputPolicySnapshot`; mandatory in assembly identity and historical replay. |

Accordingly, ADR 0025 is amended to state:

- Methodology Contract ownership belongs to `hunter.valuation_methodology` as defined above.
- Evidence Shape Registry ownership remains `hunter.evidence_assembly`.
- the Canonical Evidence Semantic Input Authority belongs exclusively to `hunter.evidence_semantic_inputs` and is upstream of Evidence Semantics;
- Evidence Semantics belongs to `hunter.evidence_assembly` and consumes only `EvidenceSemanticInputRecord`;
- `AssembledFundamentalEvidenceRecord` requires `constituent_semantic_lineage` as defined above.

No ADR 0025 lossless-composition invariant, native-evidence precedence rule, methodology-input eligibility boundary, unavailable-state rule, or correction rule is weakened.

## Compatibility

- **ADR 0021:** amended only for the new record families and activation field; observation-only native evidence is reaffirmed.
- **ADR 0022:** methodology permitted values and `CanonicalValuationService` eligibility ownership remain unchanged.
- **ADR 0023:** supplies the amendment-governed reference-data precedent.
- **ADR 0024:** scalar-semantics boundary is unchanged.
- **ADR 0025:** amended exactly as listed above.
- **ADR 0026:** Comparative Valuation receives no right to assembled evidence.
- **ADR 0027:** downstream Market Validation composition receives no new authority or activation.

Repositories remain mechanical under ADR 0009. Every selection is strict-known under ADR 0020.

## Current availability decision

Acceptance of this ADR would authorize architecture only. It would not implement or populate any authority, activate assembled evidence, dispatch a command, or unblock Issue #190. Issue #190 remains blocked until separate implementation and independent review establish all required production authorities.

## Consequences

- Observation and semantic interpretation remain separate canonical concerns.
- Evidence Semantics receives complete authoritative inputs without guessing from native evidence.
- One upstream owner controls all present and future semantic-classification input dimensions.
- Historical classification remains reproducible through exact native, policy, semantic-input, semantics, and assembly lineage.
- Implementation requires a new package, service, repository, two immutable record families, public read protocol, and the bounded ADR 0025/assembly-lineage changes described above.

## Rejected alternatives

- Adding semantic fields to `FundamentalEvidenceRecord`: rejected because it violates observation-only ownership.
- Deriving semantics from a limited tuple of native fields: rejected because those fields do not authoritatively determine every required dimension.
- Caller- or manifest-supplied semantic inputs: rejected because provenance and replay would be unauthoritative.
- Manual per-record exceptions: rejected because they create opaque competing authority.
- Letting Evidence Semantics own both semantic-input policy and downstream validation: rejected because it collapses upstream classification-input authorship into its consumer.
- Current/latest policy evaluation during replay: rejected because it rewrites historical meaning.

## Traceability

- Issue #190 remains blocked.
- Issue #191 governs this architecture preparation.
- ADPR-0005 records the evidence and option analysis.
- Draft PR #192 remains Draft.
- No implementation plan or production activation is authorized.
