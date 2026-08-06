"""Deterministic Governance Engine.

The engine validates a pull request against the repository's canonical
governance rules (``docs/DEVELOPMENT_GOVERNANCE.md``,
``docs/MERGE_READINESS_GATE.md``, ``docs/AI_REVIEW_PROTOCOL.md``) without
invoking an LLM. Any blocking finding produces ``CHANGES_REQUIRED``; the LLM
audit is never allowed to bypass a deterministic failure.

Validators are deliberately small, evidence-based checks. They are listed in
the ``_VALIDATORS`` registry, each returning at most one finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from hunter_governance_review.contracts import (
    ChangedFile,
    DeterministicResult,
    Finding,
    PullRequest,
    Severity,
)

REQUIRED_SECTIONS = (
    "Summary",
    "Scope and architecture",
    "Acceptance-criteria matrix",
    "Verification",
    "Operational validation",
    "Remaining limitations and risks",
    "Implementer readiness declaration",
)

# Docs-only contributions scale their evidence down proportionally, per
# DEVELOPMENT_GOVERNANCE.md proportionality rules.
MINIMAL_SECTIONS = ("Summary", "Acceptance-criteria matrix", "Implementer readiness declaration")

PLACEHOLDER_MARKERS = (
    "replace with",
    "todo:",
    "lorem ipsum",
    "fix me",
    "placeholder",
    "example: ",
)

PLACEHOLDER_TITLES = {"", "wip", "draft", "untitled", "update", "updates", "change", "changes"}

ACCEPTANCE_STATUSES = ("pass", "fail", "blocked", "not applicable")

READINESS_DECLARATIONS = ("ready for review", "changes required", "blocked")

GATE_PATH_MARKERS = (
    "hunter-governance-review",
    "hunter_governance_review",
    "HUNTER_GOVERNANCE_REVIEW",
)


@dataclass(frozen=True)
class ValidationContext:
    """Everything the deterministic engine needs to evaluate a PR.

    ``missing_references`` is precomputed by
    ``context.resolve_referenced_records`` against the exact base commit via
    the GitHub API -- this engine never reads the local filesystem, so a
    checkout that happens to differ from the recorded review pair's exact
    base SHA cannot produce a wrong answer here.
    """

    pr: PullRequest
    files: list[ChangedFile]
    missing_references: tuple[str, ...] = field(default_factory=tuple)

    @property
    def body(self) -> str:
        return self.pr.body

    @property
    def code_change(self) -> bool:
        """True when the PR touches production code or tooling."""
        return any(
            f.filename.startswith("src/")
            or f.filename.startswith("scripts/")
            or f.filename == "pyproject.toml"
            or f.filename.startswith("requirements/")
            for f in self.files
        )


Validator = Callable[[ValidationContext], Finding | None]


def _has_section(body: str, name: str) -> bool:
    return re.search(re.escape(name), body, re.IGNORECASE) is not None


def _parse_acceptance_matrix(body: str) -> tuple[list[tuple[str, str, str]], bool]:
    """Extract (criterion, status, evidence) rows from the acceptance matrix.

    Returns ``(rows, has_template_placeholder)``. Only rows whose status token
    is an allowed acceptance status are counted.
    """
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "Acceptance criterion" in stripped:
            in_table = True
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        if not in_table:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        status = cells[1].lower()
        if status in ACCEPTANCE_STATUSES:
            evidence = cells[2] if len(cells) > 2 else ""
            rows.append((cells[0], status, evidence))
    placeholder = any("replace with" in row[0].lower() or "replace with" in row[2].lower() for row in rows)
    return rows, placeholder


def _parse_readiness(body: str) -> tuple[str | None, str | None]:
    """Return ``(declared, problem)`` for the implementer readiness declaration.

    ``declared`` is one of the allowed declarations, or ``None`` when no
    unambiguous checked declaration exists (``problem`` explains why).
    """
    checked: list[str] = []
    for match in re.finditer(r"(?im)^\s*[-*+]\s*\[([ xX])\]\s*(.+)", body):
        marker, text = match.group(1), match.group(2).strip().lower()
        if marker not in ("x", "X"):
            continue
        # Strip markdown decoration (the template wraps declarations in
        # backticks and/or bold markers) before matching the declaration name.
        text = text.replace("`", "").replace("*", "")
        for declaration in READINESS_DECLARATIONS:
            if text.startswith(declaration):
                checked.append(declaration)
                break
    if not checked:
        return None, "no implementer readiness declaration is checked"
    if len(set(checked)) > 1:
        return None, "more than one implementer readiness declaration is checked"
    return checked[0], None


# --- Validators -------------------------------------------------------------------


def _title_validator(ctx: ValidationContext) -> Finding | None:
    title = ctx.pr.title.strip()
    if not title or title.lower() in PLACEHOLDER_TITLES:
        return Finding(
            "V-010",
            "PR title is missing or a placeholder",
            Severity.BLOCKING,
            f"title={title!r}; a descriptive title is required.",
        )
    return None


def _body_validator(ctx: ValidationContext) -> Finding | None:
    body = ctx.body.strip()
    if not body:
        return Finding(
            "V-020",
            "PR body is empty",
            Severity.BLOCKING,
            "the governance evidence package must be recorded in the PR description.",
        )
    if len(body) < 80:
        return Finding(
            "V-020",
            "PR body lacks the governance evidence package",
            Severity.BLOCKING,
            f"body is only {len(body)} characters; merge readiness requires evidence, not a stub.",
        )
    lowered = body.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return Finding(
                "V-021",
                "PR body contains template placeholders",
                Severity.BLOCKING,
                f"found placeholder marker {marker!r}; replace it with real evidence.",
            )
    return None


def _sections_validator(ctx: ValidationContext) -> Finding | None:
    required = REQUIRED_SECTIONS if ctx.code_change else MINIMAL_SECTIONS
    missing = [section for section in required if not _has_section(ctx.body, section)]
    if missing:
        return Finding(
            "V-030",
            "PR body is missing required template sections",
            Severity.BLOCKING,
            "missing: " + ", ".join(missing),
        )
    return None


def _matrix_validator(ctx: ValidationContext) -> Finding | None:
    rows, placeholder = _parse_acceptance_matrix(ctx.body)
    if placeholder:
        return Finding(
            "V-040",
            "acceptance-criteria matrix contains the template placeholder row",
            Severity.BLOCKING,
            "replace the placeholder row with real criteria and evidence.",
        )
    if not rows:
        return Finding(
            "V-040",
            "acceptance-criteria matrix is missing",
            Severity.BLOCKING,
            "every criterion must be listed with PASS, FAIL, BLOCKED, or NOT APPLICABLE.",
        )
    if not ctx.pr.draft:
        bad = [row for row in rows if row[1] in ("fail", "blocked")]
        if bad:
            return Finding(
                "V-040",
                "merge-ready PR has FAIL or BLOCKED acceptance criteria",
                Severity.BLOCKING,
                "; ".join(f"{row[0]}={row[1].upper()}" for row in bad[:5]),
            )
    return None


def _readiness_validator(ctx: ValidationContext) -> Finding | None:
    declared, problem = _parse_readiness(ctx.body)
    if declared is None:
        return Finding(
            "V-050",
            "implementer readiness declaration is missing or ambiguous",
            Severity.BLOCKING,
            problem or "exactly one checked declaration is required.",
        )
    if declared != "ready for review" and not ctx.pr.draft:
        return Finding(
            "V-050",
            "PR is not declared READY FOR REVIEW",
            Severity.BLOCKING,
            f"declared {declared.upper()}; a merge-ready PR must declare READY FOR REVIEW.",
        )
    return None


def _verification_validator(ctx: ValidationContext) -> Finding | None:
    if not ctx.code_change:
        return None
    if "replace with verification output" in ctx.body.lower():
        return Finding(
            "V-060",
            "verification evidence is a template placeholder",
            Severity.BLOCKING,
            "record the exact ruff, black, mypy, and pytest results.",
        )
    return None


def _adr_references_validator(ctx: ValidationContext) -> Finding | None:
    if ctx.missing_references:
        return Finding(
            "V-070",
            "PR references architecture records that do not exist",
            Severity.BLOCKING,
            "missing required repository evidence: " + ", ".join(ctx.missing_references),
        )
    return None


def _mergeable_validator(ctx: ValidationContext) -> Finding | None:
    if ctx.pr.mergeable == "CONFLICTING":
        return Finding(
            "V-080",
            "pull request has merge conflicts",
            Severity.BLOCKING,
            "resolve conflicts or merge the target branch before the gate can pass.",
        )
    return None


def _gate_self_modification_validator(ctx: ValidationContext) -> Finding | None:
    touched = [f.filename for f in ctx.files if any(m in f.filename for m in GATE_PATH_MARKERS)]
    if touched:
        return Finding(
            "V-090",
            "PR modifies the merge gate itself",
            Severity.INFO,
            "gate self-modification is flagged for hostile audit: " + ", ".join(touched),
        )
    return None


def _draft_validator(ctx: ValidationContext) -> Finding | None:
    if ctx.pr.draft:
        return Finding(
            "V-100",
            "PR is in draft state",
            Severity.INFO,
            "GitHub blocks merging draft PRs; the gate re-runs on ready_for_review.",
        )
    return None


_VALIDATORS: tuple[Validator, ...] = (
    _title_validator,
    _body_validator,
    _sections_validator,
    _matrix_validator,
    _readiness_validator,
    _verification_validator,
    _adr_references_validator,
    _mergeable_validator,
    _gate_self_modification_validator,
    _draft_validator,
)


def run_deterministic_engine(ctx: ValidationContext) -> DeterministicResult:
    """Run every deterministic validator against the PR context."""
    findings = [validator(ctx) for validator in _VALIDATORS]
    return DeterministicResult(findings=[finding for finding in findings if finding is not None])
