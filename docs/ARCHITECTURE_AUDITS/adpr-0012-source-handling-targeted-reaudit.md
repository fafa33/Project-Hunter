# Targeted Independent Architecture Re-Audit — ADPR-0012 Source Handling Contract

> Status: `COMPLETED`

## Metadata

- Reviewed artifact: `docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md`
- Reviewed revision: `90954330aedb3bac6fbbae69df04a2b0e443bf37`
- Repository evidence baseline: `90954330aedb3bac6fbbae69df04a2b0e443bf37`
- Audit target: main @ `90954330aedb3bac6fbbae69df04a2b0e443bf37` (PR #394 squash merge)
- Audit type: `TARGETED`
- Auditor: `Claude Code independent architecture audit agent`
- Auditor independence limitation: the corrections under audit were authored by a
  Claude Code session (PR #394, commits `ae001fd`/`c1a59e4`), and this re-audit is
  also performed by one. Recorded per the same limitation carried in the prior
  audit; the owner may commission a non-Claude audit.
- Audit date: `2026-08-31`
- Evidence cutoff: `2026-08-31T08:22:04Z`
- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Re-Audit Protocol —
  Targeted Re-Audit
- Governing issue for this audit: Issue #397
- Prior independent audit: Issue #395 / PR #396, merged `1adb65911eb5b25df87f08f1d84e054b652acc76`,
  verdict `READY_FOR_ADR_WITH_MINOR_FINDINGS`, reviewed revision `8e30a77aa19f42ffd3679042867068f40d96946c`
- Correction pull request: PR #394 (post-audit review round), reviewed HEAD
  `c1a59e4e33468d5c032515ab76b0dd7f3a798bfb`, squash-merged to main as
  `90954330aedb3bac6fbbae69df04a2b0e443bf37`
- Planned decision if this audit permits progression: ADR 0036

## Audit Scope

This is a **targeted** re-audit under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` §
Re-Audit Protocol — Targeted Re-Audit, not a full audit restart. The prior audit
(#395/#396) assessed ADPR-0012 at `8e30a77`. Independent review of PR #394 after
that audit raised four additional contract-completeness/security findings,
corrected before merge. This audit validates only:

1. four-family (`FACT`/`POLICY`/`FIELD_CATEGORY_REGISTRY`/`AUTHORIZATION_RULE`)
   publication ownership and genesis bootstrap (ADPR-0012 § 1);
2. `authorization_id` binding into the signed `PublicationAuthorization` claims
   (§ 2);
3. explicit `UNKNOWN`/known-flag semantics for `sensitivity` and
   `persistence_restriction` (§ 3, § 5);
4. transient-before-persistence Issue ingress ordering (§ 7);
5. previously corrected interface/integration consistency: the callable
   `SourceHandlingAuthorityResolver` protocol (§ 6), and architecture-index /
   audit-reference consistency.

Unchanged ADPR-0012 content (Problem Statement, Evidence Inventory, Options,
Falsification Results, Constitution/Governance Review, and the Quality
Assessment corrected under prior finding F-002) is not re-assessed; the prior
audit's conclusions on that content stand and are carried forward without
re-derivation, per the protocol's instruction not to restart an unlimited
full-document search. Confirmed via `git diff --stat` that PR #394 changed only
`docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md`
and `docs/architecture-index.md`; no runtime source was touched, so the runtime
evidence the prior audit gathered (`AuthorityStore`, `PublicationAuthorization`,
`resolve_pre_model_source_handling`, `SmartPromptMachine`) remains current and is
reused rather than re-collected.

## Prior Review Finding Re-Verification

| Prior finding | Source | Current status | Evidence |
|---|---|---|---|
| F-001 | Audit #395/#396 (Class A) | Unaffected by this delta | ADPR-0012 § Traceability still carries the same post-merge placeholder pattern; no decision consequence, not re-litigated |
| F-002 | Audit #395/#396 (Class B) | Corrected prior to this delta | ADPR-0012 § Quality Assessment restates all seventeen `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` dimensions with canonical ratings (verified present at current revision) |
| PR394-F1 | PR #394 independent review (publication ownership) | Corrected | § 1 now assigns `FACT`, `POLICY`, `FIELD_CATEGORY_REGISTRY`, `AUTHORIZATION_RULE` to the one service; genesis bootstrap specified. Consequence if unresolved would have been: a fresh deployment could never bootstrap a resolvable authority set, since `resolve_pre_model_source_handling` requires strict-known heads for all four families |
| PR394-F2 | PR #394 independent review (`authorization_id` binding) | Corrected | § 2 includes `authorization_id` in the six signed claims. Consequence if unresolved: the id could be relabeled onto a differently-scoped presentation without invalidating the signature, defeating single-use replay protection |
| PR394-F3 | PR #394 independent review (unknown semantics) | Corrected | § 3/§ 5 add `sensitivity_known` and `persistence_restriction_known`. Consequence if unresolved: those two required dimensions could be silently omitted with no fail-closed path, contradicting ADR 0033's unconditional unknown-yields-`BLOCKED` invariant |
| PR394-F4 | PR #394 independent review (retention ordering) | Corrected | § 7 now requires `FACT`/`POLICY` resolution before the durable intake write. Consequence if unresolved: Issue content the resolved policy might forbid retaining could already be durable by the time that policy existed |

## Evidence Sources Examined

Scoped to the delta; not a full evidence re-collection.

| Source | Locator | Disposition |
|---|---|---|
| ADPR-0012 §§ 1–7 | `90954330a:docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md` | re-read in full at target revision |
| Prior targeted audit artifact | `90954330a:docs/ARCHITECTURE_AUDITS/adpr-0012-source-handling-independent-audit.md` | reused for unchanged-area conclusions, not re-derived |
| `docs/architecture-index.md` | `90954330a` | re-checked both ADPR-0012 rows for staleness against the now-merged PR #394 |
| `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Re-Audit Protocol | current | governs this audit's scope and finding-admission rules |
| `AuthorityStore.publish_genesis_rule` / `publish_successor_rule` | `src/hunter/evidence_intelligence/source_handling.py:383-435` | confirmed unchanged since prior audit (`git diff --stat` over the merged range touched no `src/`); re-verified genesis bootstrap bounds against this code |
| `resolve_pre_model_source_handling` | `src/hunter/evidence_intelligence/pre_model.py:279-330` | confirmed four-family requirement (`FACT`, `POLICY`, `FIELD_CATEGORY_REGISTRY`, `AUTHORIZATION_RULE`) is exactly what § 1 now names |
| `SourceHandlingAuthorityResolver` / `PromptContextCompiler.compile()` | `src/hunter/evidence_intelligence/smart_prompt_machine.py:53-60`, `:269` | confirmed callable `__call__` protocol and call site unchanged, matching § 6 |
| Issue #397 | governing issue for this audit | defines delta scope and authorization boundaries |
| PR #394 | correction pull request | source of the four corrected findings and their resolution commits |

## Dimension Results

Only dimensions touched by the delta are assessed; unlisted dimensions are
unaffected by this revision and retain the prior audit's determination.

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Authority and ownership | `PASS` | Single service remains sole publisher across all four families; genesis bootstrap is capability-external, one-shot, and durably recorded | None |
| Persistence and replay | `PASS` | Genesis and successor publication both go through the existing append-only, head-CAS, strict-known mechanics; no new persistence path introduced | None |
| Evidence and provenance | `PASS` | `authorization_id` now inside the signed claim set; tampering is a signature failure, not a bypassable secondary check | None |
| Falsifiability | `PASS` | All hostile cases below (H-17 … H-22) resolve `PASS` with a named structural mechanism | None |
| Governance compatibility | `PASS_WITH_FINDINGS` | ADPR-0012 itself is internally consistent and complete for the delta; the navigational architecture-index is stale against the now-merged correction | F-101 |
| Traceability | `PASS_WITH_FINDINGS` | Delta is traceable from ADPR-0012 Decision History to PR #394 and back to audit #395/#396; one internal citation range is off by five lines | F-102 |

## Hostile and Falsification Checks

**H-17 — a second component attempts to publish `FIELD_CATEGORY_REGISTRY` or `AUTHORIZATION_RULE` directly, bypassing the named service.**
Requirement: single-owner preservation under ADR 0033.
Control: § 1 assigns all four families to the one `SourceHandlingAuthorityService`
holding the only `SourceHandlingPublicationCapability`; § 4 (unchanged) states the
repository "retains no path equivalent to `direct_write`."
Result: `PASS`.

**H-18 — genesis bootstrap is invoked a second time, or by an ordinary consumer, to mint a competing `AUTHORIZATION_RULE`.**
Requirement: genesis bounded, non-replayable, unavailable to ordinary callers,
cannot become a permanent alternate write path.
Control: `publish_genesis_rule` refuses "unless family history is empty"
(`source_handling.py:394`) — bounded and non-replayable by construction, since
any successful first call makes every later call fail closed. § 1 states it is
"an operator deployment action, not a runtime code path the service or any
consumer can trigger" and "out of the capability's reach the same as every
other publication surface" — unavailable to Smart Prompt Machine, n8n,
providers, fallback runtime, callers, orchestrators, and generic repositories,
none of which hold the capability or operator material. It writes into the
same append-only `compare_and_append` history as every other record
(`source_handling.py:400-406`), so it is auditable through the ordinary
read path, not a hidden or parallel channel. Non-recursive: it is a single
one-shot state transition with no self-reference.
Result: `PASS`.

**H-19 — a presented `PublicationAuthorization` has its `authorization_id` swapped for a different value while every other field, and the signature bytes, are left untouched.**
Requirement: relabeling-only tampering structurally rejected.
Control: § 2 states `authorization_id` is one of the six signed claims;
verification checks the signature over the full claim set. A swapped id changes
the signed message, so the existing signature no longer verifies against it —
rejection is a signature-verification failure, not a separate lookup an
implementer could omit.
Result: `PASS`.

**H-20 — `sensitivity` or `persistence_restriction` is omitted entirely from a FACT payload.**
Requirement: missing/unknown required authority is explicit and fails closed to
`BLOCKED`, no permissive default.
Control: § 3 pairs both with a `_known` flag exactly as the two pre-existing
dimensions are paired; § 5 states "unknown, absent, or `UNKNOWN` values for any
of the four known-flags yield `BLOCKED`, with no permissive default, enforced at
persistence" via independent rederivation from the payload, not a caller-
asserted flag.
Result: `PASS`.

**H-21 — Issue content containing a credential is submitted for classification and the process crashes before `FACT`/`POLICY` resolve for that scope.**
Requirement: transient ingress, crash/retry, and secret-bearing hostile case
closed.
Control: § 7 requires content to be "held only as transient input" and "never
written through the durable intake path" until eligibility resolves. Because
the content has no durable footprint by construction during that window, a
crash before resolution leaves nothing recoverable to leak; retry re-enters the
same transient-until-resolved gate rather than reading back a partial durable
write, since none exists. § 5's logging restriction ("no classified content is
written to logs or durable plaintext merely to classify it") applies to this
same classification activity, closing the diagnostics/logging sub-case.
Reconstruction restriction is closed by the existing `persistence_restriction`
policy dimension, which § 5 already states governs "durable retention and
reconstruction eligibility independently."
Result: `PASS`.

**H-22 — the durable `EvidenceIntelligenceIntake.ingest()` call is issued for Issue-sourced content before `FACT`/`POLICY` exist for that scope.**
Requirement: unclassified/secret-bearing content cannot become durably
recoverable before persistence authority is known.
Control: § 7's ordering is stated as binding — "Issue-sourced content must not
reach that durable `ingest()` call until the Source Handling Authority has
published `FACT` and `POLICY`... and those records resolve
`persistence_restriction` to a value that permits retention."
Result: `PASS`.

**Falsifiability determination.** All six delta-scoped hostile cases resolve
`PASS` with a named structural mechanism, consistent with the prior audit's
finding that implementation would not need to invent material architecture for
this contract.

## Findings

### F-101 — Architecture-index rows are stale against the merged PR #394 correction

- **Evidence:** `docs/architecture-index.md` line 50 states PR #394's four
  findings "remain open and are recorded on that pull request; they are owner
  decisions"; line 37 states "preparation merge not yet recorded." Both are
  false as of `90954330a`: PR #394 is merged, the four findings are corrected
  (verified in ADPR-0012 §§ 1, 2, 3, 5, 7 above), and the merge commit exists.
- **Location:** `docs/architecture-index.md` lines 37, 50.
- **Category:** Traceability / governance-registry consistency.
- **Affected dimension:** Governance compatibility.
- **Severity:** `B`
- **Decision impact:** None to the architectural decision itself — ADPR-0012 is
  the substantive record and is correct and complete for the delta. The index
  is a navigation aid only (`docs/CANONICAL_ARCHITECTURE_MAP.md`: it "creates no
  independent authority"); its staleness cannot itself distort the decision.
- **Consequence if ignored:** A reader consulting the index rather than
  ADPR-0012 directly would incorrectly believe PR #394's findings are still
  open and unresolved, weakening the index's reliability as the intended
  single source of truth for current status.
- **Required action:** Update both rows to record PR #394 as merged at
  `90954330aedb3bac6fbbae69df04a2b0e443bf37`, the four findings as corrected,
  and this targeted re-audit's outcome once available.
- **Blocks ADR:** `NO`

### F-102 — One internal citation range is off by five lines

- **Evidence:** ADPR-0012 § 1 cites `src/hunter/evidence_intelligence/pre_model.py:284-330` for the four-family requirement; the cited function (`resolve_pre_model_source_handling`) actually starts at line 279. The substantive claim — that all four families are required — is correct and fully contained within either range.
- **Location:** `docs/architecture-records/ADPR-0012-source-handling-design-implementation-contract.md` § 1.
- **Category:** Citation accuracy.
- **Affected dimension:** Traceability.
- **Severity:** `A`
- **Decision impact:** None; the substantive authority is correctly represented, only the line boundary is off.
- **Consequence if ignored:** Negligible; a reader following the citation still lands inside the correct function.
- **Required action:** Correct the citation to `pre_model.py:279-330` at next revision.
- **Blocks ADR:** `NO`

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-101 | B | None | Reduced reliability of the navigational index as current-status source of truth | NO | `docs/architecture-index.md` lines 37, 50 |
| F-102 | A | None | Negligible | NO | ADPR-0012 § 1 citation |

## Verdict Derivation

- **Highest unresolved severity:** `Class B` (F-101), with one trivial `Class A` (F-102).
- **Class C count:** 0. No finding materially distorts, invalidates, or prevents the architectural decision within the audited delta.
- **Class D count:** 0.
- **All four targeted correction areas plus the two consistency checks resolve `PASS`** with structural, not conventional, enforcement (H-17 … H-22).
- **Cumulative-Quality Rule assessment:** one Class B finding, confined to a navigational document outside ADPR-0012 itself, with no effect on the substantive record's reliability. Does not meet the bar for `CONDITIONAL_ADR_READY`.
- **Applying `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` § Verdict Derivation:** highest unresolved severity `A or B`, not cumulatively material → `READY_FOR_ADR_WITH_MINOR_FINDINGS`.

This matches the prior audit's verdict class and is not weakened by it: the
four findings that verdict left open are now independently re-verified
corrected, and the only new finding is a registry-staleness issue with no
decision impact.

## Final Verdict

- `READY_FOR_ADR_WITH_MINOR_FINDINGS`

### Progression semantics

Per Issue #397, this verdict authorizes ONLY the next ADR drafting lifecycle
for planned ADR 0036, contingent on repository-owner approval to proceed. It
does NOT authorize:

- runtime Source Handling implementation of any kind;
- Issue #390, #389, or #386 work;
- ADR 0036 acceptance (drafting readiness only);
- n8n, provider, or fallback-runtime changes;
- merge of any kind.

## Required Corrections or Conditions

None blocking ADR 0036 drafting readiness.

One condition applies before ADR 0036 approval, carried forward unchanged from
the prior audit: correct prior finding F-001 (populate ADPR-0012 §
Traceability post-merge fields).

## Non-Blocking Follow-Up

- F-101: reconcile `docs/architecture-index.md` rows 37 and 50 with the merged
  PR #394 state and this audit's outcome.
- F-102: correct the `pre_model.py` citation range in ADPR-0012 § 1.
- Carried forward from prior audit: H-11's logging/diagnostics restriction
  remains a contract obligation for ADR 0036 to bind explicitly, since durable
  surfaces are schema-constrained but the logging discipline itself is not.
- Auditor independence: unchanged limitation from the prior audit; the owner
  may commission a non-Claude audit.

## Audit Completion Check

- [x] Exact artifact and revision identified
- [x] Audit scope identified and explicitly bounded to the targeted delta
- [x] Evidence sources listed, reused where unchanged rather than re-collected
- [x] Applicable dimensions assessed (delta-scoped only, per Targeted Re-Audit rule)
- [x] All six delta-scoped hostile scenarios individually evidenced
- [x] Every finding includes all mandatory fields
- [x] No Class C or D findings; none required to demonstrate blocking decision consequence
- [x] Findings matrix completed
- [x] Verdict derived from severity and materiality
- [x] Prior review findings re-verified, including the four PR #394 corrections
- [x] Targeted re-audit rule followed: no unlimited full-document search performed
- [x] Auditor did not recommend or rank options
