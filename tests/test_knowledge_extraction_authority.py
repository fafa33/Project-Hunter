from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.evidence_intelligence.knowledge_extraction_authority import (
    KnowledgeExtractionAuthority,
    KnowledgeExtractionError,
    KnowledgeFinding,
    finding_from_dict,
)

REGISTRY = Path("docs/DEFECT_REGISTRY.json")


def _dff004() -> dict[str, object]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return next(f for f in registry["families"] if f["id"] == "DFF-004")


def _finding(**overrides: object) -> KnowledgeFinding:
    family = _dff004()
    values: dict[str, object] = {
        "source_kind": "independent-review",
        "finding_id": "pr487-codex-4081501866",
        "source_pr": 487,
        "reviewed_head_sha": "a91e673d419b428c69ccd8ca7471bc6754c92eca",
        "reviewed_base_sha": "79b0105b08942daf49aa1471e9d177f8a4dc09df",
        "reviewer": "chatgpt-codex-connector[bot]",
        "classification": "confirmed",
        "invariant": family["invariant"],
        "affected_paths": ("src/hunter/evidence_intelligence/engineering_context_authority.py",),
        "fix_reference": "PR #487 commit a91e673",
        "regression_evidence": (
            "tests/test_engineering_context_authority.py::test_noncanonical_registry_domains_fail_closed",
        ),
        "claimed_family_id": "DFF-004",
    }
    values.update(overrides)
    return KnowledgeFinding(**values)


def test_pr487_recurrence_maps_to_existing_dff004() -> None:
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(_finding())
    assert proposal.outcome == "existing-family"
    assert proposal.canonical_family_id == "DFF-004"
    assert proposal.canonical_write_authorized is False


@pytest.mark.parametrize(
    "classification",
    ["false-positive", "style", "obsolete", "infrastructure", "provider-unavailable"],
)
def test_excluded_evidence_never_becomes_defect_knowledge(classification: str) -> None:
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(
        _finding(classification=classification, claimed_family_id=None)
    )
    assert proposal.outcome == "excluded"
    assert proposal.canonical_family_id is None
    assert proposal.canonical_write_authorized is False


def test_unknown_claimed_family_fails_closed_as_ambiguous() -> None:
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(_finding(claimed_family_id="DFF-999"))
    assert proposal.outcome == "ambiguous"
    assert proposal.canonical_family_id is None


def test_confirmed_unmatched_defect_is_candidate_only() -> None:
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(
        _finding(
            finding_id="new-root-cause",
            invariant="A new independently verified invariant that has no canonical family.",
            affected_paths=("src/hunter/new_surface.py",),
            claimed_family_id=None,
        )
    )
    assert proposal.outcome == "candidate-new-family"
    assert proposal.canonical_family_id is None
    assert proposal.canonical_write_authorized is False


def test_replay_is_content_addressed_and_idempotent() -> None:
    authority = KnowledgeExtractionAuthority(REGISTRY)
    first = authority.extract(_finding())
    second = authority.extract(_finding())
    assert first == second
    assert first.proposal_id == second.proposal_id


def test_exact_head_change_changes_proposal_identity() -> None:
    authority = KnowledgeExtractionAuthority(REGISTRY)
    first = authority.extract(_finding())
    second = authority.extract(_finding(reviewed_head_sha="b" * 40))
    assert first.proposal_id != second.proposal_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewed_head_sha", "not-a-sha"),
        ("finding_id", ""),
        ("reviewer", ""),
        ("affected_paths", ()),
        ("regression_evidence", ()),
    ],
)
def test_confirmed_finding_with_incomplete_evidence_fails_closed(field: str, value: object) -> None:
    with pytest.raises(KnowledgeExtractionError):
        KnowledgeExtractionAuthority(REGISTRY).extract(_finding(**{field: value}))


def test_live_execution_seam_emits_same_replayable_proposal(tmp_path) -> None:
    from dataclasses import asdict

    finding = _finding()
    payload = {"schema_version": "hunter-knowledge-extraction-v1", **asdict(finding)}
    payload["affected_paths"] = list(finding.affected_paths)
    payload["regression_evidence"] = list(finding.regression_evidence)
    path = tmp_path / "finding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = finding_from_dict(json.loads(path.read_text(encoding="utf-8")))
    authority = KnowledgeExtractionAuthority(REGISTRY)
    assert authority.extract(loaded) == authority.extract(finding)


def test_live_execution_seam_rejects_unknown_authority_fields(tmp_path) -> None:
    from dataclasses import asdict

    finding = _finding()
    payload = {"schema_version": "hunter-knowledge-extraction-v1", **asdict(finding)}
    payload["merge_authorized"] = True
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(KnowledgeExtractionError, match="unknown fields"):
        finding_from_dict(json.loads(path.read_text(encoding="utf-8")))


def test_registry_snapshot_changes_proposal_identity(tmp_path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    first_path = tmp_path / "registry-a.json"
    second_path = tmp_path / "registry-b.json"
    first_path.write_text(json.dumps(registry), encoding="utf-8")
    mutated = json.loads(json.dumps(registry))
    mutated["families"][0]["title"] += " snapshot change"
    second_path.write_text(json.dumps(mutated), encoding="utf-8")

    first = KnowledgeExtractionAuthority(first_path).extract(_finding())
    second = KnowledgeExtractionAuthority(second_path).extract(_finding())
    assert first.registry_digest != second.registry_digest
    assert first.proposal_id != second.proposal_id


def test_proposal_preserves_normalized_finding_evidence_for_replay() -> None:
    proposal = KnowledgeExtractionAuthority(REGISTRY).extract(_finding())
    assert proposal.finding.reviewed_base_sha == _finding().reviewed_base_sha
    assert proposal.finding.reviewer == _finding().reviewer
    assert proposal.finding.classification == "confirmed"
    assert proposal.finding.invariant == _finding().invariant
    assert proposal.finding.affected_paths == _finding().affected_paths
    assert proposal.finding.fix_reference == _finding().fix_reference
    assert proposal.finding.regression_evidence == _finding().regression_evidence


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../src/hunter/fake.py",
        "../src/hunter/fake.py",
        "/src/hunter/fake.py",
        "./src/hunter/fake.py",
        "src/hunter/../hunter/fake.py",
        "src//hunter/fake.py",
    ],
)
def test_noncanonical_affected_paths_fail_closed(bad_path: str) -> None:
    with pytest.raises(KnowledgeExtractionError, match="repository-relative"):
        KnowledgeExtractionAuthority(REGISTRY).extract(_finding(affected_paths=(bad_path,)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", 7),
        ("finding_id", 7),
        ("source_pr", True),
        ("source_pr", 487.0),
        ("reviewed_head_sha", 7),
        ("reviewed_base_sha", 7),
        ("reviewer", 7),
        ("classification", 7),
        ("invariant", 7),
        ("fix_reference", 7),
        ("claimed_family_id", 7),
    ],
)
def test_wrong_scalar_types_fail_closed(field: str, value: object) -> None:
    from dataclasses import asdict

    finding = _finding()
    payload = {"schema_version": "hunter-knowledge-extraction-v1", **asdict(finding)}
    payload["affected_paths"] = list(finding.affected_paths)
    payload["regression_evidence"] = list(finding.regression_evidence)
    payload[field] = value
    with pytest.raises(KnowledgeExtractionError, match="type"):
        finding_from_dict(payload)


def test_executable_seam_has_no_caller_selected_write_path() -> None:
    script = Path("scripts/hunter_knowledge_extraction.py").read_text(encoding="utf-8")
    assert 'add_argument("--output"' not in script
    assert ".write_text(" not in script
