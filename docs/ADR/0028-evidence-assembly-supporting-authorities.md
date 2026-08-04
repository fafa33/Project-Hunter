# ADR 0028: Canonical Evidence Assembly Supporting Authorities

## Status

Proposed.

Governing preparation record: [ADPR-0005 — Canonical Evidence Assembly Supporting Authorities](../architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md). This is revision 2 of both records, produced in response to independent hostile architecture review findings against revision 1 (five `CHANGES REQUIRED` findings against Draft PR #192, tracked under Issue #191). Revision 1 left four material questions open (OQ-001 through OQ-004) and split Methodology Contract ownership ambiguously between `hunter.valuation_methodology` and a possible new sibling package. Revision 2 resolves every one of those questions with a single, singular design; none is deferred to implementation.

Neither this ADR nor ADPR-0005 has been independently accepted. This ADR is drafted for a second round of independent architecture review and carries no authority until both records are separately accepted. It does not authorize implementation of any of the three authorities it defines, does not modify `CanonicalEvidenceAssemblyService`'s or `CanonicalValuationMethodologyAuthority`'s existing code, does not resume Issue #190 (which remains `BLOCKED`), and does not activate any Market Validation input.

## Context

ADR 0025 (Accepted) establishes the Canonical Evidence Assembly Authority and fully specifies what three supporting collaborators must validate before `assemble()` may produce an `AssembledFundamentalEvidenceRecord`. `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` and ADPR-0005 confirm none of the three — Methodology Contract, Evidence Shape Registry, Evidence Semantics — has a production implementation.

Revision 1 of this ADR resolved Evidence Shape Registry ownership (reaffirming ADR 0025's own explicit text) but left Methodology Contract's scope and Evidence Semantics' authoring model as open questions, and proposed a Methodology Contract design (a standalone sibling record under `hunter.valuation_methodology`, without fully specifying its relationship to `ValuationMethodologySnapshot`) that independent review correctly identified as incomplete against ADR 0025's own accepted text: ADR 0025's "Exact amendment to ADR 0022" states "a future `ValuationMethodologySnapshot` version may declare acceptance" of assembled evidence — a sentence revision 1 acknowledged (as Open Question OQ-004) but did not resolve.

This revision resolves that tension by direct inspection of the already-accepted, unmodifiable code both the contract-consuming and methodology-owning services already run: `CanonicalEvidenceAssemblyService.assemble()`'s `_validate_methodology_contract` (`src/hunter/evidence_assembly/service.py:339-387`) checks a fetched `MethodologyEvidenceInputContract`'s `entity_id`, `representation_id`, `currency`, `unit`, `accounting_window_start`, and `accounting_window_end` against the *specific target* being assembled — fields `ValuationMethodologySnapshot` structurally cannot carry as single values, since one methodology snapshot is consumed across every entity `CanonicalValuationService` ever values (`src/hunter/valuation_methodology/models.py:44-71` carries no entity or window scope at all). This is not an implementation preference; it is a fact about code this ADR is not authorized to change. The design below is the single, exact resolution that satisfies both that structural fact and ADR 0025's literal text.

## Decision

Hunter establishes two new supporting authorities. `MethodologyEvidenceInputContract` (Methodology Contract) and `AuthoritativeEvidenceSemantics` (Evidence Semantics) each get one exact, singular design below, with no alternative left open. `EvidenceShapeRegistry` is unchanged from revision 1.

### Methodology Contract Authority

**Resolved scope: per-target, exactly.** One `MethodologyEvidenceInputContract` record declares acceptance for exactly one `(governing methodology version, entity, representation, value-capture pathway, currency, unit, accounting window)` combination. This is not a design preference — it is required by `_validate_methodology_contract`'s existing, unmodifiable field-by-field comparison against the specific target being assembled (`service.py:372-387`), and by the `strict_known_contract(*, contract_id, contract_version, known_by)` protocol's already-fixed signature (`service.py:41-44`), which carries no target parameter a global declaration could be resolved against.

**Resolved activation/declaration split.** Two records jointly control eligibility, with one unambiguous activation authority:

1. `ValuationMethodologySnapshot` gains one new field, `accepts_assembled_evidence: bool` (default `False` on every existing and future snapshot that does not explicitly set it). This is the **sole activation authority** — conforming exactly to ADR 0025's literal text that "the `ValuationMethodologySnapshot` in force explicitly declares acceptance of assembled evidence." No per-target contract may be authored, and none may be returned as strict-known, for a governing snapshot whose `accepts_assembled_evidence` is not `True` at the same replay cutoff.
2. `MethodologyEvidenceInputContract` — a new, per-target sibling record family — is the **declaration instance**: it states, for one exact target, "the exact requirements under which [the methodology] accepts [assembled evidence]" (the same ADR 0025 sentence's second clause). It exists only in reference to, and only when authorized by, an in-force snapshot with `accepts_assembled_evidence = True`.

**Resolved cross-reference.** The contract references the snapshot, never the reverse: every `MethodologyEvidenceInputContract` carries `governing_methodology_record_id`, `governing_methodology_logical_id`, and `governing_methodology_semantic_version`, naming the exact `ValuationMethodologySnapshot` it was authored under. `ValuationMethodologySnapshot` carries no list of, or reference to, any contract — an immutable snapshot does not grow a mutable back-reference list as targets are added over time.

**Resolved conflict/disagreement behavior.** At `strict_known_contract(contract_id, contract_version, known_by)` resolution time, the authority (see "Exact canonical service owner," below) must independently verify, at the *same* `known_by` cutoff:

- the per-target contract itself is strict-known (`effective_at`, `recorded_at`, `known_at` all `<= known_by`; `quality_state == "accepted"`; `conflict_state` in `{"none", "resolved"}`), and
- its referenced governing `ValuationMethodologySnapshot`, resolved via `CanonicalValuationMethodologyAuthority.strict_known_methodology`, is *also* strict-known at the identical `known_by`, and its `accepts_assembled_evidence` field is `True`.

If either check fails — including the case where a later correction to the snapshot sets `accepts_assembled_evidence` back to `False`, or the snapshot referenced by the contract has itself been superseded by a correction that is not strict-known at the requested cutoff — `strict_known_contract` returns `None`. `assemble()`'s existing, unmodified code already treats a `None` return as `CanonicalEvidenceAssemblyError("no exact strict-known methodology evidence contract")` (`service.py:109-110`). This is fail-closed, not a new failure mode: disagreement between the two records never partially succeeds and never falls back to one record alone.

**Resolved exact canonical service owner: `CanonicalValuationMethodologyAuthority`** (`src/hunter/valuation_methodology/service.py:36`), not a new service and not a new package. It gains two new public methods: `persist_evidence_input_contract(...)` (authoring) and `strict_known_contract(*, contract_id: str, contract_version: str, known_by: datetime) -> MethodologyEvidenceInputContract | None` (retrieval, satisfying the `MethodologyContractAuthority` protocol's exact existing signature without modification to `hunter.evidence_assembly.service`). One service already owns `ValuationMethodologySnapshot`'s strict-known resolution; giving the cross-check above to a second, different service would duplicate that resolution logic and violate Constitutional Rule 5 (Single Source of Truth). This resolves former Open Question OQ-003 definitively: not a new sibling service.

**Resolved exact repository owner: `ValuationMethodologyRepository`** (`src/hunter/valuation_methodology/repository.py:23`), extended with a new table and mechanical methods for `MethodologyEvidenceInputContract`, not a new repository class and not a new package. `AssembledEvidenceRepository` already establishes the precedent of one repository class owning two related record families (`AssembledFundamentalEvidenceRecord` and `AssemblyConflictRecord`) in this same codebase (`src/hunter/evidence_assembly/repository.py`); this ADR applies the identical, already-audited pattern.

**Resolved authorship-governance boundary.** A `MethodologyEvidenceInputContract` may be authored only through `CanonicalValuationMethodologyAuthority.persist_evidence_input_contract`, by whatever governance process is already authorized to call `CanonicalValuationMethodologyAuthority.persist_methodology` for corrections to the methodology itself (this ADR does not change who that is — it is an existing, out-of-scope operational-authorization question, unchanged from ADR 0022). It is never authored by `CanonicalEvidenceAssemblyService`, by an `assemble()` caller, or by any Evidence Assembly-side code — preserving the dependency direction fixed below.

**Package ownership and dependency direction (resolves Finding 5).** The entire Methodology Contract Authority — construction, validation, authorization, persistence, versioning, correction — is exclusively owned and performed by `hunter.valuation_methodology`: one service (`CanonicalValuationMethodologyAuthority`), one repository (`ValuationMethodologyRepository`). No part of that ownership is split with, delegated to, or shared with `hunter.evidence_assembly`.

*Dependency direction, precisely stated.* `hunter.evidence_assembly.service` (the consumer) never imports `hunter.valuation_methodology` at all: `MethodologyContractAuthority` is a `typing.Protocol`, already defined there and unchanged by this ADR, and Python `Protocol`s are structurally typed — a class satisfies one by shape, without the protocol's module ever importing the implementing class. So the business-logic and service-level dependency is unambiguously one-directional: Evidence Assembly (downstream) depends on Valuation Methodology's implementation (upstream); Valuation Methodology's service and repository logic depends on nothing in Evidence Assembly, and there is no import cycle in either direction.

One narrow, unavoidable exception exists at the *data-type* level, not the service or business-logic level, and this ADR states it rather than leaving it for a reviewer to discover: `MethodologyEvidenceInputContract`'s dataclass is defined in `hunter.evidence_assembly.models` because ADR 0025's already-accepted, unmodifiable `service.py` fixes the Protocol's return type to that exact class (`strict_known_contract(...) -> MethodologyEvidenceInputContract | None`). For `CanonicalValuationMethodologyAuthority.strict_known_contract` to construct and return a value of that exact type (required for `mypy`-clean typing and for `hunter.evidence_assembly.service`'s field-by-field access to work at all), `hunter.valuation_methodology.service` must import that one dataclass from `hunter.evidence_assembly.models`. This is a leaf, dependency-free data-type import — `hunter.evidence_assembly.models` itself imports only `hunter.value_capture.models` and carries no business logic, no service reference, and no import of `hunter.valuation_methodology` or `hunter.evidence_assembly.service` — so it introduces no cycle and no coupling to Evidence Assembly's actual authority, construction, or validation logic. It is the one, bounded, unmodifiable-code-forced deviation from an otherwise strictly one-directional dependency graph; every other aspect of ownership belongs exclusively to `hunter.valuation_methodology`, exactly as this section's opening paragraph states.

### Evidence Shape Registry Authority

Unchanged from revision 1: owned by `hunter.evidence_assembly`, new repository, no correction lineage (immutable per version, new content is a new version), amendment governed by the ADR 0023 pattern. See "Canonical envelope" below for the complete, Finding-4-conformant specification (revision 1 specified this narratively; revision 2 restates it against the explicit envelope template every record family must now use).

### Evidence Semantics Authority

**Resolved canonical classification-authoring authority and deterministic rule source.** Hunter establishes a new governed, versioned reference-data artifact, `EvidenceSemanticsClassificationRuleset`, structurally and governance-wise identical to `EvidenceShapeRegistry` (same package, same amendment discipline, same "no correction lineage — new content is a new version" rule, same ADR-governed-amendment-only content changes per the ADR 0023 pattern). Each ruleset version is a deterministic, total function from a native `FundamentalEvidenceRecord`'s own fields (`evidence_type`, `source_methodology`, `attribution_rule_id`, `unit`) to the six classification outputs `AuthoritativeEvidenceSemantics` must carry (`shape_id`, `currency`, `raw_unit`, `accounting_meaning`, `supply_basis_id`, `pathway_id`), or to an explicit "no matching rule" outcome for inputs it does not cover.

**Resolved service owner:** a new service, `CanonicalEvidenceSemanticsAuthority`, in `hunter.evidence_assembly` — a distinct class from `CanonicalEvidenceAssemblyService`, preserving the already-accepted constructor-injection boundary (`evidence_semantics_authority` is injected into `CanonicalEvidenceAssemblyService`, so it cannot be the same object). It owns applying the in-force `EvidenceSemanticsClassificationRuleset` to a native evidence record and persisting the resulting `AuthoritativeEvidenceSemantics`.

**Resolved persistence owner:** a new repository, `EvidenceSemanticsRepository`, in `hunter.evidence_assembly`.

**Resolved rule versioning:** `EvidenceSemanticsClassificationRuleset.version` is totally ordered by acceptance order (the order in which each version's governing ADR amendment was accepted), never by lexicographic string comparison — closing the "v9"/"v10" ordering ambiguity a naive string comparison would introduce.

**Resolved authorship: who may publish a classification.** Only `CanonicalEvidenceSemanticsAuthority`, and only as the deterministic output of applying one exact, named `EvidenceSemanticsClassificationRuleset` version to one exact, named `FundamentalEvidenceRecord` version. There is no API, method, or path by which a caller supplies a classification's output fields directly.

**Resolved manual exceptions: prohibited, explicitly.** No per-record human override, manual classification, or ad hoc exception path exists at any authority in this chain. A `FundamentalEvidenceRecord` no strict-known `EvidenceSemanticsClassificationRuleset` version can classify remains explicitly unavailable (`strict_known_semantics` returns `None`), never manually asserted. This is required by ADR 0025's own already-accepted rejection of "ad hoc human manifest approval... without persisted authority" (§"Alternatives Considered") applied one layer upstream of assembled evidence itself, and by Constitutional Rule 6 (Explainability), which prohibits an authority whose output cannot be traced to a governed, reproducible rule.

**Resolved conflict behavior:** because classification is a pure, deterministic function of `(evidence_record_id, evidence_record_version, ruleset_version)`, two classification attempts under the *same* ruleset version can never disagree by construction; there is no "competing classification" conflict class analogous to `AssemblyConflictRecord`'s omitted-evidence conflicts. The only conflict this authority recognizes is two independent root records (no `supersedes_record_id` on either) sharing the same logical identity (see "Canonical envelope" below) with different content — an implementation-error case, not a business conflict, surfaced and rejected exactly as `_authorize_correction`'s existing "a root record already exists" pattern already does elsewhere in this repository (`hunter.evidence_assembly.service`, `hunter.valuation.service`, `hunter.valuation_methodology.service`).

**Resolved missingness behavior:** unchanged from `assemble()`'s existing code — a `None` return from `strict_known_semantics` is already treated as `CanonicalEvidenceAssemblyError("no exact strict-known authoritative evidence semantics")` (`service.py:280-281`). No new missingness behavior is introduced.

**Resolved strict-known selection algorithm:** see "Canonical envelope" below.

## Canonical envelope (resolves Finding 4, all three authorities)

Every field below is either present on the existing, unmodified dataclass (`MethodologyEvidenceInputContract`, `AuthoritativeEvidenceSemantics` in `src/hunter/evidence_assembly/models.py`; `ValuationMethodologySnapshot` in `src/hunter/valuation_methodology/models.py`) or is a new field this ADR authorizes adding. No field is left for a future implementation issue to invent.

### `MethodologyEvidenceInputContract`

| Envelope field | Definition |
|---|---|
| `record_id` | Content-addressed hash of every field below except `record_id` itself, mirroring `AssembledFundamentalEvidenceRecord`'s existing `content_hash`-then-`record_id` pattern (`hunter/evidence_assembly/service.py:204-218`). |
| `logical_id` | **Exact logical identity**: deterministic hash of `(governing_methodology_logical_id, entity_id, representation_id, value_capture_pathway_id, currency, unit, accounting_window_start, accounting_window_end)`. Stable across corrections to the same target's declaration; changes only if the target scope itself changes (which is a new declaration, not a correction). |
| `record_version` / `schema_version` / `semantic_version` | Standard envelope versions, incremented per correction, mirroring every other record family. |
| `effective_at`, `recorded_at`, `known_at` | Standard bitemporal envelope, unchanged semantics from every other record family in this repository. |
| `supersedes_record_id`, `correction_reason` | Standard single-predecessor correction lineage; `bool(supersedes_record_id) == bool(correction_reason.strip())`, mirroring `ValuationMethodologySnapshot._validate_correction` (`models.py:147-151`). |
| **Provenance** | `governing_methodology_record_id`, `governing_methodology_logical_id`, `governing_methodology_semantic_version` (new fields; the exact `ValuationMethodologySnapshot` this contract was authorized under); `authorized_by` (mirrors `ValuationMethodologySnapshot.authorized_by`, `models.py:69`). |
| `quality_state`, `conflict_state` | Standard, same enums as every other record family. |
| Missingness/availability | Not a field — a behavior: unavailable is represented by `strict_known_contract` returning `None`, never by a stored "unavailable" record. |

**Exact uniqueness constraint**: at most one record may exist per exact `(logical_id, semantic_version)` pair with divergent content; a second, content-differing record for the same pair is rejected at persistence time (mirroring `AssembledEvidenceRepository._insert_authorized`'s existing divergent-duplicate rejection, `repository.py:76-112`).

**Strict-known selection algorithm**: `strict_known_contract(contract_id=logical_id, contract_version=semantic_version, known_by)` returns the unique record matching that exact `(logical_id, semantic_version)` pair whose `effective_at`, `recorded_at`, `known_at` are all `<= known_by`, whose `quality_state == "accepted"` and `conflict_state in {"none", "resolved"}`, **and** whose governing `ValuationMethodologySnapshot` (resolved via `CanonicalValuationMethodologyAuthority.strict_known_methodology` at the same `known_by`) has `accepts_assembled_evidence == True` — otherwise `None`. Because `contract_version` is an exact match (not "latest"), and divergent duplicates are rejected at persistence time, at most one record can ever satisfy the query: selection is unambiguous by construction, with no tie-breaking rule needed.

**Correction successor vs. unresolved conflict**: a correction successor explicitly names its predecessor via `supersedes_record_id` with a strictly later `recorded_at`/`known_at` and a mandatory `correction_reason` — a deliberate, authorized act. An unresolved conflict is two records with no such predecessor/successor relationship sharing the same `logical_id` (e.g., two independent roots) — rejected at persistence time before it can ever reach strict-known selection, so no `conflict_state == "open"` case is reachable for this record family in practice (unlike Evidence Semantics, below, where the "no matching rule" outcome creates a legitimate, non-error missingness case rather than a conflict).

### `EvidenceShapeRegistry`

| Envelope field | Definition |
|---|---|
| `record_id` | Not applicable as a separate field — `version` (below) is the sole persisted identity, mirroring `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`'s existing versioned-constant precedent (ADR 0023), which carries no `record_id`/`logical_id` distinction either. |
| `logical_id` | Not applicable, for the same reason. |
| `record_version` / `schema_version` / `semantic_version` | `version: str` (already defined, `registry.py:15`) is the sole version identity. |
| `effective_at`, `recorded_at`, `known_at` | Already defined (`registry.py:17-19`), unchanged. |
| `supersedes_record_id`, `correction_reason` | **Not applicable, explicitly.** A Registry version is immutable reference data; a content change is a new `version` string, never a correction to an existing one — the identical rule ADR 0023 already established for `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`, which this ADR extends rather than reinvents. |
| Provenance | Every version's content must cite the exact ADR (or ADR amendment) that authorized it, exactly as `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`'s value is cited to ADR 0023 in `hunter.value_capture.models` today. |
| `quality_state`, `conflict_state` | Already defined (`registry.py:20-21`), unchanged. |
| Missingness/availability | `strict_known_registry` returns `None` when no version matches; unchanged existing behavior. |

**Exact uniqueness constraint**: two different content payloads claiming the same `version` string is a hard rejection at persistence time — no two registries may ever share a version with different content.

**Strict-known selection algorithm**: `strict_known_registry(version, known_by)` returns the unique record with that exact `version` string whose `effective_at`, `recorded_at`, `known_at` are all `<= known_by` — an exact-match lookup, never "latest," unchanged from the existing protocol. Unambiguous by construction (uniqueness constraint above).

**Correction successor vs. unresolved conflict**: not applicable — there is no correction relationship for this record family; a conflicting duplicate is a hard persistence-time rejection, not a runtime conflict state.

### `AuthoritativeEvidenceSemantics`

| Envelope field | Definition |
|---|---|
| `record_id` | Content-addressed hash of every field below except `record_id` itself. |
| `logical_id` | **Exact, separate logical identity** (resolves Finding 4's explicit instruction not to use only `(evidence_record_id, evidence_record_version)`): deterministic hash of `(evidence_record_id, evidence_record_version, ruleset_version)`. This third coordinate — which governed ruleset produced the classification — is not present on the native `FundamentalEvidenceRecord` and gives this record family an identity space independent of, though referencing, the native record's own. |
| `record_version` / `schema_version` / `semantic_version` | Standard envelope versions, incremented per correction (an implementation-error correction to a specific classification run — see "Resolved conflict behavior," above; never a ruleset content change, which instead produces an independent, parallel `logical_id` under the new ruleset version). |
| `effective_at`, `recorded_at`, `known_at` | Standard bitemporal envelope; already defined (`models.py:74-76`), unchanged in meaning. |
| `supersedes_record_id`, `correction_reason` | **New fields**, standard single-predecessor correction lineage, added to this dataclass (currently absent) exactly as Finding 4 requires. |
| **Provenance** | `ruleset_version: str` (new field, replacing this ADR's revision-1 placeholder `classification_rule_reference`) — the exact `EvidenceSemanticsClassificationRuleset` version whose deterministic function produced this classification. |
| `quality_state`, `conflict_state` | Already defined (`models.py:77-78`), unchanged. |
| Missingness/availability | `strict_known_semantics` returns `None` when no ruleset version, strict-known at the requested cutoff, classifies the given evidence record/version; unchanged existing behavior in `assemble()`'s consuming code. |

**Exact uniqueness constraint**: at most one non-superseded, accepted record may exist per exact `(logical_id, semantic_version)` — i.e., per `(evidence_record_id, evidence_record_version, ruleset_version)` triple at a given correction depth; a second, content-differing root for the same triple is rejected at persistence time, mirroring the Methodology Contract's identical rule.

**Strict-known selection algorithm**: `strict_known_semantics(evidence_record_id, evidence_record_version, known_by)` — note this protocol signature, already fixed by `service.py:51-54`, carries no `ruleset_version` parameter — selects, among all `AuthoritativeEvidenceSemantics` records for the given `(evidence_record_id, evidence_record_version)` whose own `effective_at`/`recorded_at`/`known_at` are `<= known_by` and `quality_state == "accepted"`/`conflict_state in {"none","resolved"}`, the one produced under the **highest `ruleset_version`** (by acceptance order, not lexicographic order — see "Resolved rule versioning," above) whose *own* `EvidenceSemanticsClassificationRuleset` record is *itself* independently strict-known at the same `known_by`. This is deterministic, replay-safe, and never a "current" fallback: it always resolves to exactly what was knowable, under the most-refined-yet-knowable rule set, at the requested cutoff — never today's ruleset if a later cutoff would not yet have known it existed.

**Correction successor vs. unresolved conflict**: a correction successor supersedes an erroneously-computed classification *within the same* `(evidence_record_id, evidence_record_version, ruleset_version)` logical lineage, via `supersedes_record_id` and mandatory `correction_reason` — this fixes a bug in the authority's own application of a still-correct ruleset, never a ruleset content change (which instead produces an independent, parallel logical lineage under a new ruleset version, per "Resolved conflict behavior," above). An unresolved conflict is two independent roots for the same `(evidence_record_id, evidence_record_version, ruleset_version)` triple — rejected at persistence time, identical in mechanism to the Methodology Contract's rule.

## Current availability decision

Adoption of this ADR does not itself implement either authority, populate any `EvidenceSemanticsClassificationRuleset` or `EvidenceShapeRegistry` version, add `accepts_assembled_evidence` to any `ValuationMethodologySnapshot`, or author any `MethodologyEvidenceInputContract`. `CanonicalEvidenceAssemblyService.assemble()` remains unreachable in production until a separately authorized implementation issue builds all three authorities and Issue #190 (or a successor) is separately unblocked and completed. This is deliberate fail-closed behavior, consistent with ADR 0025's own "Current availability decision," not an implementation defect to bypass.

## Exact amendment to ADR 0022

This ADR amends only ADR 0022's `ValuationMethodologySnapshot` field description, exactly as stated here. No other ADR 0022 section — the permitted model family, the fixed 365-day horizon, entity-class Scope criteria, discount-rate and sensitivity policies, or any other decision — is changed.

ADR 0021's "Required new record families and minimum fields" table lists, for `ValuationMethodologySnapshot`: "permitted model family; horizon; currency; assumptions; discount/risk and terminal-value rules; sensitivity policy; model aggregation; required evidence; normalization policy ID; correlation group." One sentence is appended immediately after that table's existing `AssembledFundamentalEvidenceRecord` cross-reference sentence (added by ADR 0025):

> *(As amended by ADR 0028: `ValuationMethodologySnapshot` additionally carries a mandatory `accepts_assembled_evidence: bool` field, defaulting to `False` on every snapshot that does not explicitly set it, per ADR 0025's own pre-authorization that "a future `ValuationMethodologySnapshot` version may declare acceptance [of assembled evidence]... without requiring a new methodology ADR." This field is the sole activation authority for Assembled Fundamental Evidence eligibility; it grants no eligibility by itself and must be read together with ADR 0028's `MethodologyEvidenceInputContract` record family for the exact, per-target terms under which acceptance applies.)*

`ValuationMethodologySnapshot.__post_init__`'s existing Milestone-2-fixed invariants (`permitted_model_identifier`, `horizon_days`, `correlation_group`, `normalization_policy_id`) are unmodified by this amendment; the new field is purely additive.

## Exact amendment to ADR 0025

This ADR amends ADR 0025's §"Evidence Shape Registry" → "Governance owner" paragraph (unchanged from revision 1's amendment, restated here for completeness) and replaces revision 1's "Supporting authority ownership" subsection with the fully resolved design below.

ADR 0025's "Governance owner" paragraph is amended to add the following sentence, appended to that paragraph, with the original sentences otherwise unchanged:

> *(As amended by ADR 0028: implementation and persistence of Evidence Shape Registry snapshots is owned by `hunter.evidence_assembly`, exactly the same authority this paragraph already names as governance owner.)*

The subsection "Supporting authority ownership," added to ADR 0025 immediately after §"Methodology contract" by ADR 0028 revision 1, is replaced in full by:

> ### Supporting authority ownership *(added by ADR 0028, revision 2)*
>
> The Methodology Contract this ADR's "Methodology contract" section requires is jointly represented by two records, both owned by `hunter.valuation_methodology`: an `accepts_assembled_evidence` field on the in-force `ValuationMethodologySnapshot` (the sole activation authority — satisfying this ADR's requirement that "the `ValuationMethodologySnapshot` in force explicitly declares acceptance of assembled evidence"), and a per-target `MethodologyEvidenceInputContract` sibling record (the declaration instance, stating "the exact requirements under which it accepts it" for one exact entity/representation/pathway/currency/unit/accounting-window combination). `hunter.evidence_assembly` never authors either record; it only consumes them through the unchanged `MethodologyContractAuthority` protocol.
>
> The Evidence Semantics Authority referenced in this ADR's "Assembly preconditions" (invariants 1-7) and implemented in `_validate_authoritative_semantics` — the party that persists and versions each `AuthoritativeEvidenceSemantics` classification, and the governed `EvidenceSemanticsClassificationRuleset` reference data it is deterministically computed from — is owned by `hunter.evidence_assembly`.
>
> Neither assignment changes any invariant, precondition, validation rule, or record-family field this ADR otherwise defines. See ADR 0028 for complete record-family, persistence, versioning, correction, provenance, replay, conflict, and amendment-governance semantics for both authorities.

No other part of ADR 0025 — including its lossless-only rule, assembly preconditions, `AssembledFundamentalEvidenceRecord` field definitions, temporal and replay semantics, conflict-resolution rules, or compatibility table — is changed by this ADR.

## Consequences

Positive:

- Every material design question independent review raised is resolved by a single, named, unambiguous decision — no alternative is left open for implementation to choose.
- The activation/declaration split (snapshot field + per-target sibling record) satisfies ADR 0025's literal accepted text exactly, closing the normative-contradiction risk revision 1 left open as OQ-004.
- Methodology Contract ownership is fully coherent under `hunter.valuation_methodology`: one service, one repository, no split ownership, and no service-level or business-logic dependency on `hunter.evidence_assembly` from the upstream owner — only one narrow, unavoidable data-type import (see "Package ownership and dependency direction").
- Evidence Semantics gains a fully specified, deterministic, auditable authoring model with manual exceptions explicitly and permanently prohibited — closing the Constitutional Rule 6 (Explainability) risk revision 1 left open as OQ-002.
- Every one of the three record families now has a complete canonical envelope, uniqueness constraint, strict-known selection algorithm, and correction-vs-conflict distinction — no implementation issue can silently invent architecture to fill a gap.

Costs and risks:

- Three authorities (Methodology Contract's two collaborating records, Evidence Shape Registry, Evidence Semantics' two collaborating records) must still be implemented, tested, and independently reviewed before Issue #190 can be unblocked — this ADR authorizes architecture, not implementation effort.
- `EvidenceSemanticsClassificationRuleset`'s initial rule content (which `evidence_type`/`source_methodology`/`attribution_rule_id` combinations map to which `shape_id`/`accounting_meaning`) is not authored by this ADR; a future ADR amendment must publish the first version before any real evidence record can ever be classified. This ADR authorizes the mechanism, not the first version's content — publishing governed reference-data content is itself a future ADR-governed act, consistent with how ADR 0025 itself authorized the Evidence Shape Registry's mechanism without publishing its first version's content.
- Adding `accepts_assembled_evidence` to `ValuationMethodologySnapshot` and `logical_id`/`supersedes_record_id`/`correction_reason`/`ruleset_version` to `AuthoritativeEvidenceSemantics` is additive but requires updating every existing construction site in `tests/test_canonical_evidence_assembly.py` and `hunter.valuation_methodology`'s own test fixtures — a concrete, bounded implementation cost, not a blocking architectural defect.

## Alternatives Considered

### Fold the full per-target contract directly onto `ValuationMethodologySnapshot` (conform to ADR 0025's literal text with no sibling record at all)

Rejected. `_validate_methodology_contract`'s existing, unmodifiable code requires the fetched contract to carry one exact target's `entity_id`/`representation_id`/`accounting_window`; folding these onto `ValuationMethodologySnapshot` would make "the methodology" itself entity-specific, forcing a new methodology-snapshot "correction" for every new target — corrupting correction lineage, which is meant to represent methodology refinement, not target enrollment. The resolved hybrid (activation field + per-target sibling) satisfies the same accepted text without this defect.

### Standalone sibling record with no reference to `ValuationMethodologySnapshot` at all (revision 1's design)

Rejected on independent review and superseded by this revision. Left ADR 0025's "the `ValuationMethodologySnapshot` in force explicitly declares acceptance" text unsatisfied — no snapshot field asserted anything, so nothing was actually "declared" by the snapshot itself.

### New standalone package or new service for Methodology Contract, independent of `hunter.valuation_methodology`

Rejected, for the same reason revision 1 rejected it: methodology-level policy already has one canonical owner; a new package or service would duplicate that ownership, violating Constitutional Rule 5.

### Permit manual exceptions for Evidence Semantics classification, with a documented approval/provenance process

Rejected. Every exception path this alternative would require — who approves, what provenance an approval carries, how an approval replays — reintroduces exactly the "ad hoc human manifest approval... without persisted authority" failure mode ADR 0025 already rejected for composed *values*, one layer upstream, for classification *inputs* to those same values. A governed, versioned, deterministic ruleset achieves the same coverage-over-time (new evidence shapes get covered by publishing a new ruleset version, exactly as new Evidence Shapes are added to the Registry) without ever needing a per-record exception.

### Derive Evidence Semantics classification from a single global "current" ruleset, not per-classification-record ruleset versioning

Rejected. Would violate ADR 0020's strict-known replay requirement directly: a later ruleset correction would silently reclassify every historical evidence record on next read, exactly the failure mode ADR 0023's own precedent (and its explicit warning about un-versioned constants) already identifies and prohibits.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
|---|---|
| 0009 | Every new repository method remains mechanical; no eligibility, correction, or composition decision moves into a repository. The cross-service strict-known check (Methodology Contract vs. its governing snapshot) is service-owned, inside `CanonicalValuationMethodologyAuthority`, never repository-owned. |
| 0020 | Every new authority's retrieval is strict-known, bounded by exact identity coordinates and `known_by`; no current/latest fallback anywhere, including the ruleset-version selection algorithm, which is explicitly defined to never fall back to "today's" ruleset. |
| 0021 | Reaffirmed. `FundamentalEvidenceRecord`'s field contract is unmodified; the "Required new record families and minimum fields" table gains one cross-reference sentence, mirroring ADR 0025's own precedent for the identical kind of addition. |
| 0022 | Amended narrowly — see "Exact amendment to ADR 0022," above. Every Milestone-2-fixed invariant is untouched; the amendment is purely additive. |
| 0023 | Reaffirmed and relied upon as the direct precedent for both Evidence Shape Registry's and `EvidenceSemanticsClassificationRuleset`'s amendment-governance discipline and version-ordering rule. Unmodified. |
| 0024 | Unaffected; `valuation`'s scalar-semantics boundary is orthogonal to this ADR's supporting-authority ownership decisions. |
| 0025 | Amended narrowly — see "Exact amendment to ADR 0025," above. Every invariant, precondition, validation rule, and record-family definition ADR 0025 fixes is otherwise unchanged and reaffirmed. |
| 0026 | Unaffected. Comparative Valuation's exclusion from Assembled Fundamental Evidence, reaffirmed in ADR 0026 §"Compatibility," is untouched by this ADR. |
| 0027 | Unaffected. Market Validation's exclusion from any direct right to Assembled Fundamental Evidence, reaffirmed in ADR 0027 §"Compatibility," is untouched by this ADR. |

No accepted ADR is superseded, weakened, or contradicted by this ADR.
