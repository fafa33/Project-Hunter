from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
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
TRUSTED_CANDIDATE_QUALITY_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
    ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
    ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
    ("Ruff", ("ruff", "check", ".")),
    ("Black", ("black", "--check", "--diff", ".")),
    ("Mypy", ("mypy",)),
    ("Pytest", ("pytest",)),
)
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
LIFECYCLE_PATH = ROOT / "docs" / "DEFECT_PREVENTION_LIFECYCLE.json"
WRITE_POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
REVIEWER_DISPOSITIONS_PATH = ROOT / "docs" / "REVIEWER_FINDING_DISPOSITIONS.json"
VALIDATED_CLASSIFICATIONS = {
    "new_systemic_defect",
    "recurrence",
    "duplicate",
    "isolated_non_automatable",
}
VALIDATED_RESOLUTION_STATES = {"resolved", "unresolved"}
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
    errors.extend(validate_reviewer_finding_dispositions())
    return errors


def _is_non_empty_str(val: Any) -> bool:
    if type(val) is not str:
        return False
    return bool(val.strip())


def validate_reviewer_finding_dispositions() -> list[str]:
    errors: list[str] = []
    if not REVIEWER_DISPOSITIONS_PATH.is_file():
        return ["REVIEWER_FINDING_DISPOSITIONS.json is missing"]

    try:
        dispositions = _load_object(REVIEWER_DISPOSITIONS_PATH)
    except Exception as exc:
        return [f"REVIEWER_FINDING_DISPOSITIONS.json load error: {exc}"]

    if dispositions.get("version") != 1:
        errors.append("REVIEWER_FINDING_DISPOSITIONS version must be 1")

    findings = dispositions.get("findings")
    if not isinstance(findings, list):
        return errors + ["REVIEWER_FINDING_DISPOSITIONS findings must be a list"]

    try:
        registry = _load_object(REGISTRY_PATH)
        registry_defects = {
            defect["id"]: defect
            for defect in registry.get("defects", [])
            if isinstance(defect, dict) and isinstance(defect.get("id"), str)
        }
    except Exception:
        registry_defects = {}

    try:
        lifecycle = _load_object(LIFECYCLE_PATH)
        explicit_enforcement = lifecycle.get("explicit_enforcement", {})
        if not isinstance(explicit_enforcement, dict):
            explicit_enforcement = {}
    except Exception:
        explicit_enforcement = {}

    finding_ids: set[str] = set()

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"finding #{index} must be an object")
            continue

        finding_id = finding.get("id")
        if not _is_non_empty_str(finding_id):
            errors.append(f"finding #{index} has invalid id")
            continue
        if finding_id in finding_ids:
            errors.append(f"duplicate finding id: {finding_id}")
        finding_ids.add(finding_id)

        source = finding.get("source_provenance")
        if not isinstance(source, dict) or not _is_non_empty_str(source.get("reviewer")):
            errors.append(f"{finding_id}: source_provenance must be an object with a reviewer")

        val_state = finding.get("validation_state")
        if val_state not in {"validated", "unvalidated", "rejected"}:
            errors.append(f"{finding_id}: unknown validation_state {val_state!r}")
            continue

        if val_state != "validated":
            continue

        classification = finding.get("classification")
        if classification not in VALIDATED_CLASSIFICATIONS:
            errors.append(
                f"{finding_id}: validated finding requires classification in {sorted(VALIDATED_CLASSIFICATIONS)}"
            )

        res_state = finding.get("resolution_state")
        if res_state not in VALIDATED_RESOLUTION_STATES:
            errors.append(f"{finding_id}: resolution_state must be one of {sorted(VALIDATED_RESOLUTION_STATES)}")
            continue

        if res_state == "unresolved":
            errors.append(f"{finding_id}: validated substantive reviewer finding is unresolved")

        mapped_id = finding.get("mapped_defect_id")

        if classification == "duplicate":
            if not _is_non_empty_str(mapped_id):
                errors.append(f"{finding_id}: duplicate classification requires non-empty mapped_defect_id")
            elif mapped_id not in registry_defects:
                errors.append(f"{finding_id}: mapped_defect_id {mapped_id!r} not found in DEFECT_REGISTRY.json")

        elif classification == "recurrence":
            if not _is_non_empty_str(mapped_id):
                errors.append(f"{finding_id}: recurrence classification requires non-empty mapped_defect_id")
            elif mapped_id not in registry_defects:
                errors.append(f"{finding_id}: mapped_defect_id {mapped_id!r} not found in DEFECT_REGISTRY.json")
            else:
                enforcement_entry = explicit_enforcement.get(mapped_id, {})
                stage = enforcement_entry.get("state") if isinstance(enforcement_entry, dict) else None
                if stage in {"prevented", "merge-enforced"}:
                    evidence = finding.get("permanent_disposition_evidence")
                    guard_ref = finding.get("guard_reference")
                    test_ref = finding.get("test_reference")
                    if (
                        res_state != "resolved"
                        or not _is_non_empty_str(evidence)
                        or not _is_non_empty_str(guard_ref)
                        or not _is_non_empty_str(test_ref)
                    ):
                        errors.append(
                            f"{finding_id}: recurrence of {stage} defect {mapped_id} requires a resolved permanent disposition with non-empty string evidence, guard_reference, and test_reference"
                        )
        elif mapped_id is not None:
            if not _is_non_empty_str(mapped_id) or mapped_id not in registry_defects:
                errors.append(f"{finding_id}: mapped_defect_id {mapped_id!r} not found in DEFECT_REGISTRY.json")

        if res_state == "resolved":
            if classification == "new_systemic_defect":
                evidence = finding.get("permanent_disposition_evidence")
                guard_ref = finding.get("guard_reference")
                test_ref = finding.get("test_reference")
                if (
                    not _is_non_empty_str(evidence)
                    or not _is_non_empty_str(guard_ref)
                    or not _is_non_empty_str(test_ref)
                ):
                    errors.append(
                        f"{finding_id}: resolved new_systemic_defect finding requires non-empty string permanent_disposition_evidence, guard_reference, and test_reference"
                    )
            elif classification in {"duplicate", "recurrence"}:
                evidence = finding.get("permanent_disposition_evidence")
                if not _is_non_empty_str(evidence):
                    errors.append(
                        f"{finding_id}: resolved {classification} finding requires non-empty string permanent_disposition_evidence"
                    )

        if classification == "isolated_non_automatable":
            justification = finding.get("justification") or finding.get("permanent_disposition_evidence")
            bounded_control = finding.get("bounded_manual_control") or finding.get("permanent_disposition_evidence")
            if not _is_non_empty_str(justification):
                errors.append(f"{finding_id}: isolated_non_automatable finding requires explicit justification")
            if not _is_non_empty_str(bounded_control):
                errors.append(
                    f"{finding_id}: isolated_non_automatable finding requires bounded manual control statement"
                )
            if mapped_id and mapped_id in explicit_enforcement:
                stage = explicit_enforcement[mapped_id].get("state")
                if stage == "prevented":
                    errors.append(
                        f"{finding_id}: isolated_non_automatable finding cannot be falsely labeled prevented for defect {mapped_id}"
                    )

    return errors


def _module_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


def _run_preflight_uses_normal_gate_sequence(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "run_preflight":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if not isinstance(child.func, ast.Name) or child.func.id != "run_quality_gates":
                continue
            if any(isinstance(argument, ast.Name) and argument.id == "NORMAL_QUALITY_GATES" for argument in child.args):
                return True
    return False


def _workflow_has_unconditional_exit_before_preflight(content: str) -> bool:
    lines = content.splitlines()
    command_index = next(
        (index for index, line in enumerate(lines) if "python scripts/hunter_pr_preflight.py" in line),
        None,
    )
    if command_index is None:
        return False

    command_indent = len(lines[command_index]) - len(lines[command_index].lstrip())
    for line in lines[:command_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped != "exit 0":
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= command_indent:
            return True
    return False


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
        elif _workflow_has_unconditional_exit_before_preflight(content):
            errors.append("candidate preflight workflow contains unconditional exit 0 before preflight execution")

    if not script_path.is_file():
        errors.append(f"candidate preflight script missing: {script_path}")
    else:
        code = script_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(code, filename=str(script_path))
            gates_node = _module_assignment(tree, "NORMAL_QUALITY_GATES")
            if gates_node is None:
                errors.append("candidate preflight script defines no module-level NORMAL_QUALITY_GATES")
            else:
                try:
                    candidate_gates = ast.literal_eval(gates_node)
                except (TypeError, ValueError):
                    errors.append("candidate preflight script NORMAL_QUALITY_GATES is not a literal gate sequence")
                else:
                    normalized = tuple((str(name), tuple(command)) for name, command in candidate_gates)
                    if normalized != TRUSTED_CANDIDATE_QUALITY_GATES:
                        errors.append(
                            "candidate preflight script NORMAL_QUALITY_GATES must match the trusted required labels and commands"
                        )
            if not _run_preflight_uses_normal_gate_sequence(tree):
                errors.append("candidate preflight run_preflight does not execute NORMAL_QUALITY_GATES")
        except (SyntaxError, TypeError, ValueError) as exc:
            errors.append(f"candidate preflight script structure error: {exc}")

    return errors


def run_candidate_quality_gates(candidate_root: Path) -> int:
    """Execute the candidate through an immutable trusted gate list.

    The candidate dispatcher is deliberately not used as proof authority. A PR may
    edit hunter_pr_preflight.py, but it cannot remove or bypass a gate from this
    trusted controller because every required command is launched here directly.
    """
    if not candidate_root.is_dir():
        print(f"[Trusted Candidate Gates] FAIL: candidate root missing: {candidate_root}")
        return 2

    env = os.environ.copy()
    env["GITHUB_TOKEN"] = ""
    env["GH_TOKEN"] = ""

    for name, command in TRUSTED_CANDIDATE_QUALITY_GATES:
        printable = " ".join(command)
        print(f"[Trusted Candidate Gates] {name}: {printable}", flush=True)
        completed = subprocess.run(command, cwd=candidate_root, env=env, check=False)
        if completed.returncode != 0:
            print(
                f"[Trusted Candidate Gates] FAIL: {name} exited {completed.returncode}",
                flush=True,
            )
            return completed.returncode
        print(f"[Trusted Candidate Gates] PASS: {name}", flush=True)

    print("[Trusted Candidate Gates] PASS: immutable trusted gate chain executed", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hunter defect prevention lifecycle and candidate preflight validator")
    parser.add_argument(
        "--validate-candidate",
        type=Path,
        metavar="PATH",
        help="Validate proposed candidate preflight definition files at PATH without executing them.",
    )
    parser.add_argument(
        "--run-candidate-gates",
        type=Path,
        metavar="PATH",
        help="Execute every required candidate quality gate from the trusted controller.",
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

    if args.run_candidate_gates:
        return run_candidate_quality_gates(args.run_candidate_gates)

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
