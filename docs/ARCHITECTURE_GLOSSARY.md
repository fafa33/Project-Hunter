# Architecture Glossary

## Purpose

This glossary provides shared meanings for recurring Project Hunter architecture terms. It improves consistency across architecture documents, ADRs, ADPRs, Issues, implementation contracts, reviews, and code.

Definitions here clarify usage. They do not override more specific definitions in the Project Constitution, canonical architecture documents, accepted ADRs, schemas, or implementation contracts.

## Terms

### Architecture Decision Preparation Record (ADPR)

A permanent record of the validated problem, evidence, assumptions, constraints, option set, comparative analysis, falsification, rejected options, risks, readiness, recommendation, and traceability that precedes an ADR or other formal architectural action.

### Architecture Decision Record (ADR)

A canonical record of an accepted architectural decision, its scope, consequences, and governing constraints.

### Authority

The canonical right to define or determine a fact, contract, state, score, classification, or architectural rule. Authority must be explicit and must not be duplicated across independent owners unless an accepted architecture defines the relationship.

### Ownership

Responsibility for producing, maintaining, validating, and evolving a defined architectural capability or record family. Ownership does not automatically grant authority over upstream evidence or downstream interpretation.

### Boundary

A documented separation of responsibility, authority, data, or behavior between components, services, documents, or lifecycle stages.

### Canonical

Authoritative within a defined scope. A canonical artifact is the source that other components must consume or comply with rather than independently recreate.

### Calibration

The evidence-based process of determining whether outputs, confidence, thresholds, or predicted frequencies correspond to observed outcomes under defined historical and population constraints.

### Confidence

A bounded statement about the reliability or uncertainty of evidence or an output. Confidence is not a substitute for evidence and must preserve its methodology and provenance.

### Conflict

Two or more validly recorded claims, observations, versions, or authorities that cannot simultaneously be treated as the single accepted value for the same defined scope and time.

### Correction

A new, traceable record that repairs or supersedes an earlier recorded claim while preserving the original history and correction lineage.

### Correction Lineage

The explicit chain connecting an original record to its corrections, supersessions, or replacements.

### Decision Readiness

The state in which the problem, constraints, evidence, options, falsification, risks, and governance checks are sufficiently complete for a formal architectural decision without inventing missing facts.

### Deterministic Reconstruction

The ability to reproduce the same historical state or output from the same admissible inputs, ordering rules, versions, and time boundary.

### Effective Time

The time at which a fact, rule, classification, or state is asserted to apply in the modeled world.

### Engine

A component that performs a defined analytical or decision-support responsibility using explicit inputs, methodology, and outputs. An engine must not silently become the authority for evidence it merely consumes.

### Evidence

A traceable factual, observed, disclosed, derived, or validated input used to support a claim or decision. Evidence includes its identity, source, method, time semantics, provenance, and limitations.

### Evidence Inventory

The explicit list of evidence sources considered during decision preparation, including quality, relevance, authority, limitations, conflicts, and missingness.

### Evidence Provenance

The lineage showing where evidence originated, how it was acquired or derived, which version or method was used, and how it reached the current record or output.

### Falsification

An active attempt to identify evidence, counterexamples, boundary cases, or failure conditions that would invalidate an option, assumption, or claim.

### Historical Validation

Evaluation of a method against point-in-time admissible historical evidence and outcomes while preserving the information that was actually knowable at each historical boundary.

### Identity

The stable definition that determines when two records, entities, representations, observations, or decisions refer to the same thing for a defined purpose.

### Immutable Record

A historical record whose original content is not overwritten. Changes are represented by additional versioned, corrected, or superseding records.

### Logical History

The ordered sequence of versions, corrections, conflicts, or states associated with the same logical identity.

### Merge Readiness

The governance state in which required implementation, verification, review, evidence, acceptance criteria, operational validation, and blocking-finding resolution have completed sufficiently for merge consideration.

### Missingness

An explicit representation that required or desired information is absent, unavailable, unsupported, unknown, not yet observed, or not applicable. Missingness must not be converted silently into a neutral value.

### Observation

A recorded fact obtained from a source or measurement method, distinct from an analytical conclusion or score.

### Option

A materially distinct architectural approach that satisfies the fixed problem boundary and is evaluated against common criteria.

### Option Normalization

Describing alternatives at comparable depth, scope, and evaluation dimensions so that comparison is fair.

### Persistence

The durable storage contract for records, including identity, schema, ordering, versioning, correction, constraints, and retrieval semantics.

### Provenance

Traceable origin and transformation lineage for data, evidence, rules, records, or outputs.

### Recorded Time

The time at which a fact, rule, correction, or state was recorded by Hunter or became available to the relevant system of record.

### Replay

Re-execution or reconstruction of a historical process or state under an explicit time and evidence boundary.

### Representation

A specific technical or economic form of an underlying entity, asset, protocol, token, network, contract, or claim. Distinct representations must not be silently collapsed when their properties differ.

### Reviewer

An independent evaluator of preparation quality, architectural consistency, implementation, or merge readiness. A reviewer does not silently redefine architecture during review.

### Shadow Mode

Operation in which a new capability runs and records results without becoming the production authority or directly affecting canonical decisions.

### Strict-Known Replay

Historical reconstruction using only records whose recorded time was known at or before the replay boundary, while also respecting effective-time applicability, version, correction, conflict, and deterministic ordering rules.

### Sufficiency

A documented determination that available evidence meets the minimum requirements for a defined analytical, calibration, validation, or decision task. Insufficient evidence must remain explicit.

### Supersession

A traceable declaration that a newer record, ADPR, ADR, rule, or architecture replaces an older one for a defined scope while preserving the historical artifact.

### Traceability

The ability to follow a problem or decision through its evidence, Issue or Epic, ADPR, ADR, implementation, PR, commit, validation, release, and later supersession.

### Valid Time

The interval in which a fact or rule is asserted to be applicable in the modeled world. Effective time may be represented as a point or as part of a valid-time interval.

### Version

A distinguishable state of an artifact, record, method, schema, rule, or decision. Versioning must preserve the identity and ordering relationship required for replay and history.

## Usage Rule

When a document requires a narrower or domain-specific definition, it must state that definition explicitly and identify the scope in which it differs from this glossary.
