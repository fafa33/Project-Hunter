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


def _family(
    identifier: str,
    path: str = "src/hunter/",
    *,
    invariant: str | None = None,
    lifecycle: str = "regression-tested",
    equivalence_class: str | None = None,
) -> dict[str, object]:
    prevention: dict[str, object] = {"mechanism": "test", "boundary": "review"}
    if equivalence_class is not None:
        prevention["equivalence_class"] = equivalence_class
    return {
        "id": identifier,
        "title": f"title-{identifier}",
        "invariant": invariant or f"invariant-{identifier}",
        "applicability": {"changed_paths": [path], "rationale": "test"},
        "prevention": prevention,
        "regression_evidence": ["tests/test_example.py::test_example"],
        "lifecycle": lifecycle,
        "sources": ["test"],
    }


#: A long, repeated invariant paragraph, standing in for a real family's
#: verbose prose so bounded-selection tests can force overflow without
#: constructing hundreds of families.
_LONG_INVARIANT = (
    "This is a deliberately long, repeated canonical invariant paragraph used only to "
    "simulate realistic registry prose size in a test, standing in for the kind of "
    "multi-sentence rationale a real defect family's invariant field actually carries. "
) * 4


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


def test_compiled_context_carries_the_no_repository_rediscovery_execution_rule(tmp_path: Path) -> None:
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-018")]))
    context = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))

    discipline = cast(list[str], context["execution_discipline"])
    assert discipline, "execution_discipline must be non-empty for every compiled engineering context"
    joined = " ".join(discipline).lower()
    assert "restart" in joined and "onboarding" in joined
    assert "full test suite" in joined
    assert "concrete evidence" in joined


def test_compiled_context_forbids_self_declared_completion(tmp_path: Path) -> None:
    """Prevention-of-premature-completion invariant: reachable from a fresh agent
    session before it ever gets to declare anything "done"."""

    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [_family("DFF-018")]))
    context = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))

    joined = " ".join(cast(list[str], context["execution_discipline"])).lower()
    assert "does not itself complete a mission" in joined
    assert "immutable to the agent" in joined
    assert "evaluate_completion_claim" in joined


def test_execution_discipline_is_fixed_and_does_not_vary_with_scope_or_families(tmp_path: Path) -> None:
    path = _registry(tmp_path, [_family("DFF-001", "src/hunter/"), _family("DFF-002", "scripts/")])
    authority = EngineeringContextAuthority(registry_path=path)

    narrow = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/"))
    broad = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("src/hunter/", "scripts/"))

    assert narrow["execution_discipline"] == broad["execution_discipline"]


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


# --- PR #535 live evidence: bounded, relevance-driven prevention context
# --- compilation. Canonical knowledge (docs/DEFECT_REGISTRY.json) may grow
# --- without bound; the compiled per-task prompt must not grow linearly
# --- with it. Ported alongside the mechanism from PR #535's own SPM/DPM fix,
# --- since this branch's own registry additions are subject to the exact
# --- same recurring defect class (registry growth -> prompt budget overflow).


def test_unrelated_registry_growth_does_not_grow_a_narrow_tasks_prompt(tmp_path: Path) -> None:
    """TEST 1 -- registry growth != linear prompt growth for a narrow scope."""

    relevant = _family("DFF-001", "scripts/hunter_narrow_task.py", invariant=_LONG_INVARIANT)
    unrelated = [_family(f"DFF-U{i:03d}", f"unrelated/module_{i}.py", invariant=_LONG_INVARIANT) for i in range(60)]
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [relevant, *unrelated]))
    scope = _scope("scripts/hunter_narrow_task.py")

    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope)

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    assert [item["id"] for item in families] == ["DFF-001"]
    assert "elided_family_ids" not in result
    blob = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(blob.encode("utf-8")) < 4_000
    assert all(f"DFF-U{i:03d}" not in blob for i in range(60))


def test_broad_allowed_root_does_not_authorise_inlining_every_touching_family(tmp_path: Path) -> None:
    """TEST 2 -- a broad allowed_paths root is not blanket authority to inline

    every family that merely touches it in full, once the budget is
    constrained: the compiled prompt must still fit."""

    broad_families = [
        _family(f"DFF-B{i:03d}", "scripts/", invariant=_LONG_INVARIANT, lifecycle="recorded") for i in range(30)
    ]
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, broad_families))
    scope = _scope("src/", "scripts/", "tests/", "docs/")

    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=3_000)

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    assert len(families) == 30, "every broad-matching family is still applicable, none silently dropped from count"
    full_detail = [item for item in families if "invariant" in item]
    assert len(full_detail) < 30, "not every broadly-matched family may be inlined in full under a real budget"
    blob = json.dumps(families, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(blob.encode("utf-8")) <= 3_000


def test_a_specific_relevant_defect_survives_bounded_selection_in_full(tmp_path: Path) -> None:
    """TEST 3 -- compaction must never be achieved by dropping the genuinely

    relevant (exact-file-matched) prevention requirement."""

    broad_families = [
        _family(f"DFF-B{i:03d}", "scripts/", invariant=_LONG_INVARIANT, lifecycle="recorded") for i in range(30)
    ]
    relevant = _family("DFF-RELEVANT", "scripts/hunter_review_orchestrator.py", invariant=_LONG_INVARIANT)
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [relevant, *broad_families]))
    scope = _scope("src/", "scripts/", "tests/", "docs/")

    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=3_000)

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    by_id = {item["id"]: item for item in families}
    assert "DFF-RELEVANT" in by_id
    assert "invariant" in by_id["DFF-RELEVANT"], "the exact-file match must keep its full detail"


def test_equivalent_prevention_invariants_are_deduplicated_not_deleted(tmp_path: Path) -> None:
    """TEST 4 -- families explicitly declared equivalent render once."""

    first = _family("DFF-DUP-A", "scripts/hunter_x.py", invariant=_LONG_INVARIANT, equivalence_class="same-thing")
    second = _family("DFF-DUP-B", "scripts/hunter_x.py", invariant=_LONG_INVARIANT, equivalence_class="same-thing")
    registry_path = _registry(tmp_path, [first, second])
    registry_before = json.loads(registry_path.read_text(encoding="utf-8"))
    authority = EngineeringContextAuthority(registry_path=registry_path)

    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("scripts/hunter_x.py"))

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    assert len(families) == 1
    assert families[0]["id"] in {"DFF-DUP-A", "DFF-DUP-B"}
    # Canonical registry itself is untouched: both entries remain on disk.
    assert len(registry_before["families"]) == 2
    assert {f["id"] for f in registry_before["families"]} == {"DFF-DUP-A", "DFF-DUP-B"}


def test_dedup_picks_the_more_specific_representative_deterministically(tmp_path: Path) -> None:
    coarse = _family("DFF-COARSE", "scripts/", invariant=_LONG_INVARIANT, equivalence_class="shared")
    precise = _family(
        "DFF-PRECISE", "scripts/hunter_review_orchestrator.py", invariant=_LONG_INVARIANT, equivalence_class="shared"
    )
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, [coarse, precise]))

    result = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=_scope("scripts/"))

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    assert [item["id"] for item in families] == ["DFF-PRECISE"]


def test_bounded_selection_fails_closed_rather_than_truncating_when_nothing_fits(tmp_path: Path) -> None:
    """TEST 5 -- a hard bound that genuinely cannot be met fails closed."""

    exact_files = [
        _family(f"DFF-F{i:03d}", f"scripts/hunter_file_{i}.py", invariant=_LONG_INVARIANT) for i in range(10)
    ]
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, exact_files))
    scope = _scope(*(f"scripts/hunter_file_{i}.py" for i in range(10)))

    with pytest.raises(EngineeringContextAuthorityError, match="exceeds its .*-byte budget by"):
        authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=200)


def test_selection_is_deterministic_and_offline_across_repeated_compiles(tmp_path: Path) -> None:
    families = [
        _family(f"DFF-B{i:03d}", "scripts/", invariant=_LONG_INVARIANT, lifecycle="recorded") for i in range(20)
    ]
    authority = EngineeringContextAuthority(registry_path=_registry(tmp_path, families))
    scope = _scope("src/", "scripts/", "tests/", "docs/")

    first = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=2_500)
    second = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=2_500)

    assert first == second


def test_representative_engineering_implement_scope_fits_the_real_prevention_budget() -> None:
    """TEST 6 -- the real, current docs/DEFECT_REGISTRY.json must compile, for

    the exact scope every Issue Agent test shares
    (``tests/issue_agent_wire.py::issue_body_with_scope``), within the real
    unchanged prevention-context budget. This is a live regression pin against
    this branch's actual registry content, not a fixed family-id list, since
    which families are present is registry content, not mechanism behavior.
    """

    from hunter.evidence_intelligence.smart_prompt_routing import (
        ENGINEERING_IMPLEMENT_PREVENTION_CONTEXT_MAX_BYTES,
    )

    authority = EngineeringContextAuthority()
    scope = TaskScopeContract(
        task_id="test-task",
        branch_pattern="issue-*",
        base_ref="main",
        base_sha="a" * 40,
        allowed_paths=("src/", "scripts/", "tests/", "docs/"),
        prohibited_paths=(),
    )

    result = authority.compile(
        ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope, budget_bytes=ENGINEERING_IMPLEMENT_PREVENTION_CONTEXT_MAX_BYTES
    )

    families = cast(list[dict[str, object]], result["applicable_defect_families"])
    assert families, "the representative scope must select at least one applicable family"
    blob = json.dumps(families, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(blob.encode("utf-8")) <= ENGINEERING_IMPLEMENT_PREVENTION_CONTEXT_MAX_BYTES
