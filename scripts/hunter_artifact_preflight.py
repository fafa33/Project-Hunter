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
    "## Accepted ADR Coverage",
    "## Prior Review Finding Re-Verification",
    "## Dimension Results",
    "## Findings",
    "## Findings Matrix",
    "## Verdict Derivation",
    "## Final Verdict",
    "## Audit Completion Check",
    "## Progression Gate",
)
ALLOWED_VERDICTS = (
    "READY_FOR_ADR",
    "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    "CONDITIONAL_ADR_READY",
    "ADPR_REVISION_REQUIRED",
    "ARCHITECTURE_NOT_READY",
)
PENDING_PLACEHOLDER_RE = re.compile(r"(?im)(?:`PENDING[^`]*`|\|\s*PENDING\s*\||:\s*PENDING\b|\bPENDING\s*[—-])")
AUDITOR_RE = re.compile(r"(?im)^-\s*Auditor:\s*(.+?)\s*$")
REVISION_RE = re.compile(r"(?im)^-\s*Reviewed revision:\s*`?([0-9a-f]{40})`?\s*$")
CUTOFF_RE = re.compile(r"(?im)^-\s*Evidence cutoff:\s*`?([^`\n]+)`?\s*$")


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def changed_paths() -> list[Path]:
    """Return added/copied/modified/renamed paths relative to the branch base."""
    merge_base = _run_git("merge-base", "HEAD", "origin/main")
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        base = merge_base.stdout.strip()
        diff = _run_git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    else:
        diff = _run_git("diff", "--name-only", "--diff-filter=ACMR", "HEAD^", "HEAD")

    if diff.returncode != 0:
        raise RuntimeError(
            "Unable to determine changed files. Fetch repository history/origin/main "
            "before running artifact preflight."
        )
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
        if not isinstance(defect_id, str) or not re.fullmatch(r"[A-Z]+(?:-[A-Z]+)*-\d{3}", defect_id):
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
        errors.append("Registry dropped required understood defect classes: " + ", ".join(missing_ids) + ".")
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


def validate_audit_text(text: str, *, accepted_adrs: list[str]) -> list[str]:
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

    cutoff_match = CUTOFF_RE.search(text)
    if not cutoff_match:
        errors.append("Audit must record an Evidence cutoff.")
    elif not _validate_iso8601(cutoff_match.group(1)):
        errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp.")

    coverage = _section(text, "## Accepted ADR Coverage")
    for adr in accepted_adrs:
        if not re.search(rf"\bADR[ -]?{re.escape(adr)}\b", coverage):
            errors.append(f"Accepted ADR {adr} is not accounted for in Accepted ADR Coverage.")

    prior = _section(text, "## Prior Review Finding Re-Verification")
    if not prior:
        errors.append("Audit must contain prior-review finding re-verification evidence.")
    elif not re.search(r"(?i)\b(?:none|finding|PR\s*#\d+)\b", prior):
        errors.append("Prior Review Finding Re-Verification must state findings examined or explicitly none.")

    if re.search(r"(?im)^-\s*\*\*Severity:\*\*", text):
        errors.append("Finding records must use the canonical `Class` field, not `Severity`.")

    final_verdict = _section(text, "## Final Verdict")
    verdicts = [verdict for verdict in ALLOWED_VERDICTS if re.search(rf"\b{verdict}\b", final_verdict)]
    if len(verdicts) != 1:
        errors.append("Final Verdict must select exactly one permitted audit verdict.")

    gate = " ".join(_section(text, "## Progression Gate").split())
    for verdict in ALLOWED_VERDICTS:
        if verdict not in gate:
            errors.append(f"Progression Gate must explicitly account for {verdict}.")
    gate_lower = gate.lower()
    if "clean progression" not in gate_lower:
        errors.append("Progression Gate must explicitly define clean progression.")
    if "condition" not in gate_lower:
        errors.append("Progression Gate must state handling for conditional readiness.")
    if "block" not in gate_lower:
        errors.append("Progression Gate must explicitly state blocking verdict behavior.")

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
        description=("Validate the defect registry and changed governed artifacts deterministically.")
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
