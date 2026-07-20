# Project Hunter Sprint Specifications

This directory contains the canonical sprint specifications for Project Hunter.

Hunter is a governance-driven, specification-first engineering project. Every implementation must begin from an approved sprint specification that defines the exact scope of a single release.

Sprint specifications authorize implementation scope only. They never redefine architecture, governance, runtime, or engineering principles established by higher-authority documents.

---

# Canonical Governance Hierarchy

The authoritative document hierarchy for Project Hunter is:

1. docs/PROJECT_CONSTITUTION.md
2. docs/PROJECT_PRINCIPLES.md
3. docs/CANONICAL_ARCHITECTURE_MAP.md
4. docs/HUNTER_ARCHITECTURE_MANIFEST.md
5. docs/HUNTER_ARCHITECTURE_SPEC.md
6. docs/CANONICAL_RUNTIME_ARCHITECTURE.md
7. Accepted ADRs (`docs/ADR/`)
8. docs/VISION.md
9. docs/HUNTER_ROADMAP.md
10. docs/DEVELOPMENT_GOVERNANCE.md
11. docs/HUNTER_IMPLEMENTATION_CONTRACT.md
12. docs/AI_REVIEW_PROTOCOL.md
13. docs/SPRINTS/<version>.md
14. docs/CODEX_IMPLEMENTATION_GUIDE.md

The Constitution remains the highest authority.

The Canonical Architecture Map defines the complete architectural topology of Hunter.

The Runtime Architecture defines runtime execution only.

Accepted ADRs define binding architectural decisions unless superseded by a newer accepted ADR.

Sprint specifications define implementation scope only.

Nothing below may contradict anything above.

---

# Canonical Sprint Workflow

Every sprint must follow this lifecycle:

1. Create or update `docs/SPRINTS/vX.Y.Z.md`
2. Validate against all higher-governance documents
3. Verify ADR compliance
4. Verify Runtime Architecture compatibility
5. Verify Canonical Architecture compatibility
6. Verify Implementation Contract compliance
7. Freeze sprint scope
8. Implement only approved scope
9. Execute required quality gates
10. Produce implementation report
11. Commit
12. Push
13. Tag release

---

# Governance Rules

Every sprint MUST:

- Preserve deterministic execution.
- Preserve evidence provenance.
- Preserve replay correctness.
- Preserve point-in-time truth.
- Preserve idempotent persistence.
- Preserve explicit unavailable states.
- Preserve architectural boundaries.
- Preserve backward compatibility unless an approved migration exists.

A sprint MUST NOT:

- Introduce architectural drift.
- Duplicate architectural ownership.
- Redefine runtime authority.
- Introduce competing canonical workflows.
- Fabricate evidence.
- Inflate coverage.
- Hide missing evidence.
- Bypass approved ADRs.

---

# Sprint Acceptance Criteria

A sprint is complete only when:

- Implementation matches the approved specification.
- Architecture remains compliant.
- Runtime remains compliant.
- ADR compliance is verified.
- Tests pass.
- Quality gates pass.
- Documentation is updated.
- Governance documents remain consistent.

---

# Current Sprint Specifications

- TEMPLATE.md
- v2.7.0.md
- v2.7.1.md
- v2.8.0.md
- v2.9.0.md
- v3.x.x series