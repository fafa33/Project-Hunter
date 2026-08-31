# ADPR-0012 — ADR 0033 Source Handling design and implementation contract

## Metadata

- ADPR ID: `ADPR-0012`
- Preparation state: `READY_FOR_REVIEW`
- Self-assessment: `READY_FOR_ADR`
- Version: 4
- Author: repository owner-directed architecture work
- Reviewers: independent architecture audit complete and merged to main — `docs/ARCHITECTURE_AUDITS/adpr-0012-source-handling-independent-audit.md`, verdict `READY_FOR_ADR_WITH_MINOR_FINDINGS` (Issue #395 / PR #396)
- Created: 2026-08-30
- Approved: not yet
- Governing issue: #393
- Preparation PR: recorded on merge
- Exact baseline: `d237ae7cea05a6e906e6c38d092d7f56c3b8a0e5`
- Related Epic: not yet created
- Planned or produced ADR: ADR 0036 (next free ADR number; not drafted by this record)
- Governing accepted decision: [ADR 0033](../ADR/0033-source-handling-classification-authority.md)
- Base preparation: [ADPR-0008](ADPR-0008-source-handling-classification-authority.md) (`APPROVED`)
- Blocked implementation: Issue #390 governed Issue execution path
- Supersedes: not applicable
- Superseded by: not applicable

## Executive Summary

ADR 0033 assigned canonical ownership of source-handling facts and policy to the
Evidence Intelligence consumer-side Source Handling Authority, and then
deliberately deferred every mechanic to "a separate design and implementation
contract" that "must be produced and reviewed before implementation begins."
That contract was never produced. The absence is now a demonstrated blocker: no
production `SmartPromptMachine` composition root can exist, because its required
`source_handling_resolver` has no production authority to resolve from.

This record prepares that contract. Ownership is **not** reopened — ADR 0033
already fixed it, and this preparation treats that as binding. What is selected
here is the realization: a dedicated `SourceHandlingAuthorityService` acting as
the sole publisher inside the ownership domain ADR 0033 named, paired with a
mechanical, restart-safe persistence repository that stores and verifies but
never derives classification.

Five materially distinct options were compared on one criteria set. Two are
rejected because ADR 0033 forbids them outright. The recommendation is
`READY_FOR_ADR` for a narrowly scoped ADR 0036 that binds the mechanics
specified in "Selected Design and Implementation Contract" below.

This record authorizes no runtime implementation.

## Problem Statement

### Current condition

ADR 0033 is `Accepted` and binding. Its mechanics are deferred and unwritten.
Production consequences, verified on baseline `d237ae7`:

- `AuthorityStore` (`src/hunter/evidence_intelligence/source_handling.py:40`) is
  a process-local in-memory object. It has no persistence and does not survive
  restart.
- `AuthorityStore.direct_write` unconditionally raises
  `"direct repository authority writes are forbidden"`.
- Records enter only through `AuthorityStore.publish`, which requires a
  `PublicationAuthorization`.
- No production module issues a `PublicationAuthorization` or publishes `FACT`
  or `POLICY`. Every caller of `issue_publication_authorization` and of the
  fact/policy seeding path is under `tests/`.
- Consequently `SmartPromptMachine.__init__`
  (`src/hunter/evidence_intelligence/smart_prompt_routing.py:485`) cannot be
  constructed in production: its `source_handling_resolver` has nothing to
  resolve from.

### Desired condition

A reviewed contract that fixes the deferred mechanics precisely enough that a
later implementation can build a production Source Handling authority, and
strictly enough that no consumer, orchestrator, repository, provider, or
Smart Prompt Machine can fabricate authority over its own permissions.

### Decision required

Select and specify the mechanics ADR 0033 deferred: publication owner,
publication-authorization mechanics, durable record families and schemas,
persistent store semantics, secret and sensitive-content mechanics, the
production resolver seam, the Issue-sourced content boundary, correction and
replay semantics, and migration/rollout/rollback.

### In scope

- realization of the ADR 0033 owner as a concrete publishing component;
- `PublicationAuthorization` issuance, binding, anti-forgery, single use, and
  staleness rules;
- canonical record families, identities, temporal semantics, and supersession;
- restart-safe, transactional, tamper-evident persistence that stays mechanical;
- secret and sensitive-content classification mechanics and fail-closed rules;
- the exact read-only consumer resolver seam and its deterministic failure set;
- how owner-authorized GitHub Issue content becomes eligible for classification;
- correction, strict-known replay, and deterministic historical resolution;
- additive migration, rollout ordering, observability, and rollback.

### Out of scope

- runtime implementation of any of the above (no tables, services, or wiring);
- Issue #390 composition root;
- span-level classification (ADR 0033 Non-Goals);
- retroactive purge or deletion of already-persisted records (ADR 0033 Non-Goals);
- Model Adapter, Response Validator, provider routing, n8n, provider order, and
  fallback runtime, none of which this record touches;
- any reopening of ADR 0033 ownership;
- Comparative Valuation, Issue #389, Issue #386.

## Problem Validation

### Gap map

**What ADR 0033 already decides (binding; not reopened here)**

| Decided | Location |
|---|---|
| Evidence Intelligence consumer-side Source Handling Authority is sole canonical owner of handling facts and governed policy | §Canonical ownership |
| Every other component — callers, providers, orchestrators, prompt construction, persistence adapters, repositories, generic cores, Model Adapter — is a consumer only and may not create, select, override, or substitute authority | §Canonical ownership |
| Caller/provider input is evidence or expectation, never authority; may never establish a less-restrictive state | §Binding safety invariants |
| Retainability and every other permission is derived, never assertable | §Binding safety invariants |
| Simultaneous restrictions must not collapse into one mutually exclusive value | §Binding safety invariants |
| Unknown, missing, unavailable, conflicting, or ambiguous authority yields `BLOCKED` | §Binding safety invariants |
| Facts and policies are immutable, versioned historical records; corrections supersede and never rewrite | §Historical and replay invariants |
| Replay uses only authority applicable and knowable at the requested cutoff; current state never substitutes; historical absence stays absence | §Historical and replay invariants |
| Persistence independently rederives every decision and never trusts a caller-supplied classification or decision identity | §Persistence invariant |

**What ADR 0033 explicitly defers (this record's subject)**

Handling dimensions and vocabularies; classification scope and mixed-content
mechanics; admissible evidence and restriction-release rules; source and
source-type derivation; record schemas; event and policy lifecycle mechanics;
deterministic identities and canonicalization; historical applicability and
selection algorithms; conflict and reconciliation mechanics; retention
categories, dispositions, and build statuses; persistence mechanics;
rejected-build audit details; legacy decoding; typed omission and redaction
representation; secret and credential mechanics; APIs, modules, storage, and
migrations; conformance and counterfactual tests.

**What production code already implements**

| Implemented | Location |
|---|---|
| `AuthorityStore` append-only semantics, canonical-key bookkeeping, CAS on head | `source_handling.py:40`–`:120` |
| `publish()` refusing unauthorized publication, verifying payload equality, released-restriction derivation, and head CAS | `source_handling.py:50` |
| `direct_write()` unconditional refusal | `source_handling.py:110` |
| `PublicationAuthorization` shape incl. `effective_from` / `recorded_at` / `known_at` | `source_handling.py:21` |
| `strict_known_eligible()` requiring all three timestamps ≤ cutoff | `source_handling.py:491` |
| `strict_known_head()` linear non-branching supersession resolution | `source_handling.py:499` |
| `resolve_pre_model_source_handling()` binding FACT / POLICY / FIELD_CATEGORY_REGISTRY / AUTHORIZATION_RULE to one rule | `pre_model.py:279` |
| `EvidencePreModelSourceHandlingAuthority` consumer-facing shape | `pre_model.py:244` |
| One real construction site at validation time | `response_validator.py:1628` |

**What is test-only**

| Test-only | Location |
|---|---|
| All `FACT` / `POLICY` / `FIELD_CATEGORY_REGISTRY` seeding | `tests/evidence_pre_model_source_handling_fixture.py` |
| All `issue_publication_authorization` calls | `tests/test_source_handling_authority_enforcement.py`, `tests/source_handling_runtime_harness.py` |
| Every `authority_store()` construction outside the factory itself | `tests/test_source_handling_authority_contract.py` and others |
| Every `SmartPromptMachine` / registry construction | `tests/test_smart_prompt_machine_phase_*.py` |

**What must be selected before runtime implementation**

The nine areas enumerated under "Selected Design and Implementation Contract".

### Canonical sources checked

`docs/ADR/0033`, `docs/ADR/0031`, `docs/ADR/0034`, `docs/ADR/0035`,
`docs/architecture-records/ADPR-0008`, `docs/CANONICAL_ARCHITECTURE_MAP.md`,
`docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`,
`docs/ARCHITECTURE_AUDIT_PROTOCOL.md`, `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`,
`docs/architecture-index.md`, `docs/ADR/README.md`,
`docs/architecture-records/README.md`. No accepted document already resolves the
deferred mechanics.

## Motivation

Without this contract, Evidence Intelligence has a named owner that nothing can
instantiate. The observable cost today is Issue #390: the governed Issue
execution path terminates at a composition root that cannot be built. The cost
of resolving it badly is worse than the cost of delay — a resolver that
manufactures its own authority would silently declare unreviewed content safe to
process and persist, which is precisely the condition ADR 0031 forbids and
ADR 0033 exists to prevent.

## Existing Architecture

Four canonical record families participate, each scoped and resolved
independently, then required to agree on exactly one authorization rule:

```text
FACT                    scope = <document_id>
POLICY                  scope = policy:<document_id>:v1
FIELD_CATEGORY_REGISTRY scope = registry:<document_id>:v1   (bound by POLICY)
AUTHORIZATION_RULE      scope = SOURCE_HANDLING
```

Resolution is strict-known: a record participates only when `effective_from`,
`recorded_at`, and `known_at` are all present and all ≤ cutoff. Heads are
resolved by linear supersession; branching or duplicate identity is refused.
Publication is capability-gated by `PublicationAuthorization` and compare-and-set
against the exact current head. Storage is in-memory and per-process.

## Constraints

### Constitutional

Evidence-first and replay-first obligations. Missing authority may never be
represented as permission.

### Governance and accepted ADRs

ADR 0033 ownership and invariants are binding and unamended by this record.
ADR 0031 remains the governing pre-model foundation. ADR 0020 strict-known replay
is relied upon without amendment. ADR 0009 producer/repository/consumer
separation must survive. ADR 0032 consumer-side ownership must not leak into a
project-neutral core.

### Technical

The four families, their scope-binding rules, `strict_known_eligible`,
`strict_known_head`, and `publish()` CAS semantics already exist and are relied
upon rather than replaced.

### Operational

Publication requires operator-provisioned key material. A deployment without it
must fail closed rather than degrade to permissive.

### Persistence and migration

Additive only. Existing persisted records are not reclassified. No backfill may
be presented as historical truth.

### Replay and historical reconstruction

Deterministic resolution keyed by `(document_id, cutoff)`. Historical absence is
a result, not an error to be repaired from current state.

### Compatibility

`EvidenceSpan` unchanged. `EvidencePreModelSourceHandlingAuthority` shape
unchanged. `AuthorityStore` read semantics preserved so the in-memory store
remains a valid test double.

### Security and privacy

Classification must not create durable plaintext exposure of the material being
classified, and must not emit content to logs.

### Performance and scalability

Resolution is per-build and cutoff-parameterized; indexed reads on
`(family, scope)` with a `known_at` bound are sufficient at expected volume.

### Evidence and provenance

Every published record carries its authorization lineage, evidence identifiers,
and supersession predecessor.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | ADR 0033 §Design / Implementation Contract — Deferred | Accepted ADR | Mechanics deferred; contract must be produced **and reviewed** before implementation | Direct quotation of binding decision | Supports the need for this record |
| E-002 | ADR 0033 §Consequences | Accepted ADR | "this decision alone is not sufficient to implement against" | Direct quotation | Supports |
| E-003 | `source_handling.py:40-48` | Production code | `AuthorityStore` state is four in-memory dicts plus a lock; no persistence | Read at baseline | Supports Gap 1 |
| E-004 | `source_handling.py` `direct_write` | Production code | Unconditional refusal of direct writes | Read at baseline | Constrains options C |
| E-005 | grep of `issue_publication_authorization` | Repository-wide | Zero production callers; all under `tests/` | Complete search at baseline | Supports Gap 1 |
| E-006 | `smart_prompt_routing.py:485` | Production code | `SmartPromptMachine` requires `source_handling_resolver` | Read at baseline | Establishes the blocked consumer |
| E-007 | grep for `SmartPromptMachine(` | Repository-wide | No production construction anywhere in `src/` | Complete search at baseline | Establishes Issue #390 blocker |
| E-008 | `pre_model.py:279-330` | Production code | Four-family resolution requiring single-rule agreement | Read at baseline | Fixes the resolver contract shape |
| E-009 | `source_handling.py:491` | Production code | Strict-known requires all three timestamps ≤ cutoff | Read at baseline | Fixes temporal semantics |
| E-010 | SPM-001, `docs/DEFECT_REGISTRY.json` | Accepted defect class | Ed25519 signing with issuer-private / verifier-public split and fail-closed key handling is the repository's established anti-forgery pattern | Recorded and guarded | Supports the authorization anti-forgery choice |
| E-011 | `intake.py:145` | Production code | `EvidenceIntelligenceIntake.ingest()` creates `EvidenceDocument` and persists spans | Read at baseline | Supports the Issue-content boundary |
| E-012 | `pre_model_repository.py:39` | Production code | Span inventory read fails closed when empty | Read at baseline | Supports the Issue-content boundary |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The existing four-family model is adequate for document-scoped classification | It already carries every dimension ADR 0033 names | High | A required handling dimension proves unrepresentable | Contract needs a fifth family before ADR |
| A-002 | SQLite in the existing Evidence Intelligence database is sufficient | Same store already holds spans the resolution is bound to | Medium-high | Concurrency exceeds single-writer capacity | Store swaps to a server engine; contract semantics unchanged |
| A-003 | Operator-provisioned signing material is available for publication | SPM-001 already establishes this operational pattern | Medium | No operator key path exists | Publication cannot activate; consumers stay blocked, which is correct |
| A-004 | Automated detection can supply admissible secret-presence evidence | Detector verdicts are evidence, not authority | Medium | Detection proves unreliable | `secret_presence_known=false` ⇒ `BLOCKED`; correct but restrictive |

## Architectural Dimensions

1. Publication ownership and the capability that separates publisher from consumer.
2. Authorization issuance, binding, forgery resistance, single use, staleness.
3. Durable record identity, schema, temporal triple, scope, lineage, supersession.
4. Persistence: durability, transactionality, concurrency, tamper evidence, and the mechanical/semantic boundary.
5. Secret and sensitive-content mechanics, including exposure during classification.
6. The consumer resolver seam and its failure algebra.
7. Eligibility of Issue-sourced content.
8. Correction and strict-known replay.
9. Migration, rollout, observability, rollback.

## Candidate Options

### Option A — Dedicated `SourceHandlingAuthorityService` plus mechanical repository

- Description: a distinct service module inside Evidence Intelligence is the sole publisher; a separate repository persists records mechanically.
- Authority and ownership: service holds classification authority; repository holds none.
- Boundaries: publication capability exists only inside the service; consumers receive a read-only resolver.
- Persistence and replay: append-only durable records, strict-known reads by cutoff.
- Evidence and provenance: authorization lineage and evidence ids on every record.
- Compatibility: preserves ADR 0009 separation and ADR 0033 ownership.
- Advantages: sharpest authority separation; repository can never assert; testable in isolation.
- Disadvantages: one more module and its bootstrap.
- Failure modes: missing key material blocks publication (fail-closed, correct).
- Migration implications: additive tables; in-memory store retained as test double.
- Reversibility: high — disable resolver wiring and consumers fail closed.
- Open dependencies: operator key provisioning.

### Option B — Evidence Intelligence-owned publication authority (diffuse)

- Description: publication capability lives across existing Evidence Intelligence modules rather than one named service.
- Authority and ownership: satisfies ADR 0033 at package granularity.
- Boundaries: weak — any module in the package could publish.
- Persistence and replay: same as A.
- Advantages: no new module.
- Disadvantages: no enforceable publisher boundary; the "consumer only" rule becomes convention, and Prompt/Context construction lives in the same package.
- Failure modes: an orchestrator inside the package publishes for itself.
- Reversibility: low — diffuse capability is hard to withdraw.
- Open dependencies: none.

### Option C — Repository-owned publication authority

- Description: the persistence repository determines and writes FACT/POLICY.
- Authority and ownership: repository becomes the semantic authority.
- Advantages: fewest moving parts.
- Disadvantages: directly contradicts ADR 0033 ("repositories … are consumers only"; "Persistence enforces this authority but does not acquire it") and would require reversing `direct_write`'s refusal.
- Reversibility: not applicable — rejected.

### Option D — Caller/orchestrator-owned classification

- Description: the consumer needing a build supplies the classification.
- Advantages: unblocks Issue #390 immediately.
- Disadvantages: exactly the condition ADR 0031 forbids and ADR 0033 was created to close; ADPR-0008 already rejected it on reproduced evidence.
- Reversibility: not applicable — rejected.

### Option E — External classification/policy adapter with Hunter-owned canonical publication

- Description: an external service proposes classifications; the Hunter-owned authority verifies and publishes.
- Authority and ownership: publication stays Hunter-owned; external output is evidence only.
- Advantages: access to stronger detectors; ADR-compatible if the adapter's output is strictly evidence.
- Disadvantages: adds an external trust boundary, availability dependency, and a channel that may carry classified material off-process; larger security surface for no ownership benefit today.
- Failure modes: adapter unavailable ⇒ blocked; adapter compromise ⇒ poisoned evidence.
- Reversibility: medium.
- Open dependencies: vendor selection, data-egress decision — none of which exists.

## Comparative Analysis

| Criterion | A dedicated service | B diffuse EI-owned | C repository-owned | D caller-owned | E external adapter |
|---|---|---|---|---|---|
| Authority separation | Strong — capability confined to one publisher | Weak — package-wide | Violates ADR 0033 | Violates ADR 0031/0033 | Strong if adapter output stays evidence |
| Secret/security handling | Strong — classification confined, no egress | Medium | Weak | Weak | Weakest — egress of classified material |
| Strict-known replay | Full | Full | Full | Unreliable — caller controls history | Full |
| Correction/supersession | CAS successors, non-branching | Same, unenforced ownership | Repository could rewrite | Caller could rewrite | Same as A |
| Concurrency/idempotency | Single-use authorization + head CAS in one transaction | Same mechanics, multiple publishers race by design | Undefined | Undefined | Same as A |
| Persistence | Additive durable tables, mechanical only | Same | Conflates store and authority | None | Same as A |
| Migration complexity | Low — additive | Low | Medium — must reverse `direct_write` | Low | High — external contract |
| Operational complexity | Low-medium — key provisioning | Low | Low | Lowest | High |
| Auditability | Strong — one publisher, full lineage | Medium | Weak | None | Medium |
| Reversibility | High | Low | Low | Low | Medium |
| Constitutional compliance | Compliant | Compliant but unenforced | Non-compliant | Non-compliant | Compliant |
| Governance compliance | Compliant | Marginal | Rejected by ADR 0033 | Rejected by ADR 0033 and ADPR-0008 | Compliant |
| Correctness | Highest | Medium | Low | Lowest | High |

## Falsification Results

Each mandatory hostile case was applied to the recommended design. A case
"survives" only if the design defeats it structurally rather than by convention.

| # | Hostile case | Defeating mechanism | Survives |
|---|---|---|---|
| H-01 | Caller submits `operation_restrictions_known=true` as if authoritative | Caller input never reaches a record body; only the service composes payloads, and `publish()` verifies the authorized payload hash | Yes |
| H-02 | `SmartPromptMachine` seeds its own `AuthorityStore` | Resolver seam hands consumers a read-only view with no publication capability; the capability token is constructible only in service bootstrap | Yes |
| H-03 | Repository direct write fabricates FACT/POLICY | `direct_write` refuses unconditionally; the durable schema has no insert path outside the transactional publish | Yes |
| H-04 | Owner-authorized Issue treated as automatically safe | Authorization establishes *who asked*, never *what the content is*; eligibility requires published FACT/POLICY for the document scope | Yes |
| H-05 | Current policy substituted for an old cutoff | `strict_known_eligible` requires all three timestamps ≤ cutoff; later records are invisible to earlier resolution | Yes |
| H-06 | Later correction overwrites prior historical result | Corrections are successors with `supersedes_*_id`; append-only storage retains predecessors; earlier cutoff still resolves the predecessor | Yes |
| H-07 | Authority disappears after process restart | Durable tables replace the in-memory store as the production backing | Yes |
| H-08 | Two conflicting publishers race | Head CAS inside one transaction; the loser is refused and must re-resolve | Yes |
| H-09 | Stale `PublicationAuthorization` replay | `authorization_id` is single-use and consumed in the same transaction; issuance window bounded; re-presentation refused | Yes |
| H-10 | Unknown secret presence silently treated safe | `secret_presence_known=false` or `UNKNOWN` yields `BLOCKED`; no permissive default exists | Yes |
| H-11 | Secret/credential content in logs or durable plaintext | Classification consumes transient input and records only detector verdicts, categories, and counts; logs carry record ids and reason codes only | Yes |
| H-12 | n8n/provider/model mutates authority | None holds the publication capability; the resolver is read-only; the handoff carries non-content lineage only | Yes |
| H-13 | Historically unknowable cutoff resolved from current state | Resolution is cutoff-parameterized; absence returns `AUTHORITY_NOT_KNOWN_AT_CUTOFF` and never falls back | Yes |
| H-14 | Consumer publishes through the resolver API | The resolver returns `EvidencePreModelSourceHandlingAuthority` over a read-only store view; no publish method is reachable from it | Yes |

Options C and D fail H-01, H-03, H-04, H-06, and H-14 by construction. Option B
fails H-02 and H-08 because the publisher boundary is convention, not capability.
Option E fails H-11 in its default form because classified material leaves the
process.

## Rejected Options

**Option C — repository-owned publication authority.** Rejected. ADR 0033
§Canonical ownership names repositories as consumers only and states persistence
"enforces this authority but does not acquire it." Adopting C would require
reversing `direct_write`'s refusal. Reconsider only if a later accepted ADR
reassigns ownership.

**Option D — caller/orchestrator-owned classification.** Rejected. ADPR-0008
already rejected it on reproduced evidence that a consumer supplying the
classification, its provenance, and the policy body controls the outcome
regardless of typing. Reconsider never under ADR 0031 and ADR 0033 as accepted.

**Option B — diffuse Evidence Intelligence ownership.** Rejected as the
realization, not as the ownership domain. ADR 0033's owner *is* Evidence
Intelligence; B fails only because package-level scoping gives no enforceable
publisher boundary while Prompt and Context construction live in the same
package. Reconsider if a capability boundary is later enforced by other means.

**Option E — external classification adapter.** Rejected for now. It is
ADR-compatible but adds an external trust boundary and egress of the material
being classified, for no ownership benefit at current scale. Reconsider when
detector quality becomes the binding constraint and a data-egress decision
exists; its evidence-only shape is preserved by the selected design, so adoption
later is additive.

## Selected Design and Implementation Contract

This section is the substance ADR 0036 must bind.

### 1. Canonical publication owner

`SourceHandlingAuthorityService`, a dedicated component inside the Evidence
Intelligence ownership domain ADR 0033 named, is the sole participant permitted
to determine and publish every family the resolver seam requires:
`FACT`, `POLICY`, `FIELD_CATEGORY_REGISTRY`, and `AUTHORIZATION_RULE` successor
records. `resolve_pre_model_source_handling` already requires a strict-known
head for all four families before any build may proceed
(`src/hunter/evidence_intelligence/pre_model.py:284-330`); a contract naming
only two of them would leave `FIELD_CATEGORY_REGISTRY` and `AUTHORIZATION_RULE`
without any owner or trusted admission path, and every resolve would then block
forever on a fresh deployment. It is the only holder of a
`SourceHandlingPublicationCapability`, which is constructible only during service
bootstrap from operator-provisioned material and is never reachable from any
resolver, repository, transport, or handoff type. Smart Prompt Machine, n8n,
providers, the fallback runtime, callers, orchestrators, and generic
repositories hold no publication authority of any kind.

**Genesis bootstrap.** `AUTHORIZATION_RULE` is the root of trust every other
family's `PublicationAuthorization` is validated against, so it cannot itself be
admitted through the ordinary authorization path — nothing can authorize the
first rule before a rule exists. It is admitted exactly once, through the
existing operator-authorized golden-digest bootstrap already implemented as
`publish_genesis_rule` (`source_handling.py:383-406`): the exact bytes of the
genesis rule are fixed by an operator-provisioned digest, admission is refused
unless family history is empty, and normal `PublicationAuthorization` governs
every later `FIELD_CATEGORY_REGISTRY`, `FACT`, `POLICY`, and successor
`AUTHORIZATION_RULE` publication (`publish_successor_rule`,
`source_handling.py:409-435`) from that point on. The genesis bootstrap is an
operator deployment action, not a runtime code path the service or any consumer
can trigger; it is out of the capability's reach the same as every other
publication surface named above.

### 2. `PublicationAuthorization` authority

Issued only by the service's authorization issuer. Each authorization binds
exactly: `authorization_id`, `publication_kind`, `governed_subject_scope`,
`authorized_payload_sha256`, `authorization_rule_id`, and the temporal triple
`effective_from` / `recorded_at` / `known_at`. Anti-forgery is an Ed25519
signature over those canonical claims, following the SPM-001 issuer-private /
verifier-public split, with missing or malformed key material failing closed.
`authorization_id` is included in the signed claims deliberately: it is the
single-use replay key, and a key that sits outside its own signature can be
relabeled onto a differently-scoped presentation without invalidating that
signature, defeating single-use enforcement rather than merely weakening it.
Binding it into the signature makes any change to `authorization_id` a
signature-verification failure, not a separate check that consumption could
skip. Consumption verifies the signature over the full claim set including
`authorization_id` and then consumes the id in the same transaction as the
publication it authorizes; replay is refused. An authorization is stale, and
refused, when it is not strict-known eligible at publication time or falls
outside its bounded issuance window. Corrections reuse the same issuance path
and must supersede the exact current head.

### 3. Durable Source Handling records

Four families, unchanged in meaning from the existing runtime:

| Family | Scope | Carries |
|---|---|---|
| `FACT` | `<document_id>` | sensitivity (+known flag), operation restrictions (+known flag), persistence restriction (+known flag), secret presence (+known flag), withdrawn, deleted-at-source, historically-unavailable, availability-known |
| `POLICY` | `policy:<document_id>:v1` | governed policy body, bound `field_category_registry_id` |
| `FIELD_CATEGORY_REGISTRY` | `registry:<document_id>:v1` | field map, safe-control proofs |
| `AUTHORIZATION_RULE` | `SOURCE_HANDLING` | rule body and its supersession chain |

Record identity is the SHA-256 of the canonical JSON of the payload excluding the
authorization envelope. Every record carries `effective_from`, `recorded_at`, and
`known_at`; all three are required and all three bound strict-known eligibility.
History is append-only. Correction is linear supersession through
`supersedes_*_id`; branching is refused by head compare-and-set, so exactly one
head exists per `(family, scope)` at any cutoff. Simultaneous restrictions are
represented as a set and are never collapsed to a single value.

### 4. Persistent authority store

`SourceHandlingAuthorityRepository`, backed by additive tables in the existing
Evidence Intelligence database: an append-only record table, an issued- and
consumed-authorization table, and a canonical-key table. One transaction covers
authorization consumption, head compare-and-set, record append, and canonical-key
marking; partial application is impossible. Indexes on `(family, scope)` and on
record identity serve cutoff-bounded reads. Concurrency is optimistic: the head
CAS refuses the loser, which must re-resolve rather than retry blindly. Reads are
strict-known and cutoff-parameterized. Tamper evidence is the stored payload
digest plus supersession-chain verification, re-checked on read; a mismatch is
`TAMPER_DETECTED` and blocks. The repository stores, verifies, and refuses; it
never derives, defaults, or asserts a classification, and retains no path
equivalent to `direct_write`.

### 5. Secret and sensitive-content mechanics

Classification operates on transient input and records only non-reversible
observations — detector verdicts, categories, and counts — never the classified
bytes. Every FACT dimension that can be omitted is paired with its own known
flag, symmetrically: `secret_presence`/`secret_presence_known`,
`operation_restrictions`/`operation_restrictions_known`,
`sensitivity`/`sensitivity_known`, and `persistence_restriction`/
`persistence_restriction_known`. `sensitivity` and `persistence_restriction`
are required dimensions under ADR 0033's binding invariants exactly as the
other two are; a contract that gave only two of the four dimensions a known
flag would leave the other two able to be silently omitted, and ADR 0033 states
plainly that "any unknown, missing, unavailable, conflicting, or ambiguous
required source-handling fact or policy authority yields `BLOCKED`" — a rule
that does not exempt these two. Unknown, absent, or `UNKNOWN` values for any of
the four known-flags yield `BLOCKED`, with no permissive default, enforced at
persistence exactly as `secret_presence_known` and `operation_restrictions_known`
already are: the repository independently rederives the flag from the payload
and refuses the publication rather than trusting a caller-asserted flag value.
`persistence_restriction` governs durable retention and reconstruction
eligibility independently, so material may be processable without being
retainable and retainable without being reconstructable. Access, retention, and
deletion restrictions derive from the resolved policy, never from a caller.
Logging and audit carry record identities, reason codes, and decision ids only;
no classified content is written to logs or durable plaintext merely to classify
it.

### 6. Production resolver seam

The exact consumer API is the existing `SourceHandlingAuthorityResolver`
protocol, which is **callable**, not a named method:

```text
SourceHandlingAuthorityResolver.__call__(document_id: str, cutoff: datetime)
    -> EvidencePreModelSourceHandlingAuthority
```

The resolver is therefore invoked as `resolver(document_id, cutoff)`. This
matches `SourceHandlingAuthorityResolver` in
`src/hunter/evidence_intelligence/smart_prompt_machine.py`, which declares
`__call__`, and the sole consumer call site in `PromptContextCompiler.compile()`,
which invokes `self._source_handling_resolver(request.document_id, cutoff)`. An
implementation exposing only a `.resolve(...)` method would raise `TypeError` at
every Smart Prompt Machine build, so the callable shape is binding and neither
the protocol nor the consumer is changed by this contract.

The returned authority is constructed over a read-only store view; no publication capability, issuer, or mutating method is reachable through
it. Deterministic failure states, each mapping to `BLOCKED`: `AUTHORITY_ABSENT`,
`AUTHORITY_NOT_KNOWN_AT_CUTOFF`, `POLICY_SCOPE_UNBOUND`, `RULE_AMBIGUOUS`,
`REGISTRY_UNAVAILABLE`, `TAMPER_DETECTED`. Substituting current or latest state
for an unresolved historical cutoff is forbidden; absence is returned as absence.

### 7. Issue-sourced content boundary

Owner authorization (`hunter-issue-agent-authorization-v1`) establishes who
requested execution. It never establishes that the content is safe, and it grants
no processing, retention, or reconstruction permission.

**Ordering is binding, not incidental.** `EvidenceIntelligenceIntakeService.ingest()`
durably persists `EvidenceSpan.excerpt` into `evidence_spans` unconditionally
today (`src/hunter/evidence_intelligence/intake.py`); it carries no Source
Handling gate of its own, because that gate is this contract's responsibility,
not intake's. Issue-sourced content must not reach that durable `ingest()` call
until the Source Handling Authority has published `FACT` and `POLICY` for the
target document scope and those records resolve `persistence_restriction` to a
value that permits retention. Until that resolution exists, Issue content
supplied for classification is held only as transient input — the same
transient-input posture §5 already requires of classification generally — and
is never written through the durable intake path. This reverses the sequence a
literal reading of this section previously implied (publish only after intake),
because that order let content the resolved policy might forbid retaining
become durable before the policy existed to forbid it. Once `FACT`/`POLICY`
confirm eligibility, ingestion proceeds through the unchanged existing
`EvidenceIntelligenceIntake` path, producing an `EvidenceDocument` and its span
inventory exactly as it does today. The Source Handling Authority publication
this sequencing requires is itself gated by the genesis and per-scope
authorization mechanics of §1 and §2; it grants no exception to them. Absent
published authority, the build is `BLOCKED`. Raw Issue text has no path to the
Smart Prompt Machine, to a build, to a handoff, or to the fallback runtime that
does not pass through this boundary. No direct Issue-to-fallback authority
exists or may be created.

This section binds only the ordering and eligibility gate; it does not compose
the Issue trigger, the intake call site, or any other part of a production
Issue #390 execution path, which remains separately scoped and unimplemented.

### 8. Correction and replay

Corrections never erase historical state; they append a successor that supersedes
the exact current head. Resolution at any cutoff returns the head that was
strict-known at that cutoff, so a correction recorded later is invisible to
earlier replay. Current or latest authority never substitutes for an earlier
cutoff. Replay is deterministic in `(document_id, cutoff)`: the same pair yields
the same decision or the same explicit absence, for the lifetime of the record
history.

### 9. Migration, rollout, and rollback

Migration is additive: new tables only, no alteration or reclassification of
existing persisted records. The in-memory `AuthorityStore` is retained as the
test double implementing the same read interface, so existing tests remain valid
without becoming a production path. No present-day classification may be
backfilled and presented as historical truth — a bootstrap publication carries
its true `recorded_at` and `known_at`, which correctly makes it unavailable for
earlier cutoffs. Deployment order: tables, then service bootstrap and key
provisioning, then publication for in-scope document scopes, then resolver
wiring, then consumer activation. Observability exposes publication and
resolution counts and `BLOCKED` reason codes without content. Rollback removes
resolver wiring, after which consumers fail closed; durable tables are retained,
because discarding published authority would destroy historical truth.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Operator key material never provisioned | Operational | Medium | Consumers remain blocked | Fail-closed is the correct state; rollout step is explicit | Timing only |
| Detector quality insufficient for secret presence | Security | Medium | Frequent `BLOCKED` | Option E remains additively adoptable | Detector selection deferred |
| SQLite write contention under future load | Performance | Low | Publication retries | Head CAS makes contention safe, not corrupt | Volume unknown |
| Contract proves under-specified during implementation | Process | Medium | Implementation pause | Independent audit before ADR; ADR scope kept narrow | Normal |
| Classification workload for existing documents | Operational | Medium | Backlog before activation | Rollout is per-scope and incremental | Volume unknown |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Which detector supplies secret-presence evidence | No | Implementation | Detector evaluation at implementation time | Open; `BLOCKED` is the safe default meanwhile |
| Whether policy may permit metadata or hash retention where exact bytes are prohibited | No | Future ADR | Carried forward unchanged from ADPR-0008 | Open |
| Whether span-level granularity is later needed | No | Future ADR | ADR 0033 Non-Goal; unchanged | Open |
| Whether a governed deletion or purge path is later authorized | No | Future ADR | ADR 0033 Non-Goal; unchanged | Open |

None blocks ADR drafting. Each is a later decision, not a missing input to this
contract.

## Constitution Review

No conflict identified. The selected design strengthens evidence-first and
replay-first obligations: it prevents missing authority from being represented as
permission, and prevents current state from substituting for historical
authority.

## Governance Review

- ADR 0033 ownership is honoured and not reopened; this record supplies only the
  deferred mechanics, as that ADR requires.
- ADR 0031's handling obligation is satisfied at the persistence and pre-model
  boundaries.
- ADR 0020 strict-known replay is relied upon without amendment.
- ADR 0009 producer/repository/consumer separation is preserved and sharpened.
- ADR 0032 is unaffected; nothing enters a project-neutral core.
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` obligations are recorded as
  implementation duties for the later contribution, not discharged here.
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` §Scope is met on six triggers:
  canonical authority, persistence/correction/versioning/migration semantics,
  strict-known replay, evidence and provenance contracts, a service boundary, and
  a new ADR.

## Quality Assessment

Assessed under `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` across all
seventeen mandatory quality dimensions, using only that standard's rating
vocabulary. This restatement corrects audit finding F-002 recorded in
`docs/ARCHITECTURE_AUDITS/adpr-0012-source-handling-independent-audit.md`; it
records what this preparation actually contains and adds no substantive content
to improve a rating.

| # | Dimension | Rating | Rationale | Evidence in this record |
|---|---|---|---|---|
| 1 | Problem correctness | `EXCELLENT` | The blocker is a reproduced repository fact, not an implementation preference: no production `SmartPromptMachine` construction exists and every publication call site is test-only | § Problem Statement — Current condition; § Problem Validation — Gap map |
| 2 | Scope completeness | `GOOD` | In-scope, out-of-scope, decision boundary, and the dependency on ADR 0033's fixed ownership are explicit; ownership is stated as not reopened | § In scope; § Out of scope; § Decision required |
| 3 | Canonical consistency | `GOOD` | Constitution, canonical map, governance, and every referenced accepted ADR are addressed with no contradiction; the record's own constitutional analysis is accurate but terse relative to the reconstruction the independent audit performed | § Constitution Review; § Governance Review; § Constraints — Governance and accepted ADRs |
| 4 | Evidence integrity | `EXCELLENT` | Twelve evidence items carry authority, finding, and explicit quality limitations, and each is repository-verifiable at the stated baseline | § Evidence Inventory (E-001 … E-012) |
| 5 | Assumption discipline | `EXCELLENT` | Four assumptions are separated from evidence and each records rationale, confidence, a falsification condition, and the consequence if false | § Assumptions (A-001 … A-004) |
| 6 | Option completeness | `GOOD` | Five materially distinct options including both ADR-forbidden ones; depth is normalized for the three viable options, while the two forbidden options are analyzed more briefly because their rejection rests on quoted authority rather than trade-off | § Candidate Options A–E |
| 7 | Comparative fairness | `GOOD` | One criteria set of thirteen criteria applied to all five options, with costs and failure modes stated for the recommendation as well as the alternatives | § Comparative Analysis |
| 8 | Falsifiability | `GOOD` | Fourteen hostile cases with defeating mechanism and survival result, applied to the recommendation and to the rejected options; the independent audit's H-15 and H-16 are covered substantively by § 4 and § 9 but were not enumerated as scenario identifiers in this record | § Falsification Results; Selected Contract § 4, § 9 |
| 9 | Authority and ownership clarity | `EXCELLENT` | One publisher holding the only publication capability, consumers enumerated by name, prohibited overlap stated, and the capability made unreachable from every consumer seam | Selected Contract § 1, § 6 |
| 10 | Persistence and replay quality | `EXCELLENT` | Identity, versioning, correction, the effective/recorded/known triple, strict-known replay, deterministic non-branching ordering by head compare-and-set, and migration are each explicitly resolved | Selected Contract § 3, § 4, § 8; § Constraints — Persistence and migration, Replay and historical reconstruction |
| 11 | Evidence and provenance quality | `GOOD` | Authorization lineage, evidence identifiers, and supersession predecessors are carried on every record, and unknown or missing authority is explicitly non-permissive; calibration is not applicable to a control-fact authority | Selected Contract § 2, § 3; § Constraints — Evidence and provenance |
| 12 | Operational quality | `GOOD` | Failure behavior, observability, deployment ordering, rollback, and the fail-closed consequence of absent key material are addressed; operational cost is not quantified | Selected Contract § 9; § Constraints — Operational; § Risks |
| 13 | Implementation and migration impact | `GOOD` | Additive migration, required subsystem changes, transition states, compatibility, and per-option reversibility are analyzed proportionally to risk | Selected Contract § 9; § Candidate Options (Reversibility, Migration implications); § ADR Readiness |
| 14 | Testability and validation | `ACCEPTABLE` | The design is deterministically verifiable and acceptance criteria are derivable from the fail-closed states and hostile cases, and the in-memory store is retained as a test double; however this record does not state acceptance criteria or required validation explicitly, because ADR 0033 defers conformance and counterfactual tests. Documented limitation carried into the ADR 0036 scope | Selected Contract § 9; § ADR Readiness — matters the ADR must leave open |
| 15 | Maintainability and extensibility | `GOOD` | Authority service, mechanical repository, and resolver seam are separated without speculative abstraction, and Option E remains additively adoptable rather than foreclosed | § Candidate Options — Option E; § Rejected Options; Selected Contract § 1, § 4, § 6 |
| 16 | Risk quality | `GOOD` | Five material risks span operational, security, performance, process, and rollout, each with likelihood, impact, mitigation, and residual uncertainty | § Risks |
| 17 | Traceability | `ACCEPTABLE` | Issue, ADPR, planned ADR, and checklist relationships are accurate; PR and merge-commit values remain unpopulated until this record merges, which is the Class A finding F-001 recorded by the independent audit | § Traceability; § Metadata |

### Mandatory Decision Gate

Checked against `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` § Mandatory Decision Gate:

- every dimension has a recorded rating and rationale — satisfied;
- no mandatory dimension is below `ACCEPTABLE` — satisfied; the lowest ratings are `ACCEPTABLE` for testability and validation and for traceability;
- Constitution and Governance dimensions are at least `GOOD` — satisfied; canonical consistency is `GOOD`;
- evidence integrity, option completeness, comparative fairness, and falsifiability are at least `ACCEPTABLE` — satisfied at `EXCELLENT`, `GOOD`, `GOOD`, and `GOOD`;
- all self-identified blocking questions are resolved — satisfied; the four recorded open questions are later decisions, none blocking;
- residual limitations are explicitly carried into the proposed ADR scope — satisfied; the testability limitation and the four open questions appear under § ADR Readiness.

The `READY_FOR_ADR` self-assessment below is therefore supported by the gate that authorizes it. It remains a self-assessment: the independent audit verdict is recorded separately in `docs/ARCHITECTURE_AUDITS/adpr-0012-source-handling-independent-audit.md`.

## Architecture Readiness

- Outcome: `READY`
- Rationale: every deferred mechanic named by ADR 0033 has a selected position; no option was pre-selected without analysis; the two ADR-forbidden options are rejected on quotation.
- Missing evidence: none blocking.
- Unresolved conflicts: none.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR number: 0036 (next free; `0035` is the current maximum)
- Proposed ADR title: Source Handling design and implementation contract
- Proposed ADR scope: bind the nine areas in "Selected Design and Implementation Contract" as the mechanics ADR 0033 deferred.
- Decisions the ADR must fix: the sole publication owner and its capability boundary; authorization issuance, binding, anti-forgery, single use, and staleness; the four record families with identity, temporal triple, scope binding, and non-branching supersession; durable transactional tamper-evident persistence that remains mechanical; secret and sensitive-content mechanics with fail-closed unknowns and no durable plaintext exposure; the read-only resolver seam and its deterministic failure set; the Issue-sourced content eligibility boundary; correction and strict-known replay; additive migration with no historical backfill.
- Matters the ADR must leave open: detector selection; concrete module paths, type names, table and column spellings; whether metadata or hash retention may be permitted where exact bytes are prohibited; span-level granularity; any governed deletion or purge path; adoption of an external classification adapter.

## Final Recommendation

Adopt Option A as the realization of the ownership ADR 0033 already assigned: a
dedicated `SourceHandlingAuthorityService` as the sole publisher inside the
Evidence Intelligence ownership domain, paired with a mechanical, restart-safe,
tamper-evident persistence repository that stores and verifies but never derives
classification, and a strictly read-only consumer resolver seam.

Option A is recommended because it is the only option that makes the "consumer
only" rule a capability boundary rather than a convention, survives all fourteen
mandatory hostile cases structurally, preserves ADR 0009 separation and ADR 0020
strict-known replay unchanged, requires only additive migration, and remains
reversible by withdrawing resolver wiring.

This recommendation authorizes no runtime implementation. Implementation may
begin only after ADR 0036 is drafted from this record and accepted under the
normal lifecycle.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-30 | `READY_FOR_REVIEW` | Record created on baseline `d237ae7cea05a6e906e6c38d092d7f56c3b8a0e5` under Issue #393 | repository owner-directed architecture work |
| 2026-08-30 | `READY_FOR_REVIEW` | Revision 2. Quality Assessment restated across all seventeen mandatory dimensions of `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` using the canonical rating vocabulary, correcting independent-audit finding F-002. No change to the selected architecture, authority ownership, persistence or replay semantics, or Issue #390 scope | repository owner-directed architecture work |
| 2026-08-31 | `READY_FOR_REVIEW` | Revision 3. Corrected the resolver seam to the existing callable `SourceHandlingAuthorityResolver.__call__` protocol, which the sole consumer already invokes; no protocol or consumer change. Merged main to carry the merged independent audit into this branch and reconciled the stale architecture-index audit rows. Four further contract-completeness findings from PR #394 independent review remain open pending owner authorization, because correcting them would amend an already-audited record | repository owner-directed architecture work |
| 2026-08-31 | `READY_FOR_REVIEW` | Revision 4. Owner-authorized correction of the four remaining PR #394 independent-review findings. §1 now assigns publication ownership for all four families the resolver requires (`FIELD_CATEGORY_REGISTRY`, `AUTHORIZATION_RULE` successors) and specifies the existing `publish_genesis_rule` operator bootstrap as the trusted admission path for the first `AUTHORIZATION_RULE`. §2 binds `authorization_id` into the Ed25519 signed claims so it cannot be relabeled without invalidating the signature. §3/§5 add `sensitivity_known` and `persistence_restriction_known` alongside the existing two known-flags, fail-closed under the same rule. §7 reverses the previously implied ordering so Issue content cannot reach the durable intake path until `FACT`/`POLICY` resolve `persistence_restriction` eligibility. No change to the selected architecture (Option A), authority ownership, or the previously audited persistence/replay semantics; these are mechanics completions within the same decision. Under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Re-Audit Protocol this is a revision made solely to address prior findings and calls for a Targeted Re-Audit against this head, not a Full Re-Audit; that re-audit is not self-performed here and remains a required independent step before ADR 0036 drafting resumes | repository owner-directed architecture work |

## Traceability

- Epic: not yet created
- Issue: #393
- Preparation working document: this record
- Checklist review: `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md` applied in "Quality Assessment"
- ADPR: ADPR-0012
- ADR: ADR 0036 (planned; not drafted by this record)
- Implementation plan: deferred until ADR 0036 acceptance
- PR: recorded on merge
- Merge commit: recorded on merge
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record is historical evidence. Corrections that change
substantive reasoning require a new ADPR that explicitly supersedes this record.
Non-substantive link completion and typographical corrections must remain
auditable in version history.
