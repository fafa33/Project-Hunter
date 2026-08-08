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
    CanonicalPRContract,
    ChangedFile,
    DeterministicResult,
    Finding,
    PullRequest,
    Severity,
)

GATE_PATH_MARKERS = (
    "hunter-governance-review",
    "hunter_governance_review",
    "HUNTER_GOVERNANCE_REVIEW",
)


def _structural_severity(ctx: ValidationContext) -> Severity:
    """Returns Severity.INFO for draft PRs to prevent premature blocking failures, and Severity.BLOCKING for non-draft PRs."""
    return Severity.INFO if ctx.pr.draft else Severity.BLOCKING


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
        if status in CanonicalPRContract.ALLOWED_ACCEPTANCE_STATUSES:
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
        for declaration in CanonicalPRContract.ALLOWED_READINESS_DECLARATIONS:
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
    if not title or title.lower() in CanonicalPRContract.PROHIBITED_TITLES:
        return Finding(
            "V-010",
            "PR title is missing or a placeholder",
            Severity.BLOCKING,
            f"title={title!r}; a descriptive title is required.",
        )
    return None


def _body_validator(ctx: ValidationContext) -> Finding | None:
    body = ctx.body.strip()
    severity = _structural_severity(ctx)
    if not body:
        return Finding(
            "V-020",
            "PR body is empty",
            severity,
            "the governance evidence package must be recorded in the PR description.",
        )
    if len(body) < 80:
        return Finding(
            "V-020",
            "PR body lacks the governance evidence package",
            severity,
            f"body is only {len(body)} characters; merge readiness requires evidence, not a stub.",
        )
    lowered = body.lower()
    for marker in CanonicalPRContract.PROHIBITED_PLACEHOLDERS:
        if marker in lowered:
            return Finding(
                "V-021",
                "PR body contains template placeholders",
                severity,
                f"found placeholder marker {marker!r}; replace it with real evidence.",
            )
    return None


def _sections_validator(ctx: ValidationContext) -> Finding | None:
    required = CanonicalPRContract.REQUIRED_SECTIONS if ctx.code_change else CanonicalPRContract.MINIMAL_SECTIONS
    missing = [section for section in required if not _has_section(ctx.body, section)]
    if missing:
        return Finding(
            "V-030",
            "PR body is missing required template sections",
            _structural_severity(ctx),
            "missing: " + ", ".join(missing),
        )
    return None


def _matrix_validator(ctx: ValidationContext) -> Finding | None:
    rows, placeholder = _parse_acceptance_matrix(ctx.body)
    severity = _structural_severity(ctx)
    if placeholder:
        return Finding(
            "V-040",
            "acceptance-criteria matrix contains the template placeholder row",
            severity,
            "replace the placeholder row with real criteria and evidence.",
        )
    if not rows:
        return Finding(
            "V-040",
            "acceptance-criteria matrix is missing",
            severity,
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
    severity = _structural_severity(ctx)
    if declared is None:
        return Finding(
            "V-050",
            "implementer readiness declaration is missing or ambiguous",
            severity,
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
            _structural_severity(ctx),
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


def is_trusted_dependency_pr(ctx: ValidationContext) -> bool:
    """Returns True if the PR is verified to be a trusted automated dependency update.

    A PR is classified as an automated dependency update only if:
    1. The PR author is a verified bot account (e.g. dependabot[bot] or renovate[bot]).
    2. The changed files ONLY modify strict dependency constraint, configuration, or lock files.
    """
    # 1. Author check: must be a trusted automated bot login.
    trusted_bots = {"dependabot[bot]", "renovate[bot]"}
    if ctx.pr.author_login not in trusted_bots:
        return False

    # 2. Changed files check: must exclusively touch dependency configuration or lock files.
    # Must NEVER allow modifications to source, scripts, tests, docs, or CI workflows.
    for f in ctx.files:
        filename = f.filename
        # Check directories/paths that are strictly human-owned code or documentation
        if (
            filename.startswith("src/")
            or filename.startswith("scripts/")
            or filename.startswith("tests/")
            or filename.startswith("docs/")
            or filename.startswith(".github/")
        ):
            return False
        # Must only touch requirements/ or specific lockfiles in the repository root
        allowed_dirs = ("requirements/",)
        allowed_root_files = ("pyproject.toml", "poetry.lock", "package-lock.json", "pnpm-lock.yaml")

        is_allowed_dir = any(filename.startswith(d) for d in allowed_dirs)
        is_allowed_file = filename in allowed_root_files

        if not (is_allowed_dir or is_allowed_file):
            return False

    return True


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
    if is_trusted_dependency_pr(ctx):
        # Automated Dependency Path: run a safe, streamlined subset of validators
        dependency_validators = (
            _title_validator,
            _adr_references_validator,
            _mergeable_validator,
            _gate_self_modification_validator,
            _draft_validator,
        )
        findings = [validator(ctx) for validator in dependency_validators]
    else:
        # Standard Manual Path for human PRs
        findings = [validator(ctx) for validator in _VALIDATORS]
    return DeterministicResult(findings=[finding for finding in findings if finding is not None])
