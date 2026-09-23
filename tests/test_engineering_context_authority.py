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

    first = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY)
    second = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY)

    assert first == second
    assert [item["id"] for item in first["applicable_defect_families"]] == ["DFF-001", "DFF-002"]


def test_duplicate_family_identity_fails_closed(tmp_path: Path) -> None:
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-001"), _family("DFF-001")]))
    with pytest.raises(EngineeringContextAuthorityError, match="duplicate defect family"):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY)


def test_malformed_applicability_fails_closed(tmp_path: Path) -> None:
    family = _family("DFF-001")
    family["applicability"] = {"changed_paths": "src/hunter/"}
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [family]))
    with pytest.raises(EngineeringContextAuthorityError, match="changed_paths"):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY)


def test_caller_text_is_not_an_input_to_family_selection(tmp_path: Path) -> None:
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-018")]))
    context = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY)
    families = cast(list[dict[str, object]], context["applicable_defect_families"])
    assert families[0]["id"] == "DFF-018"
    assert "task_text" not in json.dumps(context, sort_keys=True)
