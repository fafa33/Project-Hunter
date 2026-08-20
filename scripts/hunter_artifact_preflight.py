from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
ADR_INDEX_PATH = ROOT / "docs" / "ADR" / "README.md"
AUDIT_PREFIX = "docs/ARCHITECTURE_AUDITS/"

REQUIRED_REGISTRY_FIELDS = {
    "id",
    "class",
    "source",
    "classification",
    "guard_boundary",
    "guard",
    "test",
    "status",
}
REQUIRED_DEFECT_IDS = {
    "PRH-001",
    "PRH-002",
    "PRH-003",
    "PRH-004",
    "PRH-005",
    "PRH-006",
    "PRH-007",
    "PRH-008",
    "PRH-009",
    "PRH-010",
    "ARCH-AUD-001",
    "ARCH-AUD-002",
    "ARCH-AUD-003",
    "ARCH-AUD-004",
    "ARCH-AUD-005",
    "ARCH-AUD-006",
}
REQUIRED_AUDIT_HEADINGS = (
    "## Metadata",
    "## Audit Scope",
    "## Evidence Sources Examined",
    "## Dimension Results",
    "## Findings",
    "## Findings Matrix",
    "## Verdict Derivation",
    "## Final Verdict",
    "## Required Corrections or Conditions",
    "## Non-Blocking Follow-Up",
    "## Audit Completion Check",
)
ALLOWED_VERDICTS = (
    "READY_FOR_ADR",
    "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    "CONDITIONAL_ADR_READY",
    "ADPR_REVISION_REQUIRED",
    "ARCHITECTURE_NOT_READY",
)
PENDING_PLACEHOLDER_RE = re.compile(
    r"(?im)(?:`PENDING[^`]*`|\|\s*PENDING\s*\||:\s*PENDING\b|\bPENDING\s*[—-])"
)
AUDITOR_RE = re.compile(r"(?im)^-\s*Auditor:\s*(.+?)\s*$")
REVISION_RE = re.compile(r"(?im)^-\s*Reviewed revision:\s*`?([0-9a-f]{40})`?\s*$")
AUDIT_TYPE_RE = re.compile(r"(?im)^-\s*Audit type:\s*`?(FULL|TARGETED)`?\s*$")
CUTOFF_RE = re.compile(r"(?im)^-\s*Evidence cutoff:\s*`?([^`\n]+)`?\s*$")
MUTABLE_EVIDENCE_RE = re.compile(r"(?i)(?:https?://|\bPR\s*#\d+\b|\bIssue\s*#\d+\b)")
PRIOR_REVIEW_SCOPE_RE = re.compile(r"(?i)(?:prior\s+review|previous\s+finding|\bPR\s*#\d+\b)")
FINDING_CLASS_FIELD_RE = re.compile(r"(?im)^-\s*\*\*Class:\*\*")
BLOCKS_ADR_YES_RE = re.compile(r"(?im)(?:\*\*Blocks ADR:\*\*\s*`?YES`?|\|[^\n]*\|\s*YES\s*\|)")


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def changed_paths() -> list[Path]:
    """Return changed paths against the proven branch merge-base with origin/main."""
    merge_base = _run_git("merge-base", "HEAD", "origin/main")
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise RuntimeError(
            "Unable to prove merge-base with origin/main. Fetch complete repository history "
            "before running artifact preflight."
        )

    base = merge_base.stdout.strip()
    diff = _run_git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    if diff.returncode != 0:
        raise RuntimeError("Unable to determine changed files from the proven branch merge-base.")
    return [Path(line.strip()) for line in diff.stdout.splitlines() if line.strip()]


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("DEFECT_REGISTRY version must be 1.")

    defects = data.get("defects")
    if not isinstance(defects, list):
        return errors + ["DEFECT_REGISTRY defects must be a list."]

    seen: set[str] = set()
    for index, item in enumerate(defects):
        if not isinstance(item, dict):
            errors.append(f"Registry item {index} must be an object.")
            continue
        missing = sorted(REQUIRED_REGISTRY_FIELDS - set(item))
        if missing:
            errors.append(f"Registry item {index} missing fields: {', '.join(missing)}.")
            continue

        defect_id = item["id"]
        if not isinstance(defect_id, str) or not re.fullmatch(
            r"[A-Z]+(?:-[A-Z]+)*-\d{3}", defect_id
        ):
            errors.append(f"Registry item {index} has invalid id {defect_id!r}.")
        elif defect_id in seen:
            errors.append(f"Duplicate defect id: {defect_id}.")
        else:
            seen.add(defect_id)

        if item["classification"] not in {"systemic", "isolated"}:
            errors.append(f"{defect_id}: classification must be systemic or isolated.")
        if item["status"] not in {"guarded", "open", "retired"}:
            errors.append(f"{defect_id}: unsupported status {item['status']!r}.")
        if item["status"] == "guarded":
            for field in ("guard_boundary", "guard", "test"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{defect_id}: guarded entry requires non-empty {field}.")

    missing_ids = sorted(REQUIRED_DEFECT_IDS - seen)
    if missing_ids:
        errors.append(
            "Registry dropped required understood defect classes: "
            + ", ".join(missing_ids)
            + "."
        )
    return errors


def accepted_adr_ids(index_text: str) -> list[str]:
    ids: list[str] = []
    for line in index_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        match = re.fullmatch(r"\[(\d{4})\]\([^)]+\)", cells[0])
        if match and cells[2].startswith("Accepted"):
            ids.append(match.group(1))
    return sorted(set(ids))


def _section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    remainder = text[start + len(heading) :]
    next_heading = re.search(r"(?m)^##\s+", remainder)
    if next_heading:
        remainder = remainder[: next_heading.start()]
    return remainder.strip()


def _validate_iso8601(value: str) -> bool:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _selected_verdicts(text: str) -> list[str]:
    final_verdict = _section(text, "## Final Verdict")
    return [
        verdict
        for verdict in ALLOWED_VERDICTS
        if re.search(rf"\b{verdict}\b", final_verdict)
    ]


def validate_audit_text(text: str, *, accepted_adrs: list[str]) -> list[str]:
    """Validate canonical audit requirements without inventing prose-only ceremony."""
    errors: list[str] = []

    for heading in REQUIRED_AUDIT_HEADINGS:
        if heading not in text:
            errors.append(f"Missing mandatory audit heading: {heading}.")

    if PENDING_PLACEHOLDER_RE.search(text):
        errors.append("Audit contains unresolved PENDING placeholder content.")

    auditor_match = AUDITOR_RE.search(text)
    if not auditor_match or not auditor_match.group(1).strip():
        errors.append("Audit must record an independent auditor identity.")
    elif "pending" in auditor_match.group(1).lower():
        errors.append("Audit auditor identity may not be pending.")

    if not REVISION_RE.search(text):
        errors.append("Audit must pin Reviewed revision to an immutable 40-hex commit.")

    audit_type_match = AUDIT_TYPE_RE.search(text)
    if not audit_type_match:
        errors.append("Audit type must be FULL or TARGETED.")
        audit_type = None
    else:
        audit_type = audit_type_match.group(1)

    evidence = _section(text, "## Evidence Sources Examined")
    cutoff_match = CUTOFF_RE.search(text)
    if MUTABLE_EVIDENCE_RE.search(evidence):
        if not cutoff_match:
            errors.append(
                "Audit using mutable PR/Issue/URL evidence must record an Evidence cutoff."
            )
        elif not _validate_iso8601(cutoff_match.group(1)):
            errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp.")
    elif cutoff_match and not _validate_iso8601(cutoff_match.group(1)):
        errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp when present.")

    if audit_type == "FULL":
        coverage_heading = "## Accepted ADR Coverage"
        if coverage_heading not in text:
            errors.append("FULL audit must include explicit Accepted ADR Coverage.")
        coverage = _section(text, coverage_heading)
        for adr in accepted_adrs:
            if not re.search(rf"\bADR[ -]?{re.escape(adr)}\b", coverage):
                errors.append(
                    f"Accepted ADR {adr} is not accounted for in FULL-audit Accepted ADR Coverage."
                )

    scope = _section(text, "## Audit Scope")
    if PRIOR_REVIEW_SCOPE_RE.search(scope):
        prior = _section(text, "## Prior Review Finding Re-Verification")
        if not prior:
            errors.append(
                "Audit scope declares prior review history but lacks Prior Review Finding Re-Verification."
            )
        elif not re.search(r"(?i)\b(?:none|finding|PR\s*#\d+)\b", prior):
            errors.append(
                "Prior Review Finding Re-Verification must state findings examined or explicitly none."
            )

    if FINDING_CLASS_FIELD_RE.search(text):
        errors.append(
            "Finding records must use canonical `Severity`; `Class` is reserved for the Findings Matrix."
        )

    for match in re.finditer(r"(?im)^-\s*\*\*Severity:\*\*\s*`?([^`\n]+)`?\s*$", text):
        if not re.fullmatch(r"[A-D]", match.group(1).strip()):
            errors.append("Finding Severity must be exactly A, B, C, or D.")

    verdicts = _selected_verdicts(text)
    if len(verdicts) != 1:
        errors.append("Final Verdict must select exactly one permitted audit verdict.")
        selected_verdict = None
    else:
        selected_verdict = verdicts[0]

    corrections = _section(text, "## Required Corrections or Conditions")
    if selected_verdict == "CONDITIONAL_ADR_READY" and (
        not corrections or corrections.strip().lower() in {"none", "n/a", "not applicable"}
    ):
        errors.append("CONDITIONAL_ADR_READY requires explicit mandatory conditions.")

    if selected_verdict in {"ADPR_REVISION_REQUIRED", "ARCHITECTURE_NOT_READY"}:
        findings = _section(text, "## Findings")
        matrix = _section(text, "## Findings Matrix")
        if not BLOCKS_ADR_YES_RE.search(findings + "\n" + matrix):
            errors.append(
                f"{selected_verdict} requires at least one evidence-backed finding with Blocks ADR = YES."
            )

    return errors


def validate_changed_artifacts(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    accepted = accepted_adr_ids(ADR_INDEX_PATH.read_text(encoding="utf-8"))
    for relative in paths:
        posix = relative.as_posix()
        if not posix.startswith(AUDIT_PREFIX) or relative.suffix.lower() != ".md":
            continue
        full_path = ROOT / relative
        if not full_path.exists():
            continue
        for error in validate_audit_text(
            full_path.read_text(encoding="utf-8"),
            accepted_adrs=accepted,
        ):
            errors.append(f"{posix}: {error}")
    return errors


def run_artifact_preflight(paths: list[Path] | None = None) -> int:
    errors = validate_registry(load_registry())
    if paths is None:
        try:
            paths = changed_paths()
        except RuntimeError as exc:
            print(f"[Hunter Artifact Preflight] FAIL: {exc}", flush=True)
            return 2
    errors.extend(validate_changed_artifacts(paths))

    if errors:
        for error in errors:
            print(f"[Hunter Artifact Preflight] FAIL: {error}", flush=True)
        return 1

    print(
        "[Hunter Artifact Preflight] PASS: defect registry and changed governed artifacts",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the defect registry and changed governed artifacts deterministically."
        )
    )
    parser.add_argument(
        "--all-audits",
        action="store_true",
        help="Validate all architecture-audit markdown files instead of only changed files.",
    )
    args = parser.parse_args()
    if args.all_audits:
        paths = sorted((ROOT / "docs" / "ARCHITECTURE_AUDITS").glob("*.md"))
        return run_artifact_preflight([path.relative_to(ROOT) for path in paths])
    return run_artifact_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
