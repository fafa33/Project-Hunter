from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.evidence_intelligence.knowledge_event_ingestion import EventIngestionError, ingest_event
from hunter.evidence_intelligence.knowledge_extraction_authority import KnowledgeExtractionAuthority

REGISTRY = Path("docs/DEFECT_REGISTRY.json")


def family():
    return next(f for f in json.loads(REGISTRY.read_text())["families"] if f["id"] == "DFF-008")


def payload(provider="sonar"):
    return {
        "schema_version": "hunter-finding-event-v1",
        "provider": provider,
        "event_id": "evt-489-s8707",
        "source_pr": 489,
        "reviewed_head_sha": "e" * 40,
        "reviewed_base_sha": "c" * 40,
        "reviewer": "sonarcloud",
        "classification": "confirmed",
        "invariant": family()["invariant"],
        "affected_paths": ["scripts/hunter_knowledge_extraction.py"],
        "fix_reference": "PR #489 commit e5cd436",
        "regression_evidence": [
            "tests/test_knowledge_extraction_authority.py::test_executable_seam_has_no_caller_selected_write_path"
        ],
        "claimed_family_id": "DFF-008",
    }


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("sonar", "deterministic-gate"),
        ("github-review", "independent-review"),
        ("hunter-ci", "ci"),
        ("hunter-governance", "governance"),
    ],
)
def test_adapters_converge(source, kind):
    f = ingest_event(source, payload(source))
    assert f.source_kind == kind
    assert f.finding_id.startswith(source + ":")
    assert KnowledgeExtractionAuthority(REGISTRY).extract(f).outcome == "existing-family"


def test_unknown_field_fails_closed():
    p = payload()
    p["merge_authorized"] = True
    with pytest.raises(EventIngestionError, match="unknown fields"):
        ingest_event("sonar", p)


def test_duplicate_delivery_deterministic():
    event = payload()
    first = ingest_event("sonar", event)
    second = ingest_event("sonar", dict(event))
    assert first == second
    assert first.finding_id == second.finding_id


def test_provider_drift_rejected():
    p = payload()
    p["new_sonar_magic"] = "drift"
    with pytest.raises(EventIngestionError, match="unknown fields"):
        ingest_event("sonar", p)


def test_false_positive_excluded():
    p = payload()
    p["classification"] = "false-positive"
    p["claimed_family_id"] = None
    assert KnowledgeExtractionAuthority(REGISTRY).extract(ingest_event("sonar", p)).outcome == "excluded"


def test_same_provider_event_id_with_changed_payload_has_different_content_identity() -> None:
    first = ingest_event("sonar", payload())
    changed = payload()
    changed["fix_reference"] = "PR #489 commit DIFFERENT"
    second = ingest_event("sonar", changed)
    assert first.finding_id != second.finding_id
