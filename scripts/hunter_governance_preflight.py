from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hunter_github_transport as transport

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
CANONICAL_PR_HEADINGS = (
    "## Summary",
    "## Scope and architecture",
    "## Acceptance-criteria matrix",
    "## Verification",
    "## Operational validation",
    "## Remaining limitations and risks",
    "## Implementer readiness declaration",
)
COMMAND_TIMEOUT_SECONDS = 30

# Sleeper hook so tests can inject a deterministic fake instead of sleeping.
_sleeper = time.sleep
TRACE_RE = re.compile(
    r"<!--\s*hunter-governance-preflight:v1\s+issue=(?P<issue>\d+)\s+"
    r"head=(?P<head>[0-9a-f]{7,64})\s+base=(?P<base>[0-9a-f]{7,64})\s*-->",
    re.IGNORECASE,
)
ISSUE_REFERENCE_RE = re.compile(r"(?im)^\s*(?:closes|fixes)\s+#(?P<number>\d+)\s*$")
READINESS_RE = re.compile(r"(?im)^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s*(?P<text>.+)$")
MATRIX_HEADER = ("acceptance criterion", "status", "evidence")
ALLOWED_STATUSES = {"pass", "fail", "blocked", "not applicable"}
CODERABBIT_AUTHORS = {"coderabbitai", "coderabbitai[bot]"}
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
INCOMPLETE_EVIDENCE_MARKERS = (
    "pending exact command/result evidence",
    "replace with verification output summary",
    "replace with commands, record ids, query/replay results, and environment details",
)
EVIDENCE_CONTRADICTION_RE = re.compile(
    r"\b(?:fail(?:ed|ure)?|fabricat(?:ed|ion)|not\s+(?:performed|run|executed|verified)|"
    r"no\s+commands?\s+(?:were\s+)?run|skipped|incomplete|unverified|missing|unavailable)\b",
    re.IGNORECASE,
)
EVIDENCE_VALUE_PLACEHOLDERS = {"", "fabricated", "placeholder", "none", "n/a", "pending", "tbd"}
EXPLICIT_NEGATIVE_EVIDENCE_RE = re.compile(
    r"\b(?:broken|bogus|fabricat(?:e|ed|ion|ing)?|placeholder)\b|"
    r"\bnot\s+(?:run|performed|executed|verified|probed|tested|checked|available)\b|"
    r"\b(?:verification|validation|evidence|review|scope|probe|tests?|checks?|guard|boundary|report)\s+"
    r"(?:was\s+|were\s+|is\s+|are\s+)?(?:unavailable|missing|bogus|broken|fabricated|"
    r"not\s+(?:run|performed|executed|verified|probed|tested|checked|available))\b|"
    r"\bno\s+(?:real\s+)?(?:evidence|verification|validation|review|probe|guard|boundary|report)\b",
    re.IGNORECASE,
)
NONPASS_RESULT_RE = re.compile(
    r"\b(?:ruff|black(?:\s+\d+(?:\.\d+)*)?|mypy|(?:full\s+)?pytest|operational validation)\s*:\s*"
    r"(?!(?:pass|not[ -]?applicable)\b)[A-Za-z][A-Za-z-]*",
    re.IGNORECASE,
)
STRUCTURED_EVIDENCE_VALUE_RE = re.compile(
    r"\Aresult=(?P<result>verified|complete|disclosed);\s*"
    r"reference=(?P<reference>https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\S+)\Z",
    re.IGNORECASE,
)
PAIR_EVIDENCE_RECORD_RE = re.compile(
    r"<!--\s*hunter-evidence:v1\s+kind=(?P<kind>verification|operational)\s+"
    r"result=(?P<result>verified|complete|not-applicable)\s+"
    r"reference=(?P<reference>https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\S+)\s+"
    r"head=(?P<head>[0-9a-f]{40})\s+base=(?P<base>[0-9a-f]{40})\s*-->",
    re.IGNORECASE,
)
REQUIRED_READY_CHECKLISTS: dict[str, tuple[str, ...]] = {
    "## Verification": (
        "`ruff check .`",
        "`black --check .` or the repository-approved equivalent",
        "`mypy`",
        "Full `pytest` suite",
        "Required migrations/configuration checks",
    ),
    "## Operational validation": (
        "All required runbooks were executed in a suitable environment.",
        "Required live providers/APIs were exercised.",
        "Required records were persisted and independently queried/replayed.",
        "Evidence identifiers, provenance, and logical history were verified.",
        "No fixture, fabricated response, or current-state substitution was used where live or point-in-time evidence was required.",
        "Environment/network limitations are disclosed below.",
    ),
}
VERIFICATION_RESULT_MARKER_RE = re.compile(
    r"<!--\s*hunter-verification:v1\s+ruff=pass\s+black=pass\s+mypy=pass\s+pytest=pass\s*-->",
    re.IGNORECASE,
)
OPERATIONAL_RESULT_MARKER_RE = re.compile(
    r"<!--\s*hunter-operational-validation:v1\s+outcome=(?:pass|not-applicable)\s*-->",
    re.IGNORECASE,
)
BRANCH_ISSUE_RE_TEMPLATE = r"(?:^|[-_/])issue[-_/]?{number}(?:[-_/]|$)"
FINDING_MARKER_RE = re.compile(
    r"<!--\s*hunter-review-finding:v1\s+id=(?P<id>[A-Za-z0-9._-]+)\s+"
    r"severity=(?P<severity>blocking|nonblocking)\s+"
    r"classification=(?P<classification>isolated|systemic)\s+"
    r"head=(?P<head>[0-9a-f]{40})\s+base=(?P<base>[0-9a-f]{40})\s*-->",
    re.IGNORECASE,
)
VERIFICATION_MARKER_RE = re.compile(
    r"<!--\s*hunter-review-verification:v1\s+finding=(?P<id>[A-Za-z0-9._-]+)\s+"
    r"head=(?P<head>[0-9a-f]{40})\s+base=(?P<base>[0-9a-f]{40})\s+"
    r"outcome=(?P<outcome>resolved|unresolved)\s*-->",
    re.IGNORECASE,
)
HOSTILE_REVIEW_MARKER_RE = re.compile(
    r"<!--\s*hunter-hostile-review:v1\s+head=(?P<head>[0-9a-f]{40})\s+"
    r"base=(?P<base>[0-9a-f]{40})\s+"
    r"outcome=(?P<outcome>no-blocking-findings|changes-required|blocked)\s*-->",
    re.IGNORECASE,
)
HOSTILE_REVIEW_EVIDENCE_RE = re.compile(r"(?im)^Hostile review evidence:\s*(?P<value>\S.+?)\s*$")
HOSTILE_REVIEW_SCOPE_RE = re.compile(r"(?im)^Scope probed:\s*(?P<value>\S.+?)\s*$")
HOSTILE_REVIEW_LIMITATIONS_RE = re.compile(r"(?im)^Limitations:\s*(?P<value>\S.+?)\s*$")
HOSTILE_REVIEW_BLOCKER_COUNT_RE = re.compile(r"(?im)^Unresolved blocking findings:\s*(?P<count>\d+)\s*$")
CLASSIFICATION_EVIDENCE_RE = re.compile(r"(?im)^Classification evidence:\s*(?P<value>\S.+?)\s*$")
REUSABLE_BOUNDARY_RE = re.compile(r"(?im)^Reusable boundary:\s*(?P<value>\S.+?)\s*$")
VERIFICATION_EVIDENCE_RE = re.compile(r"(?im)^Verification evidence:\s*(?P<value>\S.+?)\s*$")
DURABLE_GUARD_EVIDENCE_RE = re.compile(r"(?im)^Durable guard evidence:\s*(?P<value>\S.+?)\s*$")
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


@dataclass(frozen=True)
class LiveReviewEvidence:
    url: str
    author: str
    body: str
    pull_request_number: int = 0
    kind: str = "unknown"
    review_state: str = ""
    commit_id: str = ""


def _normalize(value: str) -> str:
    value = value.replace("`", "").replace("*", "")
    return re.sub(r"\s+", " ", value.strip().lower()).rstrip(".")


def _run(command: Sequence[str], *, cwd: Path | None = None) -> str:
    """Run an external command with bounded time and status-safe failures.

    Raw command diagnostics are never embedded in PreflightError because that
    exception can cross the Merge Readiness status boundary. Diagnostics are
    written to stderr for workflow logs; the raised message is deterministic
    and safe for a public commit-status description.

    Transient GitHub CLI failures (HTTP 429/500/502/503/504 or a node-
    resolution 404 in the CLI diagnostics) are retried with the shared bounded
    policy; after exhaustion the failure is reported as an explicit
    infrastructure-unavailable PreflightError instead of a generic command
    failure. Non-GitHub commands (git) are never retried.
    """
    try:
        return transport.execute_with_retry(
            lambda: _run_once(command, cwd=cwd),
            what=f"external command {command[0]}",
            sleeper=_sleeper,
        )
    except transport.GitHubUnavailable as exc:
        print(f"[Hunter Governance Preflight] GitHub infrastructure unavailable: {exc}", file=sys.stderr)
        raise PreflightError(
            f"external command failed after bounded GitHub availability retries: {command[0]}"
        ) from exc


def _run_once(command: Sequence[str], *, cwd: Path | None) -> str:
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreflightError(f"external command timed out after {COMMAND_TIMEOUT_SECONDS}s: {command[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if detail:
            print(f"[Hunter Governance Preflight] external command diagnostic:\n{detail}", file=sys.stderr)
        classified = transport.classify_cli_failure(detail or "no output")
        if classified.retryable:
            raise classified
        raise PreflightError(f"external command failed ({completed.returncode}): {command[0]}")
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


def load_issue(repository: str, issue_number: int) -> IssueIdentity:
    """Resolve production Issue identity exclusively from live GitHub state."""
    payload = _gh_json((f"repos/{repository}/issues/{issue_number}",))
    issue = IssueIdentity.from_payload(repository, payload)
    if issue.number != int(issue_number):
        raise PreflightError(f"Issue identity mismatch: requested #{issue_number}, resolved #{issue.number}.")
    if issue.state != "open":
        raise PreflightError(f"Issue #{issue.number} is not open.")
    return issue


def validate_canonical_pr_sections(text: str, *, source: str = "PR body") -> None:
    positions: list[int] = []
    for heading in CANONICAL_PR_HEADINGS:
        matches = list(re.finditer(rf"(?mi)^{re.escape(heading)}\s*$", text))
        if len(matches) != 1:
            raise PreflightError(
                f"{source} must contain exactly one canonical section {heading!r}; found {len(matches)}."
            )
        positions.append(matches[0].start())
    if positions != sorted(positions):
        raise PreflightError(f"{source} canonical sections are out of order.")


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
        try:
            template_text = template.read_text(encoding="utf-8")
            validate_canonical_pr_sections(template_text, source=TEMPLATE_PATH)
            for heading, expected in REQUIRED_READY_CHECKLISTS.items():
                actual = tuple(item for _mark, item in _checklist_items(_canonical_section(template_text, heading)))
                if tuple(map(_normalize, actual)) != tuple(map(_normalize, expected)):
                    failures.append(f"{TEMPLATE_PATH} {heading} checklist diverges from executable projection")
        except PreflightError as exc:
            failures.append(str(exc))
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
    in_section = False
    in_table = False
    section_level: int | None = None
    for line in body.splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            level = len(heading.group(1))
            title = _normalize(heading.group(2))
            if title == "acceptance-criteria matrix":
                in_section = True
                in_table = False
                section_level = level
                continue
            if in_section and section_level is not None and level <= section_level:
                break
            continue
        if not in_section:
            continue
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


def blocked_acceptance_criteria(body: str) -> tuple[str, ...]:
    """Return the FAIL/BLOCKED acceptance-criteria names in a PR body.

    Every authority that publishes promotion success treats a FAIL/BLOCKED
    matrix row as blocking. The check must not depend on the checked readiness
    declaration, because an advisory layer may legitimately carry CHANGES
    REQUIRED while a criterion is still FAIL/BLOCKED — and must then never
    claim promotion success.
    """
    return tuple(row.criterion for row in parse_acceptance_matrix(body) if row.status in {"fail", "blocked"})


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


def _canonical_section(text: str, heading: str) -> str:
    start = re.search(rf"(?mi)^{re.escape(heading)}\s*$", text)
    if start is None:
        raise PreflightError(f"PR body is missing canonical section {heading!r}.")
    following = re.search(r"(?m)^##\s+", text[start.end() :])
    end = start.end() + following.start() if following is not None else len(text)
    return text[start.end() : end]


def _checklist_items(section: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (match.group("mark").lower(), match.group("text").strip())
        for match in re.finditer(
            r"(?im)^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s*(?P<text>.+?)\s*$",
            section,
        )
    )


def _has_substantive_evidence(section: str) -> bool:
    ignored_prefixes = ("record exact commands and results:", "operational evidence:")
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("- [", "```")) or line.lower().startswith(ignored_prefixes):
            continue
        normalized = _normalize(line)
        if normalized and not any(marker in normalized for marker in INCOMPLETE_EVIDENCE_MARKERS):
            return True
    return False


def _contains_contradictory_evidence(section: str) -> bool:
    """Inspect supplied evidence prose without treating checklist policy as evidence."""
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("- [", "<!--", "```")):
            continue
        if _has_explicit_negative_evidence(line, status_context=True):
            return True
    return False


def _unstructured_evidence_lines(section: str) -> tuple[str, ...]:
    ignored_prefixes = ("record exact commands and results:", "operational evidence:")
    unstructured: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith(("- [", "```"))
            or line.lower().startswith(ignored_prefixes)
            or VERIFICATION_RESULT_MARKER_RE.fullmatch(line)
            or OPERATIONAL_RESULT_MARKER_RE.fullmatch(line)
            or PAIR_EVIDENCE_RECORD_RE.fullmatch(line)
        ):
            continue
        unstructured.append(line)
    return tuple(unstructured)


def _has_explicit_negative_evidence(value: str, *, status_context: bool = False) -> bool:
    if EXPLICIT_NEGATIVE_EVIDENCE_RE.search(value):
        return True
    if status_context and (EVIDENCE_CONTRADICTION_RE.search(value) or NONPASS_RESULT_RE.search(value)):
        return True
    return False


def _require_substantive_evidence(value: str, *, label: str, status_context: bool = False) -> None:
    if _normalize(value) in EVIDENCE_VALUE_PLACEHOLDERS or _has_explicit_negative_evidence(
        value, status_context=status_context
    ):
        raise PreflightError(f"{label} contains placeholder or explicitly negative evidence.")


def _require_structured_evidence_value(value: str, *, label: str) -> None:
    if STRUCTURED_EVIDENCE_VALUE_RE.fullmatch(value.strip()) is None:
        raise PreflightError(f"{label} must be a closed structured evidence record with a GitHub provenance reference.")


def validate_ready_evidence(body: str) -> None:
    head_sha, base_sha = trace_identity(body)[1:]
    for heading, expected in REQUIRED_READY_CHECKLISTS.items():
        section = _canonical_section(body, heading)
        checklist = _checklist_items(section)
        actual = tuple(item for _mark, item in checklist)
        if tuple(map(_normalize, actual)) != tuple(map(_normalize, expected)):
            raise PreflightError(f"READY FOR REVIEW requires the complete canonical {heading[3:]} checklist.")
        unchecked = tuple(item for mark, item in checklist if mark != "x")
        if unchecked:
            raise PreflightError(f"READY FOR REVIEW requires every {heading[3:]} item complete: {unchecked[0]}")
        normalized = _normalize(section)
        if any(marker in normalized for marker in INCOMPLETE_EVIDENCE_MARKERS) or not _has_substantive_evidence(
            section
        ):
            raise PreflightError(f"READY FOR REVIEW lacks complete evidence in {heading}.")
        result_marker = VERIFICATION_RESULT_MARKER_RE if heading == "## Verification" else OPERATIONAL_RESULT_MARKER_RE
        if len(result_marker.findall(section)) != 1:
            raise PreflightError(f"READY FOR REVIEW requires one canonical structured result marker in {heading}.")
        kind = "verification" if heading == "## Verification" else "operational"
        records = [record for record in PAIR_EVIDENCE_RECORD_RE.finditer(section) if record.group("kind") == kind]
        if len(records) != 1:
            raise PreflightError(f"READY FOR REVIEW requires one exact-pair evidence record in {heading}.")
        record = records[0]
        if record.group("head").lower() != head_sha or record.group("base").lower() != base_sha:
            raise PreflightError(f"READY FOR REVIEW evidence record in {heading} is stale or cross-pair.")
        if _unstructured_evidence_lines(section):
            raise PreflightError(f"READY FOR REVIEW forbids unstructured evidence prose in {heading}.")
        if _contains_contradictory_evidence(section):
            raise PreflightError(f"READY FOR REVIEW contains contradictory failure evidence in {heading}.")


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
    validate_canonical_pr_sections(body)
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
        raise PreflightError(
            "PR body contains acceptance criteria not present in governing Issue: " + "; ".join(extras)
        )

    for row in rows:
        if row.status != "pass":
            continue
        if _normalize(row.evidence) in PASS_EVIDENCE_PLACEHOLDERS:
            raise PreflightError(
                f"PASS criterion lacks explicit evidence: {row.criterion!r}. Green CI alone is not completion evidence."
            )
        _require_substantive_evidence(
            row.evidence,
            label=f"Acceptance criterion {row.criterion!r} evidence",
            status_context=True,
        )

    readiness = checked_readiness(body)
    if readiness == "ready for review":
        validate_ready_evidence(body)
    marker_issue, marker_head, marker_base = trace_identity(body)
    if marker_issue != issue.number:
        raise PreflightError("PR body preflight marker names a different Issue.")
    if marker_head != head_sha.lower():
        raise PreflightError("PR body evidence is stale relative to the current source head.")
    if marker_base != base_sha.lower():
        raise PreflightError("PR body evidence is stale relative to the current target revision.")

    if promotion:
        blocked = blocked_acceptance_criteria(body)
        if blocked:
            raise PreflightError(
                "Ready-for-review promotion is blocked by FAIL/BLOCKED criteria: " + "; ".join(blocked)
            )
        if readiness != "ready for review":
            raise PreflightError(f"Ready-for-review promotion requires READY FOR REVIEW, found {readiness.upper()}.")


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
        if status == "PASS":
            if _normalize(detail) in PASS_EVIDENCE_PLACEHOLDERS:
                raise PreflightError(f"PASS criterion {criterion!r} requires explicit evidence.")
            _require_substantive_evidence(
                detail,
                label=f"Acceptance criterion {criterion!r} evidence",
                status_context=True,
            )
        return status, detail
    return "BLOCKED", "Pending explicit criterion-specific evidence."


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PreflightError(f"Canonical PR template must contain exactly one {label}; found {count}.")
    return text.replace(old, new, 1)


def _insert_after_heading(text: str, heading: str, content: str) -> str:
    marker = f"{heading}\n\n"
    return _replace_once(text, marker, f"{marker}{content}\n\n", label=f"{heading} insertion point")


def _mark_section_checkboxes(text: str, heading: str) -> str:
    section = _canonical_section(text, heading)
    checked = re.sub(r"(?m)^(\s*[-*+]\s*)\[[ xX]\]", r"\1[x]", section)
    return text.replace(section, checked, 1)


def generate_pr_body(
    issue: IssueIdentity,
    *,
    repo_root: Path,
    base_ref: str,
    template_text: str,
    changed_files: Sequence[str],
    evidence: Mapping[str, Mapping[str, str]] | None = None,
    verification: Sequence[str] = (),
    operational_evidence: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> str:
    validate_canonical_pr_sections(template_text, source="Canonical PR template")
    head_sha, base_sha = _git_exact_pair(repo_root, base_ref)

    rows = [
        (criterion, *_criterion_evidence(criterion, evidence or {}))
        for criterion in issue_acceptance_criteria(issue.body)
    ]
    # Generation happens before hosted exact-head checks and independent hostile
    # review can exist. Caller-supplied prose is useful trace material, but it is
    # never authority to promote a generated body.
    readiness = "CHANGES REQUIRED"
    scope = "\n".join(f"- `{path}`" for path in sorted(set(changed_files))) or "- No changed files resolved."
    matrix = "\n".join(
        f"| {criterion.replace('|', '/')} | {status} | {detail.replace('|', '/')} |"
        for criterion, status, detail in rows
    )
    verification_text = "\n".join(verification) or "Pending exact command/result evidence."
    operational_text = (
        "\n".join(operational_evidence)
        or "NOT APPLICABLE unless the governing Issue requires operational/runtime validation."
    )
    limitations_text = (
        "\n".join(f"- {item}" for item in limitations) or "- Exact-head CI and independent review remain pending."
    )
    marker = (
        f"<!-- hunter-governance-preflight:v1 issue={issue.number} "
        f"head={head_sha.lower()} base={base_sha.lower()} -->"
    )

    body = template_text
    body = _replace_once(
        body,
        "Describe the exact issue/milestone scope implemented by this PR.",
        (
            f"Implements verified Issue **#{issue.number} — {issue.title}** through the mandatory Hunter governance "
            "enforcement path. Metadata is generated from canonical inputs and explicit evidence."
        ),
        label="Summary placeholder",
    )
    body = _insert_after_heading(
        body,
        "## Scope and architecture",
        (
            f"{scope}\n\n"
            "- Governing Issue identity is resolved from GitHub; sequence-inferred identity is rejected.\n"
            "- Canonical governance ownership remains with the existing owner documents.\n"
            "- Generated metadata grants no review approval or merge authority."
        ),
    )
    body = _replace_once(
        body,
        "| Replace with criterion | BLOCKED | Replace with test, runtime record, query result, or explanation |",
        matrix,
        label="acceptance-matrix placeholder row",
    )
    body = _replace_once(
        body,
        "Replace with verification output summary.",
        verification_text,
        label="verification placeholder",
    )
    body = _replace_once(
        body,
        "Replace with commands, record IDs, query/replay results, and environment details.",
        operational_text,
        label="operational-evidence placeholder",
    )
    body = _replace_once(
        body,
        "List every known incomplete item, environmental blocker, deferred requirement, and residual risk. Write `None` only after explicit verification.",
        limitations_text,
        label="limitations placeholder",
    )
    if verification:
        body = _mark_section_checkboxes(body, "## Verification")
    if operational_evidence:
        body = _mark_section_checkboxes(body, "## Operational validation")

    for label in ("READY FOR REVIEW", "CHANGES REQUIRED", "BLOCKED"):
        pattern = re.compile(rf"(?m)^- \[[ xX]\] `{re.escape(label)}`")
        matches = list(pattern.finditer(body))
        if len(matches) != 1:
            raise PreflightError(
                f"Canonical PR template must contain exactly one readiness declaration {label!r}; found {len(matches)}."
            )
        mark = "x" if label == readiness else " "
        body = pattern.sub(f"- [{mark}] `{label}`", body, count=1)

    validate_canonical_pr_sections(body)
    return f"{marker}\nCloses #{issue.number}\n\n{body.rstrip()}\n"


def _owner_reference_present(line: str, owner_path: str) -> bool:
    return owner_path.lower() in line.lower() or Path(owner_path).name.lower() in line.lower()


def validate_ownership_added_lines(added_lines: Mapping[str, Sequence[str]]) -> None:
    canonical_paths = set(CANONICAL_OWNERS.values())
    failures: list[str] = []
    for path, lines in added_lines.items():
        is_governance_prose = path.endswith(".md") or path.startswith(".github/instructions/")
        if path not in canonical_paths and not is_governance_prose:
            continue
        for line in lines:
            text = line.strip()
            if not text:
                continue
            for domain, markers in SEMANTIC_MARKERS.items():
                if not any(marker.search(text) for marker in markers):
                    continue
                owner = CANONICAL_OWNERS[domain]
                if _owner_reference_present(text, owner):
                    continue
                failures.append(f"{path}: added {domain} semantics outside canonical owner {owner}: {text[:120]!r}")
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
    _require_structured_evidence_value(
        finding.classification_evidence or "",
        label=f"Blocking finding {finding.finding_id} classification evidence",
    )
    if classification != "systemic":
        return
    _require_structured_evidence_value(
        finding.reusable_boundary or "",
        label=f"Systemic finding {finding.finding_id} reusable boundary",
    )
    _require_structured_evidence_value(
        finding.durable_guard_evidence or "",
        label=f"Systemic finding {finding.finding_id} durable reusable hardening evidence",
    )
    _require_structured_evidence_value(
        finding.verifier_evidence or "",
        label=f"Systemic finding {finding.finding_id} verifier evidence",
    )


def _review_evidence_from_url(repository: str, pr_number: int, url: str) -> LiveReviewEvidence:
    review_match = re.search(r"#discussion_r(?P<id>\d+)$", url)
    issue_match = re.search(r"#issuecomment-(?P<id>\d+)$", url)
    kind = "issue_comment"
    review_state = ""
    commit_id = ""
    if review_match is not None:
        payload = _gh_json((f"repos/{repository}/pulls/comments/{review_match.group('id')}",))
        kind = "review_comment"
        review_id = int(payload.get("pull_request_review_id") or 0)
        if not review_id or payload.get("in_reply_to_id") is not None:
            raise PreflightError("Canonical finding evidence must be a top-level PR review comment.")
        parent_review = _gh_json((f"repos/{repository}/pulls/{pr_number}/reviews/{review_id}",))
        review_author = str((parent_review.get("user") or {}).get("login") or "").strip()
        if review_author.lower() != str((payload.get("user") or {}).get("login") or "").strip().lower():
            raise PreflightError("PR review comment author does not match its canonical parent review.")
        review_state = str(parent_review.get("state") or "").strip().lower()
        commit_id = str(parent_review.get("commit_id") or "").strip().lower()
    elif issue_match is not None:
        payload = _gh_json((f"repos/{repository}/issues/comments/{issue_match.group('id')}",))
    else:
        raise PreflightError("Review evidence URL must identify a live PR review or Conversation comment.")
    live_url = str(payload.get("html_url") or "").strip()
    author = str((payload.get("user") or {}).get("login") or "").strip()
    body = str(payload.get("body") or "")
    parent_url = str(payload.get("pull_request_url") or payload.get("issue_url") or "").rstrip("/")
    parent_number_match = re.search(r"/(?:pulls|issues)/(?P<number>\d+)$", parent_url)
    parent_number = int(parent_number_match.group("number")) if parent_number_match else 0
    if live_url != url or not author or not body or parent_number != int(pr_number):
        raise PreflightError("Live review evidence is incomplete or does not match the requested URL.")
    return LiveReviewEvidence(
        url=live_url,
        author=author,
        body=body,
        pull_request_number=parent_number,
        kind=kind,
        review_state=review_state,
        commit_id=commit_id,
    )


def finding_resolution_from_live_review(
    *,
    finding_comment: LiveReviewEvidence,
    verifier_comment: LiveReviewEvidence,
    pr_author: str,
    head_sha: str,
    base_sha: str,
) -> FindingResolution:
    if not pr_author:
        raise PreflightError("Live PR author identity is required for independent finding resolution.")
    if finding_comment.url == verifier_comment.url:
        raise PreflightError("Finding classification and post-fix verification must be separate live evidence.")
    if finding_comment.author.lower() == pr_author.lower() or verifier_comment.author.lower() == pr_author.lower():
        raise PreflightError("Finding classification and verification must be independent from the PR author.")
    if finding_comment.author.lower() == verifier_comment.author.lower():
        raise PreflightError("Finding classification and verification require distinct independent authors.")
    if (
        finding_comment.kind != "review_comment"
        or finding_comment.review_state != "changes_requested"
        or finding_comment.commit_id.lower() != head_sha.lower()
    ):
        raise PreflightError(
            "Canonical blocking finding is stale or does not belong to a current exact-head CHANGES_REQUESTED review."
        )
    finding_matches = list(FINDING_MARKER_RE.finditer(finding_comment.body))
    if len(finding_matches) != 1:
        raise PreflightError("Canonical live finding must contain exactly one machine-readable finding marker.")
    finding_marker = finding_matches[0]
    finding_id = finding_marker.group("id")
    if (
        finding_marker.group("head").lower() != head_sha.lower()
        or finding_marker.group("base").lower() != base_sha.lower()
    ):
        raise PreflightError("Canonical finding classification is stale for the current exact pair.")
    classification_evidence = CLASSIFICATION_EVIDENCE_RE.search(finding_comment.body)
    if classification_evidence is None:
        raise PreflightError("Canonical finding lacks independent classification evidence.")
    reusable_boundary_match = REUSABLE_BOUNDARY_RE.search(finding_comment.body)

    verifier_matches = list(VERIFICATION_MARKER_RE.finditer(verifier_comment.body))
    if len(verifier_matches) != 1:
        raise PreflightError("Live verifier evidence must contain exactly one machine-readable verification marker.")
    verifier_marker = verifier_matches[0]
    if verifier_marker.group("id") != finding_id or verifier_marker.group("outcome").lower() != "resolved":
        raise PreflightError("Verifier evidence does not resolve the canonical finding.")
    if (
        verifier_marker.group("head").lower() != head_sha.lower()
        or verifier_marker.group("base").lower() != base_sha.lower()
    ):
        raise PreflightError("Verifier evidence is stale for the current exact pair.")
    verifier_evidence_match = VERIFICATION_EVIDENCE_RE.search(verifier_comment.body)
    if verifier_evidence_match is None:
        raise PreflightError("Live verifier comment lacks substantive Verification evidence.")
    durable_guard_match = DURABLE_GUARD_EVIDENCE_RE.search(verifier_comment.body)

    finding = FindingResolution(
        finding_id=finding_id,
        severity=finding_marker.group("severity"),
        classification=finding_marker.group("classification"),
        classification_evidence=classification_evidence.group("value"),
        reusable_boundary=(reusable_boundary_match.group("value") if reusable_boundary_match else None),
        durable_guard_evidence=(durable_guard_match.group("value") if durable_guard_match else None),
        verifier_evidence=verifier_evidence_match.group("value"),
        resolved=True,
    )
    validate_finding_resolution(finding)
    return finding


def _require_successful_coderabbit_status(
    *,
    status: dict[str, Any],
    expected_context: str = "coderabbit",
) -> bool:
    """Validate a CodeRabbit commit status with producer authentication.

    A qualifying status MUST have:
    - context == "coderabbit"
    - state == "success"
    - status["creator"]["login"] in CODERABBIT_AUTHORS
    - reject missing/malformed creator;
    - reject creator login not in CODERABBIT_AUTHORS;
    - remain fail-closed.
    """
    creator = status.get("creator")
    if not isinstance(creator, dict):
        return False
    login = creator.get("login")
    if not login:
        return False
    if login not in CODERABBIT_AUTHORS:
        return False
    if status.get("context") != expected_context:
        return False
    if status.get("state") != "success":
        return False
    return True


def require_independent_hostile_review(
    *, repository: str, pr_number: int, pr_author: str, head_sha: str, base_sha: str
) -> None:
    if not pr_author:
        raise PreflightError("Live PR author identity is required for independent hostile review.")
    reviews: list[Mapping[str, Any]] = []
    page = 1
    while True:
        payload = _gh_json((f"repos/{repository}/pulls/{pr_number}/reviews?per_page=100&page={page}",))
        if not isinstance(payload, list):
            raise PreflightError("Live hostile-review evidence is unavailable.")
        reviews.extend(review for review in payload if isinstance(review, Mapping))
        if len(payload) < 100:
            break
        page += 1
    candidates: list[tuple[str, int, Mapping[str, Any], tuple[re.Match[str], ...]]] = []
    for review in reviews:
        author = str((review.get("user") or {}).get("login") or "").strip()
        if not author or author.lower() == pr_author.lower():
            continue
        if str(review.get("commit_id") or "").lower() != head_sha.lower():
            continue
        state = str(review.get("state") or "").strip().lower()
        if state not in {"commented", "changes_requested"}:
            continue
        submitted_at = str(review.get("submitted_at") or "").strip()
        if not submitted_at:
            continue
        body = str(review.get("body") or "")
        matches = list(HOSTILE_REVIEW_MARKER_RE.finditer(body))
        exact_matches = tuple(
            marker
            for marker in matches
            if marker.group("head").lower() == head_sha.lower() and marker.group("base").lower() == base_sha.lower()
        )
        if not exact_matches:
            continue
        candidates.append((submitted_at, int(review.get("id") or 0), review, exact_matches))
    if not candidates:
        # CodeRabbit recognition fallback: only when no valid Hunter hostile-review marker exists.
        # A CodeRabbit review qualifies only if ALL are true:
        # - author login is exactly "coderabbitai" or "coderabbitai[bot]"
        # - author is not the PR author (already guaranteed by the filter above)
        # - review.commit_id == current head_sha (already guaranteed by the filter above)
        # - review state is COMMENTED for a passing review; CHANGES_REQUESTED must never qualify
        # - review body proves it reviewed the exact current pair using CodeRabbit's
        #   "Reviewing files ... between <base> and <head>" evidence
        # - stale head or wrong base may not qualify; empty/malformed bodies must not qualify
        code_rabbit_authors = {"coderabbitai", "coderabbitai[bot]"}
        for review in reviews:
            author = str((review.get("user") or {}).get("login") or "").strip()
            if author not in code_rabbit_authors:
                continue
            state = str(review.get("state") or "").strip().lower()
            if state != "commented":
                continue
            # commit_id already matches head_sha from the outer filter, but verify for safety
            if str(review.get("commit_id") or "").lower() != head_sha.lower():
                continue
            body = str(review.get("body") or "")
            # CodeRabbit's review-info evidence: "Reviewing files that changed from the base of the PR and between <base> and <head>"
            has_exact_pair = (
                f"between {base_sha.lower()} and {head_sha.lower()}" in body.lower()
                or f"between {head_sha.lower()} and {base_sha.lower()}" in body.lower()
            )
            if not has_exact_pair:
                continue
            # CodeRabbit review qualifies as independent hostile-review evidence
            return
        raise PreflightError("Ready preflight lacks independent exact-pair hostile-review evidence.")
    _submitted_at, _review_id, latest_review, latest_markers = max(candidates, key=lambda item: item[:2])
    if len(latest_markers) != 1:
        raise PreflightError("Latest independent exact-pair hostile review must contain exactly one outcome marker.")
    latest_marker = latest_markers[0]
    latest_body = str(latest_review.get("body") or "")
    evidence = HOSTILE_REVIEW_EVIDENCE_RE.search(latest_body)
    scope = HOSTILE_REVIEW_SCOPE_RE.search(latest_body)
    limitations = HOSTILE_REVIEW_LIMITATIONS_RE.search(latest_body)
    blocker_count = HOSTILE_REVIEW_BLOCKER_COUNT_RE.search(latest_body)
    if evidence is None or scope is None or limitations is None or blocker_count is None:
        raise PreflightError("Latest independent exact-pair hostile review report is incomplete.")
    for label, match in (
        ("Hostile review evidence", evidence),
        ("Hostile review scope", scope),
        ("Hostile review limitations", limitations),
    ):
        _require_structured_evidence_value(match.group("value"), label=label)
    outcome = latest_marker.group("outcome").lower()
    count = int(blocker_count.group("count"))
    latest_state = str(latest_review.get("state") or "").strip().lower()
    if outcome == "no-blocking-findings" and (latest_state != "commented" or count != 0):
        raise PreflightError("Latest independent exact-pair hostile review pass report is internally inconsistent.")
    if outcome != "no-blocking-findings" and count == 0:
        raise PreflightError("Latest independent exact-pair hostile review adverse report is internally inconsistent.")
    if outcome != "no-blocking-findings":
        raise PreflightError(f"Latest independent exact-pair hostile review reports {outcome.upper()}.")


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
    require_independent_hostile_review(
        repository=issue.repository,
        pr_number=pr_number,
        pr_author=str((pr.get("user") or {}).get("login") or ""),
        head_sha=state.head_sha,
        base_sha=state.base_sha,
    )


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


def _git_exact_pair(root: Path, base_ref: str) -> tuple[str, str]:
    head = _run(("git", "rev-parse", "HEAD"), cwd=root).strip()
    base = _run(("git", "rev-parse", base_ref), cwd=root).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise PreflightError("Git did not resolve a complete source-head/target exact pair.")
    return head, base


def _git_added_lines(root: Path, base_ref: str) -> dict[str, list[str]]:
    # Include the working tree for pre-commit authorization; checking only HEAD
    # would validate the previous commit while omitting the mutation about to be
    # committed.
    return added_lines_from_diff(_run(("git", "diff", "--unified=0", base_ref), cwd=root))


def _identity_diff_base(args: argparse.Namespace) -> str:
    """Derive the ownership-diff base from repository authority only.

    The pre-action ownership guard must never be vacated by a caller-supplied
    base ref (for example ``--base-ref HEAD`` on a clean tree makes the diff
    empty and the guard pass vacuously). Mutation actions therefore reject
    caller-supplied base refs entirely; the origin tracking ref is the only
    accepted base: the target branch's tracking ref for ``pr-create`` and the
    default-branch tracking ref for every other mutation action.
    """
    if args.action == "pr-create":
        return f"refs/remotes/origin/{args.base_branch}"
    return "refs/remotes/origin/main"


def _identity_args(parser: argparse.ArgumentParser, *, body: bool = True) -> None:
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--objective", required=True)
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
    generate.add_argument("--base-ref", default="main")
    generate.add_argument("--evidence-json")
    generate.add_argument("--verification-evidence", action="append", default=[])
    generate.add_argument("--operational-evidence", action="append", default=[])
    generate.add_argument("--limitation", action="append", default=[])
    generate.add_argument("--output")
    generate.set_defaults(handler=_cmd_generate)

    for name in ("branch", "commit", "push", "pr-create", "pr-update"):
        action = sub.add_parser(name)
        _identity_args(action)
        if name == "branch":
            action.add_argument("--branch", required=True)
        if name == "commit":
            action.add_argument("--commit-message", required=True)
        if name == "pr-create":
            action.add_argument("--base-branch", required=True)
        action.add_argument("--pr-title", required=name in {"pr-create", "pr-update"})
        action.add_argument("--pr-body-file", required=name in {"pr-create", "pr-update"})
        if name == "pr-update":
            action.add_argument("--pr", type=int, required=True)
        action.set_defaults(handler=_cmd_identity_action, action=name)

    ready = sub.add_parser("ready")
    _identity_args(ready, body=False)
    ready.add_argument("--pr", type=int, required=True)
    ready.set_defaults(handler=_cmd_ready)

    merge = sub.add_parser("merge-readiness")
    merge.add_argument("--pr", type=int, required=True)
    merge.set_defaults(handler=_cmd_merge_readiness)

    finding = sub.add_parser("resolve-finding")
    finding.add_argument("--repo", required=True)
    finding.add_argument("--pr", type=int, required=True)
    finding.add_argument("--finding-json", required=True)
    finding.set_defaults(handler=_cmd_finding)

    live = sub.add_parser("live-pr")
    live.add_argument("--repo", required=True)
    live.add_argument("--pr", type=int, required=True)
    live.set_defaults(handler=_cmd_live_pr)
    return parser


def _load_issue_from_args(args: argparse.Namespace) -> IssueIdentity:
    return load_issue(args.repo, args.issue)


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
        repo_root=root,
        base_ref=args.base_ref,
        template_text=(root / args.template).read_text(encoding="utf-8"),
        changed_files=_git_changed_files(root, args.base_ref),
        evidence=evidence,
        verification=args.verification_evidence,
        operational_evidence=args.operational_evidence,
        limitations=args.limitation,
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
    branch = args.branch if args.action == "branch" else _git_branch(root)
    commit_message = args.commit_message if args.action == "commit" else None
    if args.action in {"push", "pr-create", "pr-update"}:
        commit_message = _git_commit_message(root)
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
    if args.action in {"pr-create", "pr-update"}:
        if pr_body is None:
            raise PreflightError(f"{args.action} requires canonical PR body metadata.")
        if args.action == "pr-update":
            live_pr = _gh_json((f"repos/{args.repo}/pulls/{args.pr}",))
            live_head = live_pr.get("head") or {}
            live_base = live_pr.get("base") or {}
            live_branch = str(live_head.get("ref") or "")
            live_head_repository = str((live_head.get("repo") or {}).get("full_name") or "")
            live_title = str(live_pr.get("title") or "")
            live_body = str(live_pr.get("body") or "")
            if live_branch != branch:
                raise PreflightError("pr-update target PR head branch does not match the governed branch.")
            if live_head_repository.lower() != args.repo.lower():
                raise PreflightError("pr-update target PR head repository does not match the governed repository.")
            validate_issue_identity(
                issue,
                repository=args.repo,
                objective=args.objective,
                branch=live_branch,
                pr_title=live_title,
                pr_body=live_body,
            )
            head_sha, base_sha = _gh_pr_oids(args.repo, args.pr)
            local_head, local_base = _git_exact_pair(root, _identity_diff_base(args))
            if (
                head_sha != local_head
                or base_sha != local_base
                or str(live_head.get("sha") or "").lower() != local_head
                or str(live_base.get("sha") or "").lower() != local_base
            ):
                raise PreflightError("pr-update target PR exact pair does not match the governed checkout.")
        else:
            head_sha, base_sha = _git_exact_pair(root, f"refs/remotes/origin/{args.base_branch}")
        validate_pr_body(pr_body, issue, head_sha=head_sha, base_sha=base_sha, promotion=False)
    if args.allow_governance_diff_check:
        validate_ownership_added_lines(_git_added_lines(root, _identity_diff_base(args)))
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
    pr = _gh_json((f"repos/{readiness.repo}/pulls/{args.pr}",))
    require_independent_hostile_review(
        repository=readiness.repo,
        pr_number=args.pr,
        pr_author=str((pr.get("user") or {}).get("login") or ""),
        head_sha=state.head_sha,
        base_sha=state.base_sha,
    )
    print(f"[Hunter Governance Preflight] PASS: PR #{args.pr} is merge-ready under canonical controller state.")
    return 0


def _cmd_finding(args: argparse.Namespace) -> int:
    payload = _read_json(Path(args.finding_json))
    finding_url = str(payload.get("finding_url") or "").strip()
    verifier_url = str(payload.get("verifier_url") or "").strip()
    if not finding_url or not verifier_url:
        raise PreflightError("Finding resolution requires live finding_url and verifier_url evidence.")
    pr = _gh_json((f"repos/{args.repo}/pulls/{args.pr}",))
    head_sha, base_sha = _gh_pr_oids(args.repo, args.pr)
    finding = finding_resolution_from_live_review(
        finding_comment=_review_evidence_from_url(args.repo, args.pr, finding_url),
        verifier_comment=_review_evidence_from_url(args.repo, args.pr, verifier_url),
        pr_author=str((pr.get("user") or {}).get("login") or ""),
        head_sha=head_sha,
        base_sha=base_sha,
    )
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
    if not bool(pr.get("draft")):
        require_independent_hostile_review(
            repository=args.repo,
            pr_number=args.pr,
            pr_author=str((pr.get("user") or {}).get("login") or ""),
            head_sha=head_sha,
            base_sha=base_sha,
        )
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
