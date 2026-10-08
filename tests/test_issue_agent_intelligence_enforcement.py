"""What the Issue Agent path technically enforces about Hunter's knowledge, SPM and DPM before a model runs (Issue #574).

These tests drive the *real* ``authorize.prepare`` composition (Source Handling, SPM, DPM, signed handoff) and the real
executor/validator/publisher role checks. They prove what a hash or signature can prove: which knowledge and which
prevention families are bound into the exact prompt the model is given, that the prompt/handoff is tamper-evident, and
that nothing the model returns can widen its authority. They do NOT prove that the model followed the prompt; that
needs behavioural evidence (the validator's checks on the result, reviewer findings, the hosted canary).
"""

# ruff: noqa: F811
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_issue_agent_authorize import (  # noqa: F401 - fixtures are shared by name
    RECIPIENT,
    _prompt_keys,
    bootstrap_ledger,
    dependencies,
    run_prepare,
    world,
)

from hunter.automation import issue_agent_state as state
from hunter.automation import issue_agent_transport as transport

PROBE_ID = "KF-9001"
PROBE_INVARIANT = "ENFORCEMENT-PROBE a repeated value must be rejected by the guard"


def _overlay_entry() -> dict[str, Any]:
    registry = json.loads(Path("docs/DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    entry = json.loads(json.dumps(registry["families"][0]))
    entry.update(
        id=PROBE_ID,
        title="enforcement probe family",
        invariant=PROBE_INVARIANT,
        lifecycle=entry["lifecycle"],
    )
    entry["applicability"] = {"changed_paths": ["docs/"]}
    return entry


def _bundle(prepared: Any, sealed: bytes) -> dict[str, Any]:
    binding = transport.TransportBinding(**prepared.handoff_binding)
    return json.loads(transport.open_sealed(sealed, recipient=RECIPIENT, expected=binding))


def test_registry_prevention_reaches_the_prompt_the_model_receives(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    (prepared, sealed), _ = run_prepare(world, monkeypatch, "registry")
    prompt = _bundle(prepared, sealed)["prompt"]
    registry = json.loads(Path("docs/DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    # The DPM block names applicable registry families by id, and only families the registry actually defines.
    named = {str(family["id"]) for family in registry["families"] if str(family["id"]) in prompt}
    assert named, "no DPM registry family reached the prompt"
    assert not named & {PROBE_ID}


def test_anchored_knowledge_overlay_changes_the_bound_prompt_and_dpm_context(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    (plain, plain_sealed), _ = run_prepare(world, monkeypatch, "plain")
    deps = dependencies(world, monkeypatch, knowledge_overlay=[_overlay_entry()])
    (known, known_sealed), _ = run_prepare(world, monkeypatch, "known", deps=deps)

    plain_prompt, known_prompt = _bundle(plain, plain_sealed)["prompt"], _bundle(known, known_sealed)["prompt"]
    assert PROBE_ID in known_prompt and PROBE_ID not in plain_prompt
    # The overlay is part of the digests the ledger binds, so it cannot be swapped out afterwards.
    plain_lineage, known_lineage = plain.unsigned_authorized["lineage"], known.unsigned_authorized["lineage"]
    assert known_lineage["prompt_sha256"] != plain_lineage["prompt_sha256"]
    assert known_lineage["dpm_context_sha256"] != plain_lineage["dpm_context_sha256"]
    assert known_lineage["prompt_sha256"] == state.sha256_hex(known_prompt.encode())
