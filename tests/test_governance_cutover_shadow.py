from __future__ import annotations

from pathlib import Path

from hunter.governance_cutover.shadow import ShadowRecorder


def project(inputs):
    return {"decision": inputs["decision"], "reason": inputs["reason"]}


def test_shadow_records_parity_without_side_effect_surface(tmp_path):
    recorder = ShadowRecorder(tmp_path)
    record = recorder.observe(
        generation=1,
        head_sha="abc",
        domain="governance",
        inputs={"decision": "BLOCK", "reason": "missing"},
        legacy=project,
        successor=project,
        observed_at="2026-09-22T05:00:00Z",
    )
    assert record.parity is True
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_shadow_divergence_is_evidence_not_authority(tmp_path):
    recorder = ShadowRecorder(tmp_path)
    record = recorder.observe(
        generation=1,
        head_sha="abc",
        domain="readiness",
        inputs={"decision": "BLOCK", "reason": "x"},
        legacy=project,
        successor=lambda _: {"decision": "CLEAR", "reason": "x"},
        observed_at="2026-09-22T05:00:00Z",
    )
    assert record.parity is False


def test_duplicate_shadow_event_is_idempotent(tmp_path):
    recorder = ShadowRecorder(tmp_path)
    kwargs = dict(
        generation=1,
        head_sha="abc",
        domain="admission",
        inputs={"decision": "BLOCK", "reason": "x"},
        legacy=project,
        successor=project,
        observed_at="2026-09-22T05:00:00Z",
    )
    recorder.observe(**kwargs)
    recorder.observe(**kwargs)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_shadow_module_has_no_github_or_network_publication_imports():
    source = Path("src/hunter/governance_cutover/shadow.py").read_text()
    forbidden = ("requests", "urllib", "github", "transport", "subprocess", "socket", "statuses/", "workflow_dispatch")
    assert not any(token in source.lower() for token in forbidden)
