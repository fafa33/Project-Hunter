# ADR 0036: Source Handling Design and Implementation Contract

## Status

Proposed.

This drafting contribution creates the ADR record. It does not accept it and
authorizes no runtime implementation. Acceptance remains a separate,
owner-authorized lifecycle transition.

## Date

2026-08-31.

## Governing Preparation

[ADPR-0012](../architecture-records/ADPR-0012-source-handling-design-implementation-contract.md)
(`READY_FOR_ADR` self-assessment, Version 4) is the governing preparation
record. It was prepared under Issue #393 and merged to `main` through PR #394
at `90954330aedb3bac6fbbae69df04a2b0e443bf37`. It supplies the mechanics
[ADR 0033](0033-source-handling-classification-authority.md) deliberately
deferred to "a separate design and implementation contract."

Two independent reviews cover this preparation:

- the prior full architecture audit (Issue #395, PR #396, merged at
  `1adb65911eb5b25df87f08f1d84e054b652acc76`), verdict
  `READY_FOR_ADR_WITH_MINOR_FINDINGS`, with finding F-002 subsequently
  corrected in ADPR-0012 Version 2 and finding F-001 (traceability) remaining
  open as a non-blocking Class A item;
- the targeted re-audit (Issue #397, PR #398, merged at
  `7a035c1774510ee50b9a11755e519f72444272f2`) scoped exactly to the four
  Version 4 corrections (four-family ownership and genesis bootstrap,
  `authorization_id` signed-claim binding, `sensitivity`/`persistence_restriction`
  `UNKNOWN` semantics, and transient-before-persistence Issue ingress) plus
  resolver-callable and architecture-index consistency, verdict
  `READY_FOR_ADR_WITH_MINOR_FINDINGS`, with no finding blocking ADR drafting.
  It recorded two further non-blocking items: F-101 (architecture-index rows
  for ADPR-0012 not yet reflecting the PR #394 merge or its corrected
  findings) and F-102 (a citation in ADPR-0012 §1 naming
  `pre_model.py:284-330` where the cited function begins at line 279). F-101
  is corrected in the architecture-index update accompanying this drafting
  contribution. F-102 is not a defect in the mechanics this ADR binds; this
  ADR cites the corrected `pre_model.py:279-330` range directly rather than
  amending the historical ADPR-0012 record.

Both reviews confirm the governing baseline is unchanged since ADPR-0012
Version 4: `git diff --stat` over the reviewed range touches only
documentation, so no runtime evidence this ADR relies on has changed.

## Context

ADR 0033 assigned canonical ownership of source-handling facts and governed
policy to the Evidence Intelligence consumer-side Source Handling Authority,
and bound the safety, historical/replay, and persistence invariants that
authority must satisfy. It deliberately deferred every mechanic — publication
ownership realization, authorization mechanics, record families and schemas,
persistent store semantics, secret and sensitive-content mechanics, the
resolver seam, the Issue-sourced content boundary, and correction/replay
mechanics — to a design and implementation contract that "must be produced
and reviewed before implementation begins."

That absence is a demonstrated blocker, not a theoretical gap: no production
`SmartPromptMachine` composition root can exist, because its required
`source_handling_resolver` has no production authority to resolve from, and
every publication call site in the repository is test-only.

ADPR-0012 produced and independent review validated that contract. This ADR
binds it as architecture. It does not reopen ADR 0033 ownership or its
invariants, and it does not authorize runtime implementation.

## Decision

### 1. Canonical publication owner

`SourceHandlingAuthorityService`, a dedicated component inside the Evidence
Intelligence ownership domain ADR 0033 named, is the sole participant
permitted to determine and publish every family the resolver seam requires:
`FACT`, `POLICY`, `FIELD_CATEGORY_REGISTRY`, and `AUTHORIZATION_RULE`
successor records. `resolve_pre_model_source_handling` already requires a
strict-known head for all four families before any build may proceed
(`src/hunter/evidence_intelligence/pre_model.py:279-330`); an owner naming
only two of them would leave `FIELD_CATEGORY_REGISTRY` and
`AUTHORIZATION_RULE` without any owner or trusted admission path, blocking
every resolve on a fresh deployment. It is the only holder of a
`SourceHandlingPublicationCapability`, constructible only during service
bootstrap from operator-provisioned material and never reachable from any
resolver, repository, transport, or handoff type. Smart Prompt Machine, n8n,
providers, the fallback runtime, callers, orchestrators, and generic
repositories hold no publication authority of any kind.

**Genesis bootstrap.** `AUTHORIZATION_RULE` is the root of trust every other
family's `PublicationAuthorization` is validated against, so it cannot itself
be admitted through the ordinary authorization path. It is admitted exactly
once, through the operator-authorized golden-digest bootstrap
`publish_genesis_rule` (`source_handling.py:383-406`): the exact bytes of the
genesis rule are fixed by an operator-provisioned digest, admission is
refused unless family history is empty, and normal `PublicationAuthorization`
governs every later `FIELD_CATEGORY_REGISTRY`, `FACT`, `POLICY`, and
successor `AUTHORIZATION_RULE` publication (`publish_successor_rule`,
`source_handling.py:409-435`) from that point on. Genesis bootstrap is a
one-time operator deployment action, is refused once family history is
non-empty, and is not reachable from `SourceHandlingPublicationCapability` or
any other runtime path — bounded, auditable, non-recursive, and
non-replayable.

### 2. `PublicationAuthorization` authority

Issued only by the service's authorization issuer. Each authorization binds
exactly: `authorization_id`, `publication_kind`, `governed_subject_scope`,
`authorized_payload_sha256`, `authorization_rule_id`, the temporal triple
`effective_from` / `recorded_at` / `known_at`, and every other
permission-bearing provenance field the authorization carries —
`evidence_ids`, `evidence_strength`, `evidence_method`, `verifier_ids`,
`verifier_type`, and `released_restrictions`. Anti-forgery is an Ed25519
signature over that full canonical claim set, with missing or malformed key
material failing closed; no field that admissibility checking consumes may
sit outside the signature, so none can be substituted post-issuance without
invalidating it. `authorization_id` is included in the signed claims: it is
the single-use replay key, and binding it into the signature makes any change
to it a signature-verification failure rather than a separate check
consumption could skip. Consumption verifies the signature over the full
claim set including `authorization_id` and consumes the id in the same
transaction as the publication it authorizes; replay is refused. An
authorization is stale, and refused, when it is not strict-known eligible at
publication time or falls outside its bounded issuance window. Corrections
reuse the same issuance path and must supersede the exact current head.

### 3. Durable Source Handling records

Four families, unchanged in meaning from the existing runtime:

| Family | Scope | Carries |
|---|---|---|
| `FACT` | `<document_id>` | sensitivity (+known flag), operation restrictions (+known flag), persistence restriction (+known flag), secret presence (+known flag), withdrawn, deleted-at-source, historically-unavailable, availability-known |
| `POLICY` | `policy:<document_id>:v1` | governed policy body, bound `field_category_registry_id` |
| `FIELD_CATEGORY_REGISTRY` | `registry:<document_id>:v1` | field map, safe-control proofs |
| `AUTHORIZATION_RULE` | `SOURCE_HANDLING` | rule body and its supersession chain |

Record identity is the SHA-256 of the canonical JSON of the payload excluding
the authorization envelope. Every record carries `effective_from`,
`recorded_at`, and `known_at`; all three are required and all three bound
strict-known eligibility, but all three are issuer claims. The repository
additionally stamps every record with an immutable, repository-assigned
`admission_time` at the moment of append, which no issuer or caller can set,
backdate, or supersede. A record is eligible at a cutoff only when it is
strict-known by the issuer-claimed triple **and** `admission_time` is at or
before that cutoff, so a successor appended after a cutoff can never become
eligible for that already-observable cutoff regardless of what `known_at` or
`recorded_at` it claims — closing clock-skew, retry, and issuer-backdating
backfill. History is append-only. Correction is linear
supersession through `supersedes_*_id`; branching is refused by head
compare-and-set, so exactly one head exists per `(family, scope)` at any
cutoff. Simultaneous restrictions are represented as a set and are never
collapsed to a single value.

### 4. Persistent authority store

`SourceHandlingAuthorityRepository`, backed by additive tables in the
existing Evidence Intelligence database: an append-only record table, an
issued- and consumed-authorization table, and a canonical-key table. One
transaction covers authorization consumption, head compare-and-set, record
append, and canonical-key marking; partial application is impossible.
Concurrency is optimistic: the head CAS refuses the loser, which must
re-resolve. Reads are strict-known and cutoff-parameterized. Tamper evidence
is the stored payload digest plus supersession-chain verification, re-checked
on read; a mismatch is `TAMPER_DETECTED` and blocks. The repository stores,
verifies, and refuses; it never derives, defaults, or asserts a
classification, and retains no path equivalent to `direct_write`.

### 5. Secret and sensitive-content mechanics

Classification operates on transient input and records only non-reversible
observations — detector verdicts, categories, and counts — never the
classified bytes. Every FACT dimension that can be omitted is paired with its
own known flag, symmetrically: `secret_presence`/`secret_presence_known`,
`operation_restrictions`/`operation_restrictions_known`,
`sensitivity`/`sensitivity_known`, and `persistence_restriction`/
`persistence_restriction_known`. `sensitivity` and `persistence_restriction`
are required dimensions under ADR 0033's binding invariants exactly as the
other two are. Unknown, absent, or `UNKNOWN` values for any of the four known
flags yield `BLOCKED`, with no permissive default, enforced at persistence:
the repository independently rederives the flag from the payload and refuses
the publication rather than trusting a caller-asserted value.
`persistence_restriction` governs durable retention and reconstruction
eligibility independently, so material may be processable without being
retainable and retainable without being reconstructable. Access, retention,
and deletion restrictions derive from the resolved policy, never from a
caller. Logging and audit carry record identities, reason codes, and decision
ids only; no classified content is written to logs or durable plaintext
merely to classify it.

### 6. Production resolver seam

The exact consumer API is the existing `SourceHandlingAuthorityResolver`
protocol, which is **callable**, not a named method:

```text
SourceHandlingAuthorityResolver.__call__(document_id: str, cutoff: datetime)
    -> EvidencePreModelSourceHandlingAuthority
```

The resolver is invoked as `resolver(document_id, cutoff)`. This matches
`SourceHandlingAuthorityResolver` in
`src/hunter/evidence_intelligence/smart_prompt_machine.py`, which declares
`__call__`, and the sole consumer call site in
`PromptContextCompiler.compile()`, which invokes
`self._source_handling_resolver(request.document_id, cutoff)`. Neither the
protocol nor the consumer changes under this decision.

The returned authority is constructed over a read-only store view; no
publication capability, issuer, or mutating method is reachable through it.
Deterministic failure states, each mapping to `BLOCKED`: `AUTHORITY_ABSENT`,
`AUTHORITY_NOT_KNOWN_AT_CUTOFF`, `POLICY_SCOPE_UNBOUND`, `RULE_AMBIGUOUS`,
`REGISTRY_UNAVAILABLE`, `TAMPER_DETECTED`. Substituting current or latest
state for an unresolved historical cutoff is forbidden; absence is returned
as absence.

### 7. Issue-sourced content boundary

Owner authorization (`hunter-issue-agent-authorization-v1`) establishes who
requested execution. It never establishes that the content is safe, and it
grants no processing, retention, or reconstruction permission.

Ordering is binding, not incidental. Issue-sourced content must not reach the
durable `EvidenceIntelligenceIntakeService.ingest()` call until the Source
Handling Authority has published `FACT`, `POLICY`, and
`FIELD_CATEGORY_REGISTRY` for the target document scope and
`derive_source_handling_decision()` resolves the complete decision from all
four record families to a state that permits retention: `retention_decision`
is `ALLOW`, and every entry the target payload requires in
`durable_dispositions` is a `PERSIST`-permitting disposition, with no
`deletion_lifecycle_decision` restriction blocking the write. A permissive
`persistence_restriction` flag on `FACT` alone is necessary but never
sufficient; it never substitutes for the complete resolved decision. Until
that full resolution permits retention, Issue content supplied for
classification is held only as transient input and is never written through
the durable intake path. Once the complete resolved decision confirms
eligibility, ingestion proceeds through the unchanged existing
`EvidenceIntelligenceIntake` path, producing an `EvidenceDocument` and its
span inventory exactly as it does today. This publication is itself gated by
the genesis and per-scope authorization mechanics of §1 and §2; it grants no
exception to them. Absent published authority or a complete permissive
decision, the build is `BLOCKED`. Raw Issue text has no path to the Smart
Prompt Machine, to a build, to a handoff, or to the fallback runtime that
does not pass through this boundary. No direct Issue-to-fallback authority
exists or may be created.

This section binds only the ordering and eligibility gate; it does not
compose the Issue trigger, the intake call site, or any other part of a
production Issue #390 execution path, which remains separately scoped and
unimplemented.

### 8. Correction and strict-known replay

Corrections never erase historical state; they append a successor that
supersedes the exact current head. Resolution at any cutoff returns the head
that was strict-known at that cutoff **and** repository-admitted at or before
that cutoff per §3's `admission_time`, so a correction recorded later is
invisible to earlier replay even if its issuer-claimed timestamps are not.
Current or latest authority never substitutes for an earlier cutoff. Replay
is deterministic in `(document_id, cutoff)`: the same pair yields the same
decision or the same explicit absence, for the lifetime of the record
history — a guarantee `admission_time` makes lifetime-stable rather than
contingent on issuer-claimed time remaining honest.

### 9. Migration, rollout, and rollback

Migration is additive: new tables only, no alteration or reclassification of
existing persisted records. The in-memory `AuthorityStore` is retained as the
test double implementing the same read interface, so existing tests remain
valid without becoming a production path. No present-day classification may
be backfilled and presented as historical truth. Deployment order: tables,
then service bootstrap and key provisioning, then publication for in-scope
document scopes, then resolver wiring, then consumer activation.
Observability exposes publication and resolution counts and `BLOCKED` reason
codes without content. Rollback removes resolver wiring, after which
consumers fail closed; durable tables are retained, because discarding
published authority would destroy historical truth.

## Compatibility

- `EvidenceSpan` and `EvidencePreModelSourceHandlingAuthority` are unchanged.
- `SourceHandlingAuthorityResolver`'s callable protocol and its sole consumer
  call site in `PromptContextCompiler.compile()` are unchanged.
- ADR 0033 ownership and invariants are binding and unamended by this
  decision.
- ADR 0031 remains the governing pre-model foundation; ADR 0020 strict-known
  replay is relied upon without amendment; ADR 0009 producer/repository/
  consumer separation is preserved and sharpened; ADR 0032 consumer-side
  ownership is unaffected, and nothing here enters a project-neutral core.
- No accepted ADR is superseded by this decision.

## Non-Goals

This ADR does not define or authorize:

- runtime implementation of any component named above;
- the Issue #390 composition root or execution path;
- span-level classification (ADR 0033 Non-Goal, unchanged);
- retroactive purge or deletion of already-persisted records (ADR 0033
  Non-Goal, unchanged);
- Model Adapter, Response Validator, provider routing, n8n, provider order,
  or fallback-runtime changes;
- detector selection, concrete module/table/column naming, metadata-or-hash
  retention where exact bytes are prohibited, or an external classification
  adapter (Option E) — each remains explicitly open per ADPR-0012 §ADR
  Readiness;
- Issue #389, Issue #386, or any unrelated refactor.

## Implementation Status

Architecture only. This drafting contribution authorizes no runtime
implementation. Implementation remains subject to ADR acceptance and
separately approved scope under the normal development lifecycle, and
Issue #390 remains blocked until acceptance.

## Consequences

- Evidence Intelligence gains a concrete, capability-bounded realization of
  the ADR 0033 publication owner: a dedicated service holding the sole
  publication capability, paired with a mechanical, restart-safe,
  tamper-evident repository that never derives classification.
- `SmartPromptMachine`'s `source_handling_resolver` seam has a named,
  bindable production authority once implemented; until then it remains
  correctly unconstructible in production.
- Unknown, missing, or conflicting required authority — including
  `sensitivity` and `persistence_restriction` — fails closed to `BLOCKED`
  with no permissive default, symmetric across all four `FACT` dimensions.
- `authorization_id` tampering is a signature-verification failure, not a
  separate consumable check.
- Issue-sourced content acquires no durable footprint before `FACT`/`POLICY`
  resolve retention eligibility, closing the crash/retry and secret-bearing
  hostile cases structurally rather than by convention.
- Historical replay remains strict-known and cutoff-parameterized; current
  state never substitutes for unresolved history.
- Migration is additive only; rollback fails closed without destroying
  published historical authority.

## Alternatives Considered

### Diffuse Evidence Intelligence-owned publication authority

Rejected as the realization, not as the ownership domain. ADR 0033's owner
*is* Evidence Intelligence; package-level scoping gives no enforceable
publisher boundary while Prompt and Context construction live in the same
package, and it fails the hostile cases for a two-publisher race and
self-seeded authority stores.

### Repository-owned publication authority

Rejected. ADR 0033 names repositories as consumers only and states
persistence "enforces this authority but does not acquire it." Adopting this
would require reversing `direct_write`'s unconditional refusal.

### Caller- or orchestrator-owned classification

Rejected. This is exactly the condition ADR 0031 forbids and ADR 0033 exists
to prevent; ADPR-0008 already rejected it on reproduced evidence that a
consumer supplying its own classification, provenance, and policy controls
the outcome regardless of typing.

### External classification/policy adapter with Hunter-owned publication

Rejected for now. It is ADR-compatible in shape — publication stays
Hunter-owned and external output is evidence only — but adds an external
trust boundary and an egress channel for the material being classified, for
no ownership benefit at current scale. Its evidence-only shape is preserved
by the selected design, so adoption later remains additive rather than
foreclosed.
