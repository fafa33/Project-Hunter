"""Canary fixtures must never become a production proof authority (Issue #682).

The hosted canary signs and validates with test material. The production lifecycle must keep using the single
trusted validator, the pinned trust roots and the Kilo container isolation, with no injectable safety port.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts/hunter_issue_agent_lifecycle.py"
CANARY_MARKERS = ("local_safety", "kilo_canary_independent_validator", "CANARY_ONLY", "test_issue_agent_roles")


def _calls(tree: ast.AST, attribute: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attribute
    ]


def test_production_validate_uses_the_default_trusted_safety_without_an_override() -> None:
    tree = ast.parse(LIFECYCLE.read_text())
    calls = _calls(tree, "run_validator")
    assert calls, "the lifecycle must call the production run_validator"
    for call in calls:
        assert "safety" not in {keyword.arg for keyword in call.keywords}, "a safety override would replace the proof"
    import hunter.automation.issue_agent_roles as roles

    defaults = roles.run_validator.__kwdefaults__ or {}
    default = defaults["safety"]
    assert default.__name__ == "credential_free_candidate_safety"


def test_production_entry_points_reference_no_canary_material() -> None:
    for path in (LIFECYCLE, *sorted((ROOT / "src/hunter/automation").glob("issue_agent_*.py"))):
        text = path.read_text()
        for marker in CANARY_MARKERS:
            assert marker not in text, f"{path.name} references canary-only material {marker!r}"


def test_production_execute_selects_the_container_isolation_for_kilo() -> None:
    text = LIFECYCLE.read_text()
    assert "KiloContainerIsolation(" in text and "roles.SudoIsolation(ISOLATION_USER)" in text
    import hunter.automation.issue_agent_roles as roles

    source = Path(roles.__file__).read_text()
    assert "require_governed_kilo_isolation(config.model_argv, isolation)" in source


def test_trust_roots_are_pinned_public_material_only() -> None:
    roots = json.loads((ROOT / "config/issue_agent_trust_roots.json").read_text())
    assert roots["provisioned"] is True
    serialized = json.dumps(roots).lower()
    for forbidden in ("private", "secret", "-----begin"):
        assert forbidden not in serialized


def test_canary_workflow_grants_no_secrets_and_no_write_permission() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/hunter-issue682-hosted-canary.yml").read_text())
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["signed-kilo-canary"]
    assert job["permissions"] == {"actions": "read"}
    text = json.dumps(workflow)
    assert "secrets." not in text and "environment" not in job
