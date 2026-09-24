from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from hunter.evidence_intelligence.engineering_context_authority import (
    EngineeringContextAuthority,
    EngineeringContextAuthorityError,
)
from hunter.evidence_intelligence.smart_prompt_routing import ENGINEERING_IMPLEMENT_TASK_KEY
from hunter.task_scope import TaskScopeContract


def _scope(*paths: str) -> TaskScopeContract:
    return TaskScopeContract(task_id="test-task", branch_pattern="issue-*", base_sha="a" * 40, allowed_paths=paths)


def _registry(tmp_path: Path, families: list[dict[str, object]]) -> Path:
    path = tmp_path / "DEFECT_REGISTRY.json"
    path.write_text(json.dumps({"version": 1, "families": families}), encoding="utf-8")
    return path


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


def test_implementation_context_selects_applicable_families_deterministically(tmp_path: Path) -> None:
    path = _registry(tmp_path, [_family("DFF-002", "scripts/"), _family("DFF-001", "src/hunter/")])
    authority = EngineeringContextAuthority(registry_path=path)

    first = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))
    second = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))

    assert first == second
    assert [item["id"] for item in first["applicable_defect_families"]] == ["DFF-001", "DFF-002"]


def test_duplicate_family_identity_fails_closed(tmp_path: Path) -> None:
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-001"), _family("DFF-001")]))
    with pytest.raises(EngineeringContextAuthorityError, match="duplicate defect family"):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))


def test_malformed_applicability_fails_closed(tmp_path: Path) -> None:
    family = _family("DFF-001")
    family["applicability"] = {"changed_paths": "src/hunter/"}
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [family]))
    with pytest.raises(EngineeringContextAuthorityError, match="changed_paths"):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("lifecycle", "invented", "lifecycle must be canonical"),
        ("prevention_boundary", "deploy-now", "prevention boundary must be canonical"),
    ],
)
def test_noncanonical_registry_domains_fail_closed(tmp_path: Path, field: str, value: str, match: str) -> None:
    family = _family("DFF-001")
    if field == "lifecycle":
        family["lifecycle"] = value
    else:
        prevention = cast(dict[str, object], family["prevention"])
        prevention["boundary"] = value
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [family]))

    with pytest.raises(EngineeringContextAuthorityError, match=match):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))


def test_caller_text_is_not_an_input_to_family_selection(tmp_path: Path) -> None:
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-018")]))
    context = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))
    families = cast(list[dict[str, object]], context["applicable_defect_families"])
    assert families[0]["id"] == "DFF-018"
    assert "task_text" not in json.dumps(context, sort_keys=True)


def test_multi_surface_family_survives_when_one_surface_is_still_permitted(tmp_path: Path) -> None:
    family = _family("DFF-MULTI", "src/hunter/automation/")
    family["applicability"] = {"changed_paths": ["src/hunter/automation/", "scripts/"], "rationale": "test"}
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [family]))
    scope = TaskScopeContract(
        task_id="test-task",
        branch_pattern="issue-*",
        base_sha="a" * 40,
        allowed_paths=("src/hunter/automation/",),
        prohibited_paths=("scripts/",),
    )
    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope)
    assert [item["id"] for item in result["applicable_defect_families"]] == ["DFF-MULTI"]


def test_broad_family_survives_nested_prohibition_when_permitted_surface_remains(tmp_path: Path) -> None:
    family = _family("DFF-BROAD", "src/hunter/")
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [family]))
    scope = TaskScopeContract(
        task_id="test-task",
        branch_pattern="issue-*",
        base_sha="a" * 40,
        allowed_paths=("src/hunter/",),
        prohibited_paths=("src/hunter/automation/",),
    )
    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope)
    assert [item["id"] for item in result["applicable_defect_families"]] == ["DFF-BROAD"]
