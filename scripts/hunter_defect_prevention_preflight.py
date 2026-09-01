from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal

import hunter_connector_write_ingress as ingress
from hunter_workflow_state import path_matches_scope_entry

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
# Files that define or bind the connector write ingress itself. If the grant let a
# connector rewrite these, the ingress could widen its own boundary, so the grant
# is invalid unless its prohibited scope covers every one of them.
CONNECTOR_AUTHORIZATION_RECEIPT_PATH = ".hunter/connector-write-authorization.json"
MUST_BE_PROHIBITED_FROM_CONNECTOR_WRITES = (
    ".githooks/pre-push",
    ".github/workflows/ci.yml",
    ".github/workflows/hunter-trusted-preflight-upgrade.yml",
    "scripts/hunter_pr_preflight.py",
    "scripts/hunter_connector_write_ingress.py",
    "scripts/hunter_governance_review_v2.py",
    "docs/CODE_WRITE_POLICY.json",
)
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

VALIDATED_CLASSIFICATIONS = frozenset(
    {
        "new_systemic_defect",
        "recurrence",
        "duplicate",
        "isolated_non_automatable",
    }
)
VALIDATED_RESOLUTION_STATES = frozenset({"unresolved", "resolved"})
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


def _is_non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


ReferenceRole = Literal["guard", "test"]
ReferenceSymbol = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _find_reference_symbol(tree: ast.Module, selector_parts: tuple[str, ...]) -> ReferenceSymbol | None:
    body = tree.body
    selected: ReferenceSymbol | None = None
    for index, name in enumerate(selector_parts):
        selected = next(
            (
                node
                for node in body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name
            ),
            None,
        )
        if selected is None:
            return None
        if index < len(selector_parts) - 1:
            if not isinstance(selected, ast.ClassDef):
                return None
            body = selected.body
    return selected


def _validate_reference_target(ref_str: str, *, role: ReferenceRole) -> str | None:
    """Statically validate one repository-local guard or pytest target reference."""
    if "::" not in ref_str:
        return "reference must use '<repo-relative-python-path>::<explicit-selector>'"

    file_part, selector = ref_str.split("::", 1)
    file_part = file_part.strip()
    selector = selector.strip()
    if not file_part:
        return "reference path must be non-empty"
    if not selector:
        return "reference selector must be non-empty"

    selector_parts = tuple(part.strip() for part in selector.split("::"))
    if any(not part or not part.isidentifier() for part in selector_parts):
        return f"selector {selector!r} must contain non-empty Python identifiers separated by '::'"

    relative_path = Path(file_part)
    if relative_path.is_absolute():
        return f"path {file_part!r} must be repository-relative"
    if ".." in relative_path.parts:
        return f"path {file_part!r} must not contain '..' traversal"
    if relative_path.suffix != ".py":
        return f"file {file_part!r} must be a Python file"

    resolved_root = ROOT.resolve()
    try:
        target_path = (resolved_root / relative_path).resolve(strict=True)
    except FileNotFoundError:
        return f"file {file_part!r} does not exist"
    except (OSError, RuntimeError) as exc:
        return f"failed to resolve file {file_part!r}: {exc}"

    try:
        resolved_relative_path = target_path.relative_to(resolved_root)
    except ValueError:
        return f"file {file_part!r} resolves outside the repository root"

    if not target_path.is_file():
        return f"file {file_part!r} is not a regular file"

    lexical_is_test = bool(relative_path.parts) and relative_path.parts[0] == "tests"
    resolved_is_test = bool(resolved_relative_path.parts) and resolved_relative_path.parts[0] == "tests"
    if role == "guard" and (lexical_is_test or resolved_is_test):
        return f"guard reference {file_part!r} must not target the tests tree"
    if role == "test":
        if not lexical_is_test or not resolved_is_test:
            return f"test reference {file_part!r} must target the tests tree"
        if not (relative_path.name.startswith("test_") or relative_path.name.endswith("_test.py")):
            return f"test reference {file_part!r} must use a pytest-discoverable filename"

    try:
        tree = ast.parse(target_path.read_text(encoding="utf-8"), filename=file_part)
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        return f"failed to parse AST for {file_part!r}: {exc}"

    selected = _find_reference_symbol(tree, selector_parts)
    if selected is None:
        return f"symbol {selector!r} not found in {file_part!r}"

    if role == "test":
        if len(selector_parts) == 1:
            if not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)) or not selected.name.startswith(
                "test_"
            ):
                return f"selector {selector!r} is not a pytest test function"
        elif len(selector_parts) == 2:
            class_name, method_name = selector_parts
            test_class = _find_reference_symbol(tree, (class_name,))
            if (
                not isinstance(test_class, ast.ClassDef)
                or not class_name.startswith("Test")
                or any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__"
                    for node in test_class.body
                )
                or not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef))
                or not method_name.startswith("test_")
            ):
                return f"selector {selector!r} is not a pytest Test*::test_* target"
        else:
            return f"selector {selector!r} is not a supported pytest test target"

    return None


def validate_reviewer_finding_dispositions() -> list[str]:
    errors: list[str] = []
    if not REVIEWER_DISPOSITIONS_PATH.is_file():
        return ["REVIEWER_FINDING_DISPOSITIONS.json is missing"]

    dispositions = _load_object(REVIEWER_DISPOSITIONS_PATH)
    if dispositions.get("version") != 1:
        errors.append("REVIEWER_FINDING_DISPOSITIONS version must be 1")

    findings = dispositions.get("findings")
    if not isinstance(findings, list):
        return errors + ["REVIEWER_FINDING_DISPOSITIONS findings must be a list"]

    registry = _load_object(REGISTRY_PATH)
    registry_defects = {
        defect["id"]: defect
        for defect in registry.get("defects", [])
        if isinstance(defect, dict) and isinstance(defect.get("id"), str)
    }

    lifecycle = _load_object(LIFECYCLE_PATH)
    explicit_enforcement = lifecycle.get("explicit_enforcement", {})

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
                    else:
                        guard_err = _validate_reference_target(str(guard_ref), role="guard")
                        if guard_err:
                            errors.append(f"{finding_id}: invalid guard_reference {guard_ref!r}: {guard_err}")
                        test_err = _validate_reference_target(str(test_ref), role="test")
                        if test_err:
                            errors.append(f"{finding_id}: invalid test_reference {test_ref!r}: {test_err}")
        elif mapped_id is not None:
            if not _is_non_empty_str(mapped_id) or mapped_id not in registry_defects:
                errors.append(f"{finding_id}: mapped_defect_id {mapped_id!r} not found in DEFECT_REGISTRY.json")

        if res_state == "resolved":
            if classification in {"new_systemic_defect", "duplicate", "recurrence"}:
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

    errors.extend(validate_connector_write_ingress(policy))
    return errors


def validate_connector_write_ingress(policy: dict[str, Any]) -> list[str]:
    """Validate the Issue #403 connector code-write ingress grant.

    The grant is an additional narrow ingress, so this guard exists to stop it
    from silently becoming a way around the boundaries it sits beside: it may not
    claim local pre-push equivalence, may not auto-promote or auto-merge, may not
    drop the exact base tip requirement, and -- because the connector writes as an
    account that is also a clone-capable signer -- an active grant may not leave
    admission resting on signature identity alone. Overlapping identities are
    therefore allowed and the separation is enforced as evidence: an active grant
    must require trusted hosted exact-head proof for every candidate.
    """
    errors: list[str] = []
    grant = policy.get("connector_write_ingress")
    if not isinstance(grant, dict):
        return ["CODE_WRITE_POLICY connector_write_ingress must be an object"]

    enabled = grant.get("enabled")
    if not isinstance(enabled, bool):
        errors.append("connector write ingress must declare an explicit boolean enabled state")
    if grant.get("local_pre_push_equivalent") is not False:
        errors.append("connector write ingress must never claim local pre-push equivalence")
    if grant.get("require_exact_base_tip") is not True:
        errors.append("connector write ingress must require an exact base tip")

    if not _is_non_empty_str(grant.get("required_capability")):
        errors.append("connector write ingress must declare a required writer capability")
    if grant.get("base_ref") != "main":
        errors.append("connector write ingress must be based on main")

    forbidden = grant.get("forbidden_target_refs")
    if not isinstance(forbidden, list) or "main" not in forbidden:
        errors.append("connector write ingress must forbid main as a write target")

    namespace = grant.get("branch_namespace")
    if not isinstance(namespace, str) or not namespace.strip() or not namespace.endswith("/"):
        errors.append("connector write ingress must declare a connector branch namespace ending in '/'")
        namespace = ""

    template = grant.get("branch_pattern_template")
    if not isinstance(template, str) or "{issue}" not in template:
        errors.append("connector write ingress branch pattern must bind the governing Issue")
    elif namespace and not template.startswith(namespace):
        # The namespace is what tells the trusted controller that a candidate is
        # required to carry an authorization receipt at all, so a pattern outside
        # it would leave connector branches unrecognised.
        errors.append("connector write ingress branch pattern must live inside the connector namespace")

    if grant.get("authorization_receipt_path") != CONNECTOR_AUTHORIZATION_RECEIPT_PATH:
        errors.append(f"connector write ingress must bind authorizations to {CONNECTOR_AUTHORIZATION_RECEIPT_PATH}")
    if enabled is True and not _is_non_empty_str(grant.get("authorization_binding")):
        errors.append("an active connector write ingress must state how authorizations bind to the exact candidate")
    if enabled is True and not _is_non_empty_str(grant.get("base_tip_authority")):
        errors.append("an active connector write ingress must state that the base tip comes from trusted state")

    allowed = grant.get("allowed_paths")
    if not isinstance(allowed, list) or not [item for item in allowed if _is_non_empty_str(item)]:
        errors.append("connector write ingress must declare a non-empty allowed path scope")
    prohibited = grant.get("prohibited_paths")
    if not isinstance(prohibited, list):
        errors.append("connector write ingress prohibited_paths must be a list")
    else:
        # Checked semantically rather than by literal entry text: ".githooks/",
        # ".githooks/*" and ".githooks/**" are the same scope statement, and a
        # guard that accepts only one spelling would reject valid policy.
        entries = [entry for entry in prohibited if _is_non_empty_str(entry)]
        for guarded in MUST_BE_PROHIBITED_FROM_CONNECTOR_WRITES:
            if not any(path_matches_scope_entry(guarded, entry) for entry in entries):
                errors.append(f"connector write ingress must prohibit writes to {guarded}")

    admission = grant.get("hosted_admission")
    if not isinstance(admission, dict):
        errors.append("connector write ingress must declare a hosted admission path")
    else:
        if admission.get("unadmitted_head_state") != "draft":
            errors.append("connector-written candidates must stay Draft until admitted")
        if admission.get("auto_ready") is not False:
            errors.append("connector write ingress must never auto-promote a PR to Ready")
        if admission.get("auto_merge") is not False:
            errors.append("connector write ingress must never enable automatic merge")
        if not _is_non_empty_str(admission.get("status_context_prefix")):
            errors.append("connector write ingress must name the trusted hosted exact-head proof status")
        if enabled is True and admission.get("require_for_all_candidates") is not True:
            # Signature identity cannot separate the channels while the grant is
            # active, so this declaration is the safety property that replaces
            # identity disjointness. An active grant without it would silently
            # reduce admission to signature-only.
            errors.append(
                "an active connector write ingress must require trusted hosted exact-head proof for all candidates"
            )

    if enabled is True and not _is_non_empty_str(grant.get("provenance_separation")):
        errors.append("an active connector write ingress must state how connector proof is separated from pre-push")

    writers = grant.get("authorized_writers")
    if not isinstance(writers, list) or not writers:
        return errors + ["connector write ingress must declare at least one writer grant"]

    bound = 0
    for index, entry in enumerate(writers):
        if not isinstance(entry, dict):
            errors.append(f"connector writer grant #{index} must be an object")
            continue
        if not _is_non_empty_str(entry.get("identity")):
            errors.append(f"connector writer grant #{index} must declare an identity")
        # One entry carries one capability. Since Issue #405 the grant may define
        # more than the required one, so the entry must name a capability the
        # grant actually defines rather than only the ordinary one.
        additional = grant.get("additional_capabilities")
        defined_capabilities = {grant.get("required_capability")}
        if isinstance(additional, dict):
            defined_capabilities |= set(additional)
        if entry.get("capability") not in defined_capabilities:
            errors.append(f"connector writer grant #{index} must carry a granted capability")
        login = entry.get("login")
        if not isinstance(login, str):
            errors.append(f"connector writer grant #{index} login must be a string")
            continue
        login = login.strip().lower()
        if not login:
            continue
        bound += 1

    if enabled is True and bound == 0:
        errors.append("connector write ingress is enabled but binds no writer identity")

    errors.extend(validate_governance_maintenance_capability(grant))
    return errors


def _gate_owned_scripts() -> tuple[str, ...]:
    """Repository scripts the canonical gate chain itself executes.

    Derived from the gate chain rather than restated as a literal list: a gate
    that gains, loses, or renames a script moves this set with it, so the
    root-of-trust floor cannot silently fall behind the authority it protects.
    """
    scripts: set[str] = set()
    for _name, command in TRUSTED_CANDIDATE_QUALITY_GATES:
        scripts.update(argument for argument in command if argument.startswith("scripts/") and argument.endswith(".py"))
    return tuple(sorted(scripts))


#: Authority files that decide whether a candidate may be written and admitted at
#: all, beyond the gate scripts derived above. A capability that could write these
#: could rewrite the authority evaluating it, so no capability on this ingress may
#: reach them and no governance scope may unblock them.
STATIC_ROOT_OF_TRUST = (
    ".githooks/pre-push",
    "scripts/hunter_pr_preflight.py",
    ".github/workflows/hunter-trusted-preflight-upgrade.yml",
    ".github/workflows/hunter-governance-review.yml",
    ".github/workflows/hunter-merge-readiness.yml",
    "scripts/hunter_connector_write_ingress.py",
    "scripts/hunter_governance_review_v2.py",
    "scripts/hunter_merge_readiness_v2.py",
    "scripts/hunter_workflow_state.py",
    "scripts/install_hunter_git_hooks.py",
    "docs/CODE_WRITE_POLICY.json",
)
GOVERNANCE_MAINTENANCE_CAPABILITY = "governance-maintenance"


def validate_governance_maintenance_capability(grant: dict[str, Any]) -> list[str]:
    """Validate the Issue #405 root-of-trust floor and governance-maintenance capability.

    Three properties carry the anti-self-escalation design, and each is checked
    structurally rather than by wording:

    1. the root-of-trust floor actually covers the authority that evaluates a
       candidate -- the gate chain's own scripts, the push boundary, the trusted
       hosted workflows, the authorizer/controllers, and this policy;
    2. every capability, ordinary and additional alike, prohibits that floor, so
       the floor is not a property of whichever capability remembered to declare
       it; and
    3. no named governance scope unblocks a floor path or reaches outside its
       capability's own allowed paths, and every authorized Issue names only
       scopes the grant defines.

    A grant that declares no additional capabilities is valid and unaffected: this
    is an added capability model, not a new requirement on the #403 grant.
    """
    errors: list[str] = []

    root_of_trust = grant.get("root_of_trust_paths")
    if not isinstance(root_of_trust, list) or not [item for item in root_of_trust if _is_non_empty_str(item)]:
        return ["connector write ingress must declare a non-empty root_of_trust_paths floor"]
    floor = [item for item in root_of_trust if _is_non_empty_str(item)]

    for required in _gate_owned_scripts() + STATIC_ROOT_OF_TRUST:
        if not any(path_matches_scope_entry(required, entry) for entry in floor):
            errors.append(f"connector write ingress root of trust must cover {required}")

    def prohibits_floor(prohibited: object, label: str) -> None:
        if not isinstance(prohibited, list):
            errors.append(f"{label} prohibited_paths must be a list")
            return
        entries = [item for item in prohibited if _is_non_empty_str(item)]
        for guarded in floor:
            if not any(path_matches_scope_entry(guarded, entry) for entry in entries):
                errors.append(f"{label} must prohibit root-of-trust path {guarded}")

    prohibits_floor(grant.get("prohibited_paths"), "connector write ingress")

    capabilities = grant.get("additional_capabilities", {})
    if not isinstance(capabilities, dict):
        return errors + ["connector write ingress additional_capabilities must be an object"]

    outer_bound: list[str] = []
    for name, entry in sorted(capabilities.items()):
        label = f"connector capability {name!r}"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        if name == grant.get("required_capability"):
            errors.append(f"{label} must not redefine the required capability")
        allowed = entry.get("allowed_paths")
        if not isinstance(allowed, list) or not [item for item in allowed if _is_non_empty_str(item)]:
            errors.append(f"{label} must declare a non-empty allowed path scope")
        else:
            outer_bound.extend(item for item in allowed if _is_non_empty_str(item))
        prohibits_floor(entry.get("prohibited_paths"), label)
        if not isinstance(entry.get("requires_issue_authorization"), bool):
            errors.append(f"{label} must declare an explicit Issue-authorization requirement")
        if name == GOVERNANCE_MAINTENANCE_CAPABILITY and entry.get("requires_issue_authorization") is not True:
            # Without it the capability would be a second, wider default rather
            # than work a governing Issue has to authorize.
            errors.append(f"{label} must require explicit per-Issue authorization")

    scopes = grant.get("governance_maintenance_scopes", {})
    if not isinstance(scopes, dict):
        return errors + ["connector write ingress governance_maintenance_scopes must be an object"]

    known_scopes: set[str] = set()
    for name, entry in sorted(scopes.items()):
        label = f"governance scope {name!r}"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        known_scopes.add(name)
        unblocked = entry.get("unblocked_paths")
        if not isinstance(unblocked, list) or not [item for item in unblocked if _is_non_empty_str(item)]:
            errors.append(f"{label} must declare the paths it unblocks")
            continue
        for path in (item for item in unblocked if _is_non_empty_str(item)):
            if any(ingress.scope_entries_overlap(path, guarded) for guarded in floor):
                errors.append(f"{label} must not unblock root-of-trust path {path}")
            if outer_bound and not any(path_matches_scope_entry(path, bound) for bound in outer_bound):
                errors.append(f"{label} unblocks {path}, which is outside every capability's allowed paths")

    authorizations = grant.get("governance_maintenance_authorizations", [])
    if not isinstance(authorizations, list):
        return errors + ["connector write ingress governance_maintenance_authorizations must be a list"]

    seen_issues: set[str] = set()
    for index, entry in enumerate(authorizations):
        label = f"governance authorization #{index}"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        issue = str(entry.get("issue", "")).strip().lstrip("#")
        if not issue.isdigit():
            errors.append(f"{label} must name one governing Issue number")
        elif issue in seen_issues:
            errors.append(f"{label} authorizes Issue #{issue} a second time")
        else:
            seen_issues.add(issue)
        if not _is_non_empty_str(entry.get("authorized_by")):
            errors.append(f"{label} must record who authorized it")
        entry_scopes = entry.get("scopes")
        if not isinstance(entry_scopes, list) or not [item for item in entry_scopes if _is_non_empty_str(item)]:
            errors.append(f"{label} must authorize at least one named scope")
            continue
        for scope_name in (item for item in entry_scopes if _is_non_empty_str(item)):
            if scope_name not in known_scopes:
                errors.append(f"{label} names unknown governance scope {scope_name!r}")

    defined = {grant.get("required_capability")} | set(capabilities)
    writers = grant.get("authorized_writers")
    if isinstance(writers, list):
        for index, entry in enumerate(writers):
            if isinstance(entry, dict) and entry.get("capability") not in defined:
                errors.append(f"connector writer grant #{index} carries a capability the grant does not define")

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
