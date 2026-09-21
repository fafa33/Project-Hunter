from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import hunter_connector_write_ingress as ingress
import hunter_pre_ready_review as pre_ready
import hunter_writer_provenance as provenance
import yaml
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
#: Issue #419: how the trusted default-branch lane distributes the candidate
#: suite across worker processes. Distribution controls only -- they change how
#: the suite is spread, never which tests are selected -- so the parallel proof
#: and the serial proof cover exactly the same tests. The gate commands above are
#: unchanged by parallelism: the lane is declared by the trusted workflow as
#: PYTEST_ADDOPTS, so the executed command line stays byte-identical and proof
#: scope cannot drift with it.
TRUSTED_PARALLEL_LANE: tuple[str, ...] = ("-n", "auto", "--dist", "loadfile")
#: The distribution the trusted default branch installs to honour that lane, and
#: the plugin it registers. Both are checked: an importable module with no
#: resolvable distribution is not a provisioned dependency, and a resolvable
#: distribution that will not import cannot register the options either.
TRUSTED_PARALLEL_RUNNER_DISTRIBUTION = "pytest-xdist"
TRUSTED_PARALLEL_RUNNER_PLUGIN = "xdist"
#: Where pytest reads a project's own ``addopts`` from. All four are checked:
#: a rule that covered only ``pyproject.toml`` would be satisfied by moving the
#: declaration one file sideways.
CANDIDATE_PYTEST_CONFIG_SOURCES: tuple[str, ...] = ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg")
#: Options a candidate may not put in its own ``addopts``, because the trusted
#: controller executes that configuration when it validates the candidate. The
#: worker options would demand a runner of an environment the candidate does not
#: provision (DFF-017); the selection options would quietly narrow the very suite
#: the trusted proof is supposed to be a proof of. Everything else -- reporting,
#: strictness, durations -- is the candidate's business and stays allowed.
FORBIDDEN_CANDIDATE_ADDOPTS: tuple[str, ...] = (
    "--collect-only",
    "--co",
    "--deselect",
    "--dist",
    "--exitfirst",
    "--failed-first",
    "--ff",
    "--ignore",
    "--ignore-glob",
    "--last-failed",
    "--lf",
    "--maxfail",
    "--new-first",
    "--nf",
    "--numprocesses",
    "--stepwise",
    "--sw",
    "-k",
    "-m",
    "-n",
    "-x",
)
#: Short options whose value may be attached rather than separated (``-nauto``).
_ATTACHABLE_SHORT_ADDOPTS: tuple[str, ...] = ("-k", "-m", "-n")
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
LIFECYCLE_PATH = ROOT / "docs" / "DEFECT_PREVENTION_LIFECYCLE.json"
WRITE_POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"
REVIEWER_DISPOSITIONS_PATH = ROOT / "docs" / "REVIEWER_FINDING_DISPOSITIONS.json"
BACKFILL_PATH = ROOT / "docs" / "HISTORICAL_DEFECT_BACKFILL.json"
BINDING_FIELD = provenance.BINDING_FIELD
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
BACKFILL_CLASSIFICATIONS = frozenset(
    {
        "confirmed",
        "duplicate",
        "false-positive",
        "style-non-defect",
        "obsolete",
        "infra-transient",
        "unknown",
    }
)
BACKFILL_STATUSES = frozenset({"guarded", "needs-guard", "duplicate", "false-positive", "obsolete"})
BACKFILL_SEVERITIES = frozenset({"P0", "P1", "P2", "P3", "none"})
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


# This digest is part of the trusted controller, independent of candidate records.
# Changing the accepted historical window requires review of this canonical pin.
TRUSTED_BACKFILL_MANIFEST_DIGEST = "891ca3fb246ecee536fb232d5fc69ae74dbd43a3554ff79a69aeff8e0d632b1c"


def historical_defect_digest(record: dict[str, Any]) -> str:
    fields = (
        "source_pr",
        "source_reference",
        "reviewer",
        "original_defect",
        "canonical_family",
        "dff_id",
        "classification",
        "severity",
        "status",
    )
    payload = {key: record.get(key) for key in fields}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def historical_manifest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "expected_count": len(records),
        "record_ids": sorted(str(r.get("id")) for r in records),
        "confirmed_ids": sorted(str(r.get("id")) for r in records if r.get("classification") == "confirmed"),
        "family_ids": sorted({str(r.get("dff_id")) for r in records if r.get("classification") == "confirmed"}),
        "included_pull_requests": sorted({r.get("source_pr") for r in records}, key=str),
        "pins": {
            str(r.get("id")): {
                **{key: r.get(key) for key in ("source_pr", "dff_id", "classification", "severity", "status")},
                "defect_digest": historical_defect_digest(r),
            }
            for r in records
        },
    }


def _historical_manifest_errors(backfill: dict[str, Any]) -> list[str]:
    manifest = backfill.get("manifest")
    if not isinstance(manifest, dict):
        return ["historical backfill manifest is missing or malformed"]
    errors = []
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != TRUSTED_BACKFILL_MANIFEST_DIGEST:
        errors.append("historical manifest differs from the trusted canonical manifest digest")
    records = backfill.get("records", [])
    expected = historical_manifest([r for r in records if isinstance(r, dict)])
    for key, value in expected.items():
        if manifest.get(key) != value:
            errors.append(f"historical manifest {key} mismatch: expected {manifest.get(key)!r}, found {value!r}")
    return errors


def _family_has_machine_gate(family: dict[str, Any]) -> bool:
    prevention = family.get("prevention", {})
    applicability = family.get("applicability", {})
    if not isinstance(prevention, dict) or not isinstance(applicability, dict):
        return False
    if prevention.get("boundary") not in MACHINE_BOUNDARIES:
        return False
    if family.get("lifecycle") not in {"locally-enforced", "hosted-enforced", "prevented"}:
        return False
    scopes = applicability.get("changed_paths")
    if not isinstance(scopes, list) or not scopes or not all(isinstance(p, str) and p.strip() for p in scopes):
        return False
    reference = prevention.get("guard_reference")
    if not isinstance(reference, str) or _validate_reference_target(reference, role="guard"):
        return False
    # A resolvable function alone does not prove that a gate invokes it for this family.
    binding = (family.get("id"), prevention.get("boundary"), reference)
    caller = MACHINE_FAMILY_BINDINGS.get(binding)
    if not caller or _validate_reference_target(caller, role="guard"):
        return False
    path, selector = caller.split("::")
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    function = next(
        (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == selector), None
    )
    callee = reference.split("::")[-1]
    return function is not None and any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == callee for n in ast.walk(function)
    )


# Audited invocation boundaries. Unknown or merely declared boundaries count as gaps.
MACHINE_FAMILY_BINDINGS: dict[tuple[str, str, str], str] = {
    (
        "DFF-012",
        "local-pre-push",
        "scripts/hunter_pre_push.py::_validate_receipt_freshness",
    ): "scripts/hunter_pre_push.py::enforce_pre_push",
    (
        "DFF-013",
        "hosted-gate",
        "scripts/hunter_governance_review_v2.py::verify_pre_ready_hostile_review",
    ): "scripts/hunter_governance_review_v2.py::candidate_admission",
    (
        "DFF-015",
        "local-pre-push",
        "scripts/hunter_defect_prevention_preflight.py::validate_recurring_defect_families",
    ): "scripts/hunter_defect_prevention_preflight.py::validate_defect_prevention_lifecycle",
    (
        "DFF-024",
        "local-pre-push",
        "scripts/hunter_defect_prevention_preflight.py::validate_review_after_remediation_boundary",
    ): "scripts/hunter_defect_prevention_preflight.py::validate_defect_prevention_lifecycle",
    (
        "DFF-025",
        "local-pre-push",
        "scripts/hunter_defect_prevention_preflight.py::validate_reviewer_unavailability_is_per_reviewer",
    ): "scripts/hunter_defect_prevention_preflight.py::validate_defect_prevention_lifecycle",
}


def historical_backfill_coverage(
    *, backfill: dict[str, Any] | None = None, registry: dict[str, Any] | None = None
) -> dict[str, int]:
    """Deterministic zero-recurrence coverage report over the curated backfill.

    Every confirmed historical defect must be guarded across all four layers --
    canonical family entry, regression coverage, applicability selector, and an
    enforcement gate -- and every duplicate must map to a family some confirmed
    record already guards. Records excluded from coverage (false positives,
    style findings, obsolete or transient dispositions) are reported separately
    so the audit trail cannot be mistaken for protection.
    """
    backfill = _load_object(BACKFILL_PATH) if backfill is None else backfill
    registry = _load_object(REGISTRY_PATH) if registry is None else registry

    records = backfill.get("records") if isinstance(backfill, dict) else []
    records = [record for record in records if isinstance(record, dict)]

    confirmed = [record for record in records if record.get("classification") == "confirmed"]
    duplicates = [record for record in records if record.get("classification") == "duplicate"]
    excluded_records = len(records) - len(confirmed) - len(duplicates)

    confirmed_families = sorted(
        {record.get("dff_id") for record in confirmed if _is_non_empty_str(record.get("dff_id"))}
    )
    families_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(registry, dict):
        raw_families = registry.get("families")
        if isinstance(raw_families, list):
            families_by_id = {
                family["id"]: family
                for family in raw_families
                if isinstance(family, dict) and isinstance(family.get("id"), str)
            }

    def family(identifier: str) -> dict[str, Any]:
        return families_by_id.get(identifier, {})

    families_with_regression = sum(
        1 for identifier in confirmed_families if family(identifier).get("regression_evidence")
    )
    families_with_selector = sum(
        1 for identifier in confirmed_families if family(identifier).get("applicability", {}).get("changed_paths")
    )
    families_with_gate = sum(1 for identifier in confirmed_families if _family_has_machine_gate(family(identifier)))

    guarded_confirmed = sum(1 for record in confirmed if record.get("status") == "guarded")
    confirmed_family_set = set(confirmed_families)
    duplicates_mapped = sum(1 for record in duplicates if record.get("dff_id") in confirmed_family_set)

    return {
        "confirmed_records": len(confirmed),
        "guarded_records": guarded_confirmed,
        "needs_guard_records": len(confirmed) - guarded_confirmed,
        "confirmed_families": len(confirmed_families),
        "families_with_regression": families_with_regression,
        "families_without_regression": len(confirmed_families) - families_with_regression,
        "families_with_selector": families_with_selector,
        "families_without_selector": len(confirmed_families) - families_with_selector,
        "families_with_gate": families_with_gate,
        "families_without_gate": len(confirmed_families) - families_with_gate,
        "duplicates_mapped": duplicates_mapped,
        "duplicates_unmapped": len(duplicates) - duplicates_mapped,
        "excluded_records": excluded_records,
    }


def validate_historical_defect_backfill() -> list[str]:
    """Structurally validate the machine-readable historical defect backfill.

    The backfill is the classified record of the historical reviewer-finding
    window that established the recurring-defect families: every confirmed real
    defect carries its canonical family, its fix, its regression test, its
    applicability selector and its enforcement gate, and no confirmed defect may
    remain unguarded. A duplicate must map to the one family a confirmed record
    already guards, and records excluded from coverage (false positives, style
    findings, obsolete or transient dispositions) must not point at a family, so
    catalogue drift cannot silently unguard a confirmed historical defect.
    """
    errors: list[str] = []
    if not BACKFILL_PATH.is_file():
        return ["HISTORICAL_DEFECT_BACKFILL.json is missing"]

    backfill = _load_object(BACKFILL_PATH)
    if backfill.get("version") != 1:
        errors.append("HISTORICAL_DEFECT_BACKFILL version must be 1")

    window = backfill.get("window")
    window_prs: list[int] = []
    if not isinstance(window, dict):
        errors.append("HISTORICAL_DEFECT_BACKFILL must declare a window object")
    else:
        pull_requests = window.get("pull_requests")
        if not isinstance(pull_requests, list) or not all(
            isinstance(number, int) and number > 0 for number in pull_requests
        ):
            errors.append("HISTORICAL_DEFECT_BACKFILL window pull_requests must be a list of positive integers")
        else:
            window_prs = pull_requests

    records = backfill.get("records")
    if not isinstance(records, list):
        return errors + ["HISTORICAL_DEFECT_BACKFILL records must be a list"]

    errors.extend(_historical_manifest_errors(backfill))

    registry = _load_object(REGISTRY_PATH)
    families_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(registry, dict) and isinstance(registry.get("families"), list):
        families_by_id = {
            family["id"]: family
            for family in registry["families"]
            if isinstance(family, dict) and isinstance(family.get("id"), str)
        }

    seen: set[str] = set()

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"backfill record #{index} must be an object")
            continue

        record_id = record.get("id")
        if not _is_non_empty_str(record_id):
            errors.append(f"backfill record #{index} has invalid id")
            continue
        if not BACKFILL_RECORD_ID_PATTERN.match(record_id):
            errors.append(f"backfill record #{index} id {record_id!r} must match HBF-NNN")
            continue
        if record_id in seen:
            errors.append(f"duplicate record id: {record_id}")
        seen.add(record_id)

        source_pr = record.get("source_pr")
        if not isinstance(source_pr, int) or source_pr <= 0:
            errors.append(f"{record_id}: source_pr must be a positive integer")
        elif window_prs and source_pr not in window_prs:
            errors.append(f"{record_id}: source_pr {source_pr} not declared in the window")

        for field in ("reviewer", "original_defect"):
            if not _is_non_empty_str(record.get(field)):
                errors.append(f"{record_id}: {field} must be a non-empty string")

        severity = record.get("severity")
        if severity not in BACKFILL_SEVERITIES:
            errors.append(f"{record_id}: unknown severity {severity!r}")
        classification = record.get("classification")
        if classification not in BACKFILL_CLASSIFICATIONS:
            errors.append(f"{record_id}: unknown classification {classification!r}")
        status = record.get("status")
        if status not in BACKFILL_STATUSES:
            errors.append(f"{record_id}: unknown status {status!r}")

        dff_id = record.get("dff_id")
        canonical_family = record.get("canonical_family")

        if classification == "confirmed":
            if status == "needs-guard":
                errors.append(f"{record_id}: confirmed real defect is not guarded (reported needs-guard)")
            if not _is_non_empty_str(dff_id) or dff_id not in families_by_id:
                errors.append(f"{record_id}: dff_id {dff_id!r} not found in DEFECT_REGISTRY families")
            if canonical_family != dff_id:
                errors.append(f"{record_id}: canonical_family must equal dff_id")
            if status == "guarded":
                if not _is_non_empty_str(record.get("fix_reference")):
                    errors.append(f"{record_id}: guarded confirmed defect requires a fix_reference")
                if not _is_non_empty_str(record.get("selector_reference")):
                    errors.append(f"{record_id}: guarded confirmed defect requires a selector_reference")
                regression_test = record.get("regression_test_reference")
                if not _is_non_empty_str(regression_test):
                    errors.append(f"{record_id}: guarded confirmed defect requires a regression_test_reference")
                else:
                    problem = _validate_reference_target(str(regression_test), role="test")
                    if problem:
                        errors.append(f"{record_id}: invalid regression_test_reference {regression_test!r}: {problem}")
                gate = record.get("gate_reference")
                if not _is_non_empty_str(gate):
                    errors.append(f"{record_id}: guarded confirmed defect requires a gate_reference")
                else:
                    problem = _validate_reference_target(str(gate), role="guard")
                    if problem:
                        errors.append(f"{record_id}: invalid gate_reference {gate!r}: {problem}")

        elif classification == "duplicate":
            if status != "duplicate":
                errors.append(f"{record_id}: duplicate classification requires status 'duplicate'")
            if canonical_family != dff_id:
                errors.append(f"{record_id}: canonical_family must equal dff_id")
            confirmed_families = {
                candidate.get("dff_id")
                for candidate in records
                if isinstance(candidate, dict)
                and candidate.get("classification") == "confirmed"
                and _is_non_empty_str(candidate.get("dff_id"))
            }
            if dff_id not in confirmed_families:
                errors.append(f"{record_id}: duplicate maps to {dff_id!r} which no confirmed record guards")

        else:
            if status != "obsolete":
                errors.append(f"{record_id}: {classification} record must carry status 'obsolete'")
            if dff_id != "none":
                errors.append(f"{record_id}: {classification} record must carry dff_id 'none', not {dff_id!r}")
            if canonical_family != "none":
                errors.append(f"{record_id}: {classification} record must carry canonical_family 'none'")

    coverage = historical_backfill_coverage(backfill=backfill, registry=registry)
    if coverage["needs_guard_records"]:
        errors.append("historical backfill leaves confirmed defects unguarded (needs_guard_records > 0)")
    if coverage["families_without_regression"]:
        errors.append(
            f"historical backfill has {coverage['families_without_regression']} confirmed family without regression coverage (families_without_regression > 0)"
        )
    if coverage["families_without_selector"]:
        errors.append(
            f"historical backfill has {coverage['families_without_selector']} confirmed family without an applicability selector (families_without_selector > 0)"
        )
    # Gate coverage is reported honestly; regression-only historical families remain
    # valid records and are not upgraded to machine enforcement by this backfill.
    if coverage["duplicates_unmapped"]:
        errors.append(
            f"historical backfill has {coverage['duplicates_unmapped']} duplicates not mapped to a confirmed family (duplicates_unmapped > 0)"
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
    if progression.get("requires_current_head_review_authority") is not True:
        errors.append("Ready progression must require current exact-head review authority")
    if progression.get("requires_current_head_codex_review") is True:
        errors.append("Ready progression must not hard-code Codex when governed failover is enabled")
    authority = progression.get("review_authority")
    if not isinstance(authority, dict):
        errors.append("Ready progression must declare its review-authority model")
    else:
        if authority.get("primary") != "codex":
            errors.append("the review-authority model must declare Codex as the primary review authority")
        if authority.get("fast_fallback") != "local-ollama":
            errors.append("the review-authority model must declare local-ollama as the fast fallback reviewer")
        reviewer_pool = authority.get("reviewer_pool")
        last_resort = reviewer_pool.get("last_resort") if isinstance(reviewer_pool, dict) else None
        if authority.get("fallback") != last_resort:
            errors.append("the review-authority fallback must match reviewer_pool.last_resort")
        if authority.get("fallback_requires_recorded_reason") is not True:
            errors.append(
                "fallback review authority must require a recorded reason and never skip the ordered reviewer pool silently"
            )
        gates = authority.get("fallback_requires_snapshot_gates")
        if not isinstance(gates, list) or not {
            "governance=success",
            "trusted_preflight=success",
            "unresolved_thread_count=0",
            "structured_evidence=complete",
        }.issubset({str(gate) for gate in gates}):
            errors.append("fallback review authority must require recorded green snapshot gates")
        # The ordered reviewer pool is the binding reviewer-ordering model: the
        # trusted local reviewer triages first, Codex is the primary review
        # authority, the remaining hosted reviewers follow it in priority order,
        # and the declared guard closes the pool as the last resort -- each with
        # a bounded, documented, machine-checkable acknowledgement and review
        # budget.
        # It is parsed by the same implementation the review verifier consumes,
        # so the guard and the verifier cannot drift into two readings of the
        # same pool.
        _pool, pool_error = pre_ready.load_reviewer_pool(policy)
        if pool_error:
            errors.append(pool_error)
        elif _pool is not None:
            # A reviewer may not be enabled in the ordered pool before the
            # collector can actually perform its declared trigger. Collection is
            # ordered and fail-closed: an unperformable trigger does not skip its
            # own reviewer, it aborts the whole walk, so every lower-priority
            # reviewer behind it is silently never attempted. The check imports
            # the collector's own dispatch registry rather than restating it, so
            # the guard and the implementation cannot drift apart.
            from hunter_reviewer_collector import unsupported_pool_triggers

            unsupported = unsupported_pool_triggers(_pool)
            if unsupported:
                errors.append(
                    "enabled reviewer(s) "
                    + ", ".join(unsupported)
                    + " declare a trigger method the trusted reviewer collector cannot perform"
                )
    finding_resolution = str(progression.get("finding_resolution") or "")
    if "structured evidence" not in finding_resolution or "regression test" not in finding_resolution:
        errors.append("finding resolution must require structured evidence and a committed regression test")

    errors.extend(validate_writer_identity_binding(policy))
    errors.extend(validate_connector_write_ingress(policy))
    return errors


def validate_writer_identity_binding(policy: dict[str, Any]) -> list[str]:
    """Validate the Issue #412 writer identity binding as a closed, exact allowlist.

    The binding is the thing that decides whether a commit may be published at
    all, so it is validated by the same authority that validates the ingress
    grant rather than only by the code that consumes it. Two properties carry the
    design and both are checked structurally:

    1. the match semantics stay exact and two-sided -- a binding that allowed
       substring matching, or that let one of author/committer stand in for the
       other, would admit an unrelated account; and
    2. every identity is self-consistent -- its canonical values, which are what
       an agent is told to configure before its first commit, are themselves
       bound values, so following the instruction cannot produce a commit the
       binding then rejects.

    Parsing is delegated to the canonical module so the guard and the enforcer
    cannot drift into two different readings of the same policy.
    """

    binding, error = provenance.parse_binding(policy)
    if binding is None:
        return [f"CODE_WRITE_POLICY {error}"]

    errors: list[str] = []
    raw = policy.get(BINDING_FIELD)
    raw = raw if isinstance(raw, dict) else {}
    for field in ("purpose", "match_semantics", "independence_semantics", "attribution_semantics", "fail_closed"):
        if not _is_non_empty_str(raw.get(field)):
            errors.append(f"{BINDING_FIELD} must declare {field}")

    enforcement = raw.get("enforcement")
    if not isinstance(enforcement, dict):
        errors.append(f"{BINDING_FIELD} must declare where the binding is enforced")
    else:
        for field in ("local_pre_push", "pre_commit_discovery", "governed_range"):
            if not _is_non_empty_str(enforcement.get(field)):
                errors.append(f"{BINDING_FIELD}.enforcement must declare {field}")

    signers = policy.get("ingress_provenance", {})
    authorized = signers.get("authorized_signers") if isinstance(signers, dict) else None
    if isinstance(authorized, list):
        allowed = {provenance.normalize_identity_value(str(item)) for item in authorized if _is_non_empty_str(item)}
        connector = policy.get("connector_write_ingress", {})
        writers = connector.get("authorized_writers") if isinstance(connector, dict) else None
        if isinstance(writers, list):
            allowed |= {
                provenance.normalize_identity_value(str(entry.get("login")))
                for entry in writers
                if isinstance(entry, dict) and _is_non_empty_str(entry.get("login"))
            }
        # A Git identity bound to a login that no ingress authorizes would be a
        # second, silent allowlist sitting beside the authorized one.
        unbound = sorted(
            identity.login
            for identity in binding.identities
            if provenance.normalize_identity_value(identity.login) not in allowed
        )
        if unbound:
            errors.append(
                f"{BINDING_FIELD} binds Git identities for logins no ingress authorizes: " + ", ".join(unbound)
            )

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


#: A repository-relative reference to a script, as it appears in a workflow `run:`
#: step, a git hook, or another script that shells out to it.
_SCRIPT_REFERENCE = re.compile(r"scripts/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py")
PUSH_BOUNDARY_HOOK = ".githooks/pre-push"
WORKFLOWS_DIRECTORY = ".github/workflows"
#: A workflow holding any of these write permissions can mint or clear a signal
#: that gates merge, so whatever it runs is authority. Read from the workflow's
#: own permissions rather than from a list of workflow names, so a new gating
#: workflow is covered the moment it is granted the permission.
GATING_WRITE_PERMISSIONS = frozenset({"statuses", "checks", "pull-requests"})
#: The authority files that are not Python and so cannot be reached by following
#: entrypoints: the push boundary itself, the tooling/gate configuration, and the
#: grant. Everything else on the required floor is derived.
STATIC_ROOT_OF_TRUST = (
    PUSH_BOUNDARY_HOOK,
    "pyproject.toml",
    "docs/CODE_WRITE_POLICY.json",
)


def _gate_owned_scripts() -> tuple[str, ...]:
    """Repository scripts the canonical gate chain itself executes."""
    scripts: set[str] = set()
    for _name, command in TRUSTED_CANDIDATE_QUALITY_GATES:
        scripts.update(argument for argument in command if argument.startswith("scripts/") and argument.endswith(".py"))
    return tuple(sorted(scripts))


def _gating_workflows() -> tuple[tuple[str, ...], list[str]]:
    """Workflow files able to mint or clear a merge-gating signal.

    Selected by the write permissions a workflow actually grants -- to statuses,
    checks, or pull requests -- rather than by name, so the set moves with the
    repository instead of with a list someone has to remember to update. An
    unreadable or unparseable workflow is an error rather than an omission: a
    floor derived from a directory that failed to read would be silently short.
    """
    directory = ROOT / WORKFLOWS_DIRECTORY
    if not directory.is_dir():
        return (), [f"{WORKFLOWS_DIRECTORY} is missing, so gating authority cannot be derived"]

    errors: list[str] = []
    gating: set[str] = set()
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{relative} is unreadable, so its gating authority cannot be derived ({exc})")
            continue
        if not isinstance(document, dict):
            errors.append(f"{relative} is not a workflow mapping, so its gating authority cannot be derived")
            continue

        blocks = [document.get("permissions")]
        jobs = document.get("jobs")
        if isinstance(jobs, dict):
            blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
        for block in blocks:
            if isinstance(block, dict) and any(
                str(block.get(name, "")).strip() == "write" for name in GATING_WRITE_PERMISSIONS
            ):
                gating.add(relative)
                break
    return tuple(sorted(gating)), errors


def _authority_entrypoints() -> tuple[tuple[str, ...], list[str]]:
    """Every script the gate chain, the push boundary, or a gating workflow runs."""
    errors: list[str] = []
    entrypoints: set[str] = set(_gate_owned_scripts())

    gating, gating_errors = _gating_workflows()
    errors.extend(gating_errors)

    for relative in (PUSH_BOUNDARY_HOOK, *gating):
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{relative} is unreadable, so the authority it runs cannot be derived ({exc})")
            continue
        entrypoints.update(_SCRIPT_REFERENCE.findall(text))

    return tuple(sorted(entry for entry in entrypoints if (ROOT / entry).is_file())), errors


def _local_script_module(name: str) -> str | None:
    """The `scripts/` file a module name resolves to, or None when it is external."""
    module = name.split(".")[0]
    for candidate in (f"scripts/{module}.py", f"scripts/{module}/__init__.py"):
        if (ROOT / candidate).is_file():
            return candidate
    return None


def _authority_closure() -> tuple[tuple[str, ...], list[str]]:
    """The authority entrypoints plus everything they depend on, as floor entries.

    Follows both imports and literal `scripts/...py` references, because an
    authority reaches its dependencies either way -- `.githooks/pre-push` shells
    out to a script that in turn shells out to the gate chain. A dependency of a
    thing that decides whether a candidate may be written or admitted is itself
    part of that decision, so the floor has to cover it: a capability able to
    rewrite the shared path-scope matcher would rewrite every scope check that
    uses it.
    """
    entrypoints, errors = _authority_entrypoints()

    seen: set[str] = set()
    queue = list(entrypoints)
    while queue:
        relative = queue.pop()
        if relative in seen:
            continue
        seen.add(relative)
        path = ROOT / relative
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{relative} could not be analysed, so its authority dependencies are unknown ({exc})")
            continue

        if relative.endswith("/__init__.py"):
            queue.extend(sibling.relative_to(ROOT).as_posix() for sibling in path.parent.rglob("*.py"))
        queue.extend(reference for reference in _SCRIPT_REFERENCE.findall(source) if (ROOT / reference).is_file())

        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                target = _local_script_module(name)
                if target is not None:
                    queue.append(target)

    # A package is protected as a directory: covering only its __init__ would
    # leave every submodule writable.
    floor = {
        relative.rsplit("/", 1)[0] + "/" if "/" in relative.removeprefix("scripts/") else relative for relative in seen
    }
    gating, _ = _gating_workflows()
    return tuple(sorted(floor | set(gating) | set(STATIC_ROOT_OF_TRUST))), errors


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

    required_floor, derivation_errors = _authority_closure()
    errors.extend(f"root-of-trust derivation failed: {reason}" for reason in derivation_errors)
    for required in required_floor:
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


FAMILY_ID_PATTERN = re.compile(r"\ADFF-[0-9]{3}\Z")
BACKFILL_RECORD_ID_PATTERN = re.compile(r"\AHBF-[0-9]{3,}(-[0-9]+)?\Z")
FAMILY_BOUNDARIES = frozenset({"review", "local-pre-push", "hosted-gate", "merge-gate"})
#: Boundaries that are an executing machine guard rather than a human pass. A
#: family claiming one has to name a guard symbol that actually resolves.
MACHINE_BOUNDARIES = frozenset({"local-pre-push", "hosted-gate", "merge-gate"})


def validate_recurring_defect_families(registry: dict[str, Any], lifecycle: dict[str, Any]) -> list[str]:
    """Structurally validate the machine-enforced recurring-defect family catalog.

    Issue #412 requires each family to carry enough structured information for a
    stable identity, its applicability, its prevention mechanism, its regression
    evidence, and its enforcement state -- and, explicitly, that a historical
    defect is not labelled guarded merely because some old test exists.

    So the enforcement stage is not a free-text claim: it must be supported by
    the evidence the family itself declares. Above ``recorded`` a resolvable
    regression test is required; above ``regression-tested`` a resolvable machine
    guard is required and the boundary must be a machine boundary; and
    ``prevented`` additionally requires a merge-gate boundary plus explicit
    lifecycle enforcement evidence for the family. A guard reference is validated
    the same way a reviewer disposition's is -- the file must exist and the symbol
    must be found in it -- so a family cannot point at a guard that was renamed
    or deleted.
    """

    errors: list[str] = []
    families = registry.get("families")
    if not isinstance(families, list) or not families:
        return ["DEFECT_REGISTRY families must be a non-empty list of recurring-defect families"]

    explicit = lifecycle.get("explicit_enforcement")
    explicit = explicit if isinstance(explicit, dict) else {}

    seen: set[str] = set()
    for index, family in enumerate(families):
        if not isinstance(family, dict):
            errors.append(f"registry family #{index} must be an object")
            continue
        family_id = family.get("id")
        if not isinstance(family_id, str) or not FAMILY_ID_PATTERN.match(family_id):
            errors.append(f"registry family #{index} must carry a stable DFF-NNN identity")
            continue
        if family_id in seen:
            errors.append(f"duplicate defect family id: {family_id}")
        seen.add(family_id)

        for field in ("title", "invariant"):
            if not _is_non_empty_str(family.get(field)):
                errors.append(f"{family_id}: {field} must be a non-empty string")

        applicability = family.get("applicability")
        if not isinstance(applicability, dict):
            errors.append(f"{family_id}: applicability must be an object")
        else:
            scope = applicability.get("changed_paths")
            if not isinstance(scope, list) or not [item for item in scope if _is_non_empty_str(item)]:
                errors.append(f"{family_id}: applicability must declare a non-empty changed_paths scope")
            if not _is_non_empty_str(applicability.get("rationale")):
                errors.append(f"{family_id}: applicability must explain why that scope is the applicable one")

        prevention = family.get("prevention")
        boundary = ""
        guard_reference = ""
        if not isinstance(prevention, dict):
            errors.append(f"{family_id}: prevention must be an object")
        else:
            if not _is_non_empty_str(prevention.get("mechanism")):
                errors.append(f"{family_id}: prevention must describe its mechanism")
            boundary = str(prevention.get("boundary") or "")
            if boundary not in FAMILY_BOUNDARIES:
                errors.append(f"{family_id}: prevention boundary must be one of {sorted(FAMILY_BOUNDARIES)}")
            raw_guard = prevention.get("guard_reference")
            if raw_guard is not None:
                if not _is_non_empty_str(raw_guard):
                    errors.append(f"{family_id}: guard_reference must be a non-empty string when present")
                else:
                    guard_reference = str(raw_guard)
                    problem = _validate_reference_target(guard_reference, role="guard")
                    if problem:
                        errors.append(f"{family_id}: invalid guard_reference {guard_reference!r}: {problem}")
                        guard_reference = ""

        evidence = family.get("regression_evidence")
        resolvable_tests = 0
        if not isinstance(evidence, list):
            errors.append(f"{family_id}: regression_evidence must be a list of pytest targets")
        else:
            for reference in evidence:
                if not _is_non_empty_str(reference):
                    errors.append(f"{family_id}: regression_evidence entries must be non-empty strings")
                    continue
                problem = _validate_reference_target(str(reference), role="test")
                if problem:
                    errors.append(f"{family_id}: invalid regression_evidence {reference!r}: {problem}")
                else:
                    resolvable_tests += 1

        sources = family.get("sources")
        if not isinstance(sources, list) or not [item for item in sources if _is_non_empty_str(item)]:
            errors.append(f"{family_id}: sources must record where the family was established")

        stage = family.get("lifecycle")
        if stage not in EXPECTED_STAGES:
            errors.append(f"{family_id}: lifecycle must be one of the canonical stages {list(EXPECTED_STAGES)}")
            continue
        stage_index = EXPECTED_STAGES.index(stage)

        if stage_index >= EXPECTED_STAGES.index("regression-tested") and resolvable_tests == 0:
            errors.append(
                f"{family_id}: {stage} requires at least one resolvable regression test; a family without one "
                "is only recorded"
            )
        if stage_index >= EXPECTED_STAGES.index("locally-enforced"):
            if not guard_reference:
                errors.append(
                    f"{family_id}: {stage} requires a resolvable guard_reference; an old test alone is detection, "
                    "not enforcement"
                )
            if boundary not in MACHINE_BOUNDARIES:
                errors.append(f"{family_id}: {stage} requires a machine prevention boundary, not {boundary!r}")
        if stage == "prevented":
            if boundary != "merge-gate":
                errors.append(f"{family_id}: prevented requires a merge-gate boundary, not {boundary!r}")
            entry = explicit.get(family_id)
            if not isinstance(entry, dict) or entry.get("state") != "prevented":
                errors.append(
                    f"{family_id}: prevented requires explicit merge-enforcement evidence in "
                    "DEFECT_PREVENTION_LIFECYCLE.json"
                )

    for family_id in sorted(set(explicit) & seen):
        entry = explicit[family_id]
        state = entry.get("state") if isinstance(entry, dict) else None
        declared = next(
            family.get("lifecycle") for family in families if isinstance(family, dict) and family.get("id") == family_id
        )
        if state != declared:
            errors.append(
                f"{family_id}: lifecycle stage {declared!r} disagrees with its explicit enforcement state {state!r}"
            )

    return errors


REVIEWER_COLLECTOR_WORKFLOW = ".github/workflows/hunter-reviewer-collector.yml"
GOVERNANCE_RECONCILE_WORKFLOW = ".github/workflows/hunter-governance-reconcile.yml"
TRUSTED_PREFLIGHT_WORKFLOW_NAME = "Hunter / Trusted Preflight Upgrade"


def validate_trusted_preflight_reconcile_wakeup() -> list[str]:
    """A completed trusted preflight must immediately wake review orchestration."""

    try:
        document = yaml.safe_load((ROOT / GOVERNANCE_RECONCILE_WORKFLOW).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"trusted-preflight reconcile workflow unavailable: {exc}"]
    if not isinstance(document, dict):
        return ["trusted-preflight reconcile workflow must be a YAML mapping"]
    triggers = document.get("on")
    # PyYAML's YAML 1.1 resolver treats the unquoted GitHub Actions key `on` as
    # boolean true. Accept that parsed key while still inspecting list structure.
    if triggers is None:
        triggers = document.get(True)
    workflow_run = triggers.get("workflow_run") if isinstance(triggers, dict) else None
    workflows = workflow_run.get("workflows") if isinstance(workflow_run, dict) else None
    types = workflow_run.get("types") if isinstance(workflow_run, dict) else None
    if not (
        isinstance(workflows, list)
        and TRUSTED_PREFLIGHT_WORKFLOW_NAME in workflows
        and isinstance(types, list)
        and "completed" in types
    ):
        return [
            "trusted preflight completion must trigger governance reconcile so exact-head review orchestration cannot stall"
        ]
    return []


def _module_ast(relative: str) -> tuple[ast.Module | None, str]:
    try:
        return ast.parse((ROOT / relative).read_text(encoding="utf-8")), ""
    except (OSError, SyntaxError) as exc:
        return None, f"{relative}: {exc}"


def _defined_functions(tree: ast.Module) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def _string_constants(tree: ast.Module) -> set[str]:
    """Every string that is actually evaluated, never a comment or a docstring.

    Comments are absent from the AST entirely and docstrings are excluded here,
    so a marker written into prose cannot satisfy a structural invariant. That
    distinction is the point: a contract satisfied by a comment is not enforced.
    """

    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    }


def validate_review_request_lifecycle_contract() -> list[str]:
    """Review-request lifecycle states must exist as executable structure.

    This guards the state-machine edges that previously allowed a stale or
    incomplete request to pass the local write boundary and then disappear
    inside a silent orchestrator no-op, surfacing later as the unrelated
    MISSING_REVIEW_AUTHORITY symptom.

    It is deliberately structural rather than a source-text search. A guard that
    matches exact source lines fails on a rename or a reformat that changes no
    behaviour -- false merge blockage is itself a defect -- and is satisfied by a
    comment that executes nothing. Functions are read from the AST and states
    from evaluated string constants, so neither failure mode is possible.
    """

    required_functions = {
        "scripts/hunter_pre_ready_review.py": ("verify_local_review_request",),
        "scripts/hunter_pre_push.py": ("enforce_declared_review_request",),
        "scripts/hunter_review_orchestrator.py": ("publish_prerequisite_block",),
    }
    errors: list[str] = []
    for relative, functions in required_functions.items():
        tree, error = _module_ast(relative)
        if tree is None:
            errors.append(f"review-request lifecycle source unavailable ({error})")
            continue
        defined = _defined_functions(tree)
        errors.extend(
            f"review-request lifecycle function missing from {relative}: {name}"
            for name in functions
            if name not in defined
        )

    try:
        import hunter_merge_readiness_v2 as readiness
        import hunter_review_orchestrator as orchestration
    except Exception as exc:  # pragma: no cover - import failure is itself the defect
        return [*errors, f"review-request lifecycle modules are unavailable: {type(exc).__name__}: {exc}"]

    # The classification itself, exercised rather than searched for. A state name
    # present in the source proves only that the token exists; it does not prove
    # which reason produces it, and a guard satisfied by a mention is satisfied by
    # a mention inside an unrelated string.
    expected = {
        "REVIEW_REQUEST_MISSING:detail": "WAITING_FOR_REVIEW_REQUEST",
        "TRUSTED_PREFLIGHT_PENDING:detail": "WAITING_FOR_PREREQUISITE",
        # An absent or failed exact-head proof is decided, not slow.
        "TRUSTED_PREFLIGHT_FAILURE:detail": "PREREQUISITE_BLOCKED",
        "TRUSTED_PREFLIGHT_MISSING:detail": "PREREQUISITE_BLOCKED",
        "REVIEW_REQUEST_INVALID:detail": "PREREQUISITE_BLOCKED",
        "REVIEW_REQUEST_MALFORMED:detail": "PREREQUISITE_BLOCKED",
        # An unrecognised reason must fail closed rather than inherit a wait.
        "SOME_FUTURE_PREREQUISITE:detail": "PREREQUISITE_BLOCKED",
    }
    for reason, state in expected.items():
        actual = orchestration.prerequisite_cycle_state(reason)
        if actual != state:
            errors.append(f"prerequisite reason {reason.split(':')[0]} must classify as {state}, not {actual}")

    # Only an absent request artifact is a transient missing request. Unreadable
    # JSON and unavailable evidence must not inherit its wait, or the transport's
    # fail-closed outcome would be republished as pending.
    read_codes = {
        "absent": "REVIEW_REQUEST_MISSING",
        "invalid": "REVIEW_REQUEST_MALFORMED",
        "unavailable": "REVIEW_REQUEST_UNAVAILABLE",
        "some_future_read_state": "REVIEW_REQUEST_MALFORMED",
    }
    for read_state, code in read_codes.items():
        actual_code = orchestration._request_read_code(read_state, None)
        if actual_code != code:
            errors.append(f"pre-ready read state {read_state} must report {code}, not {actual_code}")
        elif read_state != "absent" and orchestration.prerequisite_cycle_state(f"{actual_code}:d") != (
            "PREREQUISITE_BLOCKED"
        ):
            errors.append(f"pre-ready read state {read_state} must block rather than wait")
    if orchestration._request_read_code("present", ["not", "an", "object"]) != "REVIEW_REQUEST_MALFORMED":
        errors.append("a present pre-ready document that is not an object must report a malformed request")

    # Waiting states must be projected as pending, and a blocked prerequisite
    # must not be, or a decided failure would be reported as mere waiting.
    for state in ("WAITING_FOR_REVIEW_REQUEST", "WAITING_FOR_PREREQUISITE"):
        if readiness.review_wait_state(state, "") is None:
            errors.append(f"merge readiness must project {state} as pending rather than red")
    if readiness.review_wait_state("PREREQUISITE_BLOCKED", "") is not None:
        errors.append("merge readiness must not project PREREQUISITE_BLOCKED as pending: it is a decided block")

    # Prove the lifecycle edges are wired, not merely defined. A detached guard
    # function is dead code and must not satisfy DPM.
    pre_push_tree, pre_push_error = _module_ast("scripts/hunter_pre_push.py")
    if pre_push_tree is None:
        errors.append(f"review-request lifecycle source unavailable ({pre_push_error})")
    else:
        enforce = next(
            (
                n
                for n in ast.walk(pre_push_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "enforce_declared_review_request"
            ),
            None,
        )
        boundary = next(
            (n for n in ast.walk(pre_push_tree) if isinstance(n, ast.FunctionDef) and n.name == "enforce_pre_push"),
            None,
        )
        if enforce is None or not any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "review"
            and call.func.attr == "verify_local_review_request"
            for call in ast.walk(enforce)
        ):
            errors.append("enforce_declared_review_request must execute verify_local_review_request")
        if boundary is None or not any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "enforce_declared_review_request"
            for call in ast.walk(boundary)
        ):
            errors.append("enforce_pre_push must execute enforce_declared_review_request")

    # A prerequisite transition may not invent a dispatch identity, but it must
    # preserve one that already exists for this exact head. Otherwise a temporary
    # prerequisite regression erases idempotency state and permits duplicate work.
    tree, error = _module_ast("scripts/hunter_review_orchestrator.py")
    if tree is None:
        errors.append(f"review-request lifecycle source unavailable ({error})")
        return errors
    publisher = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "publish_prerequisite_block"
        ),
        None,
    )
    ensure_current = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "ensure_current"),
        None,
    )
    if publisher is None:
        return errors
    if not any(arg.arg == "previous" for arg in (*publisher.args.args, *publisher.args.kwonlyargs)):
        errors.append("publish_prerequisite_block must accept the previous cycle so dispatch identity cannot be erased")
    trigger_values = []
    for call in ast.walk(publisher):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "ReviewCycle":
            trigger_values.extend(kw.value for kw in call.keywords if kw.arg == "trigger_id")
    preserves_previous_trigger = any(
        any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "previous"
            and node.attr == "trigger_id"
            for node in ast.walk(value)
        )
        and any(isinstance(node, ast.Constant) and node.value is None for node in ast.walk(value))
        for value in trigger_values
    )
    if not preserves_previous_trigger:
        errors.append(
            "prerequisite publication must preserve previous.trigger_id and use None only when no dispatch exists"
        )
    if any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "current_run_id"
        for call in ast.walk(publisher)
    ):
        errors.append("a prerequisite publisher must never mint a collector trigger id from the current reconcile run")
    if ensure_current is None or not any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "publish_prerequisite_block"
        and any(
            keyword.arg == "previous"
            and any(isinstance(node, ast.Name) and node.id == "previous" for node in ast.walk(keyword.value))
            for keyword in call.keywords
        )
        for call in ast.walk(ensure_current)
    ):
        errors.append("ensure_current must pass the existing cycle into prerequisite publication")
    return errors


REVIEWER_COLLECTOR_SOURCE = "scripts/hunter_reviewer_collector.py"


def validate_reviewer_unavailability_is_per_reviewer() -> list[str]:
    """DFF-025: one undrivable reviewer must not strand the whole collection.

    Reviewer unavailability is decided in more than one place -- the dispatch,
    the adoption probe that precedes it, and the state read on an already
    dispatched run. When only some of those honoured the policy, a single
    rejection aborted collection before the remaining reviewers were invoked and
    a healthy candidate was reported as MISSING_REVIEW_AUTHORITY.

    The invariants are that the classification has exactly one decision point,
    that it refuses to classify exhausted infrastructure as unavailability, and
    that the pool loop isolates each reviewer's trigger.
    """

    tree, error = _module_ast(REVIEWER_COLLECTOR_SOURCE)
    if tree is None:
        return [f"reviewer collector source unavailable ({error})"]
    errors: list[str] = []

    predicate = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "reviewer_dispatch_unavailable"
        ),
        None,
    )
    if predicate is None:
        return [
            "reviewer unavailability must be decided by a single "
            "reviewer_dispatch_unavailable predicate so every call site agrees"
        ]
    if not any(isinstance(node, ast.Attribute) and node.attr == "GitHubUnavailable" for node in ast.walk(predicate)):
        errors.append(
            "reviewer_dispatch_unavailable must exclude GitHubUnavailable: exhausted "
            "retries (including the node-resolution 404) are fail-closed, never a skippable reviewer"
        )

    # Outside that predicate nothing may re-derive the policy from status codes.
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "reviewer_dispatch_unavailable":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Compare)
                and any(isinstance(op, ast.In | ast.NotIn) for op in inner.ops)
                and isinstance(inner.left, ast.Attribute)
                and inner.left.attr == "status_code"
            ):
                errors.append(
                    f"{node.name} re-derives reviewer unavailability from status_code; "
                    "call reviewer_dispatch_unavailable so the policy has one decision point"
                )

    collect = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "collect_attempts"),
        None,
    )
    if collect is None:
        errors.append("collect_attempts is missing from the reviewer collector")
        return errors

    # The outer loop must only catch the deliberate pre-trigger refusal.
    # Catching GitHubRequestError here would let a post-trigger persistence
    # failure escape from backend.trigger and be rewritten as trigger_id=0.
    for try_node in (node for node in ast.walk(collect) if isinstance(node, ast.Try) and node.handlers):
        for handler in try_node.handlers:
            if handler.type is None:
                errors.append(
                    "collect_attempts may not catch a bare exception when converting "
                    "reviewer unavailability; use ReviewerDispatchRefused so post-trigger "
                    "failures stay fail-closed"
                )
                break
            if isinstance(handler.type, ast.Name) and handler.type.id in {
                "Exception",
                "GitHubRequestError",
                "RuntimeError",
            }:
                errors.append(
                    "collect_attempts may not catch a broad exception when converting "
                    "reviewer unavailability; use ReviewerDispatchRefused so post-trigger "
                    "failures stay fail-closed"
                )
                break
            if isinstance(handler.type, ast.Attribute) and handler.type.attr == "GitHubRequestError":
                errors.append(
                    "collect_attempts may not catch GitHubRequestError when converting "
                    "reviewer unavailability; use ReviewerDispatchRefused so post-trigger "
                    "failures stay fail-closed"
                )
                break
        else:
            # A handler with no type check occurred above; stop scanning after reporting.
            continue
        break
    isolated = any(
        any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "trigger"
            for call in ast.walk(handler_parent)
        )
        for handler_parent in ast.walk(collect)
        if isinstance(handler_parent, ast.Try) and handler_parent.handlers
    )
    if not isolated:
        errors.append(
            "collect_attempts must invoke backend.trigger inside a try so one undrivable "
            "reviewer cannot end the collection before the rest of the pool is invoked"
        )

    # Once a durable trigger exists, provider/result/persistence failures are no
    # longer pre-trigger rejection. The backend must classify them internally and
    # must never let them escape to collect_attempts as a pre-trigger refusal.
    # Every post-trigger call site must either be inside a try that uses
    # reviewer_dispatch_unavailable, or must be routed through an internal helper
    # that already provides that protection.
    errors.extend(_check_backend_post_trigger_boundary(tree))
    return errors


def _check_backend_post_trigger_boundary(tree: ast.AST) -> list[str]:
    """Verify GitHubBackend.trigger protects every post-trigger persistence path."""

    errors: list[str] = []
    backend = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "GitHubBackend"),
        None,
    )
    trigger_method = (
        next(
            (node for node in ast.walk(backend) if isinstance(node, ast.FunctionDef) and node.name == "trigger"),
            None,
        )
        if backend is not None
        else None
    )
    if backend is None:
        return errors
    if trigger_method is None:
        return ["GitHubBackend.trigger is missing from the reviewer collector"]

    durable_trigger_calls = {"_post_trigger_comment", "_existing_trigger", "_adopt_workflow_run"}
    post_trigger_calls = {"_invoke_external", "_api_result", "_post_comment", "_post_result_comment"}
    protected_helpers: set[str] = set()
    for node in ast.walk(backend):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
            stmts = set()
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Try) and stmt.handlers:
                    stmts.add(stmt)
            for attempt in stmts:
                body_calls = {
                    call.func.attr
                    for call in ast.walk(attempt)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                }
                if "_post_comment" in body_calls and any(
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "reviewer_dispatch_unavailable"
                    for handler in attempt.handlers
                    for call in ast.walk(handler)
                ):
                    protected_helpers.add(node.name)

    durable_trigger_lines = [
        call.lineno
        for call in ast.walk(trigger_method)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in durable_trigger_calls
        and call.lineno is not None
    ]
    protected_post_trigger = False
    for attempt in (node for node in ast.walk(trigger_method) if isinstance(node, ast.Try)):
        body_calls = {
            call.func.attr
            for call in ast.walk(attempt)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }
        if not (body_calls & post_trigger_calls):
            continue
        if not (
            body_calls & durable_trigger_calls
            or (attempt.lineno is not None and any(line < attempt.lineno for line in durable_trigger_lines))
        ):
            continue
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "reviewer_dispatch_unavailable"
            for handler in attempt.handlers
            for call in ast.walk(handler)
        ):
            protected_post_trigger = True

    # A single protected path must not mask an unsafe sibling path. No helper
    # used after durable trigger creation may raise ReviewerDispatchRefused, and
    # trigger itself may not catch that pre-trigger-only exception around any
    # post-trigger operation. This is checked per helper/handler rather than by
    # one aggregate "protected_post_trigger" flag.
    post_trigger_helper_names = (post_trigger_calls | protected_helpers) - {"_post_trigger_comment"}
    helper_nodes = {
        node.name: node
        for node in ast.walk(backend)
        if isinstance(node, ast.FunctionDef) and node.name in post_trigger_helper_names
    }
    for helper_name, helper in helper_nodes.items():
        if any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "ReviewerDispatchRefused"
            for node in ast.walk(helper)
        ):
            errors.append(
                f"{helper_name} is post-trigger and may not raise ReviewerDispatchRefused; "
                "durable trigger identity must be preserved or the failure must propagate fail-closed"
            )
    for attempt in (node for node in ast.walk(trigger_method) if isinstance(node, ast.Try)):
        attempt_calls = {
            call.func.attr
            for call in ast.walk(attempt)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }
        if not (attempt_calls & post_trigger_helper_names):
            continue
        for handler in attempt.handlers:
            if isinstance(handler.type, ast.Name) and handler.type.id == "ReviewerDispatchRefused":
                errors.append(
                    "GitHubBackend.trigger catches ReviewerDispatchRefused around a post-trigger path; "
                    "that would erase durable trigger identity"
                )

    # Every raw _post_comment inside trigger must be replaced by a protected
    # helper; raw _post_comment calls are only safe when creating the trigger.
    raw_post_trigger_lines = [
        call.lineno
        for call in ast.walk(trigger_method)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_post_comment"
        and call.lineno is not None
    ]
    if raw_post_trigger_lines:
        # A raw _post_comment is allowed only when it is the durable trigger
        # creation itself. Verify each raw call is dominated by a prior durable
        # trigger creation; if not, it is an unprotected post-trigger persistence
        # path.
        for line in raw_post_trigger_lines:
            if not any(line > durable_line for durable_line in durable_trigger_lines):
                errors.append(
                    "GitHubBackend.trigger has a _post_comment call that is not protected by "
                    "reviewer_dispatch_unavailable and is not the durable trigger creation; "
                    "post-trigger persistence failures would erase the real trigger identity"
                )
                break

    if not protected_post_trigger:
        errors.append(
            "GitHubBackend.trigger must classify every post-trigger persistence failure "
            "(provider invocation, result/ack/adoption posting) without letting "
            "collect_attempts erase the durable trigger identity"
        )
    return errors


def validate_reviewer_pool_operational_admission() -> list[str]:
    """DFF-025: configured reviewers must be operationally admitted, not merely named.

    Hosted trigger creation is not acknowledgement, permanent API auth failures
    remain distinguishable from transient outages, and the declared deterministic
    last resort must be wired through trusted exhaustion verification.
    """

    collector_tree, error = _module_ast(REVIEWER_COLLECTOR_SOURCE)
    if collector_tree is None:
        return [f"reviewer collector source unavailable ({error})"]
    errors: list[str] = []
    backend = next(
        (node for node in ast.walk(collector_tree) if isinstance(node, ast.ClassDef) and node.name == "GitHubBackend"),
        None,
    )
    acknowledged = (
        next(
            (node for node in ast.walk(backend) if isinstance(node, ast.FunctionDef) and node.name == "acknowledged"),
            None,
        )
        if backend is not None
        else None
    )
    if acknowledged is None:
        errors.append("DFF-025: GitHubBackend.acknowledged is missing")
    else:
        constants = {node.value for node in ast.walk(acknowledged) if isinstance(node, ast.Constant)}
        if not {"github-pr-comment", "github-review-request"} <= constants:
            errors.append("DFF-025: hosted comment/review-request creation must not count as reviewer acknowledgement")

    invoke = (
        next(
            (
                node
                for node in ast.walk(backend)
                if isinstance(node, ast.FunctionDef) and node.name == "_invoke_external"
            ),
            None,
        )
        if backend is not None
        else None
    )
    if invoke is None:
        errors.append("DFF-025: external reviewer invocation is missing")
    else:
        constants = {node.value for node in ast.walk(invoke) if isinstance(node, ast.Constant)}
        if not {401, 403, "permanent", "transient", "failure_class"} <= constants:
            errors.append(
                "DFF-025: API auth/permission failures must remain durably distinguishable from transient outages"
            )

    governance_tree, governance_error = _module_ast("scripts/hunter_governance_review_v2.py")
    if governance_tree is None:
        errors.append(f"governance source unavailable ({governance_error})")
    else:
        verify = next(
            (
                node
                for node in ast.walk(governance_tree)
                if isinstance(node, ast.FunctionDef) and node.name == "verify_pre_ready_hostile_review"
            ),
            None,
        )
        calls = (
            {
                node.func.id
                for node in ast.walk(verify)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            if verify is not None
            else set()
        )
        last_resort_refs = (
            [
                node
                for node in ast.walk(verify)
                if isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "pool"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "last_resort"
            ]
            if verify is not None
            else []
        )
        if "load_exhaustion" not in calls or not last_resort_refs:
            errors.append(
                "DFF-025: declared last_resort must be admitted only through trusted load_exhaustion verification"
            )
    return errors


def validate_reviewer_trigger_identity_preservation() -> list[str]:
    """DFF-026: a real trigger identity must survive post-trigger failures.

    Once a durable trigger comment exists, any later failure -- provider
    invocation, result/ack posting, or adoption of an existing trigger -- must
    keep that identity. Converting those failures into a zero-id unavailable
    record lets failover proceed without verifiable evidence.
    """

    tree, error = _module_ast(REVIEWER_COLLECTOR_SOURCE)
    if tree is None:
        return [f"reviewer collector source unavailable ({error})"]
    errors: list[str] = []
    if not next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "ReviewerDispatchRefused"),
        None,
    ):
        errors.append(
            "a dedicated ReviewerDispatchRefused exception must separate pre-trigger "
            "refusals from post-trigger failures"
        )
    collect = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "collect_attempts"),
        None,
    )
    if collect is not None:
        for try_node in (node for node in ast.walk(collect) if isinstance(node, ast.Try) and node.handlers):
            for handler in try_node.handlers:
                if handler.type is None or not (
                    isinstance(handler.type, ast.Name) and handler.type.id == "ReviewerDispatchRefused"
                ):
                    errors.append(
                        "collect_attempts must catch only ReviewerDispatchRefused for pre-trigger "
                        "refusals; any other exception escaping backend.trigger would erase a real trigger identity"
                    )
                    break
            else:
                continue
            break
    errors.extend(_check_backend_post_trigger_boundary(tree))
    return errors


def validate_orchestration_status_description_is_bounded() -> list[str]:
    """DFF-027: published orchestration statuses must always fit GitHub's field limit.

    The status description carries state, trigger identity, configuration digest
    and remediation generation. Any of these can grow large enough to exceed
    GitHub's 140-character commit-status description limit, which would make the
    blocked/waiting state unpublishable at the moment it matters most.
    """

    try:
        import hunter_review_orchestrator as orchestration
    except Exception as exc:  # pragma: no cover
        return [f"orchestration module unavailable: {type(exc).__name__}: {exc}"]

    errors: list[str] = []
    max_state = "PREREQUISITE_BLOCKED"
    provider = "REVIEW_REQUEST_UNAVAILABLE"
    digest = "d" * 64
    generation = "g" * 16
    for trigger in (0, 1, 999_999_999_999, 10**15):
        cycle = orchestration.ReviewCycle(
            pr_number=1,
            head_sha="a" * 40,
            state=max_state,
            provider_id=provider,
            trigger_id=trigger,
            started_at="2026-09-19T00:00:00Z",
            config_digest=digest,
            generation_id=generation,
        )
        description = orchestration.cycle_status_description(cycle)
        if len(description) > 140:
            errors.append(
                f"orchestration status description exceeds 140 characters for trigger_id={trigger}: "
                f"{len(description)}"
            )
        parts = description.split("|")
        if len(parts) != 5:
            errors.append(f"orchestration status description is not five-field parseable: {description}")
        elif orchestration._decode_trigger_id(parts[2]) != (cycle.trigger_id or None):
            errors.append(f"orchestration status description round-trip failed for trigger_id={trigger}")
    return errors


def validate_review_after_remediation_boundary() -> list[str]:
    """DFF-024: a remediated exact head must still be able to be reviewed again.

    Issue #461 / PR #473: every reviewer invocation was keyed by head and claims
    alone, and the collector run correlating those invocations was keyed the same
    way. Once a cycle had spent them, resolving the blocking findings it produced
    could not buy another review of that immutable head, so the candidate was
    stuck reporting MISSING_REVIEW_AUTHORITY with nothing left that could clear
    it. The repair is a remediation generation, and this guard checks the three
    properties that make it both effective and safe -- as semantic invariants of
    the live implementation rather than as wording:

    1. the generation actually separates identities, so a new generation buys a
       real new invocation and an unchanged one buys nothing;
    2. the generation survives a round trip through the trigger a reviewer
       receives, so the collector can tell its own invocations apart; and
    3. it is derived from trusted state and bounded, so it can never become an
       unbounded reviewer retry.
    """

    errors: list[str] = []
    try:
        import hunter_review_orchestrator as orchestration
        import hunter_reviewer_collector as collector
    except Exception as exc:  # pragma: no cover - import failure is itself the defect
        return [f"review-after-remediation modules are unavailable: {type(exc).__name__}: {exc}"]

    head, claims = "a" * 40, "d" * 64
    first, second = "0" * 15 + "1", "0" * 15 + "2"
    base = orchestration.BASE_GENERATION_ID

    # (1) identity separation
    if collector.invocation_key(head, claims, "codex", 1, base) != collector.invocation_key(head, claims, "codex", 1):
        errors.append("the base remediation generation must reproduce the pre-remediation invocation identity")
    keys = {collector.invocation_key(head, claims, "codex", 1, item) for item in (base, first, second)}
    if len(keys) != 3:
        errors.append("a reviewer invocation identity must separate remediation generations")
    if collector.invocation_key(head, claims, "codex", 1, first) != collector.invocation_key(
        head, claims, "codex", 1, first
    ):
        errors.append("a reviewer invocation identity must be stable within one remediation generation")
    names = {orchestration.collector_run_name(1, head, item) for item in (base, first, second)}
    if len(names) != 3 or orchestration.collector_run_name(1, head, base) != orchestration.collector_run_name(1, head):
        errors.append("the collector run name must correlate one run per remediation generation")

    # A separated identity function is not enough: the seam that actually adopts
    # an already-posted reviewer trigger has to consume it, or a superseded
    # generation's trigger is adopted forever and no reviewer is asked again.
    def marker(generation: str) -> str:
        backend = collector.GitHubBackend("owner/repo", "", 1, head, claims, 1, 1, generation)
        return backend.invocation_marker({"id": "codex"}, 1)

    if len({marker(item) for item in (base, first, second)}) != 3 or marker(first) != marker(first):
        errors.append("reviewer trigger adoption must separate remediation generations")

    # (2) trigger round trip
    native_agent = {
        "id": "codex",
        "trigger_method": "github-pr-comment:@codex review",
        "github_login": "chatgpt-codex-connector[bot]",
    }
    api_agent = {"id": "gemini", "trigger_method": "api:gemini"}
    for parse, body in (
        (collector.parse_native_trigger, collector.trigger_body(head, claims, native_agent, 1, 1, 1, first)),
        (collector.parse_api_trigger, collector.api_trigger_body(head, claims, api_agent, 1, 1, 1, first)),
    ):
        parsed = parse(body)
        if not isinstance(parsed, dict) or parsed.get("remediation_generation_id") != first:
            errors.append("a reviewer trigger must carry the remediation generation it was minted for")
        if parse(body.replace(first, second)) is not None:
            errors.append("a reviewer trigger must not verify against a substituted remediation generation")

    # (3) trusted derivation and bounds
    resolved = orchestration.BlockingThread("PRRT_1", 1, "2026-09-18T01:35:24Z", True)
    other = orchestration.BlockingThread("PRRT_2", 2, "2026-09-18T01:35:24Z", True)
    identities = {
        orchestration.remediation_generation_id(head, claims, ()),
        orchestration.remediation_generation_id(head, claims, (resolved,)),
        orchestration.remediation_generation_id(head, claims, (resolved, other)),
        orchestration.remediation_generation_id("b" * 40, claims, (resolved,)),
        orchestration.remediation_generation_id(head, "e" * 64, (resolved,)),
    }
    if len(identities) != 5:
        errors.append("a remediation generation must bind the exact head, the claims digest and the resolved set")
    if orchestration.remediation_generation_id(head, claims, ()) != orchestration.remediation_generation_id(
        head, claims, []
    ):
        errors.append("a remediation generation must be a deterministic function of its evidence")
    if not isinstance(orchestration.MAX_REMEDIATION_GENERATIONS, int) or (
        orchestration.MAX_REMEDIATION_GENERATIONS < 1
    ):
        errors.append("remediation generations must be bounded by a positive budget")
    cycle = orchestration.ReviewCycle(1, head, "WAITING_FOR_REVIEWER", "", 1, "", "d" * 64, first)
    if orchestration.remediation_generation_admissible("owner/repo", "", cycle, first) is not False:
        errors.append("an unchanged remediation generation must never authorise another reviewer invocation")

    # The generation must be derived where the dispatch decision is made. A
    # derivation nothing consumes would leave the orchestrator dispatching the
    # base generation forever, with every identity above still perfectly correct.
    try:
        tree = ast.parse((ROOT / "scripts/hunter_review_orchestrator.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [*errors, f"trusted review orchestrator source is unreadable: {type(exc).__name__}: {exc}"]
    ensure_current = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "ensure_current"),
        None,
    )
    if ensure_current is None or not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "current_remediation_generation"
        for node in ast.walk(ensure_current)
    ):
        errors.append("the trusted orchestrator must derive the remediation generation where it decides to dispatch")

    workflow = ROOT / REVIEWER_COLLECTOR_WORKFLOW
    try:
        definition = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [*errors, f"reviewer collector workflow is unreadable: {type(exc).__name__}: {exc}"]
    triggers = definition.get(True) if isinstance(definition, dict) else None
    inputs = ((triggers or {}).get("workflow_dispatch") or {}).get("inputs") or {}
    if "generation_id" not in inputs:
        errors.append("the reviewer collector workflow must accept the remediation generation it is dispatched for")
    steps = [
        step
        for job in (definition.get("jobs") or {}).values()
        if isinstance(job, dict)
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]
    if not any("--generation" in str(step.get("run") or "") for step in steps):
        errors.append("the reviewer collector workflow must forward the remediation generation to the collector")
    return errors


def _authority_cutover_contract_errors(workflows: dict[str, str]) -> list[str]:
    errors: list[str] = []
    review = workflows.get("hunter-governance-review.yml", "")
    reconcile = workflows.get("hunter-governance-reconcile.yml", "")
    readiness = workflows.get("hunter-merge-readiness.yml", "")
    admission = workflows.get("hunter-candidate-admission.yml", "")
    bridge = "bootstrap_external_review_469.py"

    for name, text in workflows.items():
        if bridge in text:
            errors.append(f"DFF-028: legacy bootstrap bridge remains runtime-reachable from {name}")

    if "hunter_governance_review_v2.py" not in review:
        errors.append("DFF-028: governance review must invoke the canonical governance controller")
    if "hunter_governance_review_v2.py" not in reconcile:
        errors.append("DFF-028: governance reconcile must invoke the canonical governance controller")
    if "python scripts/hunter_merge_readiness_v2.py" not in readiness:
        errors.append("DFF-028: merge readiness must invoke the canonical readiness projector")
    if "engine/scripts/hunter_candidate_admission.py" not in admission:
        errors.append("DFF-028: candidate admission must invoke the canonical admission controller")
    if "python scripts/hunter_review_orchestrator.py ensure" not in reconcile:
        errors.append("DFF-028: privileged reviewer orchestration must remain on reconcile")
    if "python scripts/hunter_review_orchestrator.py ensure" in review:
        errors.append("DFF-028: PR-reachable governance review may not dispatch reviewer orchestration")
    if "actions: write" in review:
        errors.append("DFF-028: PR-reachable governance review may not have actions: write")
    if "actions: write" not in reconcile:
        errors.append("DFF-028: trusted reconcile must retain actions: write for reviewer dispatch")
    return errors


def validate_authority_cutover_single_owner() -> list[str]:
    workflow_root = ROOT / ".github" / "workflows"
    names = (
        "hunter-governance-review.yml",
        "hunter-governance-reconcile.yml",
        "hunter-merge-readiness.yml",
        "hunter-candidate-admission.yml",
    )
    workflows: dict[str, str] = {}
    errors: list[str] = []
    for name in names:
        path = workflow_root / name
        try:
            workflows[name] = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"DFF-028: cannot read {name}: {exc}")
    if errors:
        return errors
    return _authority_cutover_contract_errors(workflows)


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

    family_semantics = lifecycle.get("family_lifecycle_semantics")
    if not isinstance(family_semantics, dict):
        errors.append("defect prevention lifecycle must declare family_lifecycle_semantics")
    else:
        for field in ("authority", "purpose", "rule", "guard"):
            if not _is_non_empty_str(family_semantics.get(field)):
                errors.append(f"family_lifecycle_semantics must declare {field}")
        guard_reference = family_semantics.get("guard")
        if _is_non_empty_str(guard_reference):
            problem = _validate_reference_target(str(guard_reference), role="guard")
            if problem:
                errors.append(f"family_lifecycle_semantics guard {guard_reference!r} is invalid: {problem}")

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

    errors.extend(validate_recurring_defect_families(registry, lifecycle))
    errors.extend(validate_trusted_preflight_reconcile_wakeup())
    errors.extend(validate_review_request_lifecycle_contract())
    errors.extend(validate_reviewer_unavailability_is_per_reviewer())
    errors.extend(validate_reviewer_pool_operational_admission())
    errors.extend(validate_reviewer_trigger_identity_preservation())
    errors.extend(validate_orchestration_status_description_is_bounded())
    errors.extend(validate_review_after_remediation_boundary())
    errors.extend(validate_authority_cutover_single_owner())
    errors.extend(validate_code_write_policy())
    errors.extend(validate_reviewer_finding_dispositions())
    errors.extend(validate_historical_defect_backfill())
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


def _forbidden_addopt(token: str) -> str | None:
    """The forbidden option ``token`` spells, or ``None``.

    Matched on the option itself rather than on the raw text, so ``-k slow``,
    ``-kslow``, ``--maxfail=1`` and ``--ignore tests/`` are all the same finding,
    and a value that merely looks like an option (``-m`` inside a quoted marker
    expression) is not one -- the caller has already tokenized with shell rules,
    so only actual argument positions reach here.
    """
    if not token.startswith("-"):
        return None
    option = token.split("=", 1)[0]
    if option in FORBIDDEN_CANDIDATE_ADDOPTS:
        return option
    for short in _ATTACHABLE_SHORT_ADDOPTS:
        if token.startswith(short) and len(token) > len(short):
            return short
    return None


def _declared_candidate_addopts(candidate_root: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Every ``addopts`` a candidate declares, with where it declared it.

    Reads the four sources pytest itself reads. An unreadable or malformed one is
    an error rather than an omission: a rule derived from a file that failed to
    parse would silently pass everything it could not see.
    """
    declarations: list[tuple[str, str]] = []
    errors: list[str] = []
    for relative in CANDIDATE_PYTEST_CONFIG_SOURCES:
        path = candidate_root / relative
        if not path.is_file():
            continue
        try:
            if relative == "pyproject.toml":
                document = tomllib.loads(path.read_text(encoding="utf-8"))
                section = document.get("tool", {}).get("pytest", {}).get("ini_options", {})
                sections = {"[tool.pytest.ini_options]": section} if isinstance(section, dict) else {}
            else:
                parser = configparser.ConfigParser()
                parser.read_string(path.read_text(encoding="utf-8"))
                sections = {
                    f"[{name}]": dict(parser[name]) for name in ("pytest", "tool:pytest") if parser.has_section(name)
                }
        except (OSError, UnicodeError, ValueError, configparser.Error) as exc:
            errors.append(f"candidate pytest configuration is unreadable: {relative} ({type(exc).__name__}: {exc})")
            continue

        for label, values in sections.items():
            declared = values.get("addopts")
            if declared is None:
                continue
            if isinstance(declared, str):
                declarations.append((f"{relative} {label}", declared))
            elif isinstance(declared, list) and all(isinstance(item, str) for item in declared):
                declarations.append((f"{relative} {label}", " ".join(declared)))
            else:
                errors.append(f"candidate {relative} {label} addopts must be a string or a list of strings")
    return declarations, errors


def validate_candidate_pytest_configuration(candidate_root: Path) -> list[str]:
    """Refuse a candidate whose own pytest configuration rewrites its validation.

    The trusted controller runs the repository's ``pytest`` gate *inside* the
    candidate tree, so the candidate's own configuration is read by the run that
    is supposed to prove the candidate. Two things must not reach it: a worker
    requirement of an environment the candidate does not provision, and anything
    that decides which tests are selected. Checked here, from trusted code and
    before any gate executes, rather than only by a test the candidate owns.
    """
    declarations, errors = _declared_candidate_addopts(candidate_root)
    for where, declared in declarations:
        try:
            tokens = shlex.split(declared)
        except ValueError as exc:
            errors.append(f"candidate {where} addopts is not parseable ({exc})")
            continue
        for token in tokens:
            option = _forbidden_addopt(token)
            if option is not None:
                errors.append(
                    f"candidate {where} addopts declares {option}, which changes what its own trusted "
                    f"validation runs: {declared.strip()!r}"
                )
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

    errors.extend(validate_candidate_pytest_configuration(candidate_root))

    return errors


def trusted_parallel_runner_problem() -> str | None:
    """Why this environment cannot execute the trusted parallel lane, or ``None``.

    Asked of the interpreter that is about to launch the gate chain, which is the
    trusted default-branch environment itself -- never of the candidate. The
    candidate's own configuration, dependency manifests and pins live in a
    separate checkout that this environment never installs from, so nothing a
    candidate declares can add, remove, downgrade or substitute the runner that
    executes its validation.
    """
    if importlib.util.find_spec(TRUSTED_PARALLEL_RUNNER_PLUGIN) is None:
        return (
            f"the trusted parallel test runner {TRUSTED_PARALLEL_RUNNER_DISTRIBUTION} does not import as "
            f"{TRUSTED_PARALLEL_RUNNER_PLUGIN!r} in the trusted environment"
        )
    try:
        metadata.version(TRUSTED_PARALLEL_RUNNER_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return (
            f"the trusted parallel test runner {TRUSTED_PARALLEL_RUNNER_DISTRIBUTION} is not an installed "
            "distribution in the trusted environment"
        )
    return None


def trusted_lane_problem(addopts: str) -> str | None:
    """Why the declared trusted pytest lane may not be executed, or ``None``.

    Two refusals, both fail-closed. A declaration this environment cannot honour
    is refused rather than run some other way: a lane that quietly became serial
    would publish a different execution under the same proof name, and the
    difference would be invisible in the status it produces. A declaration that
    is not exactly the canonical distribution-only lane is refused too, because
    that is the only surface on which selection could be narrowed -- `-k`, `-m`,
    `--deselect`, `--ignore`, `-x`, `--lf` or a path would all arrive here.

    An empty declaration is the serial trusted lane and stays valid. Parallelism
    is a speed property, never a proof property: the gate chain, the commands and
    the tests selected are identical either way, so nothing is weakened by
    running without it -- only by running something other than what was declared.
    """
    tokens = tuple(addopts.split())
    if not tokens:
        return None
    if tokens != TRUSTED_PARALLEL_LANE:
        return (
            f"PYTEST_ADDOPTS declares {' '.join(tokens)}, which is not the canonical trusted "
            f"distribution-only lane {' '.join(TRUSTED_PARALLEL_LANE)}"
        )
    return trusted_parallel_runner_problem()


def verify_trusted_parallel_runner() -> int:
    """Report whether the trusted environment can run the parallel candidate lane."""
    problem = trusted_parallel_runner_problem()
    if problem is not None:
        print(f"[Trusted Parallel Runner] FAIL: {problem}")
        print(
            "[Trusted Parallel Runner] FAIL: the trusted candidate lane declares parallel execution and "
            "must not silently run serially under the same proof name."
        )
        return 2
    version = metadata.version(TRUSTED_PARALLEL_RUNNER_DISTRIBUTION)
    print(
        f"[Trusted Parallel Runner] PASS: {TRUSTED_PARALLEL_RUNNER_DISTRIBUTION} {version} is provisioned by the "
        f"trusted default branch; lane: pytest {' '.join(TRUSTED_PARALLEL_LANE)}"
    )
    return 0


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

    lane_problem = trusted_lane_problem(env.get("PYTEST_ADDOPTS", ""))
    if lane_problem is not None:
        print(f"[Trusted Candidate Gates] FAIL: {lane_problem}", flush=True)
        print(
            "[Trusted Candidate Gates] FAIL: the trusted lane executes what it declares or nothing at all; "
            "it does not fall back to a different execution under the same proof name.",
            flush=True,
        )
        return 2

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
    parser.add_argument(
        "--verify-parallel-runner",
        action="store_true",
        help=(
            "Verify that the trusted environment has the parallel test runner its candidate lane declares, "
            "failing loudly rather than letting the lane degrade into a different execution."
        ),
    )
    args = parser.parse_args()

    if args.verify_parallel_runner:
        return verify_trusted_parallel_runner()

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
    coverage = historical_backfill_coverage()
    print(
        "[Defect Prevention Guard] PASS: historical backfill coverage: "
        f"{coverage['guarded_records']}/{coverage['confirmed_records']} confirmed records guarded, "
        f"{coverage['confirmed_families']} confirmed families; needs_guard="
        f"{coverage['needs_guard_records']} without_regression={coverage['families_without_regression']} "
        f"without_selector={coverage['families_without_selector']} without_gate="
        f"{coverage['families_without_gate']} duplicates_unmapped={coverage['duplicates_unmapped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
