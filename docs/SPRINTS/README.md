# Project Hunter Sprint Specifications

This directory contains the canonical sprint specifications for Project Hunter.

Hunter is a governance-driven, specification-first engineering project. Every implementation must begin from an approved sprint specification that defines the exact scope of a single release.

Sprint specifications authorize implementation scope only. They never redefine architecture, governance, runtime, or engineering principles established by higher-authority documents.

---

# Canonical Authority Reference

The canonical document-authority hierarchy is owned and defined by:

`docs/CANONICAL_ARCHITECTURE_MAP.md`

Every sprint specification must comply with that hierarchy and with all applicable accepted ADRs.

This document owns sprint governance only. It does not duplicate or redefine canonical document precedence.

---

# Canonical Sprint Workflow

Every sprint must follow this lifecycle:

1. Create or update `docs/SPRINTS/vX.Y.Z.md`.
2. Validate against all higher-authority documents.
3. Verify ADR compliance.
4. Verify Runtime Architecture compatibility.
5. Verify Canonical Architecture compatibility.
6. Verify Implementation Contract compliance.
7. Freeze sprint scope.
8. Implement only approved scope.
9. Execute required quality gates.
10. Produce an implementation report.
11. Commit.
12. Push.
13. Tag the release when applicable.

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

# Ownership Boundary

This document owns:

- sprint-specification workflow;
- sprint-scope authorization;
- sprint acceptance criteria;
- the index of sprint specifications.

This document does not own:

- canonical document precedence;
- constitutional governance;
- architecture;
- runtime authority;
- implementation contracts;
- architectural decisions.

---

# Current Sprint Specifications

- `TEMPLATE.md`
- `v2.7.0.md`
- `v2.7.1.md`
- `v2.8.0.md`
- `v2.9.0.md`
- `v3.x.x` series