# Live DPM Knowledge Feedback Loop — Design

Issue: #488
Status: implementation-authorizing design
Base: PR #487 merged on main

## Mission

Close the live return path from a validated engineering finding to a bounded,
replayable Knowledge Extraction proposal. The path strengthens the existing DPM
registry and therefore future Smart Prompt Machine prevention context without
creating a second prompt, review, registry, or merge authority.

## Authority graph

validated finding evidence
→ KnowledgeExtractionAuthority (normalize + validate + deduplicate)
→ immutable KnowledgeExtractionProposal
→ canonical-integration decision by existing repository/human authority
→ DEFECT_REGISTRY
→ EngineeringContextAuthority
→ SmartPromptMachine
→ signed implementation handoff

A proposal is evidence. It is never canonical truth and cannot mutate the
registry. Reviewers/models remain evidence producers only.

## Contract

The v1 input carries source kind, stable finding identity, PR, reviewed exact
head/base, reviewer, classification/disposition, normalized invariant, affected
paths, fix reference, regression evidence, and optional claimed family.

The v1 output is immutable and has one outcome:
- existing-family
- candidate-new-family
- excluded
- ambiguous

It includes a deterministic proposal id/digest and enough provenance to replay
the decision.

## Deterministic existing-family matching

No semantic model is allowed to invent equivalence. Existing-family mapping is
accepted only when a claimed family exists and deterministic evidence proves
the recurrence against that family: the normalized invariant must equal the
canonical invariant after bounded normalization and at least one affected path
must intersect the family's declared applicability. If a claimed family is
unknown, invariant/path evidence conflicts, or more than one authoritative
mapping is asserted, fail closed as ambiguous.

This deliberately prefers false-negative proposal-only handling over silently
polluting canonical knowledge.

## Exclusions

False positives, style-only findings, obsolete findings, reviewer/provider
availability, quota/rate-limit state, and infrastructure-only observations are
excluded from defect learning. Exclusion remains auditable and replayable.

## Candidate new family

A confirmed defect with complete provenance that cannot deterministically map
to an existing family may be emitted as candidate-new-family. That outcome has
no registry-write authority and requires canonical approval before integration.

## Idempotency and replay

Proposal identity is a SHA-256 digest over canonical v1 finding evidence.
Replaying identical evidence produces the identical proposal id and payload.
Changing exact-head provenance or substantive evidence changes identity.

## Fail-closed rules

Malformed SHA, missing PR/finding/reviewer, unsupported classification/source,
empty invariant/path/fix/regression evidence for a confirmed defect, unknown
claimed family, incomplete registry family schema, or noncanonical registry
domains cannot become existing-family knowledge.

## Historical convergence

HISTORICAL_DEFECT_BACKFILL remains the historical audit artifact. It is not
rewritten by this PR. Future backfill ingestion must translate each historical
record into this same v1 finding/proposal contract; no second learning schema is
authorized.

## Failure-mode checklist

- duplicate DFF: prevented by deterministic existing-family mapping;
- reviewer prose as truth: proposal-only authority;
- false positive/style/infrastructure pollution: explicit exclusions;
- stale/missing provenance: exact reviewed SHA validation;
- replay duplication: content-addressed proposal id;
- external outage: no provider/network dependency;
- registry mutation: no write API exists in this authority;
- SPM bypass: output only feeds canonical integration, never prompt dispatch;
- historical/live divergence: one future convergence contract.

## Verification

TDD first. Regression must prove the PR #487 noncanonical lifecycle/boundary
finding maps to DFF-004, exclusion classes cannot become defect proposals,
unknown claimed families fail closed/ambiguous, replay is idempotent, and
candidate-new-family cannot claim canonical authority.

## Follow-on: closing the loop from candidate to canonical registry

`hunter-knowledge-learning.yml` builds the exact-head ledger and renders a
registry candidate on every `pull_request_review`/`pull_request_review_comment`
event -- capture and candidate-rendering already run off the merge-critical
path, since neither step is a required check. But `materialize_learning_ledger`
(the one function with actual write authority over `docs/DEFECT_REGISTRY.json`,
still gated exactly as this design specifies: local-content-only, never
committing, pushing, or opening a PR) had no caller. The workflow only
uploaded the ledger and candidate as a 90-day, non-authoritative CI artifact,
so the "canonical-integration decision by existing repository/human authority"
step in the authority graph above had no ingress: nothing ever turned a
rendered candidate back into a change a human could review and merge through
the normal PR path.

`scripts/hunter_canonicalize_learning.py` closes that gap by composing the
existing `build_learning_ledger` and `materialize_learning_ledger` authorities
behind one CLI, with no new registry, persistence, or replay semantics:

```
python scripts/hunter_canonicalize_learning.py \
  --pr <N> --head <exact-head-sha> --base <exact-base-sha> \
  --observations <path-to-collected-observations.json> [--dry-run]
```

- **Durable backlog, no new persistence layer.** GitHub's own PR review/comment
  history is the backlog: `hunter_collect_learning_observations.py` and
  `hunter_collect_sonar_observations.py` already page through the *complete*
  history for an exact head on every run, not a delta, so a missed CI run, a
  stopped worker, or a GitHub-Actions outage loses nothing -- rerunning later
  reproduces the identical observation set. `docs/DEFECT_REGISTRY.json` itself
  is the second half of the backlog contract: `already_integrated` and the
  registry-digest replay check in `controlled_learning_integration.py` make
  reprocessing any PR, at any later time, safe and idempotent, and a registry
  that has since moved on fails closed with "stale registry snapshot" rather
  than silently mis-integrating -- no separate pending-queue file is needed or
  introduced.
- **Local/Mac execution path.** The script takes no network or provider
  dependency; it requires only a previously-collected `--observations` file
  (the same input contract `hunter_incremental_knowledge.py` already defines).
  It can run identically on a local machine or inside CI.
- **No caller-selected write target.** Matching the existing
  `hunter_materialize_learning_candidate.py`/`hunter_incremental_knowledge.py`
  convention, the script exposes no `--registry`/`--output` flag; the registry
  and ledger paths are read from `controlled_learning_integration`'s own
  module constants, so a caller cannot redirect where the write lands.
- **Still never merge-blocking.** The script is not wired into any required
  check or into `hunter_pr_preflight.py`. Applying its result to
  `docs/DEFECT_REGISTRY.json` is a local file edit a human then carries through
  the unchanged, fully-governed commit/push/PR/review/merge path -- exactly
  the "normal PR + owner-merge path remains the sole route to canonical main"
  guarantee this design already states.
- **Bounded DPM/SPM reuse, already wired.** No further integration step exists
  or is needed: `EngineeringContextAuthority` already reads
  `docs/DEFECT_REGISTRY.json` live and performs the bounded family selection
  that feeds `SmartPromptMachine`. Once a canonicalized update merges through
  the normal path, it is available on the next SPM invocation with no
  additional wiring.

Regression: `tests/test_canonicalize_learning_cli.py` proves dry-run preview
without persisting, atomic apply, idempotent replay, exclusion classes never
integrating, conflicting evidence under one event identity failing closed
without mutating the registry, malformed input failing closed, and the
no-caller-selected-write-target / no-network-dependency shape invariants.

## Follow-on: automatic, non-blocking materialization proof

Applying a canonicalized change still requires a human to run
`scripts/hunter_canonicalize_learning.py` locally and carry the result through
the normal commit/push/PR/review/merge path. That is not an implementation
gap being deferred -- it is this repository's own current, deliberate
authorization boundary. `grep -rn "contents: write" .github/workflows/`
finds exactly one match, `acquire-sky-supply-basis.yml`: an explicitly
labelled "TEMPORARY OPERATIONAL ESCAPE HATCH -- NOT PRODUCTION ARCHITECTURE",
manual-`workflow_dispatch`-only, scoped to committing one unrelated data file
(`data/data_ops.sqlite`) to one hardcoded milestone branch via a
`github-actions[bot]` identity that `docs/CODE_WRITE_POLICY.json`'s
`writer_identity_binding` does not bind -- not a reusable write pathway, and
not one this follow-on extends or relies on. No production, always-on
workflow in this repository holds `contents: write`, and
`docs/CODE_WRITE_POLICY.json`'s `connector_write_ingress` -- the only grant
capable of an automated non-clone write -- has no
`governance_maintenance_authorizations` entry for the `defect-registry`
scope that unblocks `docs/DEFECT_REGISTRY.json`. Granting that scope to an
Issue is a repository-owner decision (see the one existing entry's own
`authorized_by`/`authorization` fields); it is not something a contribution
can grant itself, per `connector_write_ingress.self_escalation_boundary`. A
proposal remaining "evidence... never canonical truth" until a human
integrates it is this design's own stated guarantee, not a limitation of
this follow-on.

What *is* automatable without any new authorization, and without a second
learning/write pathway, is proving -- continuously and automatically, on the
same trigger this workflow already has -- that the exact recovery command a
human will eventually run actually produces the result it claims to. The
"Prove canonicalization materialization is reproducible" step in
`hunter-knowledge-learning.yml` runs `hunter_canonicalize_learning.py
--dry-run` against the same `learning-observations.json` the job already
collects, on every `pull_request_review`/`pull_request_review_comment` event,
with `continue-on-error: true` so a failure here cannot skip the artifact
upload or affect any required check (this workflow already is not one).
`--dry-run` never varies with the observation content -- it is the one and
only invocation of that script in this file. This closes the one gap PR #528
identified without moving the write boundary: `materialize_learning_ledger`
now runs automatically on every relevant event instead of only inside unit
tests and a human's eventual local `--apply`, so a human applying the
candidate later is applying a path already exercised against live PR data,
not a cold, untested one.

Regression: `tests/test_hunter_knowledge_learning_workflow.py` proves the
bootstrap gate covers the new script, the workflow's only invocation of it
always carries `--dry-run`, a failure in that step cannot block the job, and
the artifact upload carries its output log.

## Follow-on: automatic apply via the existing clone-capable writer channel

The owner separately authorized designing the narrowest write ingress needed
for automated canonicalization -- explicitly *not* permission to write to
`main`, bypass review, bypass provenance, or self-merge.

**Governance check performed first, per that authorization's own
instruction not to self-escalate:** the only automated-write mechanism this
repository defines, `connector_write_ingress`, cannot serve this need at
all, with or without a new Issue authorization. `docs/CODE_WRITE_POLICY.json`
requires each write to carry a receipt minted per-request (`python
scripts/hunter_connector_write_ingress.py --request write-request.json
--emit-receipt ...`) by "the owner's connected ChatGPT assistant" -- a live,
per-occasion, interactively-authenticated session, per
`docs/CONNECTOR_WRITE_INGRESS.md`. It has no mechanism for a headless,
scheduled/event-triggered job with no session behind it to invoke it. So an
Issue authorizing the `defect-registry` `governance-maintenance` scope would
not, by itself, make unattended automatic writes possible through that
channel.

The channel that *does* fit, requiring **no new repository authorization at
all**, is the other one this repository already defines and already
uses for every human contribution: `local_git_push` -- unrestricted by path,
gated only on `.githooks/pre-push` and a verified commit signature from an
address in `ingress_provenance.authorized_signers` (`claude` or `fafa33`).
This PR's own commits already went through exactly that channel. A scheduled
Claude Code Remote Routine that spawns a fresh session, which commits under
the same bound `Claude <noreply@anthropic.com>` identity and pushes through
the same `.githooks/pre-push` boundary, is not a new grant -- it is the
existing grant, invoked on a schedule instead of by a person typing a
command. Human merge approval, `Hunter Governance Review`,
`Hunter Merge Readiness`, and every other required check are unaffected and
still apply to the resulting PR like any other.

### The Routine was attempted, tested by firing it, and found blocked by a platform limitation -- not a Hunter governance one

A Claude Code Remote Routine (`Hunter Defect-Registry Canonicalization`,
hourly, `create_new_session_on_fire: true`) was created to run the same
procedure described above (list open PRs, build observations without ever
inventing classification, run the unmodified
`scripts/hunter_canonicalize_learning.py` per PR, then create-or-update one
Draft PR on a fixed branch if anything changed). Rather than trust that
design and report it as done, it was fired manually
(`fire_trigger`) and the resulting session was inspected directly.

**Result: it did nothing.** No `canonicalization/defect-registry-auto`
branch and no new PR exist on `fafa33/Project-Hunter` after the firing. The
fired session's own tool list contained neither `add_repo`/any other
`mcp__Claude_Code_Remote__*` tool nor any `mcp__github__*` tool -- only a
minimal baseline (`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Agent`,
`WebFetch`, `WebSearch`, `Artifact`, a few more). It could not attach the
repository and could not read or write anything on GitHub, so -- per its own
explicit instruction not to retry in a loop or improvise a different write
path -- it ended cleanly having done nothing. That is the correct, safe
outcome of a session missing the access it needs; it is not evidence the
procedure itself is unsound.

Per `read_documentation(topic="github.access")` and
`read_documentation(topic="connectors.add")`: **"a session's repositories
are chosen when it starts"** and **"connectors are read when a session
starts"** -- both are fixed at session-creation time, normally by a person
picking them in the claude.ai UI. `create_trigger`'s own result confirmed
this from the other side: *"this trigger stores no MCP connectors... this
call had none to pass through (no session context, or no passable connector
grants)."* A person is never present when an hourly cron fires, and this
session had no repository/connector grant of its own that `create_trigger`
could hand to a brand-new session. This is a Claude Code Remote platform
constraint on how fresh-session Routines are provisioned -- unrelated to
`docs/CODE_WRITE_POLICY.json`, `connector_write_ingress`, or any Hunter
governance document, all of which remain exactly as described above and
were never touched.

**The Routine was disabled** (`enabled: false`) immediately after this was
confirmed, rather than left firing hourly while doing nothing -- silently
inert is worse than visibly off, since an operator checking on it later
should see it needs attention rather than assume it is working.

**What this means for automatic apply today:** it is not solved by this PR.
The two things that *are* solved and merged are (a) the CLI itself
(`scripts/hunter_canonicalize_learning.py`, unchanged, fully tested, the
supported fallback/recovery path run by hand) and (b) the automatic,
continuous, non-blocking dry-run proof that CLI's materialization logic
actually works, wired into existing CI
(see "Follow-on: automatic, non-blocking materialization proof" above).

### The persistent-session alternative was also tried, and also found blocked

Owner authorization separately requested a minimal, non-destructive proof
of one specific alternative before any further implementation: whether a
*persistent* session (not fresh-per-fire) could retain repository and
GitHub access across a later Routine wake, avoiding the fresh-session
limitation above.

A dedicated persistent session was created (`create_session`, not
`create_trigger`) with a strictly read-only task: attach
`fafa33/project-hunter` via `add_repo`, then confirm via its own
`mcp__github__` tools that it could list open PRs and read `main`'s current
HEAD SHA. No branch, commit, or PR was created or attempted.

Two independent, decisive results, both against this same session, before
any Routine was ever created for it (so step 2 of that plan -- binding a
trigger to it -- was correctly never reached):

1. **Its tool list was identical to the fresh Routine-fired session's** --
   `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Agent`, `WebFetch`,
   `WebSearch`, `Artifact`, a few more -- with neither `add_repo`/any
   `mcp__Claude_Code_Remote__*` tool nor any `mcp__github__*` tool present.
   `create_session` provisions the same restricted baseline as
   `create_new_session_on_fire`; the richer toolset this conversation itself
   has is tied to how *this* session was started (through the interactive
   product surface with sources/connectors already selected), not something
   either programmatic session-creation path grants.
2. Attempting the repository-attach step itself surfaced a platform safety
   gate: the session's status became `need_input` /
   `"prompt injection detected; awaiting user confirmation"`, requiring a
   human to confirm in the claude.ai UI that they actually asked for the
   clone. This is an additional, independent blocker on top of (1): even if
   the tool were present, an unattended Routine wake has no person available
   to answer that confirmation, so it could never pass this gate either.

Per the instruction not to invent a workaround: no attempt was made to
route around either finding (no message sent asking the blocked session's
safety confirmation to be waved through, no alternate credential, no new
secret). The session was left as-is, still visible to the repository owner
if they want to inspect or clear it themselves.

**Conclusion: neither of this platform's two session-creation paths
(fresh-per-fire or persistent-via-`create_session`) currently supports an
unattended-canonicalization Routine.** Unblocking this needs a
platform-level capability outside what these tools expose today -- most
plausibly, a session created through the interactive UI (where a person
selects the repository and any needed access at creation time) bound to a
Routine via `persistent_session_id`, which this conversation cannot
provision for itself. Nothing here touched `docs/CODE_WRITE_POLICY.json`,
added a secret, or created any new write authority, per that same
instruction.

### Requirement-by-requirement -- honest status

| Requirement | Status |
|---|---|
| Automatic, non-blocking computation/proof that materialization works | **Done and merged** -- the CI dry-run step above, already exercised on every relevant PR event |
| Automatic create-or-update of a canonicalization PR with no person present | **Not achieved** -- blocked by the platform limitation just described; the Routine exists but is disabled |
| Local/Mac CLI remains a fully supported recovery/fallback path | **Done, unchanged** -- `scripts/hunter_canonicalize_learning.py`, run by hand, is currently the *only* way a canonicalized change actually reaches a PR |
| No repository-write authority broadened; no new CODE_WRITE_POLICY grant; never writes `main`; never self-merges | **True of everything actually implemented** -- nothing in this PR touches `docs/CODE_WRITE_POLICY.json`, and the disabled Routine's design (had it worked) also never would have |
| Idempotent / duplicate executions converge | **True of what is implemented** -- proven by `tests/test_canonicalize_learning_cli.py::test_sequential_apply_across_two_prs_accumulates_without_cross_pr_duplication` for the CLI composition the (disabled) Routine's design depended on |
| No new AI/provider dependency | **True** |
| DPM/SPM authority unchanged | **True** -- `EngineeringContextAuthority` still only ever reads `docs/DEFECT_REGISTRY.json` as merged on `main`; nothing unmerged is ever authoritative |

Regression: `tests/test_canonicalize_learning_cli.py::test_sequential_apply_across_two_prs_accumulates_without_cross_pr_duplication`
proves the one new, testable property this attempt depended on --
sequential apply of the existing, unmodified CLI across two independent PRs
accumulates correctly and stays idempotent. That property remains true and
useful for whoever picks this up (by hand, or from a properly-provisioned
persistent session) even though the fully-automatic Routine itself is
currently disabled.
