from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PREFLIGHT_GATES = (
    "Architecture Index Guard",
    "Artifact Guard",
    "Defect Prevention Guard",
    "Ruff",
    "Black",
    "Mypy",
    "Pytest",
)
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
LIFECYCLE_PATH = ROOT / "docs" / "DEFECT_PREVENTION_LIFECYCLE.json"
WRITE_POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
EXPECTED_STAGES = (
    "recorded",
    "regression-tested",
    "locally-enforced",
    "hosted-enforced",
    "merge-enforced",
    "prevented",
)
REQUIRED_ENFORCEMENT_FIELDS = ("local", "hosted", "merge", "recurrence")
ALLOWED_CODE_WRITE_PATHS = frozenset(
    {
        "local_git_push",
        "github_contents_api",
        "github_git_data_api",
        "api_only_agents",
    }
)


def _load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def validate_code_write_policy() -> list[str]:
    errors: list[str] = []
    policy = _load_object(WRITE_POLICY_PATH)

    if policy.get("version") != 1:
        errors.append("CODE_WRITE_POLICY version must be 1")

    paths = policy.get("code_write_paths")
    if not isinstance(paths, dict):
        return errors + ["CODE_WRITE_POLICY code_write_paths must be an object"]

    unknown_paths = sorted(set(paths) - ALLOWED_CODE_WRITE_PATHS)
    if unknown_paths:
        errors.append("CODE_WRITE_POLICY contains unrecognized code-write paths: " + ", ".join(unknown_paths))

    missing_paths = sorted(ALLOWED_CODE_WRITE_PATHS - set(paths))
    if missing_paths:
        errors.append("CODE_WRITE_POLICY is missing required code-write paths: " + ", ".join(missing_paths))

    local = paths.get("local_git_push")
    if not isinstance(local, dict) or local.get("allowed") is not True:
        errors.append("local_git_push must be an allowed code-write path")
    elif local.get("required_boundary") != ".githooks/pre-push":
        errors.append("local_git_push must require the repository pre-push boundary")

    for path_name in ("github_contents_api", "github_git_data_api"):
        entry = paths.get(path_name)
        if not isinstance(entry, dict) or entry.get("allowed") is not False:
            errors.append(f"{path_name} must be forbidden for code-changing candidates")

    api_agents = paths.get("api_only_agents")
    if not isinstance(api_agents, dict):
        errors.append("api_only_agents policy must be an object")
    elif api_agents.get("allowed_role") != "read-review-metadata-only":
        errors.append("API-only agents must be limited to read/review/metadata work")

    progression = policy.get("review_progression")
    if not isinstance(progression, dict):
        return errors + ["CODE_WRITE_POLICY review_progression must be an object"]
    if progression.get("unadmitted_head_state") != "draft":
        errors.append("unadmitted PR heads must be returned to Draft")
    if progression.get("auto_ready") is not False:
        errors.append("candidate admission must never auto-promote a PR to Ready")
    ready_requires = str(progression.get("ready_requires") or "")
    if "exact-head" not in ready_requires or "Pre-PR Preflight" not in ready_requires:
        errors.append("Ready progression must require successful exact-head Pre-PR Preflight")

    return errors


def validate_defect_prevention_lifecycle() -> list[str]:
    errors: list[str] = []
    registry = _load_object(REGISTRY_PATH)
    lifecycle = _load_object(LIFECYCLE_PATH)

    if lifecycle.get("version") != 1:
        errors.append("DEFECT_PREVENTION_LIFECYCLE version must be 1")

    stages = lifecycle.get("stages")
    if stages != list(EXPECTED_STAGES):
        errors.append("defect prevention stages must match the canonical ordered lifecycle")

    legacy = lifecycle.get("legacy_status_semantics")
    if not isinstance(legacy, dict) or legacy.get("guarded") != "detected":
        errors.append("legacy guarded status must map to detected, not prevented")

    defects = registry.get("defects")
    if not isinstance(defects, list):
        return errors + ["DEFECT_REGISTRY defects must be a list"]

    registry_ids: set[str] = set()
    statuses: set[str] = set()
    for index, defect in enumerate(defects):
        if not isinstance(defect, dict):
            errors.append(f"registry defect #{index} must be an object")
            continue
        defect_id = defect.get("id")
        status = defect.get("status")
        if not isinstance(defect_id, str) or not defect_id:
            errors.append(f"registry defect #{index} has invalid id")
            continue
        if defect_id in registry_ids:
            errors.append(f"duplicate defect id: {defect_id}")
        registry_ids.add(defect_id)
        if not isinstance(status, str) or not status:
            errors.append(f"{defect_id}: status must be a non-empty string")
        else:
            statuses.add(status)

    if isinstance(legacy, dict):
        for status in sorted(statuses):
            if status not in legacy:
                errors.append(f"legacy registry status has no prevention semantics: {status}")

    explicit = lifecycle.get("explicit_enforcement")
    if not isinstance(explicit, dict):
        return errors + ["explicit_enforcement must be an object"]

    for defect_id, evidence in explicit.items():
        if defect_id not in registry_ids:
            errors.append(f"explicit enforcement references unknown defect: {defect_id}")
            continue
        if not isinstance(evidence, dict):
            errors.append(f"{defect_id}: enforcement evidence must be an object")
            continue
        state = evidence.get("state")
        if state not in EXPECTED_STAGES:
            errors.append(f"{defect_id}: unknown enforcement state {state!r}")
            continue
        stage_index = EXPECTED_STAGES.index(state)
        if stage_index >= EXPECTED_STAGES.index("merge-enforced"):
            for field in REQUIRED_ENFORCEMENT_FIELDS:
                value = evidence.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{defect_id}: {state} requires non-empty {field} evidence")
        if state == "prevented" and evidence.get("recurrence") is None:
            errors.append(f"{defect_id}: prevented state requires recurrence escalation")

    errors.extend(validate_code_write_policy())
    return errors


def validate_candidate_preflight_definition(candidate_root: Path) -> list[str]:
    errors: list[str] = []
    workflow_path = candidate_root / ".github" / "workflows" / "hunter-pre-pr-preflight.yml"
    script_path = candidate_root / "scripts" / "hunter_pr_preflight.py"

    if not workflow_path.is_file():
        errors.append(f"candidate preflight workflow missing: {workflow_path}")
    else:
        content = workflow_path.read_text(encoding="utf-8")
        if "python scripts/hunter_pr_preflight.py" not in content:
            errors.append("candidate preflight workflow does not invoke scripts/hunter_pr_preflight.py")
        if "exit 0" in content:
            errors.append("candidate preflight workflow contains unconditional exit 0 bypass")

    if not script_path.is_file():
        errors.append(f"candidate preflight script missing: {script_path}")
    else:
        code = script_path.read_text(encoding="utf-8")
        if "exit 0" in code and "def run_quality_gates" not in code:
            errors.append("candidate preflight script contains unconditional exit 0")
        try:
            tree = ast.parse(code, filename=str(script_path))
            found_gates: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Tuple) or isinstance(node, ast.List):
                    if len(node.elts) >= 2:
                        first = node.elts[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            if first.value in REQUIRED_PREFLIGHT_GATES:
                                found_gates.add(first.value)
            missing = sorted(set(REQUIRED_PREFLIGHT_GATES) - found_gates)
            if missing:
                errors.append(
                    "candidate preflight script NORMAL_QUALITY_GATES missing required gates: " + ", ".join(missing)
                )
        except SyntaxError as exc:
            errors.append(f"candidate preflight script syntax error: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Hunter defect prevention lifecycle and candidate preflight validator")
    parser.add_argument(
        "--validate-candidate",
        type=Path,
        metavar="PATH",
        help="Validate proposed candidate preflight definition files at PATH without executing them.",
    )
    args = parser.parse_args()

    if args.validate_candidate:
        candidate_errors = validate_candidate_preflight_definition(args.validate_candidate)
        if candidate_errors:
            for message in candidate_errors:
                print(f"[Candidate Preflight Guard] FAIL: {message}")
            return 1
        print("[Candidate Preflight Guard] PASS: proposed candidate preflight definitions are complete and valid")
        return 0

    try:
        errors = validate_defect_prevention_lifecycle()
    except (OSError, json.JSONDecodeError, ValueError) as exception:
        print(f"[Defect Prevention Guard] FAIL: {exception}")
        return 2
    if errors:
        for message in errors:
            print(f"[Defect Prevention Guard] FAIL: {message}")
        return 1
    print("[Defect Prevention Guard] PASS: prevention lifecycle and code-write ingress policy are explicit and valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
