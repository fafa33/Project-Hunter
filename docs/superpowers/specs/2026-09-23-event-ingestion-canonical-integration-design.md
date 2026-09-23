# Event Ingestion and Controlled DPM Canonical Integration

## Status
Issue #490 design authority. Implementation must conform to this document.

## Objective
Close the deterministic path from validated engineering events to durable prevention knowledge without granting any external event, reviewer, provider, CI job, or model canonical-write authority.

## Pipeline
source event -> source adapter -> CommonFindingEnvelope -> KnowledgeFinding (hunter-knowledge-extraction-v1) -> KnowledgeExtractionAuthority -> KnowledgeExtractionProposal -> CanonicalIntegrationAuthority -> updated registry candidate -> existing DPM/SPM prevention path.

## Authority boundaries
1. Adapters normalize syntax only; they cannot decide family equivalence, merge authority, or canonical truth.
2. KnowledgeExtractionAuthority remains the only existing-family matcher.
3. CanonicalIntegrationAuthority accepts only existing-family proposals.
4. candidate-new-family, ambiguous, and excluded are never auto-integrated.
5. Registry mutation is optimistic-locked to proposal.registry_digest. Stale proposals fail closed.
6. Integration may strengthen an existing family only by adding provenance source and regression evidence already carried by the immutable proposal finding. It may not change invariant, lifecycle, applicability, prevention, enforcement, or family identity.
7. Integration is content-idempotent. Redelivery cannot create duplicate registry evidence.
8. Integration returns bytes/data; it has no repository, Git, network, reviewer, merge, or deployment authority. Persistence remains outside this authority.
9. No provider-specific field is trusted as canonical without exact adapter validation.
10. Historical backfill must enter through this same contract.

## Common event envelope
Version: hunter-finding-event-v1.
Required evidence: provider, stable event_id, source_pr, exact reviewed head/base SHAs, reviewer, canonical classification, invariant, affected paths, immutable fix reference, regression evidence, and optional claimed family. The envelope cannot carry write_authorized, merge_authorized, canonical_family_id, or registry mutation instructions.

## Source adapters
Explicit bounded adapters: sonar -> deterministic-gate; github-review -> independent-review; hunter-ci -> ci; hunter-governance -> governance. Provider prose is evidence, never family authority. Unknown fields fail closed.

## Canonical integration
Input: immutable proposal plus current registry bytes. Require existing-family outcome, matching family claim, exact current registry digest, and exact replay of the proposal against that registry. Append only deterministic provenance and regression evidence if absent. Preserve all other family fields. Never auto-create DFFs.

## Failure modes
Duplicate delivery -> idempotent. Payload drift -> reject. Provider outage/quota -> no event and never a merge blocker. Stale registry -> re-extract. Malicious family claim -> ambiguous. Traversal/malformed types -> reject. False positive -> excluded. New root cause -> separately governed candidate-new-family. Persistence interruption -> pure operation can be retried.

## Prevention semantics
Confirmed learning requires regression evidence. Where locally enforceable, that evidence is a deterministic test/guard. DPM then exposes the strengthened family to EngineeringContextAuthority/SPM.

## Non-goals
Sonar Connected Mode, Native Reviewer, workflow cleanup, automatic DFF creation, webhook hosting, merge automation.
