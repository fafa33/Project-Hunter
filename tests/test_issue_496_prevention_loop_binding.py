"""Issue #496 gap-closure regressions.

Most of the DPM -> SPM executable learning loop was already implemented by
merged PRs #507, #510, #519, #524-528 and #530: ``EngineeringContextAuthority``
deterministically selects applicable canonical ``DEFECT_REGISTRY`` families,
``SmartPromptMachine`` injects ``governed_prevention_context`` before
compilation, and the compiled build/envelope identity already carries that
content downstream. The existing suite proved selection determinism within
one call and caller-text injection resistance, but nothing exercised two
invariants the Issue's acceptance criteria state explicitly:

* "a historical defect rule is selected and bound into a later applicable
  SPM build" (mandatory regression 1) -- ``EngineeringContextAuthority``
  re-reads the registry file on every ``compile()`` call, so a family that
  becomes canonical between two builds (exactly what the canonical learning
  materialization path in ``scripts/hunter_canonicalize_learning.py`` does
  for an existing family, and what a governed registry change does for a new
  one) must be selected by the very next build without restarting any
  process or authority instance;
* "Selected rule IDs/versions are cryptographically/auditably bound to
  build/envelope identity using existing canonical identity machinery" --
  ``governed_prevention_context`` is embedded directly in the compiled
  ``task_text``, which content-hashes into ``build_record_id``/
  ``manifest_id`` via the existing ADR 0031 pre-model pipeline, so the build
  identity must change when the selected family set's content changes and
  stay stable when it does not.

These tests add no production code: they exercise the real (unmocked)
``SmartPromptMachine.compile_task`` -> ``PromptContextCompiler.compile`` path
against a registry file that changes between calls, proving both properties
hold end-to-end rather than merely at the unit level.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import cast

from evidence_pre_model_source_handling_fixture import source_handling_authority
from test_smart_prompt_machine_phase_a_integration import NOW, _document, _span

from hunter.evidence_intelligence.engineering_context_authority import EngineeringContextAuthority
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_IMPLEMENT_PROFILE,
    ENGINEERING_IMPLEMENT_ROUTE,
    ENGINEERING_IMPLEMENT_TASK_KEY,
    PromptMachineProfileRegistry,
    PromptTaskRequest,
    PromptTaskRouteRegistry,
    SmartPromptMachine,
)
from hunter.task_scope import TaskScopeContract

_AUTOMATION_SIGNING_KEY_HEX = "11" * 32
_AUTOMATION_VERIFYING_KEY_HEX = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _registry(path: Path, families: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"version": 1, "families": families}), encoding="utf-8")


def _family(identifier: str, path: str = "src/hunter/") -> dict[str, object]:
    return {
        "id": identifier,
        "title": f"title-{identifier}",
        "invariant": f"invariant-{identifier}",
        "applicability": {"changed_paths": [path], "rationale": "test"},
        "prevention": {"mechanism": "test", "boundary": "review"},
        "regression_evidence": ["tests/test_example.py::test_example"],
        "lifecycle": "regression-tested",
        "sources": ["test"],
    }


def _scope() -> TaskScopeContract:
    return TaskScopeContract(
        task_id="task-496", branch_pattern="issue-*", base_sha="a" * 40, allowed_paths=("src/hunter/",)
    )


def _build(monkeypatch, tmp_path: Path, families: list[dict[str, object]]) -> tuple[Path, SmartPromptMachine]:
    """One governed-implement SmartPromptMachine, pointed at a registry this
    test can mutate between builds, over the real (unmocked) compiler."""
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", _AUTOMATION_SIGNING_KEY_HEX)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", _AUTOMATION_VERIFYING_KEY_HEX)
    registry_path = tmp_path / "DEFECT_REGISTRY.json"
    _registry(registry_path, families)
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    repository.save_document(_document())
    repository.save_span(_span("span-a", "governed context", 0))
    profiles = PromptMachineProfileRegistry((ENGINEERING_IMPLEMENT_PROFILE,))
    routes = PromptTaskRouteRegistry((ENGINEERING_IMPLEMENT_ROUTE,), profiles=profiles)

    def resolver(document_id: str, cutoff: datetime):
        return source_handling_authority(document_id=document_id, cutoff=cutoff)

    machine = SmartPromptMachine(
        repository=repository,
        profiles=profiles,
        routes=routes,
        source_handling_resolver=resolver,
        engineering_context_authority=EngineeringContextAuthority(registry_path=registry_path),
        clock=_Clock(),
    )
    return registry_path, machine


def _request() -> PromptTaskRequest:
    return PromptTaskRequest(
        document_id="document-1",
        execution_owner_id="task-496",
        task_key=ENGINEERING_IMPLEMENT_TASK_KEY,
        task_text="implement only the configured symbol within the available budget",
    )


def test_a_family_materialized_after_the_first_build_is_selected_and_bound_into_the_next_build(
    monkeypatch, tmp_path: Path
) -> None:
    """Mandatory regression 1: a historical defect rule reaches a LATER build.

    ``DFF-BASE`` is applicable from the start so every build stays dispatchable.
    ``DFF-LEARNED`` does not exist yet at the first build -- exactly the state
    before a canonical learning observation has materialized it -- and is
    written into the SAME registry file the authority already points at
    before the second build, standing in for the canonical materialization
    write (``scripts/hunter_canonicalize_learning.py`` / PR #510, #525-528).
    No new authority instance, process, or SmartPromptMachine is constructed
    between the two builds.
    """
    registry_path, machine = _build(monkeypatch, tmp_path, [_family("DFF-BASE")])

    first = machine.compile_task(_request(), implementation_scope=_scope())
    first_content = _compiled_prompt_content(first)
    assert "DFF-BASE" in first_content
    assert "DFF-LEARNED" not in first_content

    # The canonical learning path materializes a new family into the SAME
    # registry file between the two builds.
    _registry(registry_path, [_family("DFF-BASE"), _family("DFF-LEARNED")])

    second = machine.compile_task(_request(), implementation_scope=_scope())
    second_content = _compiled_prompt_content(second)
    assert "DFF-BASE" in second_content
    assert "DFF-LEARNED" in second_content
    assert first.compilation.manifest.build_record_id != second.compilation.manifest.build_record_id
    assert first.envelope.build_record_id != second.envelope.build_record_id


def test_build_identity_changes_when_the_selected_prevention_set_changes_and_is_stable_when_it_does_not(
    monkeypatch, tmp_path: Path
) -> None:
    """Prevention-rule identity is bound to build/envelope identity.

    Same registry, same scope, same task -> identical manifest/build/envelope
    identity on replay (nothing to audit differently). Adding an applicable
    family -> every one of those identities changes, because
    ``governed_prevention_context`` is embedded in the compiled ``task_text``
    that content-hashes into them, through the existing ADR 0031 build
    identity machinery -- no parallel hashing or identity authority.
    """
    registry_path, machine = _build(monkeypatch, tmp_path, [_family("DFF-ONE")])

    baseline = machine.compile_task(_request(), implementation_scope=_scope())
    replay = machine.compile_task(_request(), implementation_scope=_scope())

    assert replay.compilation.manifest.build_record_id == baseline.compilation.manifest.build_record_id
    assert replay.compilation.manifest.manifest_id == baseline.compilation.manifest.manifest_id
    assert replay.envelope.build_record_id == baseline.envelope.build_record_id
    assert replay.envelope.build_manifest_id == baseline.envelope.build_manifest_id

    _registry(registry_path, [_family("DFF-ONE"), _family("DFF-TWO")])
    changed = machine.compile_task(_request(), implementation_scope=_scope())

    assert changed.compilation.manifest.build_record_id != baseline.compilation.manifest.build_record_id
    assert changed.compilation.manifest.manifest_id != baseline.compilation.manifest.manifest_id
    assert changed.envelope.build_record_id != baseline.envelope.build_record_id
    assert changed.envelope.build_manifest_id != baseline.envelope.build_manifest_id


def _compiled_prompt_content(result) -> str:
    """The real rendered prompt text this build actually produced.

    ``governed_prevention_context`` is embedded in the compiled ``task_text``
    passed to ``PromptContextCompiler.compile``, which threads it into the
    OBJECTIVE section of the rendered prompt artifact -- the same content the
    build/envelope identity is hashed over. Reading it back here is what
    proves the selected family set actually reached this exact build's
    content, not merely that some id happened to change.
    """
    artifact = result.compilation.orchestration.build_result.prompt_artifact
    assert artifact is not None, "a READY build must carry a concrete prompt artifact"
    return cast(str, artifact.content)
