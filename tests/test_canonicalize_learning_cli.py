from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from hunter.evidence_intelligence import controlled_learning_integration as learning

_SCRIPT = Path("scripts/hunter_canonicalize_learning.py")
_spec = importlib.util.spec_from_file_location("hunter_canonicalize_learning_cli", _SCRIPT)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
main = _module.main

REGISTRY = Path("docs/DEFECT_REGISTRY.json")
HEAD = "a" * 40
BASE = "b" * 40


def _observation(pr: int, classification: str = "confirmed") -> dict[str, object]:
    family = next(f for f in json.loads(REGISTRY.read_text())["families"] if f["id"] == "DFF-008")
    return {
        "source": "sonar",
        "provider": "sonar",
        "event_id": f"issue-{pr}",
        "source_pr": pr,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "reviewer": "deterministic-fixture",
        "path": "scripts/hunter_knowledge_extraction.py",
        "line": 1,
        "message": "validated recurrence",
        "availability": "available",
        "classification": classification,
        "invariant": family["invariant"] if classification == "confirmed" else "",
        "affected_paths": ["scripts/hunter_knowledge_extraction.py"] if classification == "confirmed" else [],
        "fix_reference": f"PR #{pr} focused remediation" if classification == "confirmed" else "",
        "regression_evidence": (
            ["tests/test_canonicalize_learning_cli.py::test_apply_atomically_updates_registry"]
            if classification == "confirmed"
            else []
        ),
        "claimed_family_id": "DFF-008" if classification == "confirmed" else None,
    }


@pytest.fixture(autouse=True)
def _canonical_targets(monkeypatch, tmp_path):
    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_bytes(REGISTRY.read_bytes())
    monkeypatch.setattr(learning, "CANONICAL_DEFECT_REGISTRY", registry)
    monkeypatch.setattr(learning, "CANONICAL_LEARNING_LEDGER", tmp_path / "hunter-learning-ledger.json")
    return registry


def _write_observations(tmp_path: Path, observations: list[dict[str, object]]) -> Path:
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    return path


def test_dry_run_previews_without_persisting(tmp_path, capsys, _canonical_targets):
    registry = _canonical_targets
    before = registry.read_bytes()
    observations = _write_observations(tmp_path, [_observation(910)])

    code = main(["--pr", "910", "--head", HEAD, "--base", BASE, "--observations", str(observations), "--dry-run"])

    assert code == 0
    assert registry.read_bytes() == before
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "changed=true" in out


def test_apply_atomically_updates_registry(tmp_path, capsys, _canonical_targets):
    registry = _canonical_targets
    observations = _write_observations(tmp_path, [_observation(911)])

    code = main(["--pr", "911", "--head", HEAD, "--base", BASE, "--observations", str(observations)])

    assert code == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "changed=true" in out
    family = next(f for f in json.loads(registry.read_text())["families"] if f["id"] == "DFF-008")
    assert any("issue-911" in source for source in family["sources"])


def test_apply_is_idempotent_on_replay(tmp_path, _canonical_targets):
    registry = _canonical_targets
    observations = _write_observations(tmp_path, [_observation(912)])
    argv = ["--pr", "912", "--head", HEAD, "--base", BASE, "--observations", str(observations)]

    first_code = main(argv)
    after_first = registry.read_bytes()
    second_code = main(argv)

    assert first_code == 0 and second_code == 0
    assert registry.read_bytes() == after_first
    family = next(f for f in json.loads(registry.read_text())["families"] if f["id"] == "DFF-008")
    assert sum("issue-912" in source for source in family["sources"]) == 1


def test_excluded_classification_never_integrates(tmp_path, _canonical_targets):
    registry = _canonical_targets
    before = registry.read_bytes()
    observations = _write_observations(tmp_path, [_observation(913, classification="false-positive")])

    code = main(["--pr", "913", "--head", HEAD, "--base", BASE, "--observations", str(observations)])

    assert code == 0
    assert registry.read_bytes() == before


def test_malformed_observations_file_fails_closed_without_mutating_registry(tmp_path, _canonical_targets):
    registry = _canonical_targets
    before = registry.read_bytes()
    bad = tmp_path / "observations.json"
    bad.write_text("not json", encoding="utf-8")

    code = main(["--pr", "914", "--head", HEAD, "--base", BASE, "--observations", str(bad)])

    assert code == 2
    assert registry.read_bytes() == before


def test_observations_must_be_a_list(tmp_path, _canonical_targets):
    registry = _canonical_targets
    before = registry.read_bytes()
    path = tmp_path / "observations.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    code = main(["--pr", "915", "--head", HEAD, "--base", BASE, "--observations", str(path)])

    assert code == 2
    assert registry.read_bytes() == before


def test_conflicting_evidence_for_same_event_identity_fails_closed(tmp_path, _canonical_targets):
    registry = _canonical_targets
    before = registry.read_bytes()
    first = _observation(916)
    conflicting = dict(first)
    conflicting["message"] = "a materially different finding under the same event id"
    conflicting["fix_reference"] = "PR #916 a different remediation entirely"
    observations = _write_observations(tmp_path, [first, conflicting])

    code = main(["--pr", "916", "--head", HEAD, "--base", BASE, "--observations", str(observations)])

    assert code == 2
    assert registry.read_bytes() == before


def test_cli_has_no_caller_selected_write_target():
    script = Path("scripts/hunter_canonicalize_learning.py").read_text(encoding="utf-8")
    assert 'add_argument("--registry"' not in script
    assert 'add_argument("--output"' not in script
    assert 'registry = Path("docs/DEFECT_REGISTRY.json")' not in script


def test_cli_has_no_network_or_provider_dependency():
    script = Path("scripts/hunter_canonicalize_learning.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import urllib", "import socket", "hunter_governance_review_v2"):
        assert forbidden not in script
