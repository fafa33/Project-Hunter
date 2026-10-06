# ADR 0038: Source Handling Authority Store on the GitHub-Native Anchored Ledger

## Status

**Accepted** (2026-10-04, owner decision OD-5 on Issue #560, together with ADR 0037 revision 3). Originally
drafted under OD-5 (direction approved,
conditional on the OD-4 anti-rollback result, which ADR 0037 revision 3 now provides).

- **What it amends.** [ADR 0036](0036-source-handling-design-implementation-contract.md) §4 ("Persistent
  authority store") and §9 ("Migration, rollout, and rollback"), and only those sections.
- **What it reaffirms.** ADR 0036 §1–§3 and §5–§8, ADR 0033 and ADR 0031, all unchanged.
- **Acceptance.** It is accepted together with [ADR 0037](0037-github-native-issue-agent-execution.md) or
  not at all.

## Context

ADR 0036 §4 places `SourceHandlingAuthorityRepository` in additive tables of the Evidence Intelligence
SQLite database. In production that database lives on the Railway service
`Project-hunter/hunter-issue-agent-issuer`, which ADR 0037 retires. A read-only trace of that service's
variable names (2026-10-04) shows it also holds `HUNTER_SOURCE_HANDLING_SIGNING_KEY`.

The owner forbids any always-on replacement. The owner also requires that a previously valid signed prefix
can never regain authority by moving a reference backward (OD-4). ADR 0037 D2a meets that requirement with
a ruleset-anchored, forward-only branch namespace, proven live in Slice 0 (FMEA AR-1, AR-3).

## Decision

### 1. Store location and shape (replaces the storage medium in ADR 0036 §4)

`SourceHandlingAuthorityRepository` persists to the forward-only branch
`refs/heads/hunter-state/v1/source-handling`, under the ADR 0037 D2a anchor ruleset. The tree of each
commit:

```text
root.json                                  # pinned operator root, genesis-rule digest, verification-key ids
records/<record_id>.json                   # append-only FACT / POLICY / FIELD_CATEGORY_REGISTRY / AUTHORIZATION_RULE
provenance/<record_id>.json                # append-only EVIDENCE / VERIFIER provenance records
heads/<family>/<scope_sha256>.json         # current supersession head per scope
authorizations/<authorization_id>.json     # issued and consumed PublicationAuthorization state
canonical-keys/<key_sha256>.json           # canonical-key marking
```

**One ADR 0036 transaction is exactly one commit.** It covers authorization consumption, head
compare-and-set, record append, and canonical-key marking. Partial application is impossible, because a
commit either becomes the branch head through the CAS or does not exist.

### 2. Concurrency and idempotency

- **Head CAS.** The ADR 0036 head CAS is
  `git push --force-with-lease=refs/heads/hunter-state/v1/source-handling:<observed>`. It is live-proven to
  admit exactly one writer per observed head (FMEA A-1). The loser re-resolves, as ADR 0036 already
  requires.
- **Provisioning.** Per-Issue provisioning stays idempotent. Identical content is a no-op. An existing,
  different head for the same scope is refused ("refusing to replace") with zero writes, and is never
  superseded silently.
- **Lost ACK.** After any push outcome the writer re-reads the branch. Its exact commit present means
  success.

### 3. Tamper evidence and anti-rollback (extends ADR 0036 §4)

ADR 0036 tamper evidence is retained: the stored payload digest plus supersession-chain verification,
re-checked on every read. The following are **added**, each failing as `TAMPER_DETECTED` and blocking:

- every record keeps its existing K_SH `PublicationAuthorization` binding;
- the first-parent chain: each commit's parent is the previous store head, and each commit applies exactly
  one transaction;
- trusted workflow provenance (ADR 0037 D2 check 5): the writing run is the control-environment
  `authorize` job (or the owner-dispatched bootstrap) on `main`, at attempt 1;
- **anchor integrity** (ADR 0037 D2a). This is authenticated-read verification that the pinned anchor
  ruleset is active, unmodified (`updated_at`), still carries `deletion` and `non_fast_forward`, and covers
  this branch.

**Anti-rollback.** Because the branch is forward-only for every principal, including the repository admin
(live: `GH013`), strict-known cutoff reads run over a history that cannot be truncated or rewound. An
anchor weakening is detected and blocks all resolution: no permissive fallback, and ADR 0033's
"unresolved authority is BLOCKED".

### 4. Confidentiality

The repository is public.

- **What the store may hold.** Only classification metadata, decisions, provenance, identities, digests and
  timestamps. These are the ADR 0036 record families as currently defined, which carry no source content
  and no secret material.
- **Schema enforcement.** The store schema refuses any field holding source content, prompt or evidence
  bytes, or key material.
- **When something cannot be stored.** If a future Source Handling decision needs a record that is not
  public-safe, this store cannot hold it. The decision resolves `BLOCKED`. It is never written publicly,
  and never kept in a side channel.

### 5. Writers, readers, read view

- **Sole publisher.** ADR 0036 §1 is unchanged: `SourceHandlingAuthorityService`, run inside the
  control-environment job that holds K_SH. No other job holds K_SH.
- **Readers.** Anyone can read; the branch is public. The canonical resolver
  (`SourceHandlingAuthorityResolver`) reads through an ephemeral, per-run SQLite **read view**. The view is
  materialized only after full verification of the branch (§3). It is never authoritative and never
  persisted beyond the job.
- **Unreachable store.** A GitHub outage or an ambiguous read resolves `BLOCKED`. It never resolves to a
  cached or default answer.

### 6. Strict-known replay (reaffirms ADR 0036 §8)

- Records keep `effective_from`, `recorded_at` and `known_at`.
- Physical admission time is the K_SH-signed `recorded_at` of the commit that admitted the record. The
  no-backdating rule stays.
- Commit order is admission order. Cutoff-parameterized reads replay deterministically over the
  forward-only history.

### 7. Migration, rollout, rollback (replaces ADR 0036 §9 for the production store)

1. **Rotate.** K_SH is rotated, because Railway held it. A new operator root and genesis rule are
   bootstrapped into the anchored store by an owner-dispatched, control-environment bootstrap. Its
   verification key id and fingerprint are pinned in repository content on `main`.
2. **No migration of the legacy store.** The legacy Railway SQLite authority history is **not** migrated
   into the public store. The owner exports it offline for audit (it sits beside INTERNAL build records),
   and it is never committed. Prior authorizations are not replayed (ADR 0037 D8).
3. **No dual authority.** The Railway service is suspended before the first anchored write.
4. **Rollback.** Disable the lifecycle workflows. Resolution then returns `BLOCKED`, which is fail-closed.
   Rollback never re-enables Railway.

## Consequences

- ADR 0036's sole-publisher, signed-authorization, `UNKNOWN`/`BLOCKED`, strict-known and correction
  semantics are preserved. Only the medium changes, from SQLite tables to an anchored signed Git ledger.
- Source Handling history becomes public. That is consistent with its non-secret definition, and is
  enforced by schema.
- Changing the anchor ruleset is a governed trust-root rotation. It blocks resolution until it is
  re-pinned.

## Alternatives Considered

| Alternative | Why not selected |
|---|---|
| Keep SQLite on Railway | Railway retired (owner) |
| Embed SH records in each Issue ledger commit | would merge two authorities' ledgers and break ADR 0036's sole-publisher boundary |
| Ephemeral per-run SH database re-provisioned each run | violates immutable versioned history and strict-known replay (ADR 0033/0036) |
| Custom refs or tags | no rollback protection (custom refs) or unenforced rulesets (tags), both live-falsified |
| Private repository or external database | owner decisions B-2/B-7 |

## Implementation Status

The amendment is **accepted and binding** (2026-10-04, OD-5). Its runtime is implemented: PR #562 delivered the
anchored store (`issue_agent_source_handling_store`), the one-shot `source-handling-bootstrap` command in the
lifecycle entry point, and the owner-dispatched `Hunter / Issue Agent Source Handling Bootstrap` workflow; PR #564
provisioned the repository-pinned public trust roots. No production path has written
`refs/heads/hunter-state/v1/source-handling` yet. The first anchored write is the owner-dispatched bootstrap, and
§7.3 keeps it gated on the Railway service being suspended first.
