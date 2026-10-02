"""Canonical binding between an authorized writer and its Git commit identity.

Issue #412. PR #411 exposed a governance trap that this module closes: correct
code and an exact receipt could still be permanently non-admissible because the
commits were created under an implementation agent's Git identity rather than
the authorization-bound writer identity, and the mismatch was discovered only
after a hosted push.

Four provenance claims are deliberately kept separate here, because conflating
them is the defect:

``commit identity``
    The ``author`` and ``committer`` headers of a commit object. Caller-chosen,
    so they are checked against a closed allowlist rather than trusted.
``signing key identity``
    The SSH signing key embedded in the commit's ``gpgsig`` header, read from
    the commit object itself. Header identity alone is caller-chosen, so the key
    that actually signed the commit is bound to the same writer. This is what
    keeps the local boundary aligned with trusted hosted governance, which
    resolves the signature rather than the headers.
``authenticated push actor``
    The GitHub account whose authenticated push published a commit. Not visible
    locally; it is verified by the trusted controller
    (``hunter_governance_review_v2``), never here.
``implementation attribution``
    Who or what wrote the change (a coding agent, a session URL). It lives in
    commit *trailers* only, so an agent keeps its attribution while the commit is
    still recorded under the authorization-bound writer. No trailer can
    establish, replace or mutate a bound identity.

Exactly one trailer is read, and only on a path of its own: the
``Hunter-Writer-Recovery`` declaration, which is consulted after a commit's
identity and signing key have already been resolved and bound. It is a
*permission* to continue a range under a different writer, never evidence of
who wrote anything, so reading it cannot make an unbound or mis-signed commit
admissible. Any other trailer is never parsed.

Matching is exact after Unicode/whitespace/case normalisation, never substring
and never "one of author/committer matched, therefore allowed": the author and
the committer are each resolved to a bound identity independently, and each
epoch of the governed range must resolve to one single writer. Missing policy, a
malformed binding, an empty allowlist, or unreadable commit metadata all fail
closed.

A governed range that genuinely contains two authorized writers is neither
silently allowed nor silently refused: it is a recovery case, and it is admitted
only through an explicit, owner-signed recovery boundary that leaves every
earlier commit attributed exactly as it already was.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODE_WRITE_POLICY_RELATIVE_PATH = "docs/CODE_WRITE_POLICY.json"
CODE_WRITE_POLICY_PATH = ROOT / CODE_WRITE_POLICY_RELATIVE_PATH
BINDING_FIELD = "writer_identity_binding"
EXACT_MATCH_MODE = "exact-normalized"

SIGNING_KEY_BINDINGS_FIELD = "signing_key_bindings"
OWNER_WRITER_FIELD = "owner_writer"
OWNER_RECOVERY_FIELD = "owner_recovery"

#: An SSH SHA-256 key fingerprint exactly as git prints it in ``%GK``.
SSH_KEY_FINGERPRINT = re.compile(r"\ASHA256:[A-Za-z0-9+/]{43}\Z")

#: One record per commit, as produced by ``git log`` with this format. The unit
#: separator cannot appear in a name, an email, a SHA, or a key fingerprint,
#: and the record separator cannot appear inside any of those fields either.
#: ``%P`` carries the parent list and ``%GK`` the SSH signing key fingerprint,
#: both read from the commit object rather than from anything the caller chose.
GIT_FIELD_SEPARATOR = "\x1f"
GIT_RECORD_SEPARATOR = "\x1e"
GIT_LOG_FORMAT = GIT_FIELD_SEPARATOR.join(("%H", "%an", "%ae", "%cn", "%ce", "%P", "%GK")) + GIT_RECORD_SEPARATOR

#: git populates ``%GK`` for an SSH signature only after running signature
#: verification, and it refuses to verify at all unless
#: ``gpg.ssh.allowedSignersFile`` names an existing file. Without that setting --
#: the default on hosted runners and fresh clones -- every commit would read as
#: unsigned. An empty allowed-signers file lets git check the signature against
#: the key embedded in the commit (``%G?`` = ``U``) without trusting any local
#: keyring, so the fingerprint is read identically everywhere. A signature that
#: does not verify still yields no fingerprint and fails closed.
GIT_SIGNATURE_READ_CONFIG = ("-c", f"gpg.ssh.allowedSignersFile={os.devnull}")


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
    signing_keys: frozenset[str] = frozenset()

    def matches(self, name: str, email: str) -> bool:
        """True only when BOTH fields are bound to this same identity.

        A bound name with an unbound email is not a partial match that some other
        identity can complete: identity is the pair, so the conjunction is
        evaluated per identity rather than across the allowlist.
        """

        return normalize_identity_value(name) in self.names and normalize_identity_value(email) in self.emails


@dataclass(frozen=True)
class OwnerRecovery:
    """The explicit owner-authorized writer-recovery boundary."""

    owner_login: str
    trailer: str
    schema: str
    max_boundaries: int


@dataclass(frozen=True)
class WriterIdentityBinding:
    """The canonical, closed allowlist of authorization-bound writer identities."""

    identities: tuple[WriterIdentity, ...]
    require_single_writer_per_range: bool
    require_key_bound_to_writer: bool = False
    owner_recovery: OwnerRecovery | None = None

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
    parents: str = ""
    signing_key: str = ""
    recovery_declaration: str = ""

    @property
    def first_parent(self) -> str:
        return self.parents.split(" ")[0].strip()


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


def _fingerprint_set(entry: dict[str, Any], label: str) -> frozenset[str] | None:
    raw = entry if isinstance(entry, list) else []
    if not raw:
        return None
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not SSH_KEY_FINGERPRINT.match(item.strip()):
            return None
        values.add(item.strip())
    return frozenset(values)


def parse_signing_key_bindings(policy: Any) -> tuple[dict[str, frozenset[str]] | None, str]:
    """Parse writer -> signing-key fingerprint bindings.

    Returns ``None`` on any structural problem, so every caller fails closed on
    the same condition rather than silently treating a broken binding as "no
    binding required".
    """

    if not isinstance(policy, dict):
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} must be a JSON object"
    scope = policy.get(BINDING_FIELD)
    scope = scope if isinstance(scope, dict) else {}
    section = scope.get(SIGNING_KEY_BINDINGS_FIELD)
    if not isinstance(section, dict):
        return None, f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD} must be an object"
    raw = section.get("bindings")
    if not isinstance(raw, dict) or not raw:
        return None, f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD}.bindings must be a non-empty object"

    bindings: dict[str, frozenset[str]] = {}
    for login, value in raw.items():
        if not isinstance(login, str) or not login.strip():
            return None, f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD}.bindings has an unnamed writer"
        keys = _fingerprint_set(value, login)
        if keys is None:
            return None, (
                f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD}.bindings.{login} must be a non-empty array of "
                "full SSH SHA-256 fingerprints"
            )
        bindings[login.strip()] = keys
    return bindings, ""


def parse_owner_recovery(policy: Any) -> tuple[OwnerRecovery | None, str]:
    """Parse the owner-recovery boundary rule, or explain why it is unusable.

    ``None`` with a reason means recovery is unavailable. Recovery is
    unavailable in two distinct ways -- absent policy and broken policy -- and
    both are handled by the caller as "no recovery may be granted", so a
    malformed grant can never widen what a range is allowed to contain.
    """

    if not isinstance(policy, dict):
        return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} must be a JSON object"
    scope = policy.get(BINDING_FIELD)
    scope = scope if isinstance(scope, dict) else {}
    owner = scope.get(OWNER_WRITER_FIELD)
    if not isinstance(owner, dict):
        return None, f"{BINDING_FIELD}.{OWNER_WRITER_FIELD} must be an object"
    owner_login = owner.get("login")
    if not isinstance(owner_login, str) or not owner_login.strip():
        return None, f"{BINDING_FIELD}.{OWNER_WRITER_FIELD}.login must name the repository owner"

    section = scope.get(OWNER_RECOVERY_FIELD)
    if not isinstance(section, dict):
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD} must be an object"
    if section.get("enabled") is not True:
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD} is not enabled"

    trailer = section.get("declaration_trailer")
    if not isinstance(trailer, str) or not trailer.strip():
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD}.declaration_trailer must be a non-empty string"
    schema = section.get("declaration_schema")
    if not isinstance(schema, str) or not schema.strip():
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD}.declaration_schema must be a non-empty string"

    max_boundaries = section.get("max_boundaries_per_range")
    if not isinstance(max_boundaries, int) or isinstance(max_boundaries, bool) or max_boundaries < 1:
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD}.max_boundaries_per_range must be a positive integer"

    required = section.get("required_claims")
    expected = {"schema", "departed_writer", "recovery_writer", "parent_sha"}
    if not isinstance(required, list) or {str(item) for item in required} != expected:
        return None, f"{BINDING_FIELD}.{OWNER_RECOVERY_FIELD}.required_claims must declare exactly {sorted(expected)}"

    return OwnerRecovery(owner_login.strip(), trailer.strip(), schema.strip(), max_boundaries), ""


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

    # The signing-key binding is what stops a caller-chosen author/committer
    # header from standing in for a signature. It is parsed before the identity
    # loop so every identity inherits its own key set, and a binding naming a
    # login no identity binds is a structural error rather than a silently
    # ignored extra allowlist.
    signing_keys, key_error = parse_signing_key_bindings(policy)
    if signing_keys is None:
        return None, key_error
    require_key_binding = binding.get(SIGNING_KEY_BINDINGS_FIELD)
    require_key_binding = (
        require_key_binding.get("require_key_bound_to_resolved_writer") is True
        if isinstance(require_key_binding, dict)
        else False
    )
    bound_logins = {
        entry.get("login").strip()
        for entry in raw_identities
        if isinstance(entry, dict) and isinstance(entry.get("login"), str)
    }
    unbound_keys = sorted(login for login in signing_keys if login not in bound_logins)
    if unbound_keys:
        return None, (
            f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD} binds signing keys for logins no identity binds: "
            + ", ".join(unbound_keys)
        )
    missing_keys = sorted(login for login in bound_logins if login not in signing_keys)
    if require_key_binding and missing_keys:
        return None, (
            f"{BINDING_FIELD}.{SIGNING_KEY_BINDINGS_FIELD} binds no signing key for: " + ", ".join(missing_keys)
        )

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
                signing_keys=signing_keys.get(login.strip(), frozenset()),
            )
        )

    owner_recovery, recovery_error = parse_owner_recovery(policy)
    return (
        WriterIdentityBinding(
            tuple(identities),
            single_writer,
            require_key_bound_to_writer=require_key_binding,
            owner_recovery=owner_recovery if recovery_error == "" else None,
        ),
        "",
    )


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


def verify_signing_key_identity(
    binding: WriterIdentityBinding, commit: CommitProvenance, writer: WriterIdentity
) -> str | None:
    """Bind the key that actually signed the commit to the writer it claims.

    The author/committer headers are caller-chosen, so on their own they assert
    an identity without proving it. ``%GK`` reads the SSH signing key embedded
    in the commit's own ``gpgsig`` header, so it is commit evidence rather than
    a claim, and it needs no keyring, no private key and no network.

    A mismatch here is precisely what trusted hosted governance reports as
    ``unknown_key``: GitHub resolves the signature against the *claimed*
    committer and finds no key registered to that account. Checking it here
    moves that verdict from after publication to before it.
    """

    if not binding.require_key_bound_to_writer:
        return None
    short = commit.sha[:10] or "(unknown)"
    key = commit.signing_key.strip()
    if not key:
        return (
            f"commit {short} claims writer {writer.login!r} but carries no readable SSH signing key, so nothing "
            "binds the claimed identity to a signature"
        )
    if not writer.signing_keys:
        return f"commit {short} writer {writer.login!r} has no signing key bound in policy"
    if key not in writer.signing_keys:
        return (
            f"commit {short} is claimed by writer {writer.login!r} but is signed with {key}, which is not bound "
            f"to {writer.login!r}. This is the identity/key mismatch trusted hosted governance rejects as "
            "unknown_key: re-sign the commit with a key registered to the identity it claims"
        )
    return None


def parse_recovery_declaration(commit: CommitProvenance, recovery: OwnerRecovery) -> tuple[dict[str, Any] | None, str]:
    """Parse the owner-recovery declaration carried by one commit, if any.

    Returns ``(None, "")`` when the commit carries no declaration at all, and
    ``(None, reason)`` when it carries one that cannot be trusted. The two are
    different outcomes on purpose: an absent declaration is ordinary history,
    while a malformed one is an attempted grant that fails closed.
    """

    raw = commit.recovery_declaration.strip()
    if not raw:
        return None, ""
    short = commit.sha[:10] or "(unknown)"
    try:
        document = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return None, f"commit {short} carries a malformed {recovery.trailer} declaration ({type(exc).__name__})"
    if not isinstance(document, dict):
        return None, f"commit {short} {recovery.trailer} declaration is not a JSON object"
    expected = {"schema", "departed_writer", "recovery_writer", "parent_sha"}
    if set(document) != expected:
        return None, (
            f"commit {short} {recovery.trailer} declaration must carry exactly {sorted(expected)}, "
            f"not {sorted(document)}"
        )
    if document.get("schema") != recovery.schema:
        return None, (
            f"commit {short} {recovery.trailer} declaration schema {document.get('schema')!r} is not "
            f"{recovery.schema!r}"
        )
    for claim in ("departed_writer", "recovery_writer"):
        value = document.get(claim)
        if not isinstance(value, str) or not value.strip():
            return None, f"commit {short} {recovery.trailer} declaration has an empty {claim}"
    parent = document.get("parent_sha")
    if not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{40}", parent.strip().lower()):
        return None, f"commit {short} {recovery.trailer} declaration parent_sha is not a full commit SHA"
    return document, ""


def evaluate_owner_recovery(
    binding: WriterIdentityBinding,
    recovery: OwnerRecovery,
    resolved: tuple[tuple[CommitProvenance, WriterIdentity], ...],
) -> ProvenanceVerdict:
    """Admit a mixed range only through one explicit, owner-signed boundary.

    Everything checked here is what separates this from simply permitting mixed
    writers: the boundary is declared explicitly rather than inferred, it is
    authored and signed by the owner rather than by any agent, the departed and
    recovery writers were both already authorized before the boundary existed,
    each epoch is independently single-writer, and the commits before the
    boundary are evaluated exactly as they always were and never re-attributed.
    """

    declared: list[tuple[int, CommitProvenance, dict[str, Any]]] = []
    for index, (commit, _writer) in enumerate(resolved):
        declaration, error = parse_recovery_declaration(commit, recovery)
        if error:
            return ProvenanceVerdict(False, error)
        if declaration is not None:
            declared.append((index, commit, declaration))

    if not declared:
        writers = sorted({writer.login for _commit, writer in resolved})
        return ProvenanceVerdict(
            False,
            f"governed range mixes authorization-bound writers: {', '.join(writers)}",
        )
    if len(declared) > recovery.max_boundaries:
        return ProvenanceVerdict(
            False,
            f"governed range declares {len(declared)} writer-recovery boundaries; at most "
            f"{recovery.max_boundaries} is permitted",
        )

    index, boundary, declaration = declared[0]
    short = boundary.sha[:10] or "(unknown)"
    _boundary_commit, boundary_writer = resolved[index]

    if normalize_identity_value(boundary_writer.login) != normalize_identity_value(recovery.owner_login):
        return ProvenanceVerdict(
            False,
            f"commit {short} declares a writer-recovery boundary but is bound to {boundary_writer.login!r}, not "
            f"the repository owner {recovery.owner_login!r}",
        )
    departed = declaration["departed_writer"].strip()
    recovery_writer = declaration["recovery_writer"].strip()
    if normalize_identity_value(recovery_writer) != normalize_identity_value(recovery.owner_login):
        return ProvenanceVerdict(
            False,
            f"commit {short} declares recovery_writer {recovery_writer!r}, which is not the repository owner "
            f"{recovery.owner_login!r}",
        )
    if binding.identity_for(departed) is None:
        return ProvenanceVerdict(
            False,
            f"commit {short} declares departed_writer {departed!r}, which is not an authorization-bound writer "
            f"({', '.join(binding.logins)})",
        )
    if declaration["parent_sha"].strip().lower() != boundary.first_parent.lower():
        return ProvenanceVerdict(
            False,
            f"commit {short} declares parent_sha {declaration['parent_sha'][:10]} but its exact parent is "
            f"{boundary.first_parent[:10] or '(none)'}",
        )

    if index == 0:
        return ProvenanceVerdict(
            False,
            f"commit {short} declares a writer-recovery boundary at the first position of the governed range, "
            "where there is no departed writer epoch to recover from",
        )

    departed_writers = {writer.login for _commit, writer in resolved[:index]}
    if len(departed_writers) != 1:
        return ProvenanceVerdict(
            False,
            "the departed epoch before the recovery boundary must be single-writer, but it mixes: "
            + ", ".join(sorted(departed_writers)),
        )
    actual_departed = next(iter(departed_writers))
    if normalize_identity_value(actual_departed) != normalize_identity_value(departed):
        return ProvenanceVerdict(
            False,
            f"commit {short} declares departed_writer {departed!r} but the epoch before it is written by "
            f"{actual_departed!r}",
        )

    recovery_writers = {writer.login for _commit, writer in resolved[index:]}
    if recovery_writers != {boundary_writer.login}:
        foreign = sorted(recovery_writers - {boundary_writer.login})
        return ProvenanceVerdict(
            False,
            f"the recovery epoch after the owner boundary must contain only {boundary_writer.login!r}, but it "
            f"also carries: {', '.join(foreign)}",
        )

    return ProvenanceVerdict(
        True,
        f"{len(resolved)} commit(s) in the governed range are single-writer across an owner-authorized recovery "
        f"boundary at {short}: epoch {actual_departed!r} ({index} commit(s)) then epoch "
        f"{boundary_writer.login!r} ({len(resolved) - index} commit(s)). Historical attribution before the "
        "boundary is unchanged.",
        boundary_writer.login,
    )


def evaluate_range(binding: WriterIdentityBinding, commits: tuple[CommitProvenance, ...]) -> ProvenanceVerdict:
    """Evaluate every commit in the governed range, one single-writer epoch at a time.

    An empty range is not silently admissible: a range that carries no commit
    evidence is exactly the state that cannot be checked, so it fails closed.
    """

    if not commits:
        return ProvenanceVerdict(False, "governed commit range carries no commit provenance evidence")

    resolved: list[tuple[CommitProvenance, WriterIdentity]] = []
    for commit in commits:
        verdict = evaluate_commit(binding, commit)
        if not verdict.ok:
            return verdict
        writer = binding.identity_for(verdict.writer_login)
        if writer is None:
            return ProvenanceVerdict(False, f"commit {verdict.reason} could not be resolved to a bound writer")
        key_problem = verify_signing_key_identity(binding, commit, writer)
        if key_problem is not None:
            return ProvenanceVerdict(False, key_problem)
        resolved.append((commit, writer))

    writers = {writer.login for _commit, writer in resolved}
    if len(writers) <= 1:
        writer = sorted(writers)[0]
        return ProvenanceVerdict(
            True,
            f"{len(commits)} commit(s) in the governed range are bound to writer {writer!r}",
            writer,
        )

    if not binding.require_single_writer_per_range:
        writer = sorted(writers)[0]
        return ProvenanceVerdict(
            True,
            f"{len(commits)} commit(s) in the governed range are bound to {len(writers)} writer(s) because "
            f"{BINDING_FIELD}.require_single_writer_per_range is disabled",
            writer,
        )
    if binding.owner_recovery is None:
        return ProvenanceVerdict(
            False,
            "governed range mixes authorization-bound writers: " + ", ".join(sorted(writers)),
        )
    return evaluate_owner_recovery(binding, binding.owner_recovery, tuple(resolved))


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

    A record that does not carry at least the five identity fields is unreadable
    commit metadata, which fails closed rather than being skipped. The parent
    list, signing key and recovery declaration are optional here and required
    only where a rule needs them, so a five-field record still parses.
    """

    commits: list[CommitProvenance] = []
    for record in raw.split(GIT_RECORD_SEPARATOR):
        stripped = record.strip("\n")
        if not stripped.strip():
            continue
        fields = stripped.split(GIT_FIELD_SEPARATOR)
        if len(fields) < 5:
            raise GitEvidenceUnavailable("commit metadata could not be parsed into canonical provenance fields")
        sha, author_name, author_email, committer_name, committer_email = fields[:5]
        parents = fields[5].strip() if len(fields) > 5 else ""
        signing_key = fields[6].strip() if len(fields) > 6 else ""
        commits.append(
            CommitProvenance(
                sha=sha.strip(),
                author_name=author_name,
                author_email=author_email,
                committer_name=committer_name,
                committer_email=committer_email,
                parents=parents,
                signing_key=signing_key,
            )
        )
    return tuple(commits)


def read_range_commits(base: str, head: str, *, cwd: Path | None = None) -> tuple[CommitProvenance, ...]:
    """Commit provenance for ``base..head``, oldest first.

    The owner-recovery declaration is read separately with git's own
    ``%(trailers)`` pretty-format rather than by parsing the whole message, so
    a commit body can neither be mistaken for a declaration nor accidentally
    break record parsing.
    """

    raw = _run_git(
        *GIT_SIGNATURE_READ_CONFIG, "log", "--reverse", f"--format={GIT_LOG_FORMAT}", f"{base}..{head}", cwd=cwd
    )
    commits = parse_commit_records(raw)
    recovery = load_binding()[0]
    if recovery is None or recovery.owner_recovery is None or not commits:
        return commits
    trailer = recovery.owner_recovery.trailer
    declarations = _run_git(
        "log",
        "--reverse",
        f"--format=%H{GIT_FIELD_SEPARATOR}%(trailers:key={trailer},valueonly,separator={GIT_FIELD_SEPARATOR})",
        f"{base}..{head}",
        cwd=cwd,
    )
    found: dict[str, str] = {}
    for line in declarations.splitlines():
        if GIT_FIELD_SEPARATOR not in line:
            continue
        sha, _, value = line.partition(GIT_FIELD_SEPARATOR)
        if sha.strip() and value.strip():
            found[sha.strip().lower()] = value.strip()
    if not found:
        return commits
    return tuple(
        commit if commit.sha.lower() not in found else replace(commit, recovery_declaration=found[commit.sha.lower()])
        for commit in commits
    )


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
