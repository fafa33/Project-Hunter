# Competitive and Peer Intelligence

Competitive Intelligence is an additive Hunter layer that compares trusted candidates without changing scoring, ranking, valuation, committee logic, Opportunity Timing, Market Validation, or the canonical runtime.

Phase 1 establishes only the domain models, deterministic identifiers, versioned algorithmic peer policy, and Predicate Registry extensions required by `docs/SPRINTS/v3.1.0.md`.

Phase 2 adds SQL-authoritative storage for competitive relationships, algorithmic peer relationships, peer sets, comparison dimensions, assessments, normalized evidence/span/conflict lineage, processing runs, and checkpoints.

Phase 3 adds input selection only. `CompetitiveInputSelector` reads trusted candidates from Candidate Registry and selects v3.0.0 Evidence Intelligence claims and relationship projections with replay-safe lifecycle, document, and authority checks.

Phase 4 adds `CompetitiveRelationshipBuilder`, which converts only available canonical Evidence Intelligence claims and relationship projections into persisted evidence-backed competitive relationships with normalized evidence and span lineage.

Phase 5 operationalizes deterministic algorithmic peer policy. `AlgorithmicPeerBuilder` evaluates only approved comparison dimensions, persists accepted algorithmic peer relationships separately from evidence-backed competition, and stores per-dimension comparison rows for explainability.

Phase 6 adds `PeerSetBuilder`, which constructs peer sets from persisted evidence-backed competitive relationships and persisted algorithmic peer relationships while preserving their distinct member roles and relationship kinds.

Phase 7 adds competitive conflict detection and replay-safe point-in-time queries. `CompetitiveConflictDetector` evaluates only evidence-backed competitive relationships, while `CompetitiveReplayQuery` exposes current, strict known-by-Hunter, and reconstructed-after-cutoff views.

Phase 8 adds the `hunter competitive` CLI namespace, `CompetitiveReporter`, and `CompetitiveAutomationManager`. The CLI exposes coverage, peer, competitor, explain, conflict, report, and automation commands without changing analytical runtime behavior.

## Semantics

- Evidence-backed competitive relationships are represented separately from algorithmic peer relationships.
- `CompetitiveRelationship` requires an evidence-backed claim id and predicate context.
- `AlgorithmicPeerRelationship` is deterministic similarity output and must never be reported as a competitor claim.
- `AlgorithmicPeerPolicy` can compare only approved dimensions such as category, chain, use case, protocol type, and market segment.
- Co-mention, ticker similarity, popularity, provider rank, market-cap proximity, and price movement are not valid peer-policy dimensions.
- Phase 3 does not infer competition or peers. It only marks canonical inputs available or unavailable for later phases.
- Candidate inputs are available only when Identity/Trust resolves them as `exact` or `probable`; unresolved, ambiguous, conflicted, rejected, or missing identity states remain unavailable.
- Claim inputs are available only when claim lifecycle, document lifecycle, and source authority are usable at the requested cutoff.
- Relationship projection inputs inherit availability from their canonical claim and never become independent truth.
- Evidence-backed relationship building supports explicit competitive predicates only: `competes_with`, `substitutes_for`, `centralized_incumbent_of`, `same_category_as`, and `same_use_case_as`.
- Unsupported predicates, co-mention predicates, negative-polarity claims, missing peer candidate identity, and unavailable lifecycle states do not create relationships.
- Built relationships remain distinct from algorithmic peers and retain claim id, projection id, source evidence links, span links, scope, modality, polarity, confidence, freshness, and replay mode metadata.
- Algorithmic peer relationships are always stored in `algorithmic_peer_relationships` with `relationship_kind=algorithmic_similarity` metadata and never in `competitive_relationships`.
- Algorithmic policy uses only approved deterministic dimensions. Missing dimensions are recorded as `missing`; co-mention, ticker, symbol, popularity, provider rank, market-cap proximity, and price movement remain forbidden.
- Policy id, policy version, compared dimensions, match status, similarity, confidence, freshness, effective time, and recorded time are persisted for repeatable explainability and replay.
- Peer sets keep evidence-backed competitors and algorithmic peers separate through `PeerSetMember.member_role` and `relationship_kind`.
- Peer-set confidence, coverage, freshness, and completeness are projected from persisted relationship rows, evidence/span lineage, and comparison dimension rows.
- Conflict-aware peer-set status is a reportable state only; it does not become an investment-quality conclusion or alter scoring.
- Competitive conflict detection is predicate-, scope-, qualifier-, modality-, lifecycle-, and time-aware. Different peers, chains, scopes, products, or non-overlapping validity periods do not create false conflicts.
- Conflict links are persisted without changing algorithmic peer rows, scores, rankings, Opportunity Timing, or Evidence Intelligence truth.
- Strict replay filters competitive relationships, peer sets, and conflict links by the cutoff state known to Hunter. Reconstruction mode is explicitly labeled and does not claim knowledge at the cutoff.

## Input Selection

`CompetitiveInputSelector` supports current mode, historical strict known-by-Hunter mode, and reconstructed-after-cutoff mode.

Strict known-by-Hunter selection requires claim lifecycle events, document lifecycle events, source authority verification events, and relationship projection creation time to be known by Hunter at the cutoff. Reconstruction mode may use later-recorded lifecycle or authority evidence whose effective time is within the cutoff, but the resulting inputs are labeled `reconstructed_after_cutoff` and must not be reported as knowledge known at the cutoff.

Indexed candidate selection uses `CandidateRegistryRepository.get(candidate_id)` when candidate ids are supplied. Broad selection remains bounded by an explicit limit. Evidence claim and projection selectors preserve missing and unavailable states instead of forcing availability.

## Evidence-Backed Relationship Building

`CompetitiveRelationshipBuilder` reads Phase 3 inputs and v3.0.0 Evidence Intelligence repository state. It requires both the claim input and relationship projection input to be available, confirms the current canonical claim/projection remains active or historical-only, resolves the peer candidate through the projected object entity, and persists the resulting `CompetitiveRelationship` through `CompetitiveRepository.save_relationship_with_lineage`.

The builder does not perform similarity matching, peer-set construction, conflict detection, reporting, automation, scoring, or valuation. It never treats category labels alone, co-mentions, ticker similarity, market proximity, or provider popularity as competition.

## Algorithmic Peer Policy

`AlgorithmicPeerPolicy` provides deterministic dimension comparison and returns an explainable decision with matched, different, and missing dimensions. `AlgorithmicPeerBuilder` converts accepted decisions into `AlgorithmicPeerRelationship` plus `ComparisonDimension` rows. Re-running the same inputs yields the same ids, similarity, decision, and persisted rows.

Algorithmic output is not evidence-backed truth. It does not create `KnowledgeClaim`, `KnowledgeRelationship`, `CompetitiveRelationship`, score changes, rankings, or Opportunity Timing eligibility.

## Peer-Set Construction

`PeerSetBuilder` reads persisted `competitive_relationships`, `algorithmic_peer_relationships`, normalized relationship evidence/span links, and algorithmic comparison dimensions. It creates `PeerSet`, `PeerSetMember`, `PeerSetEvidenceLink`, and `PeerSetSpanLink` records idempotently through `CompetitiveRepository`.

Peer-set confidence components are stored in metadata for explainability after indexed authoritative columns exist. Evidence-backed lineage coverage requires both supporting evidence and spans. Algorithmic dimension coverage is computed from persisted comparison dimensions and preserves missing dimensions. A disputed relationship or non-empty relationship conflict status marks the peer set disputed without changing relationship truth, scores, rankings, or downstream runtime behavior.

## Conflict Detection and Replay

`CompetitiveConflictDetector` compares evidence-backed competitive relationships only. It requires the same subject candidate, peer candidate, predicate, scope, qualifier, modality, and overlapping validity period before considering a contradiction. Opposite polarity under those compatible conditions creates a detected conflict. Superseded, retracted, or source-removed relationships may be linked as resolved historical conflicts without destructive overwrite.

`CompetitiveReplayQuery` provides current, `historical_strict_known_by_hunter`, and `reconstructed_after_cutoff` views over persisted competitive relationships, peer sets, and conflict links. Strict mode requires both `effective_at <= cutoff` and `recorded_at <= cutoff`; reconstruction mode requires effective time only and is labeled as not known at cutoff.

## CLI, Reporting, and Automation

`CompetitiveReporter` reads only `CompetitiveRepository` state through the requested replay context. Strict reports use only peer sets known at the cutoff, reconstructed reports are explicitly labeled, and current peer sets are never reused under a historical label. Reports expose relationship kind, confidence, coverage, freshness, lineage references, missing evidence, conflict status, and replay mode. Evidence-backed competitors and algorithmic peers remain labeled separately in peer, competitor, explain, and conflict outputs.

Relationship building consumes the replay-safe claim and projection state selected upstream. It does not re-read current claim lifecycle, document authority, or relationship projection state after input selection. Explicit candidate selection remains gated by Identity/Trust availability, so unresolved or conflicted candidates cannot force downstream evidence selection.

Competitive relationship, peer-set, algorithmic peer, and comparison-dimension rows are versioned by identity plus effective and recorded time. Repeated writes of the same version remain idempotent, while later versions preserve prior historical state for strict replay. Historical explain output reads comparison dimensions through the same cutoff context as report output, so missing-evidence state cannot leak from current rows into strict or reconstructed reports.

The CLI commands are:

- `hunter competitive coverage`
- `hunter competitive report`
- `hunter competitive peers <candidate>`
- `hunter competitive competitors <candidate>`
- `hunter competitive explain <candidate>`
- `hunter competitive conflicts`
- `hunter competitive automation install`
- `hunter competitive automation status`

`CompetitiveAutomationManager` installs idempotent jobs into the existing `configs/automation.yaml` Scheduler format for input refresh, evidence-backed relationship build, algorithmic peer-set refresh, conflict detection, and reporting. The installed jobs use `run_type=competitive_intelligence_pipeline` and `scheduler_role=operational_only`; no competitive analytical logic is placed in the Scheduler.

## Predicate Extensions

The competitive Predicate Registry extension is versioned as `competitive-predicate-v1` and includes predicates such as `competes_with`, `substitutes_for`, `same_category_as`, `same_use_case_as`, `targets_market_segment`, `uses_technology`, and `centralized_incumbent_of`.

Graph-ready predicates remain projections of v3.0.0 Evidence Intelligence claims. Literal predicates, such as `targets_market_segment`, are not graph-projected.

## Phase Boundary

The Phase 8 CLI, reporting, and automation surfaces are additive and separate from Evidence Intelligence persistence. They do not change Candidate Registry identity semantics, Evidence Intelligence truth semantics, scoring, ranking, valuation, committee logic, Opportunity Timing, Market Validation, or canonical runtime behavior.

Phase 8 completes v3.1.0. It does not add claim ingestion, scoring integration, Opportunity Timing integration, valuation, portfolio behavior, or dashboard redesign.
