"""Deterministic identity and receipts for expensive repository validation.

Issue #415: one immutable candidate was paying for the same full repository
Pytest suite up to four times -- an agent's manual run, the pre-push hook, the
hosted branch preflight, and pull-request CI -- so a single unchanged head cost
half an hour of identical work before anyone could review it. Removing that
duplication safely needs one question answered the same way everywhere: is an
existing proof actually a proof *of the work in front of me*?

This module answers it, and only it. A proof is reusable when three identities
agree:

``content``
    The exact tree being validated, named by its Git tree object. Content
    addressing is what makes "the same candidate" a fact rather than a label,
    so a changed HEAD, a foreign head, or an integration tree that differs from
    the validated candidate tree can never collide with a proof it did not earn.

``definition``
    What the full lane actually *does*: the gate chain plus the files that
    define it. A validation definition change invalidates every prior proof
    even when the code under test is untouched.

``toolchain``
    The versions that executed it. A tree pins its toolchain, but a runner can
    still install something else, so the executing versions are measured rather
    than assumed.

A receipt is never a trust anchor. Every identity it carries is recomputed by
the verifier from content the verifier already holds, so a receipt can only
ever *narrow* reuse: one that agrees with nothing is refused, and one that
agrees is authorizing nothing the verifier did not independently establish.
Absent, malformed, stale, failed and mismatched receipts all fail closed onto
running the full lane.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path

RECEIPT_SCHEMA = "hunter.validation-receipt/1"

#: The only lane a receipt may stand for. The push-safety lane is cheap and is
#: never reused: its whole purpose is to run again on the exact head being
#: published.
FULL_LANE = "full"
LANES = frozenset({FULL_LANE})

PASSED = "passed"
FAILED = "failed"
RESULTS = frozenset({PASSED, FAILED})

#: A receipt is bound to immutable identity, so age is not what makes it wrong.
#: The bound exists because the environment around an identity does drift, and
#: an old proof is the one most likely to have been produced by something this
#: verifier can no longer observe. Fail closed rather than trust indefinitely.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

RECEIPT_DIR = Path(".hunter") / "validation-receipts"
RECEIPT_NAME = "full.json"

#: Files whose content decides what the full validation lane means. A change to
#: any of them changes the definition identity and retires every prior proof.
#: The gate chain itself is folded in separately, so a gate list edited without
#: touching a file here still invalidates reuse.
DEFINITION_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    ".github/workflows/hunter-pre-pr-preflight.yml",
    "pyproject.toml",
    "requirements/ci-constraints.txt",
    "scripts/hunter_architecture_index_preflight.py",
    "scripts/hunter_artifact_preflight.py",
    "scripts/hunter_defect_prevention_preflight.py",
    "scripts/hunter_pr_preflight.py",
    "scripts/hunter_validation_receipt.py",
    "tests/conftest.py",
)

#: Distributions that actually execute the gates. Everything else the lane
#: imports is pinned transitively by requirements/ci-constraints.txt, which is
#: already part of the definition identity.
TOOLCHAIN_DISTRIBUTIONS: tuple[str, ...] = ("black", "mypy", "pytest", "ruff")

_PYTHON_VERSION_RE = re.compile(r"^\s*python-version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)[\"']?\s*$", re.MULTILINE)
_PIN_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*==\s*([^\s#]+)\s*$")
_TREE_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

#: Workflows that install the toolchain the full lane runs under. Both must pin
#: the same interpreter, otherwise "the pinned toolchain" is not a single thing
#: and no reuse decision can be made from it.
_TOOLCHAIN_PIN_WORKFLOWS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    ".github/workflows/hunter-pre-pr-preflight.yml",
)


class ValidationEvidenceUnavailable(RuntimeError):
    """Identity evidence could not be established, so nothing may be reused."""


def _digest(parts: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x00")
    return f"sha256:{hasher.hexdigest()}"


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValidationEvidenceUnavailable(detail)
    return completed.stdout.strip()


def is_object_sha(value: str) -> bool:
    """Whether ``value`` is a fully resolved 40-hex Git object name."""
    return bool(_TREE_SHA_RE.match(value or ""))


def tree_identity(tree_sha: str) -> str:
    """Name a Git tree object as a content identity."""
    if not _TREE_SHA_RE.match(tree_sha):
        raise ValidationEvidenceUnavailable(f"not a resolved tree object: {tree_sha!r}")
    return f"tree:{tree_sha}"


def commit_tree_identity(root: Path, commit: str) -> str:
    """The content identity of an arbitrary commit, for cross-checking a head."""
    return tree_identity(_run_git(root, "rev-parse", f"{commit}^{{tree}}"))


def resolve_head_sha(root: Path) -> str:
    """The exact commit whose tree a receipt is being bound to."""
    return _run_git(root, "rev-parse", "HEAD")


def content_identity(root: Path) -> str:
    """The content identity of the checked-out tree.

    A dirty tree is refused rather than approximated: the committed tree is the
    only thing a receipt can be bound to, and validating a worktree that no
    commit describes would produce a proof no later verifier could reconstruct.
    """
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ValidationEvidenceUnavailable("working tree is not clean, so no content identity exists")
    return commit_tree_identity(root, "HEAD")


def definition_identity(root: Path) -> str:
    """Identity of what the full lane does, over its declared definition files."""
    import hunter_pr_preflight as preflight

    parts: list[str] = [RECEIPT_SCHEMA, FULL_LANE]
    for name, command in preflight.NORMAL_QUALITY_GATES:
        parts.append(f"gate:{name}:{' '.join(command)}")
    for relative in DEFINITION_PATHS:
        path = root / relative
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValidationEvidenceUnavailable(
                f"validation definition file is unreadable: {relative} ({exc})"
            ) from exc
        parts.append(f"file:{relative}:{hashlib.sha256(payload).hexdigest()}")
    return _digest(parts)


def measured_toolchain() -> dict[str, str]:
    """The versions actually installed in this interpreter."""
    resolved = {"python": ".".join(str(part) for part in sys.version_info[:3])}
    for name in TOOLCHAIN_DISTRIBUTIONS:
        try:
            resolved[name] = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise ValidationEvidenceUnavailable(f"validation toolchain distribution is missing: {name}") from exc
    return resolved


def pinned_toolchain(root: Path) -> dict[str, str]:
    """The versions the tree pins for the full lane.

    Comparing this against :func:`measured_toolchain` is what lets one boundary
    conclude something about another's toolchain without shipping it a receipt:
    two runs that both install from the same pinned files in the same tree, and
    both match the pin, match each other.
    """
    interpreters: set[str] = set()
    for relative in _TOOLCHAIN_PIN_WORKFLOWS:
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationEvidenceUnavailable(f"toolchain pin file is unreadable: {relative} ({exc})") from exc
        found = _PYTHON_VERSION_RE.findall(content)
        if not found:
            raise ValidationEvidenceUnavailable(f"{relative} pins no interpreter version")
        interpreters.update(found)
    if len(interpreters) != 1:
        raise ValidationEvidenceUnavailable(
            f"validation workflows disagree on the pinned interpreter: {sorted(interpreters)}"
        )

    constraints = root / "requirements" / "ci-constraints.txt"
    try:
        lines = constraints.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationEvidenceUnavailable(f"toolchain constraints are unreadable ({exc})") from exc

    pins: dict[str, str] = {}
    for line in lines:
        match = _PIN_RE.match(line)
        if match is None:
            continue
        pins[match.group(1).strip().lower().replace("_", "-")] = match.group(2).strip()

    resolved = {"python": interpreters.pop()}
    for name in TOOLCHAIN_DISTRIBUTIONS:
        pinned = pins.get(name)
        if pinned is None:
            raise ValidationEvidenceUnavailable(f"requirements/ci-constraints.txt pins no version for {name}")
        resolved[name] = pinned
    return resolved


def toolchain_identity(toolchain: Mapping[str, str]) -> str:
    return _digest(f"{name}=={version}" for name, version in sorted(toolchain.items()))


@dataclass(frozen=True)
class ValidationIdentity:
    """What a proof must be a proof *of* to be reusable."""

    content: str
    definition: str
    toolchain: str


def current_identity(root: Path) -> ValidationIdentity:
    return ValidationIdentity(
        content=content_identity(root),
        definition=definition_identity(root),
        toolchain=toolchain_identity(measured_toolchain()),
    )


def _text(document: Mapping[str, object], key: str) -> tuple[str, str]:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        return "", f"{key} must be a non-empty string"
    return value.strip(), ""


@dataclass(frozen=True)
class ValidationReceipt:
    """A recorded claim that one lane ran, over one identity, with one result."""

    lane: str
    result: str
    head_sha: str
    content_identity: str
    definition_identity: str
    toolchain_identity: str
    produced_at: datetime
    produced_by: str

    @classmethod
    def from_document(cls, document: object) -> tuple[ValidationReceipt | None, str]:
        """Parse a receipt, refusing anything that is not exactly one."""
        if not isinstance(document, Mapping):
            return None, "receipt must be a JSON object"
        schema = document.get("schema")
        if schema != RECEIPT_SCHEMA:
            return None, f"unsupported receipt schema {schema!r}"

        values: dict[str, str] = {}
        for key in (
            "lane",
            "result",
            "head_sha",
            "content_identity",
            "definition_identity",
            "toolchain_identity",
            "produced_at",
            "produced_by",
        ):
            value, problem = _text(document, key)
            if problem:
                return None, problem
            values[key] = value

        if values["lane"] not in LANES:
            return None, f"unsupported receipt lane {values['lane']!r}"
        if values["result"] not in RESULTS:
            return None, f"unsupported receipt result {values['result']!r}"
        try:
            produced_at = datetime.fromisoformat(values["produced_at"].replace("Z", "+00:00"))
        except ValueError:
            return None, f"produced_at is not an ISO-8601 instant: {values['produced_at']!r}"
        if produced_at.tzinfo is None:
            return None, "produced_at must carry an explicit timezone"

        return (
            cls(
                lane=values["lane"],
                result=values["result"],
                head_sha=values["head_sha"],
                content_identity=values["content_identity"],
                definition_identity=values["definition_identity"],
                toolchain_identity=values["toolchain_identity"],
                produced_at=produced_at.astimezone(UTC),
                produced_by=values["produced_by"],
            ),
            "",
        )

    def to_document(self) -> dict[str, str]:
        return {
            "schema": RECEIPT_SCHEMA,
            "lane": self.lane,
            "result": self.result,
            "head_sha": self.head_sha,
            "content_identity": self.content_identity,
            "definition_identity": self.definition_identity,
            "toolchain_identity": self.toolchain_identity,
            "produced_at": self.produced_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "produced_by": self.produced_by,
        }


def verify(
    receipt: ValidationReceipt,
    expected: ValidationIdentity,
    *,
    head_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> str | None:
    """Return why ``receipt`` may not be reused, or ``None`` when it may.

    Every branch here is a refusal. There is deliberately no path that accepts
    a receipt because of something the receipt itself asserts.

    ``head_sha`` is required rather than optional. It was optional once, and the
    local reuse path silently omitted it while the push boundary supplied it --
    so content identity alone decided reuse on one of the two production paths.
    Two commits can carry the same tree (an amended message is the everyday
    case), so that path would have let a proof recorded for one candidate
    authorize another. A binding that can be dropped by forgetting a keyword is
    not a binding; having no default is what makes forgetting impossible.
    """
    if receipt.lane != FULL_LANE:
        return f"receipt is for the {receipt.lane!r} lane, not the full repository lane"
    if receipt.result != PASSED:
        return f"receipt records result {receipt.result!r}, which is not a proof"
    if receipt.content_identity != expected.content:
        return f"receipt content identity {receipt.content_identity} is not {expected.content}"
    if receipt.head_sha != head_sha:
        return f"receipt head {receipt.head_sha} is foreign to {head_sha}"
    if receipt.definition_identity != expected.definition:
        return "validation definition changed since the receipt was produced"
    if receipt.toolchain_identity != expected.toolchain:
        return "validation toolchain changed since the receipt was produced"

    moment = now if now is not None else datetime.now(UTC)
    age = moment - receipt.produced_at
    if age < timedelta(0):
        return "receipt was produced in the future"
    if age > timedelta(seconds=max_age_seconds):
        return f"receipt is stale ({int(age.total_seconds())}s older than the {max_age_seconds}s bound)"
    return None


def receipt_path(root: Path) -> Path:
    return root / RECEIPT_DIR / RECEIPT_NAME


def load(path: Path) -> tuple[ValidationReceipt | None, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no receipt has been recorded for this repository"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"receipt is unreadable ({type(exc).__name__}: {exc})"
    return ValidationReceipt.from_document(document)


def store(receipt: ValidationReceipt, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt.to_document(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record(root: Path, *, head_sha: str, produced_by: str, result: str = PASSED) -> ValidationReceipt:
    """Record the outcome of a full-lane run over the current identity."""
    identity = current_identity(root)
    receipt = ValidationReceipt(
        lane=FULL_LANE,
        result=result,
        head_sha=head_sha,
        content_identity=identity.content,
        definition_identity=identity.definition,
        toolchain_identity=identity.toolchain,
        produced_at=datetime.now(UTC),
        produced_by=produced_by,
    )
    store(receipt, receipt_path(root))
    return receipt


def reuse_blocker(
    root: Path,
    *,
    head_sha: str | None = None,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> str | None:
    """Why the recorded local receipt may not stand in for a full-lane run.

    ``head_sha`` is resolved from the repository when the caller has not already
    established one. A caller that has -- the push boundary, which validated the
    exact head before anything else -- passes it in, but a caller that simply
    asks "may I reuse?" must not thereby get an unbound answer.
    """
    try:
        expected = current_identity(root)
        bound_head = head_sha if head_sha is not None else resolve_head_sha(root)
    except ValidationEvidenceUnavailable as exc:
        return f"validation identity is unavailable ({exc})"
    receipt, problem = load(receipt_path(root))
    if receipt is None:
        return problem
    return verify(receipt, expected, head_sha=bound_head, now=now, max_age_seconds=max_age_seconds)
