from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_ACTIONS = (
    "branch",
    "commit",
    "push",
    "pr-create",
    "pr-update",
    "ready",
    "resolve-finding",
    "merge-readiness",
)

CANONICAL_OWNERS: dict[str, str] = {
    "lifecycle": "docs/DEVELOPMENT_GOVERNANCE.md",
    "merge-readiness": "docs/DEVELOPMENT_GOVERNANCE.md",
    "contribution-review": "docs/AI_REVIEW_PROTOCOL.md",
    "architecture-audit": "docs/ARCHITECTURE_AUDIT_PROTOCOL.md",
    "implementation": "docs/HUNTER_IMPLEMENTATION_CONTRACT.md",
}

OWNER_SENTINELS: dict[str, tuple[str, ...]] = {
    "docs/DEVELOPMENT_GOVERNANCE.md": (
        "# Development Governance",
        "# Development Lifecycle",
        "# Merge Readiness",
    ),
    "docs/AI_REVIEW_PROTOCOL.md": (
        "# Project Hunter AI Review Protocol",
        "# Review Roles",
        "# Blocking Findings",
    ),
    "docs/ARCHITECTURE_AUDIT_PROTOCOL.md": (
        "Architecture",
        "materiality",
        "verdict",
    ),
    "docs/HUNTER_IMPLEMENTATION_CONTRACT.md": (
        "Implementation",
        "test",
    ),
}

TEMPLATE_PATH = ".github/pull_request_template.md"
TRACE_RE = re.compile(
    r"<!--\s*hunter-governance-preflight:v1\s+issue=(?P<issue>\d+)\s+"
    r"head=(?P<head>[0-9a-f]{7,64})\s+base=(?P<base>[0-9a-f]{7,64})\s*-->",
    re.IGNORECASE,
)
ISSUE_REFERENCE_RE = re.compile(r"(?im)^\s*(?:closes|fixes)\s+#(?P<number>\d+)\s*$")
READINESS_RE = re.compile(r"(?im)^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s*(?P<text>.+)$")
MATRIX_HEADER = ("acceptance criterion", "status", "evidence")
ALLOWED_STATUSES = {"pass", "fail", "blocked", "not applicable"}
READINESS_DECLARATIONS = ("ready for review", "changes required", "blocked")
PASS_EVIDENCE_PLACEHOLDERS = {
    "",
    "ci green",
    "green ci",
    "checks green",
    "ci passed",
    "tests pass",
    "pending",
    "tbd",
    "n/a",
}
BRANCH_ISSUE_RE_TEMPLATE = r"(?:^|[-_/])issue[-_/]?{number}(?:[-_/]|$)"

SEMANTIC_MARKERS: dict[str, tuple[re.Pattern[str], ...]] = {
    "lifecycle": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\blifecycle\b",
            r"\bready for review\b",
            r"\bdraft pull request\b",
            r"\bmerge readiness\b",
            r"\bstage\s+[0-9]+\b",
        )
    ),
    "contribution-review": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bblocking finding\b",
            r"\bfinding classification\b",
            r"\bisolated\b",
            r"\bsystemic\b",
            r"\breviewer\b",
            r"\bverifier\b",
            r"\breview outcome\b",
            r"\bapproval\b",
        )
    ),
    "architecture-audit": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\barchitecture audit\b",
            r"\bmateriality\b",
            r"\baudit verdict\b",
            r"\bre-audit\b",
        )
    ),
    "implementation": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bimplementation obligation\b",
            r"\bnon-vacuous\b",
            r"\bdurable root-cause\b",
            r"\breusable boundary\b",
            r"\btest weakening\b",
        )
    ),
}


class PreflightError(RuntimeError):
    """A deterministic governance preflight failure."""


@dataclass(frozen=True)
class IssueIdentity:
    repository: str
    number: int
    title: str
    body: str
    state: str

    @classmethod
    def from_payload(cls, repository: str, payload: Mapping[str, Any]) -> IssueIdentity:
        if payload.get("pull_request") is not None:
            raise PreflightError("Rule 21 identity target resolves to a pull request, not an Issue.")
        number = int(payload.get("number") or 0)
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "")
        state = str(payload.get("state") or "").strip().lower()
        if not number or not title:
            raise PreflightError("Issue identity payload is incomplete.")
        return cls(repository=repository, number=number, title=title, body=body, state=state)


@dataclass(frozen=True)
class MatrixRow:
    criterion: str
    status: str
    evidence: str


@dataclass(frozen=True)
class FindingResolution:
    finding_id: str
    severity: str
    classification: str | None
    classification_evidence: str | None
    reusable_boundary: str | None
    durable_guard_evidence: str | None
    verifier_evidence: str | None
    resolved: bool


def _normalize(value: str) -> str:
    value = value.replace("`", "").replace("*", "")
    return re.sub(r"\s+", " ", value.strip().lower()).rstrip(".")


def _run(command: Sequence[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        tuple(command),
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise PreflightError(f"{' '.join(command)} failed ({completed.returncode}): {detail}")
    return completed.stdout


def _gh_json(args: Sequence[str]) -> Any:
    output = _run(("gh", "api", *args))
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise PreflightError("GitHub CLI returned non-JSON output.") from exc


def _gh_pr_oids(repository: str, pr_number: int) -> tuple[str, str]:
    raw = _run(
        (
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repository,
            "--json",
            "baseRefOid,headRefOid",
        )
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError("GitHub CLI returned invalid PR OID JSON.") from exc
    head = str(payload.get("headRefOid") or "").strip()
    base = str(payload.get("baseRefOid") or "").strip()
    if not head or not base:
        raise PreflightError("Current headRefOid/baseRefOid are required for exact-pair preflight.")
    return head, base


def load_issue(repository: str, issue_number: int, issue_json: Path | None = None) -> IssueIdentity:
    payload = (
        json.loads(issue_json.read_text(encoding="utf-8"))
        if issue_json is not None
        else _gh_json((f"repos/{repository}/issues/{issue_number}",))
    )
    issue = IssueIdentity.from_payload(repository, payload)
    if issue.number != int(issue_number):
        raise PreflightError(f"Issue identity mismatch: requested #{issue_number}, resolved #{issue.number}.")
    if issue.state != "open":
        raise PreflightError(f"Issue #{issue.number} is not open.")
    return issue


def validate_canonical_governance(repo_root: Path) -> None:
    failures: list[str] = []
    for path, sentinels in OWNER_SENTINELS.items():
        target = repo_root / path
        if not target.is_file():
            failures.append(f"missing {path}")
            continue
        text = target.read_text(encoding="utf-8").lower()
        absent = [sentinel for sentinel in sentinels if sentinel.lower() not in text]
        if absent:
            failures.append(f"{path} missing sentinel(s): {', '.join(absent)}")

    template = repo_root / TEMPLATE_PATH
    if not template.is_file():
        failures.append(f"missing {TEMPLATE_PATH}")
    else:
        template_text = template.read_text(encoding="utf-8").lower()
        for heading in (
            "## Summary",
            "## Scope and architecture",
            "## Acceptance-criteria matrix",
            "## Verification",
            "## Operational validation",
            "## Remaining limitations and risks",
            "## Implementer readiness declaration",
        ):
            if heading.lower() not in template_text:
                failures.append(f"{TEMPLATE_PATH} missing canonical section {heading!r}")
    if failures:
        raise PreflightError("Canonical governance cannot be resolved: " + "; ".join(failures))


def issue_acceptance_criteria(issue_body: str) -> tuple[str, ...]:
    lines = issue_body.splitlines()
    start: int | None = None
    level: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,6})\s+Acceptance criteria\s*$", line.strip(), re.IGNORECASE)
        if match:
            start = index + 1
            level = len(match.group(1))
            break
    if start is None or level is None:
        raise PreflightError("Governing Issue has no parseable 'Acceptance criteria' section.")

    criteria: list[str] = []
    for line in lines[start:]:
        heading = re.match(r"^(#{1,6})\s+", line.strip())
        if heading and len(heading.group(1)) <= level:
            break
        match = re.match(r"^\s*[-*+]\s+(?:\[[ xX]\]\s*)?(?P<text>.+?)\s*$", line)
        if match and match.group("text").strip():
            criteria.append(match.group("text").strip())
    if not criteria:
        raise PreflightError("Governing Issue acceptance criteria are empty.")
    normalized = [_normalize(item) for item in criteria]
    if len(normalized) != len(set(normalized)):
        raise PreflightError("Governing Issue contains duplicate acceptance criteria.")
    return tuple(criteria)


def validate_issue_identity(
    issue: IssueIdentity,
    *,
    repository: str,
    objective: str,
    branch: str | None = None,
    commit_message: str | None = None,
    pr_title: str | None = None,
    pr_body: str | None = None,
) -> None:
    if repository != issue.repository:
        raise PreflightError(
            f"Issue repository mismatch: issue belongs to {issue.repository}, mutation targets {repository}."
        )
    if _normalize(objective) != _normalize(issue.title):
        raise PreflightError(
            f"Issue objective mismatch: verified title is {issue.title!r}, requested objective is {objective!r}."
        )
    if branch is not None:
        pattern = re.compile(BRANCH_ISSUE_RE_TEMPLATE.format(number=issue.number), re.IGNORECASE)
        if pattern.search(branch) is None:
            raise PreflightError(f"Branch {branch!r} does not carry verified Issue #{issue.number} identity.")
    if commit_message is not None and re.search(rf"(?<!\d)#{issue.number}(?!\d)", commit_message) is None:
        raise PreflightError(f"Commit message does not reference verified Issue #{issue.number}.")
    if pr_title is not None and _normalize(pr_title) != _normalize(issue.title):
        raise PreflightError("Pull-request title must equal the verified Issue title.")
    if pr_body is not None:
        numbers = [int(match.group("number")) for match in ISSUE_REFERENCE_RE.finditer(pr_body)]
        if numbers != [issue.number]:
            raise PreflightError(
                f"PR body must contain exactly one 'Closes #{issue.number}' or 'Fixes #{issue.number}' identity line."
            )


def parse_acceptance_matrix(body: str) -> tuple[MatrixRow, ...]:
    rows: list[MatrixRow] = []
    in_table = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            lowered = tuple(cell.lower() for cell in cells[:3])
            if len(lowered) >= 3 and lowered[:3] == MATRIX_HEADER:
                in_table = True
                continue
            if in_table and len(cells) >= 3 and all(set(cell) <= {"-", ":"} and cell for cell in cells[:3]):
                continue
            if in_table and len(cells) >= 3 and cells[1].lower() in ALLOWED_STATUSES:
                rows.append(MatrixRow(cells[0], cells[1].lower(), cells[2]))
                continue
        if in_table and stripped and not stripped.startswith("|"):
            break
    if not rows:
        raise PreflightError("Acceptance-criteria matrix is missing or unparseable.")
    return tuple(rows)


def checked_readiness(body: str) -> str:
    checked: list[str] = []
    for match in READINESS_RE.finditer(body):
        if match.group("mark").lower() != "x":
            continue
        normalized = _normalize(match.group("text"))
        for declaration in READINESS_DECLARATIONS:
            if normalized.startswith(declaration):
                checked.append(declaration)
                break
    if len(checked) != 1:
        raise PreflightError("Exactly one implementer readiness declaration must be checked.")
    return checked[0]


def trace_identity(body: str) -> tuple[int, str, str]:
    matches = list(TRACE_RE.finditer(body))
    if len(matches) != 1:
        raise PreflightError("PR body must contain exactly one Hunter governance preflight identity marker.")
    match = matches[0]
    return int(match.group("issue")), match.group("head").lower(), match.group("base").lower()


def validate_pr_body(
    body: str,
    issue: IssueIdentity,
    *,
    head_sha: str,
    base_sha: str,
    promotion: bool,
) -> None:
    validate_issue_identity(issue, repository=issue.repository, objective=issue.title, pr_body=body)
    required = issue_acceptance_criteria(issue.body)
    rows = parse_acceptance_matrix(body)

    by_key: dict[str, MatrixRow] = {}
    for row in rows:
        key = _normalize(row.criterion)
        if key in by_key:
            raise PreflightError(f"Acceptance criterion is duplicated in PR body: {row.criterion!r}.")
        by_key[key] = row

    required_keys = {_normalize(item) for item in required}
    missing = [item for item in required if _normalize(item) not in by_key]
    if missing:
        raise PreflightError("PR body omits governing Issue acceptance criteria: " + "; ".join(missing))
    extras = [row.criterion for key, row in by_key.items() if key not in required_keys]
    if extras:
        raise PreflightError("PR body contains acceptance criteria not present in governing Issue: " + "; ".join(extras))

    for row in rows:
        if row.status == "pass" and _normalize(row.evidence) in PASS_EVIDENCE_PLACEHOLDERS:
            raise PreflightError(
                f"PASS criterion lacks explicit evidence: {row.criterion!r}. Green CI alone is not completion evidence."
            )

    readiness = checked_readiness(body)
    marker_issue, marker_head, marker_base = trace_identity(body)
    if marker_issue != issue.number:
        raise PreflightError("PR body preflight marker names a different Issue.")
    if marker_head != head_sha.lower():
        raise PreflightError("PR body evidence is stale relative to the current source head.")
    if marker_base != base_sha.lower():
        raise PreflightError("PR body evidence is stale relative to the current target revision.")

    if promotion:
        blocked = [row.criterion for row in rows if row.status in {"fail", "blocked"}]
        if blocked:
            raise PreflightError(
                "Ready-for-review promotion is blocked by FAIL/BLOCKED criteria: " + "; ".join(blocked)
            )
        if readiness != "ready for review":
            raise PreflightError(
                f"Ready-for-review promotion requires READY FOR REVIEW, found {readiness.upper()}."
            )


def _criterion_evidence(
    criterion: str,
    evidence: Mapping[str, Mapping[str, str]],
) -> tuple[str, str]:
    normalized = _normalize(criterion)
    for key, value in evidence.items():
        if _normalize(key) != normalized:
            continue
        status = str(value.get("status") or "").strip().upper()
        detail = str(value.get("evidence") or "").strip()
        if status.lower() not in ALLOWED_STATUSES:
            raise PreflightError(f"Invalid evidence status for criterion {criterion!r}: {status!r}.")
        if status == "PASS" and _normalize(detail) in PASS_EVIDENCE_PLACEHOLDERS:
            raise PreflightError(f"PASS criterion {criterion!r} requires explicit evidence.")
        return status, detail
    return "BLOCKED", "Pending explicit criterion-specific evidence."


def generate_pr_body(
    issue: IssueIdentity,
    *,
    template_text: str,
    changed_files: Sequence[str],
    head_sha: str,
    base_sha: str,
    evidence: Mapping[str, Mapping[str, str]] | None = None,
    verification: Sequence[str] = (),
    operational_evidence: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> str:
    required_headings = (
        "## Summary",
        "## Scope and architecture",
        "## Acceptance-criteria matrix",
        "## Verification",
        "## Operational validation",
        "## Remaining limitations and risks",
        "## Implementer readiness declaration",
    )
    for heading in required_headings:
        if heading.lower() not in template_text.lower():
            raise PreflightError(f"Canonical PR template is missing {heading!r}.")

    rows = [
        (criterion, *_criterion_evidence(criterion, evidence or {}))
        for criterion in issue_acceptance_criteria(issue.body)
    ]
    ready = all(status in {"PASS", "NOT APPLICABLE"} for _, status, _ in rows)
    readiness = "READY FOR REVIEW" if ready else "CHANGES REQUIRED"
    scope = "\n".join(f"- `{path}`" for path in sorted(set(changed_files))) or "- No changed files resolved."
    matrix = "\n".join(
        f"| {criterion.replace('|', '/')} | {status} | {detail.replace('|', '/')} |"
        for criterion, status, detail in rows
    )
    verification_text = "\n".join(f"- {item}" for item in verification) or "- Pending exact command/result evidence."
    operational_text = (
        "\n".join(f"- {item}" for item in operational_evidence)
        or "- NOT APPLICABLE unless the governing Issue requires operational/runtime validation."
    )
    limitations_text = "\n".join(f"- {item}" for item in limitations) or "- Exact-head CI and independent review remain pending."
    declarations = "\n".join(
        f"- [{'x' if label == readiness else ' '}] `{label}`"
        for label in ("READY FOR REVIEW", "CHANGES REQUIRED", "BLOCKED")
    )
    marker = (
        f"<!-- hunter-governance-preflight:v1 issue={issue.number} "
        f"head={head_sha.lower()} base={base_sha.lower()} -->"
    )
    return (
        f"{marker}\nCloses #{issue.number}\n\n"
        "## Summary\n\n"
        f"Implements verified Issue **#{issue.number} — {issue.title}** through the mandatory Hunter governance "
        "enforcement path. Metadata is generated from canonical inputs and explicit evidence.\n\n"
        "## Scope and architecture\n\n"
        f"{scope}\n\n"
        "- Governing Issue identity is resolved from GitHub; sequence-inferred identity is rejected.\n"
        "- Canonical governance ownership remains with the existing owner documents.\n"
        "- Generated metadata grants no review approval or merge authority.\n\n"
        "## Acceptance-criteria matrix\n\n"
        "| Acceptance criterion | Status | Evidence |\n|---|---|---|\n"
        f"{matrix}\n\n"
        "- No criterion is omitted or inferred from green CI.\n"
        "- PASS requires criterion-specific evidence.\n\n"
        "## Verification\n\n"
        f"{verification_text}\n\n"
        "## Operational validation\n\n"
        f"{operational_text}\n\n"
        "## Remaining limitations and risks\n\n"
        f"{limitations_text}\n\n"
        "## Implementer readiness declaration\n\n"
        f"{declarations}\n\n"
        "> This is the implementer's self-assessment only. Independent review and human merge approval remain required.\n"
    )


def _owner_reference_present(line: str, owner_path: str) -> bool:
    return owner_path.lower() in line.lower() or Path(owner_path).name.lower() in line.lower()


def validate_ownership_added_lines(added_lines: Mapping[str, Sequence[str]]) -> None:
    canonical_paths = set(CANONICAL_OWNERS.values())
    failures: list[str] = []
    for path, lines in added_lines.items():
        if path not in canonical_paths:
            continue
        for line in lines:
            text = line.strip()
            if not text:
                continue
            for domain, markers in SEMANTIC_MARKERS.items():
                if not any(marker.search(text) for marker in markers):
                    continue
                owner = CANONICAL_OWNERS[domain]
                if path == owner or _owner_reference_present(text, owner):
                    continue
                failures.append(
                    f"{path}: added {domain} semantics outside canonical owner {owner}: {text[:120]!r}"
                )
    if failures:
        raise PreflightError("Canonical ownership violation: " + "; ".join(failures))


def added_lines_from_diff(diff_text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, [])
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            result[current].append(line[1:])
    return result


def validate_finding_resolution(finding: FindingResolution) -> None:
    if finding.severity.lower() != "blocking" or not finding.resolved:
        return
    classification = (finding.classification or "").strip().lower()
    if classification not in {"isolated", "systemic"}:
        raise PreflightError(
            f"Blocking finding {finding.finding_id} cannot be resolved without canonical isolated/systemic classification."
        )
    if not (finding.classification_evidence or "").strip():
        raise PreflightError(f"Blocking finding {finding.finding_id} lacks classification evidence.")
    if classification != "systemic":
        return
    if not (finding.reusable_boundary or "").strip():
        raise PreflightError(f"Systemic finding {finding.finding_id} lacks the reusable boundary.")
    if not (finding.durable_guard_evidence or "").strip():
        raise PreflightError(f"Systemic finding {finding.finding_id} lacks durable reusable hardening evidence.")
    if not (finding.verifier_evidence or "").strip():
        raise PreflightError(f"Systemic finding {finding.finding_id} lacks verifier evidence for the durable guard.")


def _require_current_state_ready(pr_number: int, issue: IssueIdentity) -> None:
    import hunter_merge_readiness as readiness

    readiness.init_globals()
    readiness.repo = issue.repository
    readiness.repo_owner = issue.repository.split("/", 1)[0]
    if not readiness.token:
        readiness.token = os.environ.get("GH_TOKEN", "") or _run(("gh", "auth", "token")).strip()
    state = readiness.read_current_state(pr_number)
    if state is None:
        raise PreflightError(f"PR #{pr_number} is not open.")
    if not state.draft:
        raise PreflightError(f"PR #{pr_number} is already out of Draft; pre-action Ready preflight was bypassed.")

    pr = _gh_json((f"repos/{issue.repository}/pulls/{pr_number}",))
    validate_issue_identity(
        issue,
        repository=issue.repository,
        objective=issue.title,
        branch=str((pr.get("head") or {}).get("ref") or ""),
        pr_title=state.title,
        pr_body=state.body,
    )
    validate_pr_body(state.body, issue, head_sha=state.head_sha, base_sha=state.base_sha, promotion=True)

    problem = readiness.feedback_error(state)
    if problem:
        raise PreflightError(problem)
    failures: list[str] = []
    pending: list[str] = []
    for check in state.required:
        if not check.present or not check.completed:
            pending.append(check.name)
        elif check.conclusion != "success":
            failures.append(f"{check.name}={check.conclusion or 'unknown'}")
    if state.governance is None:
        pending.append("Hunter Governance Review exact-pair evidence")
    elif state.governance.state != "success":
        failures.append(f"Hunter Governance Review={state.governance.state}")
    if state.shared_head_pull_requests:
        pending.append("head SHA is shared with another open PR")
    if failures:
        raise PreflightError("Ready preflight failed exact-head prerequisites: " + ", ".join(failures))
    if pending:
        raise PreflightError("Ready preflight is waiting for exact-head prerequisites: " + ", ".join(pending))


def validate_trace_against_state(body: str, *, head_sha: str, base_sha: str) -> str | None:
    try:
        _issue, marker_head, marker_base = trace_identity(body)
    except PreflightError as exc:
        return str(exc)
    if marker_head != head_sha.lower():
        return "PR body evidence is stale relative to the current source head."
    if marker_base != base_sha.lower():
        return "PR body evidence is stale relative to the current target revision."
    return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_branch(root: Path) -> str:
    return _run(("git", "branch", "--show-current"), cwd=root).strip()


def _git_commit_message(root: Path) -> str:
    return _run(("git", "log", "-1", "--pretty=%B"), cwd=root).strip()


def _git_changed_files(root: Path, base_ref: str) -> tuple[str, ...]:
    output = _run(("git", "diff", "--name-only", f"{base_ref}...HEAD"), cwd=root)
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def _git_added_lines(root: Path, base_ref: str) -> dict[str, list[str]]:
    return added_lines_from_diff(_run(("git", "diff", "--unified=0", f"{base_ref}...HEAD"), cwd=root))


def _identity_args(parser: argparse.ArgumentParser, *, body: bool = True) -> None:
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--issue-json")
    if body:
        parser.add_argument("--allow-governance-diff-check", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Hunter governance preflight.")
    parser.add_argument("--repo-root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    self_check = sub.add_parser("self-check")
    self_check.set_defaults(handler=_cmd_self_check)

    generate = sub.add_parser("generate-pr-body")
    _identity_args(generate, body=False)
    generate.add_argument("--template", default=TEMPLATE_PATH)
    generate.add_argument("--head-sha", required=True)
    generate.add_argument("--base-sha", required=True)
    generate.add_argument("--base-ref", default="main")
    generate.add_argument("--evidence-json")
    generate.add_argument("--output")
    generate.set_defaults(handler=_cmd_generate)

    for name in ("branch", "commit", "push", "pr-create", "pr-update"):
        action = sub.add_parser(name)
        _identity_args(action)
        action.add_argument("--base-ref", default="main")
        action.add_argument("--branch")
        action.add_argument("--commit-message")
        action.add_argument("--pr-title")
        action.add_argument("--pr-body-file")
        action.set_defaults(handler=_cmd_identity_action, action=name)

    ready = sub.add_parser("ready")
    _identity_args(ready, body=False)
    ready.add_argument("--pr", type=int, required=True)
    ready.set_defaults(handler=_cmd_ready)

    merge = sub.add_parser("merge-readiness")
    merge.add_argument("--pr", type=int, required=True)
    merge.set_defaults(handler=_cmd_merge_readiness)

    finding = sub.add_parser("resolve-finding")
    finding.add_argument("--finding-json", required=True)
    finding.set_defaults(handler=_cmd_finding)

    live = sub.add_parser("live-pr")
    live.add_argument("--repo", required=True)
    live.add_argument("--pr", type=int, required=True)
    live.set_defaults(handler=_cmd_live_pr)
    return parser


def _load_issue_from_args(args: argparse.Namespace) -> IssueIdentity:
    path = Path(args.issue_json) if getattr(args, "issue_json", None) else None
    return load_issue(args.repo, args.issue, path)


def _cmd_self_check(args: argparse.Namespace) -> int:
    validate_canonical_governance(Path(args.repo_root).resolve())
    print("[Hunter Governance Preflight] PASS: canonical governance surfaces resolved.")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    validate_canonical_governance(root)
    issue = _load_issue_from_args(args)
    validate_issue_identity(issue, repository=args.repo, objective=args.objective)
    evidence = _read_json(Path(args.evidence_json)) if args.evidence_json else {}
    body = generate_pr_body(
        issue,
        template_text=(root / args.template).read_text(encoding="utf-8"),
        changed_files=_git_changed_files(root, args.base_ref),
        head_sha=args.head_sha,
        base_sha=args.base_sha,
        evidence=evidence,
    )
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
    else:
        print(body)
    return 0


def _cmd_identity_action(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    validate_canonical_governance(root)
    issue = _load_issue_from_args(args)
    branch = args.branch or _git_branch(root)
    commit_message = args.commit_message
    if args.action in {"commit", "push", "pr-create", "pr-update"}:
        commit_message = commit_message or _git_commit_message(root)
    pr_body = Path(args.pr_body_file).read_text(encoding="utf-8") if args.pr_body_file else None
    validate_issue_identity(
        issue,
        repository=args.repo,
        objective=args.objective,
        branch=branch,
        commit_message=commit_message,
        pr_title=args.pr_title,
        pr_body=pr_body,
    )
    if args.allow_governance_diff_check:
        validate_ownership_added_lines(_git_added_lines(root, args.base_ref))
    print(f"[Hunter Governance Preflight] PASS: {args.action} authorized by verified Issue #{issue.number}.")
    return 0


def _cmd_ready(args: argparse.Namespace) -> int:
    issue = _load_issue_from_args(args)
    validate_issue_identity(issue, repository=args.repo, objective=args.objective)
    _require_current_state_ready(args.pr, issue)
    print(f"[Hunter Governance Preflight] PASS: PR #{args.pr} may be promoted from Draft.")
    return 0


def _cmd_merge_readiness(args: argparse.Namespace) -> int:
    import hunter_merge_readiness as readiness

    readiness.init_globals()
    if not readiness.repo:
        readiness.repo = os.environ.get("GH_REPO", "") or os.environ.get("GITHUB_REPOSITORY", "")
    if not readiness.repo:
        raise PreflightError("GH_REPO or GITHUB_REPOSITORY is required for live merge-readiness preflight.")
    readiness.repo_owner = readiness.repo.split("/", 1)[0]
    if not readiness.token:
        readiness.token = os.environ.get("GH_TOKEN", "") or _run(("gh", "auth", "token")).strip()
    state = readiness.read_current_state(args.pr)
    if state is None:
        raise PreflightError(f"PR #{args.pr} is not open.")
    decision = readiness.decide(state)
    if decision.state != "success":
        raise PreflightError(decision.description)
    print(f"[Hunter Governance Preflight] PASS: PR #{args.pr} is merge-ready under canonical controller state.")
    return 0


def _cmd_finding(args: argparse.Namespace) -> int:
    payload = _read_json(Path(args.finding_json))
    finding = FindingResolution(
        finding_id=str(payload.get("finding_id") or payload.get("validator_id") or ""),
        severity=str(payload.get("severity") or ""),
        classification=payload.get("classification"),
        classification_evidence=payload.get("classification_evidence"),
        reusable_boundary=payload.get("reusable_boundary"),
        durable_guard_evidence=payload.get("durable_guard_evidence"),
        verifier_evidence=payload.get("verifier_evidence"),
        resolved=bool(payload.get("resolved")),
    )
    if not finding.finding_id:
        raise PreflightError("Finding evidence has no finding identifier.")
    validate_finding_resolution(finding)
    print(f"[Hunter Governance Preflight] PASS: finding {finding.finding_id} resolution evidence is complete.")
    return 0


def _cmd_live_pr(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    validate_canonical_governance(root)
    pr = _gh_json((f"repos/{args.repo}/pulls/{args.pr}",))
    body = str(pr.get("body") or "")
    refs = [int(match.group("number")) for match in ISSUE_REFERENCE_RE.finditer(body)]
    if len(refs) != 1:
        raise PreflightError("Live PR must contain exactly one Closes/Fixes Issue identity.")
    issue = load_issue(args.repo, refs[0])
    validate_issue_identity(
        issue,
        repository=args.repo,
        objective=issue.title,
        branch=str((pr.get("head") or {}).get("ref") or ""),
        pr_title=str(pr.get("title") or ""),
        pr_body=body,
    )
    head_sha, base_sha = _gh_pr_oids(args.repo, args.pr)
    validate_pr_body(body, issue, head_sha=head_sha, base_sha=base_sha, promotion=not bool(pr.get("draft")))
    diff = _run(
        (
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.v3.diff",
            f"repos/{args.repo}/pulls/{args.pr}",
        )
    )
    validate_ownership_added_lines(added_lines_from_diff(diff))
    print(f"[Hunter Governance Preflight] PASS: live PR #{args.pr} body, identity, trace, and ownership are valid.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (PreflightError, OSError, json.JSONDecodeError) as exc:
        print(f"[Hunter Governance Preflight] FAIL: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
