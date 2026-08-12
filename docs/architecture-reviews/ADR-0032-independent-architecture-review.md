# Independent Architecture Review — ADR 0032

## Metadata

- Review target: `docs/architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md`
- Decision target: `docs/ADR/0032-project-agnostic-prompt-intelligence-core.md`
- Governing issue: #247
- Source architecture merge: PR #239 / `4938d2db494d72ec7479b27931814b1a979140b5`
- Audit type: `FULL`
- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`
- Review date: 2026-08-12

## Independence

This review re-derives the decision quality from the merged ADPR/ADR, accepted ADR 0031, the architecture-audit protocol, and the repository state. It does not treat PR #239's implementer declaration or prior Governance Review as an architecture-approval substitute.

## Audit Scope

The review covers problem correctness, scope, canonical consistency, evidence integrity, assumptions, option completeness, comparative fairness, falsifiability, authority/ownership, persistence/replay, provenance, migration, operational impact, testability, maintainability, governance compatibility, traceability, and unresolved uncertainty.

The reviewed decision is deliberately narrower than a populated shared-core contract: it establishes a project-neutral boundary and a rule for admitting future contracts into that boundary.

## Evidence Examined

- ADPR-0007, merged on `main` through PR #239.
- ADR 0032, merged on `main` as `Proposed` through PR #239.
- ADR 0031, especially its binding deferral of generic Prompt/Context ownership until a real second consumer provides comparable contracts.
- `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`.
- `docs/ADR/README.md` lifecycle semantics.
- `docs/architecture-index.md` traceability state.
- PR #239 review history, including the correction that removed Iran-OS as proof of shared semantics.

## Dimension Results

| Dimension | Result | Rationale |
|---|---|---|
| Problem correctness | PASS | Hunter has a real portability-boundary problem even before a second consumer exists; the record no longer conflates that problem with proof of shared contracts. |
| Scope completeness | PASS | Boundary, adapters, admission, persistence, replay, migration, security, extraction, and non-goals are explicit. |
| Canonical consistency | PASS | ADR 0031 remains authoritative for Hunter's current contracts and is reaffirmed rather than silently superseded. |
| Evidence integrity | PASS | Hunter evidence supports defining the boundary; absent second-consumer evidence is explicitly disclosed and correctly blocks contract promotion, not boundary definition. |
| Assumption discipline | PASS | Future reuse is treated as an assumption/goal, not as evidence that a shared contract already exists. |
| Option completeness | PASS | Hunter-only, copy-per-project, evidence-gated neutral boundary, and immediate standalone extraction are materially distinct options. |
| Comparative fairness | PASS | The same correctness, authority, replay, maintainability, complexity, migration, reversibility, and extensibility dimensions are applied across options. |
| Falsifiability | PASS | Option 3 is explicitly invalidated by any future promotion of Hunter-only semantics without independent consumer evidence. |
| Authority and ownership | PASS | Consumer domain authority, source eligibility, permissions, persistence, and downstream promotion remain consumer-owned. |
| Persistence and replay | PASS | Shared artifacts cannot acquire consumer persistence authority and must preserve exact identities, provenance, temporal coordinates, missingness, and reconstruction semantics. |
| Evidence and provenance | PASS | Promotion requires attributable, versioned evidence from at least two independent consumers plus lossless mapping tests. |
| Implementation impact | PASS | Acceptance establishes an architectural boundary only; no concrete contract is automatically moved into shared ownership. |
| Migration impact | PASS | Existing ADR 0031 identities remain historical and valid; migration is additive and lossless or it does not occur. |
| Operational impact | PASS | No remote service, model provider, or production LLM is required by the decision. |
| Testability and validation | PASS | No-reverse-dependency, deterministic identity/output, omission accounting, replay, missingness, and adapter-losslessness are all testable gates. |
| Maintainability and extensibility | PASS | The boundary avoids both permanent Hunter coupling and premature standalone-service complexity. |
| Governance compatibility | PASS | Hunter Governance Review remains outside Prompt Intelligence authority; future contract admission requires normal architecture/governance review. |
| Traceability | PASS | ADPR-0007, ADR 0032, Issue #237, PR #239, and the acceptance path under Issue #247 are identifiable. |
| Unresolved risk and uncertainty | PASS | The first concrete shared contract, package name, provider/model architecture, response validation, and standalone extraction timing remain explicitly open rather than silently assumed. |

## Findings

No Class C or Class D findings were identified.

### F-001 — Lifecycle wording must be synchronized during acceptance

- Evidence: ADR 0032 currently says `Proposed` and its Implementation Status says it is not authorized by "this Proposed ADR"; ADPR-0007 remains `READY_FOR_REVIEW` with independent review pending.
- Location: ADR 0032 `Status`, `Non-Goals`, and `Implementation Status`; ADPR-0007 metadata/traceability.
- Category: lifecycle / traceability.
- Severity: Class A.
- Decision impact: none; the substantive architecture is internally consistent.
- Consequence if ignored: lifecycle registries would conflict with the acceptance outcome.
- Required action: the acceptance contribution must update lifecycle/status wording and registries without changing the reviewed architecture substance.
- Blocks ADR: NO.

## Materiality Assessment

F-001 is purely a lifecycle synchronization requirement created by moving from review to acceptance. It does not change option viability, authority, evidence, replay, persistence, migration, or the architectural conclusion.

No unsupported cross-project equivalence remains. In particular, Iran-OS is illustrative only and has no authority under the decision. The two-independent-consumer rule prevents a Hunter-specific contract from becoming project-neutral merely because it appears reusable.

## Verdict Derivation

- Highest finding severity: Class A.
- Class C findings: 0.
- Class D findings: 0.
- Material unresolved uncertainty that blocks the boundary decision: none.
- Missing second-consumer evidence: intentionally blocks concrete shared-contract admission, not acceptance of the boundary/admission policy itself.

Under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`, this yields:

**`READY_FOR_ADR`**

Because ADR 0032 already exists as `Proposed`, the operational meaning of this verdict is: the reviewed ADR is ready for a separate lifecycle-only acceptance contribution, provided that contribution changes status/traceability only and does not expand architecture or authorize implementation.

## Acceptance Conditions

The acceptance contribution must:

1. change ADR 0032 from `Proposed` to `Accepted` without altering the reviewed decision substance;
2. mark ADPR-0007 `APPROVED` and record this independent review;
3. synchronize `docs/ADR/README.md` and `docs/architecture-index.md`;
4. keep all current Hunter ADR 0031 contracts Hunter-owned by default;
5. preserve the two-independent-consumer evidence gate before any concrete contract enters shared ownership;
6. keep provider/model routing, invocation, response validation, external-project adapters, and runtime implementation unauthorized by acceptance alone.

## Final Verdict

**PASS — ADR 0032 is `READY_FOR_ADR` and may proceed to a lifecycle-only acceptance change.**

This review does not itself accept ADR 0032, merge code, authorize runtime implementation, populate the shared core, or grant authority over another project.