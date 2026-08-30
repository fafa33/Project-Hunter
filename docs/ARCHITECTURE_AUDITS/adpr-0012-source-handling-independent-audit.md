# Independent Architecture Audit — ADPR-0012 Source Handling Contract

> Status: `COMPLETED`

## Metadata

- Reviewed artifact: `docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md`
- Reviewed revision: `8e30a77aa19f42ffd3679042867068f40d96946c`
- Repository evidence baseline: `8e30a77aa19f42ffd3679042867068f40d96946c`
- Audit target: PR #394 (the pull request that prepared ADPR-0012)
- Audit type: `FULL`
- Auditor: `Claude Code independent architecture audit agent`
- Auditor independence limitation: the ADPR-0012 text audited here was authored by a Claude Code session. This audit was performed against the exact frozen revision using the controlling documents as the sole authority, and the prior `READY_FOR_ADR` self-assessment and the prior audit verdict were both discarded before re-derivation. The owner should treat this limitation as material when deciding whether an additional non-Claude audit is warranted.
- Audit date: `2026-08-30`
- Evidence cutoff: `2026-08-30T22:48:34Z`
- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`
- Governing issue for this audit: Issue #395
- Pull request carrying this audit contribution: PR #396
- Preparation issue for the audited artifact: Issue #393
- Planned decision if audit permits progression: ADR 0036

### Artifact relationship statement

These relationships are distinct and are not to be conflated:

- ADPR-0012 was **prepared** in PR #394 under Issue #393.
- PR #394 is the **audited target**; its head `8e30a77aa19f42ffd3679042867068f40d96946c` is the frozen review revision.
- Issue #395 **governs this independent audit**.
- PR #396 **carries this audit contribution**. PR #394 did not prepare this audit.

## Audit Scope

This document records the independent architecture audit of ADPR-0012 ("ADR 0033 Source Handling design and implementation contract").

The audit evaluates whether ADPR-0012 is complete, internally consistent, evidenced, and materially reliable enough that ADR 0036 can later be drafted without implementation having to invent material architecture.

Per `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § "Relationship to ADPR Self-Assessment", the author self-assessment is preparation evidence only. The ADPR-0012 `READY_FOR_ADR` self-assessment was set aside and the verdict re-derived from evidence.

This revision is a re-derivation following prior review findings raised on PR #396. The controlling governance documents named in the audit protocol's precedence list were independently read and applied rather than cited by filename, and all sixteen hostile scenarios required by Issue #395 were evaluated individually.

## Prior Review Finding Re-Verification

Five prior review findings were raised against the earlier revision of this audit artifact on PR #396. Each is re-verified below with evidence and decision consequence.

| Prior finding | Source | Evidence | Decision consequence if ignored | Disposition |
|---|---|---|---|---|
| PR396-F1 | Codex P2 | Earlier revision recorded a governance pass while its evidence inventory omitted `docs/PROJECT_CONSTITUTION.md`, `docs/CANONICAL_ARCHITECTURE_MAP.md`, `docs/DEVELOPMENT_GOVERNANCE.md`, and `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` | A governance verdict not derived from the controlling documents is unsupported, and a real quality-standard breach stays undetected | Accepted and corrected. The controlling documents are now read and applied in "Controlling Document Review"; doing so produced new finding F-002 |
| PR396-F2 | CodeRabbit Minor | Earlier revision recorded audit date `2026-08-31` and evidence cutoff `2026-08-31T00:00:00Z` while the audit completed on 2026-08-30 | A `COMPLETED` report claiming a future evidence cutoff misrepresents what was actually verified | Accepted and corrected to real completion and last-verification timestamps |
| PR396-F3 | CodeRabbit Minor | Earlier revision's scope sentence could be read as saying the audit was prepared in PR #394 under Issue #393 | Conflating audited target with audit contribution damages traceability and independence | Accepted and corrected in "Artifact relationship statement" |
| PR396-F4 | CodeRabbit Major | Earlier revision claimed sixteen hostile scenarios were evaluated but contained no scenario identifiers, results, or mitigation mapping | A `Falsifiability` pass that cannot be independently checked is an unsupported dimension result | Accepted and corrected in "Hostile and Falsification Scenario Matrix" |
| PR396-F5 | CodeRabbit Minor | Earlier revision's Progression Gate omitted the merge prohibition required by Issue #395 | An incomplete authorization boundary could be read as permitting merge of PR #394 | Accepted and corrected in "Progression semantics" |

## Evidence Sources Examined

All repository evidence is the exact state at commit `8e30a77aa19f42ffd3679042867068f40d96946c`.

### Controlling documents (precedence order of `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Document Precedence)

| Precedence | Document | Relevance to this decision | Compatibility assessment |
|---|---|---|---|
| 1 | `docs/PROJECT_CONSTITUTION.md` | Rules 2, 3, 4, 5, 6, 7, 8 all bind an authority-and-persistence decision | No contradiction. See "Controlling Document Review" |
| 2 | `docs/CANONICAL_ARCHITECTURE_MAP.md` | Fixes the document-authority hierarchy and the non-authoritative status of ADPRs | No contradiction. ADPR-0012 correctly disclaims binding force and routes binding mechanics to ADR 0036 |
| 3 | Accepted ADRs | ADR 0033 governs; ADR 0031, 0020, 0009, 0032, 0004, 0016 are referenced or adjacent | No contradiction. Per-ADR accounting below |
| 4 | `docs/DEVELOPMENT_GOVERNANCE.md` | Owns lifecycle and the prohibition on inventing architecture or leaving placeholders | No contradiction |
| 5 | `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` | Owns audit classification, materiality, and verdict vocabulary | Applied. Governs the verdict recorded here |
| 6 | `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` | Owns preparation lifecycle, significance triggers, and readiness outcomes | No contradiction |
| 7 | `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` | Owns the mandatory quality dimensions and rating scale for preparation records | **Contradiction found.** Source of finding F-002 |
| 8 | `docs/ARCHITECTURE_AUDIT_TEMPLATE.md` | Owns this report's structure | Applied |
| 9 | Issue #395, PR #396 instructions | Local audit instructions | Applied where not in conflict with higher precedence. See "Verdict vocabulary reconciliation" |

Additional canonical sources examined: `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`; `docs/architecture-records/README.md`; `docs/ADR/README.md`; `docs/architecture-index.md`; `docs/templates/ADPR_TEMPLATE.md`.

### Runtime evidence examined

| Source | Locator | Finding |
|---|---|---|
| `AuthorityStore` | `src/hunter/evidence_intelligence/source_handling.py` | Process-local in-memory state; no durable backing; confirms the blocker ADPR-0012 states |
| `AuthorityStore.direct_write` | same file | Unconditional refusal; confirms ADPR-0012's H-03 control is already real |
| `PublicationAuthorization` | same file | Carries `effective_from` / `recorded_at` / `known_at`; confirms ADPR-0012's temporal triple |
| `strict_known_eligible` / `strict_known_head` | same file | All three timestamps must be ≤ cutoff; linear non-branching supersession |
| `resolve_pre_model_source_handling` | `src/hunter/evidence_intelligence/pre_model.py` | Four-family resolution requiring single-rule agreement; matches ADPR-0012 § 3 |
| `SmartPromptMachine` | `src/hunter/evidence_intelligence/smart_prompt_routing.py` | Requires `source_handling_resolver`; confirms the blocked consumer |
| Publication seeding call sites | `tests/evidence_pre_model_source_handling_fixture.py`, `tests/source_handling_runtime_harness.py` | Test-only; no production publisher exists |

### Accepted ADR accounting

| ADR | Disposition |
|---|---|
| ADR 0001 | reviewed; not applicable to this authority decision |
| ADR 0002 | reviewed; no conflict — evidence-first reinforced by fail-closed unknowns |
| ADR 0003 | reviewed; not applicable |
| ADR 0004 | reviewed; no conflict — ADR 0033 already separates epistemic trust from data handling |
| ADR 0005 | reviewed; not applicable |
| ADR 0006 | reviewed; not applicable |
| ADR 0007 | reviewed; no conflict |
| ADR 0008 | reviewed; not applicable |
| ADR 0009 | reviewed; no conflict — producer/repository/consumer separation preserved and sharpened |
| ADR 0010 | reviewed; not applicable |
| ADR 0011 | reviewed; not applicable |
| ADR 0012 | reviewed; not applicable |
| ADR 0013 | reviewed; not applicable |
| ADR 0014 | reviewed; not applicable |
| ADR 0015 | reviewed; not applicable |
| ADR 0016 | reviewed; no conflict — no analytical authority or registry entry created |
| ADR 0017 | reviewed; not applicable |
| ADR 0018 | reviewed; not applicable |
| ADR 0019 | reviewed; not applicable |
| ADR 0020 | reviewed; no conflict — strict-known replay relied upon without amendment |
| ADR 0021 | reviewed; not applicable |
| ADR 0022 | reviewed; not applicable |
| ADR 0023 | reviewed; not applicable |
| ADR 0024 | reviewed; not applicable |
| ADR 0025 | reviewed; not applicable |
| ADR 0026 | reviewed; not applicable |
| ADR 0028 | reviewed; no conflict |
| ADR 0031 | reviewed; no conflict — handling obligation satisfied at pre-model and persistence boundaries |
| ADR 0032 | reviewed; no conflict — nothing enters a project-neutral core |
| ADR 0033 | reviewed; governing decision. Ownership honoured, mechanics supplied, no amendment attempted |
| ADR 0034 | reviewed; no conflict — Model Adapter remains a consumer |
| ADR 0035 | reviewed; no conflict — Response Validator remains a consumer |

## Controlling Document Review

This section records the substantive verification required by the audit protocol's Stage 1, not a citation list.

**`docs/PROJECT_CONSTITUTION.md`.** Rule 2 (Evidence Authority) requires unknown to remain unknown and missing to remain missing; ADPR-0012 § 5 makes unknown secret presence and unknown operation restrictions yield `BLOCKED` with no permissive default, satisfying it. Rule 3 (Deterministic Intelligence) requires historical decisions to be reproducible from information available at the historical time; ADPR-0012 § 8 binds resolution to strict-known state at the cutoff and forbids current-state substitution. Rule 4 (Architectural Integrity) states architectural convenience never justifies violating boundaries; ADPR-0012 rejects Options B, C and D on precisely that ground rather than on effort. Rule 5 (Single Source of Truth) prohibits competing authorities; ADPR-0012 realizes the owner ADR 0033 already named and creates no second owner. Rule 6 (Explainability) requires preserved provenance; ADPR-0012 § 3 carries authorization lineage, evidence identifiers and supersession predecessors on every record. Rule 8 (Governance) is satisfied by routing binding force to ADR 0036. **No constitutional conflict.**

**`docs/CANONICAL_ARCHITECTURE_MAP.md`.** The hierarchy places accepted ADRs at position 7 and states that architecture preparation records "do not approve architecture or implementation." ADPR-0012 states this correctly, declares it authorizes no runtime implementation, and defers binding force to ADR 0036. **No conflict.**

**`docs/DEVELOPMENT_GOVERNANCE.md`.** Its scope rule requires architecturally significant work to follow the architecture preparation and ADR process, and prohibits silently expanding scope, inventing architecture, or leaving placeholders. ADPR-0012 follows the preparation process, bounds its scope explicitly, and its four open questions are carried as later decisions rather than placeholders inside the contract. **No conflict.**

**`docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`.** § Scope makes preparation mandatory on six triggers that this decision meets. The permitted self-assessment outcomes are author assessments only, which is how ADPR-0012's `READY_FOR_ADR` is treated here. **No conflict.**

**`docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.** It governs implementation obligations, not preparation. ADPR-0012 records the contract's obligations as duties for the later implementation contribution rather than discharging them. **No conflict.**

**`docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`.** This review produced a contradiction. The standard defines seventeen mandatory quality dimensions and a five-value rating scale (`EXCELLENT`, `GOOD`, `ACCEPTABLE`, `NEEDS_IMPROVEMENT`, `UNACCEPTABLE`), and its Mandatory Decision Gate permits an author `READY_FOR_ADR` declaration only when "every dimension has a recorded rating and rationale". `docs/templates/ADPR_TEMPLATE.md` § Quality Assessment repeats the instruction to "record the rating for every required dimension". ADPR-0012 § Quality Assessment records seven rows using the non-canonical vocabulary "Strong" and "Adequate". Ten mandatory dimensions carry no recorded rating: scope completeness, assumption discipline, comparative fairness, persistence and replay quality, evidence and provenance quality, operational quality, implementation and migration impact, testability and validation, maintainability and extensibility, and risk quality. This is recorded as finding F-002 and classified under the protocol's Existing-Substance Rule below.

### Verdict vocabulary reconciliation

Issue #395 and the PR #396 instruction both ask for a final verdict of `READY_FOR_ADR` or `CHANGES_REQUIRED`. `CHANGES_REQUIRED` is not a verdict defined by `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Verdicts. Under that protocol's own precedence list, the protocol (position 5) outranks local issue and prompt instructions (position 9), and Issue #395 itself defers to "the canonical `READY_FOR_ADR` verdict defined by the repository audit protocol". This report therefore uses the canonical protocol vocabulary. The canonical verdicts that correspond to the requested `CHANGES_REQUIRED` outcome are `ADPR_REVISION_REQUIRED` (Class C) and `ARCHITECTURE_NOT_READY` (Class D); neither is reached here.

## Hostile and Falsification Scenario Matrix

All sixteen scenarios required by Issue #395 were evaluated individually against ADPR-0012 at the reviewed revision. "Structurally enforceable" means the control is a capability, schema, or transactional property rather than a convention an implementer could quietly drop.

| ID | Scenario | Result | Finding |
|---|---|---|---|
| H-01 | Caller asserts `operation_restrictions_known=true` as canonical | `PASS` | None |
| H-02 | Smart Prompt Machine seeds its own Source Handling authority | `PASS` | None |
| H-03 | Repository direct-write fabricates FACT/POLICY | `PASS` | None |
| H-04 | Owner-authorized Issue automatically treated as safe | `PASS` | None |
| H-05 | Current policy substituted for an older cutoff | `PASS` | None |
| H-06 | Later correction erases historical state | `PASS` | None |
| H-07 | Process-local authority disappears after restart | `PASS` | None |
| H-08 | Conflicting publishers race | `PASS` | None |
| H-09 | Stale or mismatched `PublicationAuthorization` replay | `PASS` | None |
| H-10 | Unknown secret presence treated safe | `PASS` | None |
| H-11 | Secret bytes leak through logs, records, migration, errors, or correction | `PASS` | None |
| H-12 | n8n, provider, or model mutates Source Handling authority | `PASS` | None |
| H-13 | Historically unknowable cutoff reconstructed from current state | `PASS` | None |
| H-14 | Resolver consumer also publishes | `PASS` | None |
| H-15 | Persistence gains semantic classification authority by convenience | `PASS` | None |
| H-16 | Rollback re-enables process-local fabricated authority | `PASS` | None |

### Per-scenario evidence

**H-01 — caller asserts `operation_restrictions_known=true` as canonical.**
Requirement tested: ADR 0033 § Binding safety invariants, "Caller and provider inputs are evidence or expectations only. They are never authority."
Control in ADPR-0012: § 1 confines payload composition to `SourceHandlingAuthorityService`; § 2 binds `authorized_payload_sha256` into the authorization.
Governance constraint: ADR 0033 § Canonical ownership; Constitution Rule 2.
Structurally enforceable: yes — a caller-supplied field never reaches a record body, and a payload not matching the authorized digest is refused by the existing `publish()` verification.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-02 — Smart Prompt Machine seeds its own Source Handling authority.**
Requirement tested: ADR 0033 § Canonical ownership, "prompt construction … are consumers only".
Control: § 1 makes `SourceHandlingPublicationCapability` constructible only in service bootstrap and unreachable from any resolver; § 6 hands consumers a read-only store view.
Governance constraint: ADR 0033; ADR 0032 consumer-side ownership.
Structurally enforceable: yes — capability possession, not documentation, separates publisher from consumer.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-03 — repository direct-write fabricates FACT/POLICY.**
Requirement tested: ADR 0033 § Persistence invariant, "Persistence enforces this authority but does not acquire it."
Control: § 4 states the repository "retains no path equivalent to `direct_write`"; the existing `AuthorityStore.direct_write` already refuses unconditionally, verified in runtime evidence above.
Governance constraint: ADR 0033; ADR 0009.
Structurally enforceable: yes — the durable schema exposes no insert path outside the transactional publish.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-04 — owner-authorized Issue automatically treated as safe.**
Requirement tested: ADR 0031's obligation that handling classification precede processing or persistence.
Control: § 7 states owner authorization "establishes who requested execution … never establishes that the content is safe", and requires published FACT and POLICY for the document scope before any build.
Governance constraint: ADR 0031; ADR 0033 § Binding safety invariants.
Structurally enforceable: yes — eligibility is gated on published authority, and `orchestrate_evidence_pre_model` already fails closed without a span inventory.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-05 — current policy substituted for an older cutoff.**
Requirement tested: ADR 0033 § Historical and replay invariants; ADR 0020 strict-known replay; Constitution Rule 3.
Control: § 8 binds resolution to the strict-known head at the cutoff; `strict_known_eligible` already requires `effective_from`, `recorded_at` and `known_at` all ≤ cutoff.
Structurally enforceable: yes — later records are arithmetically invisible to an earlier cutoff.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-06 — later correction erases historical state.**
Requirement tested: ADR 0033, "Corrections supersede. They never rewrite prior history."
Control: § 3 and § 8 make corrections append-only successors linked by `supersedes_*_id`, with predecessors retained.
Structurally enforceable: yes — append-only storage plus head compare-and-set leaves no update path.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-07 — process-local authority disappears after restart.**
Requirement tested: the blocker ADPR-0012 exists to close; ADR 0033's requirement that authority be resolvable.
Control: § 4 replaces the in-memory store with durable additive tables as the production backing.
Structurally enforceable: yes — durability is a storage property.
Implementation must invent material architecture: no. Table and column spellings are explicitly deferred as implementation choices, which the protocol's Existing-Substance Rule does not treat as a gap.
Result: `PASS`.

**H-08 — conflicting publishers race.**
Requirement tested: non-branching correction history.
Control: § 2 and § 4 place authorization consumption, head compare-and-set, record append, and canonical-key marking in one transaction; the loser is refused and must re-resolve.
Structurally enforceable: yes — CAS is a transactional property; `publish()` already refuses when the head moved.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-09 — stale or mismatched `PublicationAuthorization` replay.**
Requirement tested: anti-forgery and single-use publication.
Control: § 2 requires an Ed25519 signature over canonical claims, single-use `authorization_id` consumed in the same transaction, and refusal when not strict-known eligible or outside the issuance window.
Governance constraint: the repository's accepted SPM-001 issuer-private / verifier-public pattern.
Structurally enforceable: yes — replay fails on the consumed-identity check; forgery fails on signature verification.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-10 — unknown secret presence treated safe.**
Requirement tested: ADR 0033, "Any unknown, missing, unavailable, conflicting, or ambiguous required source-handling fact or policy authority yields `BLOCKED`."
Control: § 5 keeps `secret_presence` and `secret_presence_known` separate and blocks on unknown with no permissive default.
Governance constraint: Constitution Rule 2.
Structurally enforceable: yes — the known-flag is a required field and the block is unconditional.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-11 — secret bytes leak through logs, records, migration, errors, or correction.**
Requirement tested: ADR 0031's prohibition on intentionally persisting credentials in artifact history.
Control: § 5 confines classification to transient input, records only non-reversible observations (verdicts, categories, counts), and restricts logs and audit output to record identities, reason codes and decision ids. § 9 forbids reclassification of existing persisted records during migration, and § 3 makes corrections new records carrying the same non-reversible observation shape, so the correction path inherits the same restriction.
Structurally enforceable: partially — the record schema structurally excludes plaintext, and the migration rule structurally excludes reclassification; the logging restriction is a stated contract obligation that ADR 0036 must bind and implementation must honour. Recorded as a residual condition rather than a finding, because the durable surfaces are schema-constrained.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-12 — n8n, provider, or model mutates Source Handling authority.**
Requirement tested: ADR 0033 § Canonical ownership; ADR 0034 and ADR 0035 consumer status.
Control: § 1 gives none of them the publication capability; the signed handoff carries non-content lineage only.
Structurally enforceable: yes — the handoff type cannot express a classification.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-13 — historically unknowable cutoff reconstructed from current state.**
Requirement tested: ADR 0033, "Historical absence remains explicit absence."
Control: § 6 defines `AUTHORITY_NOT_KNOWN_AT_CUTOFF` as a terminal `BLOCKED` state and § 8 forbids substitution.
Structurally enforceable: yes — absence is a returned result, not an error path that invites a fallback.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-14 — resolver consumer also publishes.**
Requirement tested: ADR 0033 consumer-only rule at the seam actually used by Smart Prompt Machine.
Control: § 6 returns `EvidencePreModelSourceHandlingAuthority` over a read-only store view with no publish method reachable.
Structurally enforceable: yes — the returned type's reachable surface excludes mutation.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-15 — persistence gains semantic classification authority by convenience.**
Requirement tested: ADR 0033 § Persistence invariant; Constitution Rule 4 on convenience never justifying boundary violation.
Control: § 4 states the repository "stores, verifies, and refuses; it never derives, defaults, or asserts a classification". Option C is rejected on quotation of the same invariant, so the boundary is defended in both the design and the option analysis.
Structurally enforceable: yes — the repository holds no capability token and no classification input path.
Implementation must invent material architecture: no.
Result: `PASS`.

**H-16 — rollback re-enables process-local fabricated authority.**
Requirement tested: fail-closed rollback; ADR 0033's prohibition on permissive fallback.
Control: § 9 defines rollback as removing resolver wiring so consumers fail closed, retains durable tables because discarding published authority would destroy historical truth, and confines the in-memory `AuthorityStore` to a test double that is explicitly not a production path.
Structurally enforceable: yes — rollback removes a wiring, it does not select an alternative authority source.
Implementation must invent material architecture: no.
Result: `PASS`.

**Falsifiability determination.** All sixteen scenarios are documented with authoritative requirement, control, structural-enforceability judgement and result. Fifteen are fully structurally enforced; H-11's logging restriction is a contract obligation carried into ADR 0036 scope with its durable surfaces schema-constrained. The dimension result is supported.

## Dimension Results

Dimensions are those listed in `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Audit Dimensions.

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | `PASS` | Blocker independently reproduced: no production `SmartPromptMachine` construction exists and all publication seeding is test-only | None |
| Scope completeness | `PASS` | All nine mechanics deferred by ADR 0033 are bounded and addressed | None |
| Canonical consistency | `PASS` | Verified against Constitution, canonical map, governance, and every accepted ADR; no contradiction | None |
| Evidence integrity | `PASS` | Twelve evidence items re-verified against repository state at the reviewed revision | None |
| Assumption discipline | `PASS` | Four assumptions carry falsification conditions and consequences; none is presented as evidence | None |
| Option completeness | `PASS` | Five materially distinct options including both ADR-forbidden ones | None |
| Option normalization | `PASS` | One criteria set applied to all five options | None |
| Comparative fairness | `PASS` | Hostile cases applied to the recommendation and to rejected options alike | None |
| Falsifiability | `PASS` | Sixteen scenarios individually evidenced in the matrix above | None |
| Authority and ownership | `PASS` | Realizes the ADR 0033 owner; creates no competing authority; capability-enforced publisher boundary | None |
| Persistence and replay | `PASS` | Durable transactional storage, strict-known cutoff resolution, non-branching supersession | None |
| Evidence and provenance | `PASS` | Signed single-use authorization with lineage on every record | None |
| Implementation impact | `PASS` | ADR 0036 scope and deferred items stated explicitly; no material architecture left to invent | None |
| Migration impact | `PASS` | Additive only; no historical backfill; ordering and rollback defined | None |
| Operational impact | `PASS` | Key provisioning identified as an explicit rollout precondition that fails closed | None |
| Testability and validation | `PASS` | In-memory store retained as test double; conformance tests correctly deferred to ADR 0036 scope | None |
| Maintainability and extensibility | `PASS` | Option E preserved as additively adoptable | None |
| Governance compatibility | `PASS_WITH_FINDINGS` | Lifecycle and precedence honoured, but the quality-standard gate precondition is unmet | F-002 |
| Traceability | `PASS_WITH_FINDINGS` | Complete except for post-merge placeholders | F-001 |
| Risks and unresolved uncertainty | `PASS` | Five risks with mitigations and residual uncertainty; four open questions correctly non-blocking | None |

## Findings

### F-001 — Traceability placeholders pending merge

- **Evidence:** ADPR-0012 § Traceability records `PR: recorded on merge` and `Merge commit: recorded on merge`.
- **Location:** `docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md` § Traceability.
- **Category:** Traceability / documentation quality.
- **Affected dimension:** Traceability.
- **Severity:** `A`
- **Decision impact:** None. The values cannot exist before merge.
- **Consequence if ignored:** None; normal lifecycle completion.
- **Required action:** Populate on merge of PR #394.
- **Blocks ADR:** `NO`

### F-002 — Quality Assessment omits mandatory dimensions and uses a non-canonical rating scale

- **Evidence:** `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` defines seventeen mandatory quality dimensions and the rating scale `EXCELLENT` / `GOOD` / `ACCEPTABLE` / `NEEDS_IMPROVEMENT` / `UNACCEPTABLE`, and its Mandatory Decision Gate permits an author `READY_FOR_ADR` declaration only when "every dimension has a recorded rating and rationale". `docs/templates/ADPR_TEMPLATE.md` § Quality Assessment repeats that instruction. ADPR-0012 § Quality Assessment records seven rows rated "Strong" or "Adequate". Ten mandatory dimensions have no recorded rating: scope completeness, assumption discipline, comparative fairness, persistence and replay quality, evidence and provenance quality, operational quality, implementation and migration impact, testability and validation, maintainability and extensibility, and risk quality.
- **Location:** `docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md` § Quality Assessment.
- **Category:** Governance compatibility / preparation-record quality.
- **Affected dimension:** Governance compatibility.
- **Severity:** `B`
- **Decision impact:** The author `READY_FOR_ADR` self-assessment was declared without satisfying the gate that authorizes it, so that self-assessment carries less weight than it appears to. It does not alter the architectural decision, because the protocol already treats self-assessment as non-authoritative and this audit derives the verdict independently.
- **Consequence if ignored:** Reduced auditability of the preparation record and a precedent for declaring readiness without the mandated dimension record.
- **Existing-substance analysis:** Per `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Existing-Substance Rule, the omitted dimensions were checked for substance elsewhere in the record. Scope completeness is covered by § In scope / Out of scope; assumption discipline by the Assumptions table; comparative fairness by the Comparative Analysis table; persistence and replay quality by Constraints § Persistence and migration, § Replay, and Selected Contract § 4 and § 8; evidence and provenance quality by the Evidence Inventory and § 2; operational quality by Constraints § Operational and § 9; implementation and migration impact by § 9 and ADR Readiness; testability and validation by § 9's test-double retention; maintainability and extensibility by the Option E reconsideration path; risk quality by the Risks table. The substance is present; the canonical rating record is not. The defect is therefore documentation quality, not decision distortion.
- **Required action:** Before ADR 0036 approval, restate ADPR-0012 § Quality Assessment across all seventeen mandatory dimensions using the canonical rating scale. This is a preparation-record correction and requires no change to the selected design.
- **Blocks ADR:** `NO`

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-001 | A | None | None | NO | ADPR-0012 § Traceability |
| F-002 | B | Weakens the author self-assessment; does not alter the decision | Reduced auditability of the preparation record | NO | ADPR-0012 § Quality Assessment vs `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` |

## Verdict Derivation

- **Highest unresolved severity:** `Class B` (F-002), with one trivial `Class A` (F-001).
- **Class C count:** 0. No finding materially distorts, invalidates, or prevents the architectural decision.
- **Class D count:** 0. The problem is well defined, bounded, evidenced, and its option space enumerable.
- **Cumulative-Quality Rule assessment:** a single Class B finding, confined to one section, whose omitted substance is demonstrably present elsewhere in the record. Its cumulative effect does not make the preparation materially difficult to interpret, compare, or audit, so `CONDITIONAL_ADR_READY` is not reached. Defect count alone is expressly insufficient to escalate.
- **Materiality of the design itself:** all sixteen hostile scenarios resolve `PASS`, fifteen with structural enforcement. Implementation would not need to invent material architecture; the remaining choices are module paths, table spellings, detector selection, and the four recorded open questions, all correctly deferred.
- **Applying `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Verdict Derivation:** highest unresolved severity `A or B` with Class B findings that are not cumulatively material yields `READY_FOR_ADR_WITH_MINOR_FINDINGS`. Strict `READY_FOR_ADR` is not available, because that verdict permits only trivial Class A findings and one Class B finding is unresolved.

The prior `READY_FOR_ADR` verdict on this audit was not derived from a review of the controlling documents and did not survive re-derivation. It is superseded by the verdict below.

## Final Verdict

- `READY_FOR_ADR_WITH_MINOR_FINDINGS`

### Progression semantics

Progression Gate: this verdict authorizes ONLY the next ADR drafting lifecycle for planned ADR 0036. It specifically does NOT authorize any of the following:

- runtime Source Handling implementation of any kind;
- modification of PR #394 or of ADPR-0012 by this audit;
- merging PR #394;
- completion or unblocking of Issue #390;
- completion of Issue #393.

F-002 must be corrected before ADR 0036 approval. It does not block ADR 0036 drafting.

## Required Corrections or Conditions

None blocking ADR drafting.

One condition applies before ADR 0036 approval: correct F-002 by restating ADPR-0012 § Quality Assessment across all seventeen mandatory dimensions using the canonical rating scale of `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`.

## Non-Blocking Follow-Up

- F-001: populate the PR number and merge commit in ADPR-0012 § Traceability on merge of PR #394.
- H-11 residual: ADR 0036 should bind the logging and diagnostics restriction explicitly, since that surface is contract-obligated rather than schema-constrained.
- Auditor independence: the owner may commission a non-Claude audit given the limitation recorded in Metadata.

## Audit Completion Check

- [x] Exact artifact and revision identified
- [x] Audit scope identified
- [x] Evidence sources listed
- [x] Controlling documents independently reviewed and applied
- [x] Applicable dimensions assessed
- [x] All sixteen mandatory hostile scenarios individually evidenced
- [x] Every finding includes all mandatory fields
- [x] Class C or D findings demonstrate decision consequence
- [x] Findings matrix completed
- [x] Verdict derived from severity and materiality
- [x] Prior review findings re-verified
- [x] Targeted re-audit rule followed where applicable
- [x] Auditor did not recommend or rank options
