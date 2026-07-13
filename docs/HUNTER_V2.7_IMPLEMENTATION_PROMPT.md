# Project Hunter v2.7.0 — Global Discovery and Candidate Registry

```text
Silent. Final only.

Project Hunter

Current baseline:

- Release: v2.6.0
- Commit: 7e627b0fdb0c018a6ee6a53976da62133cb34965
- Tests: 451 passing
- Git status: clean against origin/main except preexisting untracked Hunter-App/
- Canonical production scoring boundary:
  EvidenceBackedProjectExecutor
- Current production universe:
  static 50-project configuration
- Architecture review completed
- Estimated reusable code:
  78%

=========================================================
ARCHITECTURAL DECISION
=========================================================

Hunter is a market discovery engine before it is a project analysis engine.

Hunter must continuously inspect the entire cryptocurrency market, identify assets that deserve further investigation, validate their identity and evidence, prioritize them, and only then submit qualified candidates to deep analysis.

Hunter is not limited to unknown assets.

It must identify any asset—known or unknown—whose remaining investment upside may be materially greater than its current market valuation implies.

Hunter must become useful long before it becomes complete.

A release is successful only if it meaningfully improves real investment decisions compared with the previous release.

=========================================================
RELEASE
=========================================================

Implement:

v2.7.0 — Global Discovery and Candidate Registry

This is a production implementation release.

Do not produce another architecture review.

Do not implement Intrinsic Value estimation yet.

Do not add another deep intelligence engine.

Do not redesign Hunter from scratch.

Preserve all production behavior unless explicitly extended below.

=========================================================
PRIMARY OBJECTIVE
=========================================================

Transform Hunter’s market entry point from:

static configured project universe

into:

dynamic market-wide discovery
→ canonical candidate registry
→ lightweight screening
→ prioritized candidate queue
→ existing evidence-backed analysis

The existing 50-project universe must remain supported as a seeded compatibility segment, but it must no longer define the complete investable universe.

=========================================================
NON-NEGOTIABLE PRESERVATION
=========================================================

Do not replace or weaken:

- EvidenceBackedProjectExecutor
- deterministic execution identity
- evidence-first validation
- explainability and evidence tracing
- historical replay cutoff discipline
- immutable record design
- idempotent persistence
- current investment committee outputs
- acquisition normalization contracts
- automation job/run lifecycle
- existing tests as characterization coverage

Existing production Market Validation behavior must continue to pass unchanged.

=========================================================
1. DISCOVERY DOMAIN
=========================================================

Create a dedicated production discovery domain under an appropriate package such as:

src/hunter/discovery/

Use existing repository conventions where technically sound.

Define typed immutable models for at least:

- DiscoverySource
- SourceAssetRecord
- CandidateIdentity
- CandidateAlias
- CandidateContract
- CandidateRepository
- CandidateCategory
- CandidateEvidenceReference
- CandidateRegistryEntry
- CandidateLifecycleState
- CandidateScreeningResult
- CandidatePriority
- CandidateQueueEntry
- DiscoveryRun
- DiscoveryCheckpoint
- DiscoveryConflict
- DiscoveryCoverageReport

All time-sensitive records must include explicit observation timestamps.

All derived conclusions must preserve source and evidence provenance.

=========================================================
2. CANDIDATE LIFECYCLE
=========================================================

Implement explicit lifecycle states:

- discovered
- identified
- evidence_pending
- screenable
- analyzable
- ranked
- deep_research
- rejected
- archived

State transitions must be deterministic, validated and persisted.

Invalid transitions must fail explicitly.

Each transition must record:

- previous state
- new state
- timestamp
- reason
- supporting evidence
- responsible discovery run

=========================================================
3. DYNAMIC CANDIDATE REGISTRY
=========================================================

Implement a durable CandidateRegistry.

The registry must support:

- canonical candidate id
- source-specific ids
- symbols
- names
- aliases
- previous names
- chains
- contract addresses
- categories and sectors
- official websites
- official repositories
- explorer references
- discovery timestamps
- most recent observation timestamp
- lifecycle state
- validation state
- source confidence
- evidence references
- migration history
- delisting or archival status

The registry must merge records from multiple sources into one canonical candidate when evidence supports the merge.

It must not merge candidates based only on ticker symbol.

Handle explicitly:

- ticker collisions
- duplicate listings
- wrapped assets
- bridged representations
- token migrations
- contract migrations
- renamed projects
- chain-specific deployments
- protocol versus token identity
- native network asset versus wrapped token
- forked or impersonating assets

Ambiguous identities must remain unresolved rather than being guessed.

=========================================================
4. DISCOVERY ADAPTER CONTRACT
=========================================================

Implement an independent adapter contract.

One source = one adapter.

Adapters must be independently:

- configured
- enabled
- disabled
- health-checked
- rate-limited
- checkpointed
- retried
- observed
- tested

Future adapters must not require architectural redesign.

The adapter contract must normalize source output into SourceAssetRecord objects.

Do not let source-specific payloads leak into the registry or downstream engines.

=========================================================
5. FIRST PRODUCTION ADAPTERS
=========================================================

Use existing public acquisition capabilities and add the smallest reliable adapter set that produces immediate market-wide value.

Implement production adapters for public sources that are actually reachable in the current environment.

Prioritize:

1. CoinGecko market asset discovery
2. DefiLlama protocol discovery
3. GeckoTerminal or DexScreener decentralized-market discovery
4. GitHub enrichment only when official repository identity can be verified

Do not claim operational support for any adapter that cannot be live-validated.

If a provider requires an unavailable paid credential, implement the typed integration boundary only when necessary, mark it unavailable explicitly, and do not count it as live coverage.

No fabricated records.

No bundled static market dumps presented as live discovery.

=========================================================
6. SEEDED COMPATIBILITY IMPORT
=========================================================

Import the existing 50 configured projects into CandidateRegistry as seeded candidates.

Preserve their current identifiers and production behavior.

Record their origin as a configured compatibility seed.

Do not treat seeded candidates as newly discovered market evidence.

The existing configuration must remain usable during migration.

=========================================================
7. DETERMINISTIC IDENTITY RESOLUTION
=========================================================

Implement a deterministic identity-resolution service.

Resolution should use evidence such as:

- verified contract addresses
- chain identifiers
- official project domains
- official repositories
- provider ids
- protocol references
- migration records

Assign resolution outcomes such as:

- exact
- probable
- ambiguous
- conflict
- rejected

Persist resolution evidence and confidence.

Never silently merge ambiguous candidates.

=========================================================
8. LIGHTWEIGHT MARKET-WIDE SCREENING
=========================================================

Implement a lightweight screening stage before deep Market Validation.

This stage must be inexpensive enough to run across thousands of candidates.

Use only evidence currently available and defensible.

Possible screening dimensions include:

- market data availability
- liquidity availability
- market-cap or FDV availability
- verified contract or native network identity
- active official web presence
- repository availability
- protocol data availability
- listing breadth
- source agreement
- evidence freshness
- obvious scam or impersonation conflicts
- minimum analyzability threshold

Do not pretend this is intrinsic valuation.

Do not issue unsupported 10x, 100x or price forecasts.

The screening result must explain:

- why the candidate advanced
- why it was deferred
- why it was rejected
- which evidence is missing
- confidence and coverage

=========================================================
9. CANDIDATE PRIORITY QUEUE
=========================================================

Implement a persistent Candidate Queue.

The queue must prioritize what Hunter should analyze next.

Priority must be deterministic and evidence-backed.

It may consider:

- source confidence
- analyzability
- evidence coverage
- freshness
- market relevance
- unusual cross-source activity
- market-cap tier
- sector representation
- novelty
- missing high-value evidence
- prior screening score

The queue must not equate popularity with investment quality.

It must avoid automatically excluding known projects.

It must allow both:

- widely known assets with significant remaining potential
- newly discovered assets with limited market recognition

Queue entries must record:

- candidate id
- priority score
- priority reasons
- missing evidence
- lifecycle state
- created time
- updated time
- source run
- eligibility for deep analysis

=========================================================
10. EXISTING ANALYSIS INTEGRATION
=========================================================

Keep EvidenceBackedProjectExecutor unchanged as the canonical production scoring boundary.

Only candidates reaching the analyzable state may be submitted to the current deep analysis path.

Add a typed service that converts a qualified CandidateRegistryEntry into the existing canonical project identity expected by Market Validation.

Do not create a second competing scoring runtime.

Do not make PipelineOrchestrator canonical unless required and fully justified by existing architecture.

=========================================================
11. PERSISTENCE
=========================================================

Use the existing SQL repository architecture as the default durable store for:

- candidates
- aliases
- identifiers
- contracts
- lifecycle history
- discovery runs
- checkpoints
- conflicts
- screening results
- candidate queue

Do not use large unindexed JSONL files as the authoritative market-wide registry.

JSONL may remain for raw immutable acquisition evidence where consistent with existing conventions.

Add database migrations where required.

All writes must be idempotent.

Re-running the same discovery payload must not duplicate candidates, aliases, transitions, screening records or queue entries.

=========================================================
12. AUTOMATION
=========================================================

Integrate discovery with the existing Automation Layer.

Install idempotent jobs for at least:

- provider health
- incremental market discovery
- candidate registry reconciliation
- lightweight candidate screening
- candidate queue refresh
- periodic candidate archival or delisting review

Avoid shelling through CLI when a typed internal service call is available.

Jobs must support:

- restart recovery
- checkpoints
- bounded retries
- cooldowns
- explicit unavailable states
- run status persistence
- idempotent installation

=========================================================
13. CLI MODULARIZATION
=========================================================

Do not continue expanding the existing large CLI module without structure.

Create modular discovery command registration consistent with current Typer architecture.

Implement commands equivalent to:

hunter discovery run
hunter discovery sync
hunter discovery stats
hunter discovery registry
hunter discovery candidates
hunter discovery queue
hunter discovery conflicts
hunter discovery validate
hunter discovery coverage

Commands must return real persisted results.

Remove no existing production command.

Do not leave readiness-only placeholder commands for newly implemented discovery behavior.

=========================================================
14. PRACTICAL INVESTMENT OUTPUT
=========================================================

This release must provide immediate practical value.

After a live discovery run, Hunter must produce a report containing at least:

- total source records observed
- unique canonical candidates
- newly discovered candidates
- updated candidates
- ambiguous identities
- conflicts
- candidates by lifecycle state
- candidates by market-cap tier when available
- candidates by sector/category
- source coverage
- evidence coverage
- top prioritized candidates for further analysis
- reason each top candidate was prioritized
- missing evidence for each candidate
- whether each candidate is ready for deep analysis

This is not an investment recommendation report.

It is a decision-useful market triage report answering:

“What should Hunter investigate next, and why?”

The report must include both known and lesser-known candidates when supported by evidence.

=========================================================
15. POINT-IN-TIME DISCOVERY
=========================================================

Persist candidate existence and source observation timestamps.

Historical validation must eventually be able to determine:

- when Hunter first knew the candidate existed
- what evidence was available at that time
- when its identity became validated
- when it entered each lifecycle state
- when it became analyzable

Do not backfill historical knowledge as though Hunter possessed it earlier.

Prevent lookahead and survivorship bias in discovery history.

=========================================================
16. DATA QUALITY GOVERNANCE
=========================================================

Add explicit policies for:

- source reliability
- conflicting source values
- stale source records
- identifier collisions
- unverifiable official links
- abandoned projects
- delisted assets
- migrated assets
- spam and impersonation candidates
- evidence expiry

Every rejected or unresolved candidate must have a machine-readable reason.

No silent drops.

=========================================================
17. COVERAGE SEMANTICS
=========================================================

Report separate coverage dimensions:

- source discovery coverage
- canonical identity coverage
- contract identity coverage
- official-link verification coverage
- screening coverage
- analyzable coverage
- deep-analysis coverage
- historical point-in-time coverage

Do not combine these into a misleading single completeness percentage.

=========================================================
18. DOCUMENTATION
=========================================================

Create or update production documentation covering:

- discovery-first architecture
- CandidateRegistry
- lifecycle states
- adapter contract
- identity resolution
- screening
- Candidate Queue
- persistence
- automation
- CLI
- coverage semantics
- data-quality policy
- migration from static project universe
- relationship with Market Validation
- known limitations
- next architectural steps

Also correct stale version and roadmap claims where they conflict with v2.7.0.

Do not rewrite unrelated documentation unnecessarily.

=========================================================
19. TESTING
=========================================================

Add deterministic production tests covering at least:

- source adapter normalization
- seeded 50-project import
- exact identity resolution
- ticker collision handling
- ambiguous identity handling
- wrapped and bridged assets
- contract migration
- renamed project reconciliation
- idempotent discovery
- idempotent registry writes
- lifecycle transition validation
- conflict persistence
- screening decisions
- queue priority determinism
- queue refresh idempotency
- checkpoint recovery
- interrupted-run resume
- adapter isolation
- unavailable provider handling
- automation installation idempotency
- point-in-time candidate existence
- conversion of analyzable candidates to current Market Validation identity
- unchanged existing production Market Validation behavior

Run the complete existing validation suite.

=========================================================
20. LIVE VALIDATION
=========================================================

Perform live validation against every enabled public discovery adapter.

Record:

- reachable sources
- unavailable sources
- rate limits
- source record counts
- canonical candidate counts
- duplicate/merge counts
- conflict counts
- screening counts
- queue counts
- analyzable counts
- live failures
- actual coverage

Do not claim support based only on unit tests.

=========================================================
21. PERFORMANCE AND SCALE
=========================================================

The design must support thousands of candidates without running deep analysis on all of them.

Use tiered processing:

market-wide discovery
→ identity resolution
→ lightweight screening
→ candidate queue
→ evidence acquisition
→ deep Market Validation

Avoid full-table scans where indexed repository queries are appropriate.

Add indexes for key candidate and discovery fields.

Do not introduce distributed workers in this release unless strictly required.

=========================================================
22. OUT OF SCOPE
=========================================================

Do not implement:

- Intrinsic Value Engine
- 10x/100x forecasts
- portfolio construction
- trading execution
- generic REST API
- Dashboard Phase 2
- distributed job infrastructure
- another deep intelligence engine
- major report-engine redesign
- speculative machine-learning models without validated training data

=========================================================
23. RELEASE AND REPOSITORY HYGIENE
=========================================================

Preserve ignored runtime data boundaries.

Do not commit secrets, provider credentials, large runtime datasets or generated caches.

Do not modify or commit the preexisting untracked Hunter-App/ directory unless explicitly necessary and justified.

Address the oversized tracked data file only if required for this release and safe to migrate without losing production evidence; otherwise report it as a remaining blocker.

Update version references consistently to v2.7.0.

Commit all intended tracked changes.

Push to origin/main.

Create and push release tag:

v2.7.0

Create the GitHub release if repository tooling and authentication allow it.

=========================================================
FINAL ACCEPTANCE CRITERIA
=========================================================

The release is complete only if:

1. Hunter no longer treats the static 50-project universe as the complete market.
2. The existing 50 projects are preserved as seeded candidates.
3. Live public adapters discover market-wide candidate records.
4. Candidate records are normalized into a durable dynamic registry.
5. Ambiguous identities are preserved as ambiguous rather than incorrectly merged.
6. Candidate lifecycle states are persisted and enforced.
7. Lightweight screening operates across discovered candidates.
8. A deterministic persistent Candidate Queue identifies what Hunter should analyze next.
9. Qualified candidates can enter the unchanged EvidenceBackedProjectExecutor path.
10. Discovery automation installs and runs idempotently.
11. Point-in-time candidate existence is preserved.
12. A practical market-triage report is generated from live discovery data.
13. Existing production behavior remains stable.
14. Ruff passes.
15. Black check passes.
16. Mypy passes.
17. Pytest passes.
18. Live discovery validation succeeds for every source claimed as operational.
19. Git status is clean except the preexisting untracked Hunter-App/ directory.
20. Release v2.7.0 is committed, pushed and tagged.

=========================================================
ARCHITECTURAL MINDSET
=========================================================

Think like the Chief Architect of the world’s most advanced crypto investment intelligence platform, not like a feature implementer.

When multiple implementation choices exist, choose the one that:

- best supports Hunter’s discovery-first mission
- produces the greatest practical investment value in the shortest safe time
- preserves existing production stability
- maximizes technically sound code reuse
- remains modular and independently extensible
- improves evidence quality and decision usefulness
- avoids unnecessary architectural perfectionism
- enables future intrinsic-value estimation without implementing it prematurely

Hunter must become useful long before it becomes complete.

Every line of code in this release must move Hunter closer to answering:

“Across the entire crypto market, what deserves deeper investment analysis next, and why?”

=========================================================
FINAL RESPONSE FORMAT
=========================================================

Return only a concise implementation report containing:

- architecture summary
- practical investment-value improvement
- files added
- files modified
- database migrations
- adapters implemented
- live adapter results
- candidate records observed
- canonical candidates created
- seeded candidates imported
- identity conflicts
- lifecycle counts
- screening counts
- candidate queue counts
- top candidate triage output
- automation jobs installed
- point-in-time coverage
- remaining blockers
- tests passed
- commit hash
- push status
- release tag
- release URL
- final git status
```
