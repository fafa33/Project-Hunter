"""Canonical binding between an authorized writer and its Git commit identity.

Issue #412. PR #411 exposed a governance trap that this module closes: correct
code and an exact receipt could still be permanently non-admissible because the
commits were created under an implementation agent's Git identity rather than
the authorization-bound writer identity, and the mismatch was discovered only
after a hosted push.

Three provenance claims are deliberately kept separate here, because conflating
them is the defect:

``commit identity``
    The ``author`` and ``committer`` headers of a commit object. Caller-chosen,
    so they are checked against a closed allowlist rather than trusted.
``authenticated push actor``
    The GitHub account whose authenticated push published a commit. Not visible
    locally; it is verified by the trusted controller
    (``hunter_governance_review_v2``), never here.
``implementation attribution``
    Who or what wrote the change (a coding agent, a session URL). It lives in
    commit *trailers* only. This module reads commit headers exclusively, so a
    trailer can never establish, replace, or mutate the bound identity -- an
    agent keeps its attribution while the commit is still recorded under the
    authorization-bound writer.

Matching is exact after Unicode/whitespace/case normalisation, never substring
and never "one of author/committer matched, therefore allowed": the author and
the committer are each resolved to a bound identity independently, and the whole
governed range must resolve to one single writer. Missing policy, a malformed
binding, an empty allowlist, or unreadable commit metadata all fail closed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODE_WRITE_POLICY_RELATIVE_PATH = "docs/CODE_WRITE_POLICY.json"
CODE_WRITE_POLICY_PATH = ROOT / CODE_WRITE_POLICY_RELATIVE_PATH
BINDING_FIELD = "writer_identity_binding"
EXACT_MATCH_MODE = "exact-normalized"

#: One record per commit, as produced by ``git log`` with this format. The unit
#: separator cannot appear in a name, an email, or a SHA, and the record
#: separator cannot appear inside any of those fields either.
GIT_FIELD_SEPARATOR = "\x1f"
GIT_RECORD_SEPARATOR = "\x1e"
GIT_LOG_FORMAT = GIT_FIELD_SEPARATOR.join(("%H", "%an", "%ae", "%cn", "%ce")) + GIT_RECORD_SEPARATOR


def normalize_identity_value(value: str) -> str:
    """Fold one identity field to its comparison form.

    NFKC first, so a homoglyph-normalised spelling cannot present as a different
    identity than the one it compares equal to; then case folding and whitespace
    collapsing, because Git preserves both and neither distinguishes accounts.
    """

    folded = unicodedata.normalize("NFKC", value).strip().casefold()
    return " ".join(folded.split())


@dataclass(frozen=True)
class WriterIdentity:
    """One authorized writer and the exact Git identities bound to it."""

    login: str
    names: frozenset[str]
    emails: frozenset[str]
    canonical_name: str
    canonical_email: str

    def matches(self, name: str, email: str) -> bool:
        """True only when BOTH fields are bound to this same identity.

        A bound name with an unbound email is not a partial match that some other
        identity can complete: identity is the pair, so the conjunction is
        evaluated per identity rather than across the allowlist.
        """

        return normalize_identity_value(name) in self.names and normalize_identity_value(email) in self.emails


@dataclass(frozen=True)
class WriterIdentityBinding:
    """The canonical, closed allowlist of authorization-bound writer identities."""

    identities: tuple[WriterIdentity, ...]
    require_single_writer_per_range: bool

    def resolve(self, name: str, email: str) -> WriterIdentity | None:
        for identity in self.identities:
            if identity.matches(name, email):
                return identity
        return None

    def identity_for(self, login: str) -> WriterIdentity | None:
        wanted = normalize_identity_value(login)
        for identity in self.identities:
            if normalize_identity_value(identity.login) == wanted:
                return identity
        return None

    @property
    def logins(self) -> tuple[str, ...]:
        return tuple(identity.login for identity in self.identities)


@dataclass(frozen=True)
class CommitProvenance:
    """The identity headers of one commit in the governed range."""

    sha: str
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str


@dataclass(frozen=True)
class ProvenanceVerdict:
    """The outcome of evaluating provenance. ``reason`` is always populated."""

    ok: bool
    reason: str
    writer_login: str = ""


def _string_set(source: dict[str, Any], field: str) -> frozenset[str] | None:
    raw = source.get(field)
    if not isinstance(raw, list) or not raw:
        return None
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None
        values.add(normalize_identity_value(item))
    return frozenset(values)


def parse_binding(policy: Any) -> tuple[WriterIdentityBinding | None, str]:
    """Parse the writer identity binding, or explain why it is unusable.

    Never raises and never returns a partially trusted binding: any structural
    problem yields ``None``, so every caller fails closed on the same condition.
    """

    if not isinstance(policy, dict):
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} must be a JSON object"
    binding = policy.get(BINDING_FIELD)
    if not isinstance(binding, dict):
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} declares no {BINDING_FIELD} object"
    if binding.get("match") != EXACT_MATCH_MODE:
        return None, f"{BINDING_FIELD}.match must be {EXACT_MATCH_MODE!r}"
    if binding.get("require_author_and_committer_independently") is not True:
        return None, f"{BINDING_FIELD} must require author and committer to match independently"
    single_writer = binding.get("require_single_writer_per_range")
    if not isinstance(single_writer, bool):
        return None, f"{BINDING_FIELD}.require_single_writer_per_range must be a boolean"

    raw_identities = binding.get("identities")
    if not isinstance(raw_identities, list) or not raw_identities:
        return None, f"{BINDING_FIELD}.identities must be a non-empty array"

    identities: list[WriterIdentity] = []
    seen_logins: set[str] = set()
    for index, entry in enumerate(raw_identities):
        label = f"{BINDING_FIELD}.identities[{index}]"
        if not isinstance(entry, dict):
            return None, f"{label} must be an object"
        login = entry.get("login")
        if not isinstance(login, str) or not login.strip():
            return None, f"{label} must name one authorized writer login"
        normalized_login = normalize_identity_value(login)
        if normalized_login in seen_logins:
            return None, f"{label} binds login {login!r} a second time"
        seen_logins.add(normalized_login)

        names = _string_set(entry, "git_names")
        emails = _string_set(entry, "git_emails")
        if names is None:
            return None, f"{label} must bind a non-empty git_names array"
        if emails is None:
            return None, f"{label} must bind a non-empty git_emails array"

        canonical_name = entry.get("canonical_git_name")
        canonical_email = entry.get("canonical_git_email")
        if not isinstance(canonical_name, str) or normalize_identity_value(canonical_name) not in names:
            return None, f"{label} canonical_git_name must be one of its bound git_names"
        if not isinstance(canonical_email, str) or normalize_identity_value(canonical_email) not in emails:
            return None, f"{label} canonical_git_email must be one of its bound git_emails"

        identities.append(
            WriterIdentity(
                login=login.strip(),
                names=names,
                emails=emails,
                canonical_name=canonical_name.strip(),
                canonical_email=canonical_email.strip(),
            )
        )

    return WriterIdentityBinding(tuple(identities), single_writer), ""


def load_binding(path: Path | None = None) -> tuple[WriterIdentityBinding | None, str]:
    """Read the binding from repository-owned policy. Missing policy fails closed."""

    target = path or CODE_WRITE_POLICY_PATH
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} is missing"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})"
    return parse_binding(document)


def evaluate_commit(binding: WriterIdentityBinding, commit: CommitProvenance) -> ProvenanceVerdict:
    """Resolve one commit's author and committer to the same bound writer."""

    short = commit.sha[:10] or "(unknown)"
    author = binding.resolve(commit.author_name, commit.author_email)
    if author is None:
        return ProvenanceVerdict(
            False,
            f"commit {short} author {commit.author_name} <{commit.author_email}> is not an "
            f"authorization-bound writer identity",
        )
    committer = binding.resolve(commit.committer_name, commit.committer_email)
    if committer is None:
        return ProvenanceVerdict(
            False,
            f"commit {short} committer {commit.committer_name} <{commit.committer_email}> is not an "
            f"authorization-bound writer identity",
        )
    if author.login != committer.login:
        return ProvenanceVerdict(
            False,
            f"commit {short} splits its provenance: author is bound to {author.login!r} but committer is "
            f"bound to {committer.login!r}",
        )
    return ProvenanceVerdict(True, f"commit {short} is bound to writer {author.login!r}", author.login)


def evaluate_range(binding: WriterIdentityBinding, commits: tuple[CommitProvenance, ...]) -> ProvenanceVerdict:
    """Evaluate every commit in the governed range under one bound writer.

    An empty range is not silently admissible: a range that carries no commit
    evidence is exactly the state that cannot be checked, so it fails closed.
    """

    if not commits:
        return ProvenanceVerdict(False, "governed commit range carries no commit provenance evidence")

    writers: set[str] = set()
    for commit in commits:
        verdict = evaluate_commit(binding, commit)
        if not verdict.ok:
            return verdict
        writers.add(verdict.writer_login)

    if binding.require_single_writer_per_range and len(writers) > 1:
        return ProvenanceVerdict(
            False,
            "governed range mixes authorization-bound writers: " + ", ".join(sorted(writers)),
        )
    writer = sorted(writers)[0]
    return ProvenanceVerdict(
        True,
        f"{len(commits)} commit(s) in the governed range are bound to writer {writer!r}",
        writer,
    )


# --- Git evidence -----------------------------------------------------------


class GitEvidenceUnavailable(RuntimeError):
    """Commit metadata could not be read, so provenance is unknown, not clean."""


def _run_git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=False,
        capture_output=True,
        text=True,
        cwd=None if cwd is None else str(cwd),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise GitEvidenceUnavailable(detail)
    return completed.stdout


def parse_commit_records(raw: str) -> tuple[CommitProvenance, ...]:
    """Parse ``git log`` output produced with :data:`GIT_LOG_FORMAT`.

    A record that does not carry exactly the five expected fields is unreadable
    commit metadata, which fails closed rather than being skipped.
    """

    commits: list[CommitProvenance] = []
    for record in raw.split(GIT_RECORD_SEPARATOR):
        stripped = record.strip("\n")
        if not stripped.strip():
            continue
        fields = stripped.split(GIT_FIELD_SEPARATOR)
        if len(fields) != 5:
            raise GitEvidenceUnavailable("commit metadata could not be parsed into canonical provenance fields")
        sha, author_name, author_email, committer_name, committer_email = fields
        commits.append(
            CommitProvenance(
                sha=sha.strip(),
                author_name=author_name,
                author_email=author_email,
                committer_name=committer_name,
                committer_email=committer_email,
            )
        )
    return tuple(commits)


def read_range_commits(base: str, head: str, *, cwd: Path | None = None) -> tuple[CommitProvenance, ...]:
    """Commit provenance for ``base..head``, oldest first."""

    raw = _run_git("log", "--reverse", f"--format={GIT_LOG_FORMAT}", f"{base}..{head}", cwd=cwd)
    return parse_commit_records(raw)


def resolve_governed_base(head: str, *, base_ref: str = "main", remote: str = "origin", cwd: Path | None = None) -> str:
    """The fork point of ``head`` from the trusted base branch.

    Only commits introduced by the candidate are governed; history already on the
    base branch was admitted under whatever regime applied to it. When the base
    is not available locally the fork point is unknown, so this raises rather
    than falling back to a guess that would silently govern the wrong range.
    """

    # Only the remote-tracking ref is consulted. A local branch of the same name
    # can lag the remote, and a stale fork point would silently govern commits
    # that were merged under an earlier regime -- blocking a valid push over
    # history this binding has no authority over.
    for candidate in (f"{remote}/{base_ref}", f"refs/remotes/{remote}/{base_ref}"):
        try:
            merge_base = _run_git("merge-base", head, candidate, cwd=cwd).strip()
        except GitEvidenceUnavailable:
            continue
        if merge_base:
            return merge_base
    raise GitEvidenceUnavailable(
        f"the fork point from {remote}/{base_ref} is unavailable; run `git fetch {remote} {base_ref}` "
        "so the governed commit range can be determined"
    )


def check_range(head: str, *, base_ref: str = "main", remote: str = "origin", cwd: Path | None = None) -> str | None:
    """Validate the governed range, returning an actionable diagnosis or ``None``."""

    binding, error = load_binding()
    if binding is None:
        return f"writer provenance is unknown ({error})"
    try:
        base = resolve_governed_base(head, base_ref=base_ref, remote=remote, cwd=cwd)
        commits = read_range_commits(base, head, cwd=cwd)
    except GitEvidenceUnavailable as exc:
        return f"writer provenance evidence is unavailable ({exc})"

    if not commits:
        # Nothing new is being published, so there is no governed range to bind.
        return None

    verdict = evaluate_range(binding, commits)
    if verdict.ok:
        return None
    return f"{verdict.reason}. {remediation(binding)}"


def remediation(binding: WriterIdentityBinding) -> str:
    """The exact, copy-free instruction that repairs a provenance mismatch."""

    lines = [
        "Commits must be recorded under an authorization-bound writer identity from "
        f"{CODE_WRITE_POLICY_RELATIVE_PATH}; implementation-agent attribution belongs in trailers only.",
        "Bound identities: "
        + "; ".join(
            f"{identity.login} = {identity.canonical_name} <{identity.canonical_email}>"
            for identity in binding.identities
        ),
        "Configure the identity before creating the first commit: "
        "python scripts/hunter_writer_provenance.py --print-identity --login <login>",
    ]
    return " ".join(lines)


def check_configured_identity(login: str | None = None, *, cwd: Path | None = None) -> str | None:
    """Validate the *currently configured* Git identity before any commit exists."""

    binding, error = load_binding()
    if binding is None:
        return f"writer provenance is unknown ({error})"
    try:
        name = _run_git("config", "user.name", cwd=cwd).strip()
        email = _run_git("config", "user.email", cwd=cwd).strip()
    except GitEvidenceUnavailable as exc:
        return f"configured Git identity is unavailable ({exc}). {remediation(binding)}"

    identity = binding.resolve(name, email)
    if identity is None:
        return f"configured Git identity {name} <{email}> is not authorization-bound. {remediation(binding)}"
    if login is not None:
        wanted = binding.identity_for(login)
        if wanted is None:
            return f"{login!r} is not an authorization-bound writer ({', '.join(binding.logins)})"
        if wanted.login != identity.login:
            return f"configured Git identity resolves to {identity.login!r}, not the requested {wanted.login!r}"
    return None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Resolve and validate the authorization-bound Git writer identity from repository-owned policy, "
            "before commits are created and again over the governed commit range."
        )
    )
    result.add_argument("--login", help="Restrict to one authorized writer login.")
    result.add_argument(
        "--print-identity",
        action="store_true",
        help="Print the canonical Git author/committer identity to configure before the first commit.",
    )
    result.add_argument(
        "--check-config",
        action="store_true",
        help="Verify the currently configured Git identity is authorization-bound.",
    )
    result.add_argument("--check-range", metavar="HEAD", help="Verify every commit in the governed base..HEAD range.")
    result.add_argument("--base-ref", default="main", help="Trusted base branch name (default: main).")
    result.add_argument("--remote", default="origin", help="Remote holding the trusted base branch (default: origin).")
    return result


def main() -> int:
    args = parser().parse_args()
    if not (args.print_identity or args.check_config or args.check_range):
        parser().print_help()
        return 2

    binding, error = load_binding()
    if binding is None:
        print(f"[Writer Provenance] FAIL: {error}", file=sys.stderr)
        return 2

    if args.print_identity:
        identities = binding.identities
        if args.login is not None:
            selected = binding.identity_for(args.login)
            if selected is None:
                print(
                    f"[Writer Provenance] FAIL: {args.login!r} is not an authorization-bound writer "
                    f"({', '.join(binding.logins)})",
                    file=sys.stderr,
                )
                return 2
            identities = (selected,)
        for identity in identities:
            print(f"{identity.login}\t{identity.canonical_name}\t{identity.canonical_email}")

    if args.check_config:
        problem = check_configured_identity(args.login)
        if problem:
            print(f"[Writer Provenance] FAIL: {problem}", file=sys.stderr)
            return 1
        print("[Writer Provenance] PASS: configured Git identity is authorization-bound")

    if args.check_range:
        problem = check_range(args.check_range, base_ref=args.base_ref, remote=args.remote)
        if problem:
            print(f"[Writer Provenance] FAIL: {problem}", file=sys.stderr)
            return 1
        print(f"[Writer Provenance] PASS: governed range up to {args.check_range} is authorization-bound")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
