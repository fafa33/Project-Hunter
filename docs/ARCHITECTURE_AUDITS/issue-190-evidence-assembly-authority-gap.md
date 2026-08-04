# Architecture Gap Report: Evidence Assembly Authority Prerequisites (Issue #190)

## Metadata

- Reviewed artifact: `src/hunter/evidence_assembly/` (`service.py`, `repository.py`, `registry.py`, `models.py`)
- Reviewed revision: `origin/main` HEAD
- Governing ADR: [ADR 0025](../ADR/0025-canonical-valuation-evidence-assembly-authority.md), Canonical Valuation Evidence Assembly Authority (Accepted)
- Triggering issue: [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190), Canonical Evidence Assembly Orchestration Module (Undispatched)
- Role: Implementer, acting under `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`
- Date: 2026-08-04

## Objective

Issue #190's own "Required authorization gate" requires proof, before any code change, that an undispatched orchestration module over `CanonicalEvidenceAssemblyService` creates no new authority, ownership, persistence, or evidence contract: "If any item is `YES` or `UNCERTAIN`, stop without code changes."

This report documents why that gate returns `UNCERTAIN`/`YES` for the module's central write operation (`assemble`), enumerates exactly what production capability is missing, and why implementing the missing capability is itself architecturally significant and out of scope for Issue #190 or any purely mechanical orchestration issue.

No production code, test code, or Pull Request was produced as part of this report. This is a documentation-only artifact.

## Summary Finding

`CanonicalEvidenceAssemblyService` — ADR 0025's sole exercising authority — cannot be constructed with production-backed collaborators today. Of its five constructor dependencies, three have **no implementation anywhere in `src/`**. Every existing construction of them is a test-only in-memory fake, confined to `tests/test_canonical_evidence_assembly.py`.

This is a structural difference from the two precedent orchestration modules this issue is modeled on:

- `hunter.mispricing.command` (Issue #183) constructs `CanonicalMispricingService` from `MispricingRepository` + `ObservedMarketFactRepository` — both real, production-backed repositories.
- `hunter.asymmetry.command` (Issue #187) constructs `CanonicalAsymmetryService` from `AsymmetryRepository` + `ObservedMarketFactRepository` — both real, production-backed repositories.
- `CanonicalEvidenceAssemblyService` requires five collaborators; only two are production-backed.

## Evidence

`CanonicalEvidenceAssemblyService.__init__` (`src/hunter/evidence_assembly/service.py:65-78`) requires all five as mandatory keyword arguments with no default:

| Constructor parameter | Protocol (`service.py`) | Production implementation | Evidence |
|---|---|---|---|
| `repository` | n/a | Yes — `AssembledEvidenceRepository` | `src/hunter/evidence_assembly/repository.py:56` |
| `native_evidence_query` | `NativeEvidenceQuery` (`service.py:29-38`) | Yes — `overlapping_evidence` | `src/hunter/value_capture/service.py:190-205` |
| `methodology_contract_authority` | `MethodologyContractAuthority` (`service.py:41-44`) | **No** | see below |
| `evidence_shape_registry_authority` | `EvidenceShapeRegistryAuthority` (`service.py:47-48`) | **No** | see below |
| `evidence_semantics_authority` | `EvidenceSemanticsAuthority` (`service.py:51-54`) | **No** | see below |

Repository-wide search confirms the three missing types are constructed nowhere in `src/`:

```
$ grep -rn "MethodologyEvidenceInputContract(" src/    # no matches
$ grep -rn "EvidenceShapeRegistry(" src/                # no matches
$ grep -rn "AuthoritativeEvidenceSemantics(" src/       # no matches
```

Every construction of these three types exists only in `tests/test_canonical_evidence_assembly.py`, as explicit test-only fakes:

- `_ContractAuthority` (`tests/test_canonical_evidence_assembly.py:159-164`) — returns one hardcoded, in-memory `MethodologyEvidenceInputContract` (`_contract()`, starting line 203), never persisted.
- `_RegistryAuthority` (`tests/test_canonical_evidence_assembly.py:167-172`) — wraps one hardcoded `EvidenceShapeRegistry` fixture, never persisted.
- `_SemanticsAuthority` (`tests/test_canonical_evidence_assembly.py:175-180`) — an in-memory `dict`, populated ad hoc per test by `_seed_authorities`, never persisted.
- `_NativeEvidenceQuery` (`tests/test_canonical_evidence_assembly.py:151-156`) is also a fake, but only because the test isolates itself from a real database; a real, production implementation of this one protocol does exist (`overlapping_evidence` above) and is usable outside tests.

## Why Evidence Assembly cannot legally construct its write path without them

1. **`assemble()` is unconditionally gated on all three.** `service.py:104-120` fetches and strict-known-validates the methodology contract; `service.py:121-135` fetches and strict-known-validates the Evidence Shape Registry snapshot; `service.py:271-310` (`_validate_authoritative_semantics`) calls `evidence_semantics_authority.strict_known_semantics` once per constituent and rejects the assembly if the authoritative record is `None` or disagrees with the constituent's declared metadata. None of these branches has a fallback, default, or optional path — a `None` return from any of them raises `CanonicalEvidenceAssemblyError`, consistent with ADR 0025's fail-closed philosophy ("Unknown remains unknown," ADR 0025 §"The lossless-only rule").

2. **Even read-only use requires full construction.** Because the three protocols are mandatory constructor arguments with no default, a caller cannot construct `CanonicalEvidenceAssemblyService` at all — including to reach only the read-only `strict_known()` method — without supplying *something* satisfying all three protocols.

3. **Issue #190's own scope forbids inventing what's missing.** Its Scope section permits the module to "call existing canonical Evidence Assembly service APIs" only — not to invent a data source those APIs depend on. Supplying real implementations of the three authorities requires deciding: what a persisted `MethodologyEvidenceInputContract`, `EvidenceShapeRegistry` version, and `AuthoritativeEvidenceSemantics` classification actually are in production; who is authorized to author and version them; what repository or table stores them; and what correction/replay lineage governs them. None of that is a decision an orchestration-layer implementer may make silently.

4. **That design work is architecturally significant under accepted governance.** `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s Scope section states the process is mandatory when a change "creates or materially changes one or more of the following": *canonical authority or ownership*; *persistence, correction, versioning, or migration semantics*; *evidence, provenance, sufficiency, or calibration contracts* — three of its seven explicit triggers are hit simultaneously by implementing these authorities. The same section adds: "When significance is uncertain, the change is treated as architecturally significant until the uncertainty is resolved."

5. **Conclusion.** Neither (a) implementing the three authorities as part of Issue #190, nor (b) writing throwaway/stub authorities inside `command.py` that would silently make an undocumented architecture decision about their shape and semantics, is in scope for Issue #190. Issue #190 authorizes wrapping of an *already-authorized, production-constructible* capability; the Evidence Assembly write path is not yet production-constructible.

## Missing Authority 1 — Methodology Contract Authority

- Backing record: `MethodologyEvidenceInputContract` (`src/hunter/evidence_assembly/models.py:98-144`).
- Must own, per ADR 0025 §"Methodology contract": persisting and versioning the declared evidence-input contract every `ValuationMethodologySnapshot` (and future methodology) states — accepted native evidence families; whether and how assembled evidence is accepted; accepted Evidence Shapes and assembly-rule versions; the required accounting interval and exact coverage rule; continuity requirements (entity/representation/pathway boundaries); provenance minimums; conflict policy; confidence/quality minimums; entity/representation/currency/unit scope; missingness behavior; strict-known cutoff behavior.
- Must expose strict-known retrieval by `(contract_id, contract_version, known_by)` — the exact shape of `strict_known_contract` (`service.py:41-44`).
- **Ownership is not yet settled by accepted governance.** ADR 0025 frames the contract as something "every valuation methodology" declares, which suggests this authority is most naturally an extension of the already-accepted `ValuationMethodologySnapshot` authority (`hunter.valuation_methodology`) rather than a wholly new one — but ADR 0025 does not say this explicitly, and no ADR currently assigns persistence ownership for this record family to any existing service. This is the first question architecture preparation must resolve.

## Missing Authority 2 — Evidence Shape Registry Authority

- Backing record: `EvidenceShapeRegistry` version snapshot (`src/hunter/evidence_assembly/registry.py:13-53`), containing `EvidenceShape` entries (`models.py:20-35`).
- Must own, per ADR 0025 §"Evidence Shape Registry": persisting versioned reference data classifying disclosure structure — native-vs-derived, cadence, accounting-period semantics, cumulative-vs-period-specific, event-driven-vs-interval, and which composition operations are structurally compatible with each shape.
- Must expose strict-known retrieval by `(version, known_by)` — the exact shape of `strict_known_registry` (`service.py:47-48`).
- **Ownership is already settled by ADR 0025.** The ADR explicitly states: "The Evidence Shape Registry is governed by the Canonical Evidence Assembly Authority under this ADR's own amendment mechanism: Registry entries are governance-authored, versioned reference data... require the same accepted-ADR-governed amendment discipline this repository already applies to fixed policy constants (the pattern ADR 0023 established for `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`)." This is the most narrowly-scoped of the three gaps: only the persistence/versioning implementation and the ADR-governed content-amendment mechanism remain to be built; the owner is already named.

## Missing Authority 3 — Evidence Semantics Authority

- Backing record: `AuthoritativeEvidenceSemantics` (`src/hunter/evidence_assembly/models.py:64-95`).
- Must own, per ADR 0025's assembly preconditions (§"Assembly preconditions," items 1-7) and `service.py:271-310` (`_validate_authoritative_semantics`): the authoritative, strict-known classification of one specific native `FundamentalEvidenceRecord` version against the Evidence Shape Registry — its `shape_id`, currency, raw unit, accounting meaning, supply-basis identity, and value-capture pathway identity — as an independently verifiable record the assembly service cross-checks against what a caller-supplied `AssemblyConstituent` merely *declares* (`service.py:282-310` rejects any constituent whose declared metadata does not match the authoritative semantics record).
- Must expose strict-known retrieval by `(evidence_record_id, evidence_record_version, known_by)` — the exact shape of `strict_known_semantics` (`service.py:51-54`).
- **Ownership is the least settled of the three.** ADR 0025 defines the assembly-time *check* this data must satisfy but, unlike the Registry, does not explicitly name who classifies and persists it. Whether shape classification is assigned by `hunter.value_capture` at evidence-ingestion time, by the Evidence Assembly Authority itself at classification time, or by a new, separate classification authority is unresolved in accepted governance and must be decided by architecture preparation, not inferred by an implementer.

## Recommendation

File one prerequisite architecture-preparation issue covering all three authorities together, since they are invoked jointly by the single `assemble()` operation and share the same open ownership question. That issue must go through `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s full lifecycle (an ADPR, and — if the preparation concludes persistence/ownership decisions are needed — a governing ADR or ADR 0025 amendment) before any implementation, because it creates or materially changes canonical authority/ownership and persistence/versioning semantics for three new record families. It is not a mechanical implementation issue of the kind Issue #183/#187/#190 were.

Proposed issue text is filed separately (see governance proof below for the issue number) and cross-links this report.

## Governance citations

- [ADR 0025](../ADR/0025-canonical-valuation-evidence-assembly-authority.md) — Canonical Valuation Evidence Assembly Authority (Accepted), §"Methodology contract," §"Evidence Shape Registry," §"Assembly preconditions," §"The lossless-only rule."
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`, §"Scope."
- `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`, §"BLOCKED," §"AWAITING HUMAN DECISION."
- [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190) — Required authorization gate.

## Traceability note

`docs/architecture-index.md`'s Decision Registry and ADR Mapping tables did not list ADR 0025 at all, despite its Accepted status (it was correctly listed in the Component Mapping table's "Valuation" row — the omission was narrower than this report originally stated). Corrected as part of Issue #191 / ADPR-0005's work: ADR 0025 is now registered in the ADR Mapping table, and the Evidence and Valuation Component Mapping rows reference ADPR-0005 and ADR 0028.

## Explicit confirmation

No production code was written or modified. No test code was written or modified. `src/`, `tests/`, and `src/hunter/__main__.py` remain byte-identical to `origin/main` HEAD. This report is introduced via PR #192 alongside other architecture-documentation changes (ADPR-0005, ADR 0028, and registry updates) for Issue #191. Only documentation files were changed; no `src/` or `tests/` changes are included.
