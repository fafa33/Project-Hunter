"""Governed connector code-write ingress authorization (Issues #403, #405).

This is the repository-owned decision function for the one narrow write path that
lets an explicitly authorized connector writer create code changes without a local
clone. It is an *additional* ingress: `.githooks/pre-push` remains the authoritative
boundary for clone-capable writers and is untouched by this module.

Issue #405 adds a second capability on the same ingress, `governance-maintenance`,
so owner-authorized ADR lifecycle and explicitly scoped governance work no longer
depends on a clone-capable external agent merely to write the change. It is a
capability, not a wider default: its prohibited scope is the ordinary one, and a
path it prohibits opens only when a named scope in the grant unblocks it *and* the
governing Issue is authorized for that scope by the owner on the trusted default
branch. Underneath both capabilities sits the root-of-trust floor -- the push
boundary, the hosted workflows, the canonical gate chain, the authorizer and
controllers, and this grant itself. No capability may write it and no scope may
unblock it, so a candidate can never rewrite the authority that evaluates it.

The decision is deliberately narrow and fail-closed. A request is authorized only
when every one of the following holds:

* the trusted policy loads, declares the ingress enabled, and binds at least one
  writer identity;
* the writer login is on the owner-bound allowlist and presents the exact granted
  capability;
* the target is a branch inside the connector namespace, and is neither the base
  branch nor a forbidden ref, so a direct `main` write is unrepresentable;
* the target branch name encodes the one governing Issue the request declares, so
  branch/commit scope is traceable to a single task;
* the base ref matches the policy base, and the base commit equals the current tip
  resolved from **trusted repository state** -- never a value supplied by the
  caller, so a stale checkout cannot certify its own staleness;
* the writer holds the presented capability, and that capability is defined by the
  grant;
* for a capability that requires Issue authorization, the governing Issue carries
  an owner-authored authorization naming the scopes it may use;
* no changed path is a root-of-trust path, and every changed path is inside the
  scope the presented capability grants for that Issue.

An authorized decision produces a `ConnectorWriteAuthorization`: a canonical,
deterministic claim set whose `authorization_id` is a SHA-256 over the exact
claims. The writer commits it to the candidate head as
`.hunter/connector-write-authorization.json`, and the trusted governance
controller re-derives every claim from trusted repository/PR evidence before the
candidate may be admitted. The receipt is therefore a *declaration that is
checked*, never a caller assertion that is believed: it can only narrow what the
trusted evidence already shows.

Authorization here is *write* authorization only. It is never pre-push proof and
never admission: a connector-written candidate stays Draft/unadmitted until the
trusted hosted exact-head canonical preflight proves it (enforced separately in
`hunter_governance_review_v2`). Hosted CI, Hunter Governance Review, independent
review, Hunter Merge Readiness, and owner merge approval all remain mandatory and
unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, fields
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from hunter_workflow_state import path_matches_scope_entry

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"

HEADS_PREFIX = "refs/heads/"
ISSUE_PLACEHOLDER = "{issue}"

#: Repository-relative path the writer commits the authorization receipt to. The
#: trusted controller reads it at the exact candidate head, so the receipt is
#: bound to that tree and cannot be replayed onto a different one unchanged.
AUTHORIZATION_RECEIPT_PATH = ".hunter/connector-write-authorization.json"
AUTHORIZATION_SCHEMA = "hunter-connector-write-authorization-v4"

_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?\Z")
_ISSUE = re.compile(r"\A[1-9][0-9]{0,9}\Z")

BaseTipResolver = Callable[[str], str]


_BLOB_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


def blob_digest(content: bytes) -> str:
    """The git blob SHA of `content`, as GitHub reports it per changed file.

    Computed the way git computes it -- ``sha1("blob <len>\\0" + content)`` -- so a
    writer can bind the exact bytes it is authorizing without a round trip, and
    the trusted controller can compare against the `sha` GitHub returns for each
    file in the pull request without trusting anything the candidate says.
    """

    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()  # noqa: S324


@dataclass(frozen=True, order=True)
class ConnectorFileChange:
    """One exact file transition authorized through the connector channel."""

    status: str
    path: str
    previous_path: str = ""
    blob_sha: str = ""

    def affected_paths(self) -> tuple[str, ...]:
        if self.status == "renamed":
            return (self.previous_path, self.path)
        return (self.path,)

    def document(self) -> dict[str, str]:
        return {
            "blob_sha": self.blob_sha,
            "path": self.path,
            "previous_path": self.previous_path,
            "status": self.status,
        }


def normalize_changes(value: Any) -> tuple[ConnectorFileChange, ...] | None:
    """Canonicalise exact file transitions, or return ``None`` if ambiguous."""

    if not isinstance(value, (list, tuple)):
        return None
    normalized: list[ConnectorFileChange] = []
    affected: set[str] = set()
    for raw in value:
        if isinstance(raw, ConnectorFileChange):
            status, path, previous_path, blob_sha = raw.status, raw.path, raw.previous_path, raw.blob_sha
        elif isinstance(raw, dict) and set(raw) == {"status", "path", "previous_path", "blob_sha"}:
            status = raw.get("status")
            path = raw.get("path")
            previous_path = raw.get("previous_path")
            blob_sha = raw.get("blob_sha")
        else:
            return None
        if not all(isinstance(item, str) for item in (status, path, previous_path, blob_sha)):
            return None
        status = status.strip().lower()
        path = path.strip()
        previous_path = previous_path.strip()
        blob_sha = blob_sha.strip().lower()
        if status not in {"added", "modified", "removed", "renamed"} or not path:
            return None
        if status == "removed":
            if previous_path or blob_sha:
                return None
        elif status == "renamed":
            if not previous_path or previous_path == path or not _BLOB_SHA.fullmatch(blob_sha):
                return None
        elif previous_path or not _BLOB_SHA.fullmatch(blob_sha):
            return None
        change = ConnectorFileChange(status, path, previous_path, blob_sha)
        paths = change.affected_paths()
        if any(item in affected for item in paths):
            return None
        affected.update(paths)
        normalized.append(change)
    return tuple(sorted(normalized))


class BaseTipUnavailable(RuntimeError):
    """Trusted repository state could not supply the current base tip."""


@dataclass(frozen=True)
class ConnectorWriteAuthorization:
    """The canonical claim set an authorized connector write is bound to.

    `authorization_id` is a SHA-256 over exactly these claims, so a hand-edited
    receipt cannot keep a stale identifier and a replayed receipt cannot claim
    different scope than the one it was minted for.

    `grant_fingerprint` pins the exact version of the grant the write was
    authorized under. The trusted controller re-derives that fingerprint from the
    **default branch**, so a receipt minted under any other version of the grant
    -- an older one, or one a candidate wrote at its own head -- is stale and
    admits nothing. `governance_scopes` records the named governance-maintenance
    scopes the governing Issue was authorized for; it is derived from the trusted
    manifest rather than declared by the caller, and is empty for an ordinary
    feature-branch write.

    `changes` binds the operation and exact resulting content. Additions and
    modifications bind the destination blob; removals bind absence; renames bind
    both source and destination plus the resulting destination blob.
    """

    writer: str
    capability: str
    issue: str
    base_ref: str
    base_sha: str
    target_ref: str
    paths: tuple[str, ...]
    governance_scopes: tuple[str, ...] = ()
    grant_fingerprint: str = ""
    changes: tuple[ConnectorFileChange, ...] = ()

    def claims(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
            "capability": self.capability,
            "changes": [change.document() for change in sorted(self.changes)],
            "governance_scopes": sorted(self.governance_scopes),
            "grant_fingerprint": self.grant_fingerprint,
            "issue": self.issue,
            "paths": sorted(self.paths),
            "target_ref": self.target_ref,
            "writer": self.writer,
        }

    @property
    def authorization_id(self) -> str:
        canonical = json.dumps(self.claims(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def document(self) -> dict[str, Any]:
        return {
            "schema": AUTHORIZATION_SCHEMA,
            "claims": self.claims(),
            "authorization_id": self.authorization_id,
        }

    @classmethod
    def from_document(cls, payload: Any) -> tuple[ConnectorWriteAuthorization | None, str]:
        """Parse a receipt, or explain why it is unusable. Never raises."""

        if not isinstance(payload, dict):
            return None, "authorization receipt must be a JSON object"
        if payload.get("schema") != AUTHORIZATION_SCHEMA:
            return None, f"authorization receipt schema must be {AUTHORIZATION_SCHEMA}"

        claims = payload.get("claims")
        if not isinstance(claims, dict):
            return None, "authorization receipt claims must be an object"

        expected_keys = {
            "base_ref",
            "base_sha",
            "capability",
            "changes",
            "governance_scopes",
            "grant_fingerprint",
            "issue",
            "paths",
            "target_ref",
            "writer",
        }
        if set(claims) != expected_keys:
            return None, "authorization receipt claims must carry exactly the canonical claim set"

        raw_paths = claims.get("paths")
        if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
            return None, "authorization receipt paths must be an array of path strings"
        changes = normalize_changes(claims.get("changes"))
        if changes is None:
            return None, "authorization receipt changes must be canonical exact file transitions"
        canonical_paths = sorted({item.strip() for item in raw_paths if item.strip()})
        if raw_paths != canonical_paths:
            return None, "authorization receipt paths must be sorted, unique, non-empty canonical paths"
        if claims.get("changes") != [change.document() for change in changes]:
            return None, "authorization receipt changes are not in canonical order and form"
        raw_scopes = claims.get("governance_scopes")
        if not isinstance(raw_scopes, list) or not all(isinstance(item, str) for item in raw_scopes):
            return None, "authorization receipt governance_scopes must be an array of scope names"
        canonical_scopes = sorted({item.strip() for item in raw_scopes if item.strip()})
        if raw_scopes != canonical_scopes:
            return None, "authorization receipt governance_scopes must be sorted, unique canonical names"
        for name in ("base_ref", "base_sha", "capability", "grant_fingerprint", "issue", "target_ref", "writer"):
            if not isinstance(claims.get(name), str):
                return None, f"authorization receipt claim {name!r} must be a string"

        authorization = cls(
            writer=str(claims["writer"]),
            capability=str(claims["capability"]),
            issue=str(claims["issue"]),
            base_ref=str(claims["base_ref"]),
            base_sha=str(claims["base_sha"]),
            target_ref=str(claims["target_ref"]),
            paths=tuple(canonical_paths),
            governance_scopes=tuple(canonical_scopes),
            grant_fingerprint=str(claims["grant_fingerprint"]),
            changes=changes,
        )
        declared_id = payload.get("authorization_id")
        if not isinstance(declared_id, str) or declared_id != authorization.authorization_id:
            return None, "authorization receipt identifier does not match its own claims"
        return authorization, ""


@dataclass(frozen=True)
class IngressDecision:
    """The authorization outcome. `reason` is always populated, including on success."""

    authorized: bool
    reason: str
    authorization: ConnectorWriteAuthorization | None = None


def _reject(reason: str) -> IngressDecision:
    return IngressDecision(False, reason)


@dataclass(frozen=True)
class ConnectorWriteRequest:
    """One proposed connector write, as machine-readable fields.

    Every field is compared against the trusted policy and against trusted
    repository state. Nothing the connector writes in prose -- commit message, PR
    body, comment -- can widen, waive, or substitute for any of them, and the
    request deliberately carries no field describing the current base tip: that
    is resolved from the repository itself so a stale caller cannot certify its
    own staleness.
    """

    writer: str
    capability: str
    issue: str
    target_ref: str
    base_ref: str
    base_sha: str
    paths: tuple[str, ...] = ()
    changes: tuple[ConnectorFileChange, ...] = ()

    @classmethod
    def from_dict(cls, payload: Any) -> ConnectorWriteRequest:
        if not isinstance(payload, dict):
            raise ValueError("write request must be a JSON object")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            # An unreadable field is a request claim the gate would silently
            # ignore, so refuse the request rather than evaluate part of it.
            # `observed_base_tip_sha` lands here on purpose: a caller-supplied
            # base tip is no longer evidence, and silently dropping it would let
            # an old caller believe its stale-base check still ran.
            raise ValueError("write request has unknown field(s): " + ", ".join(unknown))

        def text(name: str) -> str:
            value = payload.get(name)
            if value is None:
                return ""
            if not isinstance(value, str):
                raise ValueError(f"write request field {name!r} must be a string")
            return value

        raw_paths = payload.get("paths")
        if raw_paths is None:
            path_tuple: tuple[str, ...] = ()
        elif isinstance(raw_paths, str) or not isinstance(raw_paths, (list, tuple)):
            # A JSON string is iterable, so "src/x.py" would become a per-character
            # list that matches no real path and disables the scope comparison.
            raise ValueError("write request field 'paths' must be an array of paths")
        else:
            for item in raw_paths:
                if not isinstance(item, str):
                    raise ValueError("write request field 'paths' must contain only path strings")
            path_tuple = tuple(raw_paths)

        raw_changes = payload.get("changes")
        if raw_changes is None:
            changes: tuple[ConnectorFileChange, ...] = ()
        else:
            normalized = normalize_changes(raw_changes)
            if normalized is None:
                raise ValueError("write request field 'changes' must be canonical exact file transitions")
            changes = normalized

        return cls(
            writer=text("writer"),
            capability=text("capability"),
            issue=text("issue"),
            target_ref=text("target_ref"),
            base_ref=text("base_ref"),
            base_sha=text("base_sha"),
            paths=path_tuple,
            changes=changes,
        )


@dataclass(frozen=True)
class CapabilityScope:
    """The path scope one capability on this ingress grants.

    `allowed_paths` is the outer bound and `prohibited_paths` is the closed
    default, exactly as for the ordinary capability. A capability that requires
    Issue authorization grants nothing beyond that closed default until a named
    governance scope unblocks a path *and* the governing Issue is authorized for
    that scope.
    """

    name: str
    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    requires_issue_authorization: bool = False


@dataclass(frozen=True)
class GovernanceScope:
    """One named governance-maintenance scope and the paths it unblocks."""

    name: str
    unblocked_paths: tuple[str, ...]


@dataclass(frozen=True)
class IssueAuthorization:
    """One owner-authored grant of named governance scopes to one governing Issue."""

    issue: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class ConnectorIngressPolicy:
    """The owner-authored connector ingress grant, as the evaluator consumes it."""

    enabled: bool
    writers: tuple[tuple[str, str], ...]
    required_capability: str
    base_ref: str
    forbidden_target_refs: frozenset[str]
    branch_namespace: str
    branch_pattern_template: str
    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    require_exact_base_tip: bool
    local_pre_push_equivalent: bool
    root_of_trust_paths: tuple[str, ...] = ()
    additional_capabilities: tuple[CapabilityScope, ...] = ()
    governance_scopes: tuple[GovernanceScope, ...] = ()
    issue_authorizations: tuple[IssueAuthorization, ...] = ()

    def capabilities_for(self, login: str) -> frozenset[str]:
        """Every capability the grant binds to this login; empty when unbound.

        A writer holds one capability per grant entry, so a second capability is a
        second explicit owner-authored entry rather than a wider first one.
        """

        return frozenset(capability for granted_login, capability in self.writers if granted_login == login)

    def capability_scope(self, capability: str) -> CapabilityScope | None:
        """The path scope for one capability, or None when it is not granted.

        The ordinary capability's scope is synthesised from the grant's own
        `allowed_paths`/`prohibited_paths` rather than duplicated, so the two
        cannot drift into two different answers.
        """

        if capability == self.required_capability:
            return CapabilityScope(
                name=self.required_capability,
                allowed_paths=self.allowed_paths,
                prohibited_paths=self.prohibited_paths,
                requires_issue_authorization=False,
            )
        for scope in self.additional_capabilities:
            if scope.name == capability:
                return scope
        return None

    def granted_capabilities(self) -> frozenset[str]:
        return frozenset({self.required_capability} | {scope.name for scope in self.additional_capabilities})

    def governance_scope(self, name: str) -> GovernanceScope | None:
        for scope in self.governance_scopes:
            if scope.name == name:
                return scope
        return None

    def scopes_authorized_for_issue(self, issue: str) -> frozenset[str] | None:
        """Named scopes the owner authorized for this Issue, or None when unauthorized.

        None and an empty set are deliberately different answers: None means the
        Issue carries no governance-maintenance authorization at all.
        """

        for entry in self.issue_authorizations:
            if entry.issue == issue:
                return entry.scopes
        return None

    def unblocked_paths(self, scope_names: frozenset[str]) -> tuple[str, ...]:
        unblocked: list[str] = []
        for name in sorted(scope_names):
            scope = self.governance_scope(name)
            if scope is not None:
                unblocked.extend(scope.unblocked_paths)
        return tuple(unblocked)

    def is_root_of_trust(self, path: str) -> bool:
        return any(path_matches_scope_entry(path, entry) for entry in self.root_of_trust_paths)

    def in_connector_namespace(self, branch: str) -> bool:
        return branch.startswith(self.branch_namespace)

    @property
    def fingerprint(self) -> str:
        """SHA-256 over every authorization-relevant field of the grant.

        This is what an authorization receipt pins. The trusted controller
        re-derives it from the default-branch policy, so a receipt minted under a
        different version of the grant -- an older one, or one written at a
        candidate's own head -- cannot be presented as current.
        """

        canonical = {
            "additional_capabilities": [
                {
                    "allowed_paths": sorted(scope.allowed_paths),
                    "name": scope.name,
                    "prohibited_paths": sorted(scope.prohibited_paths),
                    "requires_issue_authorization": scope.requires_issue_authorization,
                }
                for scope in sorted(self.additional_capabilities, key=lambda item: item.name)
            ],
            "allowed_paths": sorted(self.allowed_paths),
            "base_ref": self.base_ref,
            "branch_namespace": self.branch_namespace,
            "branch_pattern_template": self.branch_pattern_template,
            "enabled": self.enabled,
            "forbidden_target_refs": sorted(self.forbidden_target_refs),
            "governance_scopes": [
                {"name": scope.name, "unblocked_paths": sorted(scope.unblocked_paths)}
                for scope in sorted(self.governance_scopes, key=lambda item: item.name)
            ],
            "issue_authorizations": [
                {"issue": entry.issue, "scopes": sorted(entry.scopes)}
                for entry in sorted(self.issue_authorizations, key=lambda item: item.issue)
            ],
            "local_pre_push_equivalent": self.local_pre_push_equivalent,
            "prohibited_paths": sorted(self.prohibited_paths),
            "require_exact_base_tip": self.require_exact_base_tip,
            "required_capability": self.required_capability,
            "root_of_trust_paths": sorted(self.root_of_trust_paths),
            "writers": sorted([login, capability] for login, capability in self.writers),
        }
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def issue_for_branch(self, branch: str) -> str | None:
        """The single governing Issue a branch name binds, derived from the branch.

        Derived rather than declared: the branch name is trusted pull-request
        evidence, so a receipt cannot claim a different Issue than the branch it
        was written on.
        """

        match = re.fullmatch(
            re.escape(self.branch_pattern_template).replace(re.escape(ISSUE_PLACEHOLDER), r"([1-9][0-9]{0,9})")
            # `re.escape` escapes the glob wildcard too; restore it as a
            # non-greedy any-run so the pattern keeps its fnmatch meaning.
            .replace(r"\*", r".*"),
            branch,
        )
        return match.group(1) if match else None


def scope_entries_overlap(left: str, right: str) -> bool:
    """Whether two owner-authored scope entries can ever cover a common path.

    Containment between a concrete path and a scope entry is one-directional, but
    two *entries* overlap in either direction: `docs/` covers
    `docs/CODE_WRITE_POLICY.json` without being it. Comparing only one way is the
    representation trap that would let a broad `docs/` scope quietly re-open the
    root of trust, so both directions are checked.
    """

    return path_matches_scope_entry(left, right) or path_matches_scope_entry(right, left)


def _string_list(source: dict[str, Any], field: str) -> tuple[str, ...] | None:
    """A field's non-empty string entries, or None when it is not a string list."""

    value = source.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(item.strip() for item in value if item.strip())


def _normalize_branch(ref: str) -> str | None:
    """The branch name a ref denotes, or None when the ref is not a branch."""

    ref = ref.strip()
    if not ref:
        return None
    if ref.startswith("refs/"):
        if not ref.startswith(HEADS_PREFIX):
            return None
        ref = ref[len(HEADS_PREFIX) :]
    if not ref or ref.startswith("/") or ref.endswith("/") or ".." in ref:
        return None
    return ref


def git_base_tip(base_ref: str, *, remote: str = "origin", cwd: Path | None = None) -> str:
    """Resolve the current base tip from trusted repository state.

    Reads the remote ref rather than anything in the caller's request or working
    copy: a stale checkout must not be able to present its own stale commit as
    the current tip. Any failure raises rather than returning a guess.
    """

    try:
        completed = subprocess.run(
            ("git", "ls-remote", "--exit-code", "--heads", remote, base_ref),
            cwd=str(cwd or ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaseTipUnavailable(f"git ls-remote failed: {type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise BaseTipUnavailable(f"git ls-remote could not resolve {base_ref!r} on {remote!r}: {detail}")

    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise BaseTipUnavailable(f"git ls-remote returned {len(lines)} refs for {base_ref!r}; expected exactly one")
    candidate = lines[0].split()[0].strip().lower()
    if not _SHA.fullmatch(candidate):
        raise BaseTipUnavailable(f"git ls-remote returned a malformed tip for {base_ref!r}")
    return candidate


def load_policy(path: Path | None = None) -> tuple[ConnectorIngressPolicy | None, str]:
    """Read the trusted connector ingress grant, or explain why it is unusable.

    Returning `(None, reason)` is the fail-closed outcome: a missing, unreadable,
    or structurally invalid grant authorizes nothing.
    """

    path = POLICY_PATH if path is None else path
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "canonical code-write policy is missing"
    except Exception as exc:
        return None, f"canonical code-write policy is unreadable ({type(exc).__name__}: {exc})"
    return parse_policy(document)


def parse_policy(document: Any) -> tuple[ConnectorIngressPolicy | None, str]:
    """Validate one already-parsed code-write policy document.

    Split out from `load_policy` so the trusted controller can parse the *candidate
    head's* policy with exactly the same rules it applies to the default branch's,
    and compare the two. Two parsers would be two different readings of the same
    grant.
    """

    if not isinstance(document, dict):
        return None, "canonical code-write policy must be a JSON object"

    grant = document.get("connector_write_ingress")
    if grant is None:
        return None, "canonical code-write policy declares no connector write ingress"
    if not isinstance(grant, dict):
        return None, "connector write ingress grant is malformed"

    enabled = grant.get("enabled")
    if not isinstance(enabled, bool):
        return None, "connector write ingress grant declares no explicit enabled state"

    if grant.get("local_pre_push_equivalent") is not False:
        # The grant may never claim that a connector write is equivalent to the
        # repository-local pre-push boundary; that claim is what Issue #403
        # requirement 7 forbids.
        return None, "connector write ingress must not declare local pre-push equivalence"

    required_capability = grant.get("required_capability")
    if not isinstance(required_capability, str) or not required_capability.strip():
        return None, "connector write ingress declares no required capability"

    raw_writers = grant.get("authorized_writers")
    if not isinstance(raw_writers, list):
        return None, "connector write ingress authorized_writers must be a list"

    writers: list[tuple[str, str]] = []
    for entry in raw_writers:
        if not isinstance(entry, dict):
            return None, "connector write ingress authorized_writers contains a malformed entry"
        login = entry.get("login")
        capability = entry.get("capability")
        if not isinstance(login, str) or not isinstance(capability, str):
            return None, "connector write ingress writer login and capability must be strings"
        login = login.strip().lower()
        if not login:
            # An unbound grant is declared but not activated; it grants nothing.
            continue
        if not _LOGIN.fullmatch(login):
            return None, f"connector write ingress writer login {login!r} is malformed"
        capability = capability.strip()
        # One entry grants one capability, so a writer that holds two capabilities
        # is two explicit owner-authored entries rather than one wider entry. A
        # repeated (login, capability) pair is still a duplicate grant.
        if (login, capability) in writers:
            return None, f"connector write ingress declares duplicate writer {login!r}"
        writers.append((login, capability))

    base_ref = grant.get("base_ref")
    if not isinstance(base_ref, str) or not base_ref.strip():
        return None, "connector write ingress declares no base ref"

    namespace = grant.get("branch_namespace")
    if not isinstance(namespace, str) or not namespace.strip() or not namespace.endswith("/"):
        return None, "connector write ingress must declare a connector branch namespace ending in '/'"

    template = grant.get("branch_pattern_template")
    if not isinstance(template, str) or ISSUE_PLACEHOLDER not in template:
        return None, "connector write ingress branch pattern must bind the governing Issue"
    if not template.startswith(namespace.strip()):
        return None, "connector write ingress branch pattern must live inside the connector namespace"

    forbidden_raw = grant.get("forbidden_target_refs")
    if not isinstance(forbidden_raw, list) or not all(isinstance(item, str) for item in forbidden_raw):
        return None, "connector write ingress forbidden_target_refs must be a list of refs"

    def path_list(name: str) -> tuple[str, ...] | None:
        value = grant.get(name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return None
        return tuple(item for item in value if item.strip())

    allowed_paths = path_list("allowed_paths")
    if not allowed_paths:
        # Neither "everything" nor "nothing" is a scope statement.
        return None, "connector write ingress declares no allowed paths"
    prohibited_paths = path_list("prohibited_paths")
    if prohibited_paths is None:
        return None, "connector write ingress prohibited_paths must be a list of paths"

    if grant.get("require_exact_base_tip") is not True:
        return None, "connector write ingress must require an exact base tip"

    root_of_trust = path_list("root_of_trust_paths")
    if root_of_trust is None:
        return None, "connector write ingress root_of_trust_paths must be a list of paths"
    uncovered_by_default = sorted(
        entry for entry in root_of_trust if not any(path_matches_scope_entry(entry, g) for g in prohibited_paths)
    )
    if uncovered_by_default:
        # The ordinary capability is a scope like any other, so it must close the
        # root of trust too; otherwise the floor would hold only for the
        # capabilities that happen to declare it.
        return None, ("connector write ingress must prohibit root-of-trust path(s): " + ", ".join(uncovered_by_default))

    capabilities: list[CapabilityScope] = []
    raw_capabilities = grant.get("additional_capabilities", {})
    if not isinstance(raw_capabilities, dict):
        return None, "connector write ingress additional_capabilities must be an object"
    for name, entry in sorted(raw_capabilities.items()):
        name = name.strip()
        if not name:
            return None, "connector write ingress declares an unnamed additional capability"
        if name == required_capability.strip():
            # Otherwise the ordinary capability would have two scope definitions
            # and the narrower one could be shadowed by the wider.
            return None, f"additional capability {name!r} redefines the required capability"
        if not isinstance(entry, dict):
            return None, f"connector write ingress capability {name!r} is malformed"

        capability_allowed = _string_list(entry, "allowed_paths")
        if not capability_allowed:
            return None, f"connector write ingress capability {name!r} declares no allowed paths"
        capability_prohibited = _string_list(entry, "prohibited_paths")
        if capability_prohibited is None:
            return None, f"connector write ingress capability {name!r} prohibited_paths must be a list of paths"
        requires_issue = entry.get("requires_issue_authorization")
        if not isinstance(requires_issue, bool):
            return None, f"connector write ingress capability {name!r} must declare Issue-authorization state"
        # A capability that could write the root of trust would be able to rewrite
        # the authority that evaluates it, so the grant is invalid rather than the
        # write merely being rejected later.
        uncovered = sorted(
            entry_path
            for entry_path in root_of_trust
            if not any(path_matches_scope_entry(entry_path, guard) for guard in capability_prohibited)
        )
        if uncovered:
            return None, (
                f"connector write ingress capability {name!r} must prohibit root-of-trust path(s): "
                + ", ".join(uncovered)
            )
        capabilities.append(
            CapabilityScope(
                name=name,
                allowed_paths=capability_allowed,
                prohibited_paths=capability_prohibited,
                requires_issue_authorization=requires_issue,
            )
        )

    governance_scopes: list[GovernanceScope] = []
    raw_scopes = grant.get("governance_maintenance_scopes", {})
    if not isinstance(raw_scopes, dict):
        return None, "connector write ingress governance_maintenance_scopes must be an object"
    outer_bound = tuple(sorted({path for scope in capabilities for path in scope.allowed_paths}))
    for name, entry in sorted(raw_scopes.items()):
        name = name.strip()
        if not name:
            return None, "connector write ingress declares an unnamed governance scope"
        if not isinstance(entry, dict):
            return None, f"connector write ingress governance scope {name!r} is malformed"
        raw_unblocked = entry.get("unblocked_paths")
        if not isinstance(raw_unblocked, list) or not all(isinstance(item, str) for item in raw_unblocked):
            return None, f"connector write ingress governance scope {name!r} must declare unblocked paths"
        unblocked = tuple(item.strip() for item in raw_unblocked if item.strip())
        if not unblocked:
            return None, f"connector write ingress governance scope {name!r} unblocks nothing"
        for path in unblocked:
            if any(scope_entries_overlap(path, guard) for guard in root_of_trust):
                # This is the anti-self-escalation invariant in structural form: no
                # named scope may re-open the authority that authorizes the write.
                return None, f"connector write ingress governance scope {name!r} may not unblock root-of-trust {path}"
            if not any(path_matches_scope_entry(path, bound) for bound in outer_bound):
                return None, (
                    f"connector write ingress governance scope {name!r} unblocks {path}, "
                    "which is outside every capability's allowed paths"
                )
        governance_scopes.append(GovernanceScope(name=name, unblocked_paths=unblocked))

    known_scopes = {scope.name for scope in governance_scopes}
    authorizations: list[IssueAuthorization] = []
    raw_authorizations = grant.get("governance_maintenance_authorizations", [])
    if not isinstance(raw_authorizations, list):
        return None, "connector write ingress governance_maintenance_authorizations must be a list"
    for entry in raw_authorizations:
        if not isinstance(entry, dict):
            return None, "connector write ingress governance authorization entry is malformed"
        issue = str(entry.get("issue", "")).strip().lstrip("#")
        if not _ISSUE.fullmatch(issue):
            return None, "connector write ingress governance authorization must name one governing Issue"
        raw_entry_scopes = entry.get("scopes")
        if not isinstance(raw_entry_scopes, list) or not all(isinstance(item, str) for item in raw_entry_scopes):
            return None, f"governance authorization for Issue #{issue} must declare a scope list"
        entry_scopes = {item.strip() for item in raw_entry_scopes if item.strip()}
        if not entry_scopes:
            return None, f"governance authorization for Issue #{issue} authorizes no scope"
        unknown = sorted(entry_scopes - known_scopes)
        if unknown:
            return None, f"governance authorization for Issue #{issue} names unknown scope(s): " + ", ".join(unknown)
        if any(existing.issue == issue for existing in authorizations):
            return None, f"connector write ingress authorizes Issue #{issue} more than once"
        authorizations.append(IssueAuthorization(issue=issue, scopes=frozenset(entry_scopes)))

    return (
        ConnectorIngressPolicy(
            enabled=enabled,
            writers=tuple(writers),
            required_capability=required_capability.strip(),
            base_ref=base_ref.strip(),
            forbidden_target_refs=frozenset(item.strip() for item in forbidden_raw if item.strip()),
            branch_namespace=namespace.strip(),
            branch_pattern_template=template,
            allowed_paths=allowed_paths,
            prohibited_paths=prohibited_paths,
            require_exact_base_tip=True,
            local_pre_push_equivalent=False,
            root_of_trust_paths=root_of_trust,
            additional_capabilities=tuple(capabilities),
            governance_scopes=tuple(governance_scopes),
            issue_authorizations=tuple(authorizations),
        ),
        "",
    )


def check_scope(
    paths: tuple[str, ...],
    policy: ConnectorIngressPolicy,
    *,
    capability: str | None = None,
    issue: str = "",
) -> str:
    """Why these changed paths are out of scope, or "" when every one is in scope.

    Shared by write-time authorization and admission-time re-evaluation so the
    two cannot drift into two different answers.

    `capability` selects which granted scope applies; omitting it evaluates the
    ordinary capability exactly as before. For a capability that requires Issue
    authorization, `issue` selects which named governance scopes are unblocked --
    a path the closed default prohibits is writable only when an authorized scope
    unblocks it. The root-of-trust floor is checked first and applies to every
    capability, so no scope or Issue can reach it.
    """

    if not paths or any(not path.strip() for path in paths):
        return "no changed paths to check against the granted scope"
    for path in paths:
        if path.startswith("/") or ".." in Path(path).parts:
            return f"path {path!r} is not a repository-relative path"

    root_of_trust = sorted({p for p in paths if policy.is_root_of_trust(p)})
    if root_of_trust:
        return "root-of-trust path(s) no capability on this ingress may write: " + ", ".join(root_of_trust)

    scope = policy.capability_scope(policy.required_capability if capability is None else capability)
    if scope is None:
        return f"declares capability {capability!r}, which this grant does not define"

    unblocked: tuple[str, ...] = ()
    if scope.requires_issue_authorization:
        authorized = policy.scopes_authorized_for_issue(issue)
        if authorized is None:
            return (
                f"claims capability {scope.name!r} for Issue #{issue or '(none)'}, "
                "which carries no owner-authored governance-maintenance authorization"
            )
        unblocked = policy.unblocked_paths(authorized)

    def in_scope(path: str) -> bool:
        # The capability's allowed paths are its outer bound and hold absolutely:
        # a named scope may re-open what the closed default shut, never reach
        # outside the capability it belongs to.
        if not any(path_matches_scope_entry(path, entry) for entry in scope.allowed_paths):
            return False
        if any(path_matches_scope_entry(path, entry) for entry in unblocked):
            return True
        return not any(path_matches_scope_entry(path, entry) for entry in scope.prohibited_paths)

    blocked = sorted({p for p in paths if not in_scope(p)})
    if not blocked:
        return ""
    if scope.requires_issue_authorization:
        return f"path(s) outside the governance scope authorized for Issue #{issue}: " + ", ".join(blocked)
    prohibited = sorted({p for p in blocked if any(path_matches_scope_entry(p, e) for e in scope.prohibited_paths)})
    if prohibited:
        return "prohibited path(s): " + ", ".join(prohibited)
    return "path(s) outside the granted connector scope: " + ", ".join(blocked)


def evaluate_write_request(
    request: ConnectorWriteRequest,
    policy: ConnectorIngressPolicy | None,
    *,
    resolve_base_tip: BaseTipResolver | None = None,
    policy_error: str = "",
) -> IngressDecision:
    """Authorize or reject one proposed connector write. Fail-closed throughout.

    `resolve_base_tip` reads trusted repository state; it is injected so tests can
    drive it, never so a caller can answer the question for itself.
    """

    if policy is None:
        return _reject(f"connector write ingress is unavailable: {policy_error or 'no usable policy'}")
    if not policy.enabled:
        return _reject("connector write ingress is not enabled")
    if not policy.writers:
        return _reject("connector write ingress binds no writer identity")

    writer = request.writer.strip().lower()
    if not writer:
        return _reject("write request declares no writer identity")
    granted = policy.capabilities_for(writer)
    if not granted:
        return _reject(f"unauthorized connector writer {writer!r}")

    capability = request.capability.strip()
    if capability not in granted or policy.capability_scope(capability) is None:
        return _reject(
            f"writer {writer!r} presented capability {capability or '(none)'!r}, "
            f"but the grant binds {', '.join(sorted(granted)) or '(none)'}"
        )

    base_ref = _normalize_branch(request.base_ref)
    if base_ref is None:
        return _reject("write request declares no usable base branch")
    if base_ref != policy.base_ref:
        return _reject(f"base branch {base_ref!r} is not the authorized base {policy.base_ref!r}")

    target_ref = _normalize_branch(request.target_ref)
    if target_ref is None:
        return _reject(f"write target {request.target_ref.strip() or '(none)'!r} is not a branch ref")
    if target_ref == base_ref or target_ref in policy.forbidden_target_refs:
        return _reject(f"direct write to protected branch {target_ref!r} is forbidden")
    if not policy.in_connector_namespace(target_ref):
        return _reject(f"branch {target_ref!r} is outside the connector namespace {policy.branch_namespace!r}")

    issue = request.issue.strip().lstrip("#")
    if not _ISSUE.fullmatch(issue):
        return _reject("write request declares no single governing Issue number")
    expected_pattern = policy.branch_pattern_template.replace(ISSUE_PLACEHOLDER, issue)
    # A branch name is matched as a pattern, not as a path scope: directory-prefix
    # containment would let `connector/issue-403` also admit `connector/issue-403/x`.
    if not fnmatch(target_ref, expected_pattern):
        return _reject(f"branch {target_ref!r} is out of scope for Issue #{issue} (expected {expected_pattern!r})")

    base_sha = request.base_sha.strip().lower()
    if not _SHA.fullmatch(base_sha):
        return _reject("write request declares no full base commit SHA")

    if resolve_base_tip is None:
        return _reject("connector write ingress cannot resolve the trusted base tip")
    try:
        observed_tip = resolve_base_tip(base_ref).strip().lower()
    except BaseTipUnavailable as exc:
        return _reject(f"trusted base tip is unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - any resolver failure must fail closed
        return _reject(f"trusted base tip is unavailable: {type(exc).__name__}: {exc}")
    if not _SHA.fullmatch(observed_tip):
        return _reject("trusted repository state returned no usable base tip")
    if policy.require_exact_base_tip and base_sha != observed_tip:
        return _reject(f"stale base: {base_sha[:10]} is not the current {base_ref} tip {observed_tip[:10]}")

    stripped_paths = tuple(path.strip() for path in request.paths)
    if any(not path for path in stripped_paths) or len(set(stripped_paths)) != len(stripped_paths):
        return _reject("write request paths must be unique, non-empty repository paths")
    paths = tuple(sorted(stripped_paths))
    if AUTHORIZATION_RECEIPT_PATH in paths:
        # The receipt is emitted by this authorizer, not declared as content.
        return _reject("write request must not declare the authorization receipt as changed content")
    if any(path.startswith(".hunter/") for path in paths):
        return _reject("write request must not declare governed .hunter/ ingress state as content")
    scope_error = check_scope(paths, policy, capability=capability, issue=issue)
    if scope_error:
        return _reject(f"write request {scope_error}")

    # Bind the operation as well as the bytes. In particular, GitHub reports the
    # base blob SHA for a removed file; representing removal as an explicit
    # absence prevents that SHA from masquerading as authorized retained content.
    changes = normalize_changes(request.changes)
    changed_paths = {path for change in changes or () for path in change.affected_paths()}
    if changes is None or changed_paths != set(paths):
        return _reject(
            "write request must declare exact, unambiguous change semantics for every changed path "
            f"({len(paths)} path(s), {len(changed_paths)} change-bound path(s))"
        )

    # Derived from the trusted manifest, never declared by the caller: the Issue
    # comes from the branch the write targets, and the scopes come from the
    # owner-authored authorization for that Issue.
    scope = policy.capability_scope(capability)
    governance_scopes: tuple[str, ...] = ()
    if scope is not None and scope.requires_issue_authorization:
        authorized = policy.scopes_authorized_for_issue(issue)
        governance_scopes = tuple(sorted(authorized or frozenset()))

    authorization = ConnectorWriteAuthorization(
        writer=writer,
        capability=capability,
        issue=issue,
        base_ref=base_ref,
        base_sha=base_sha,
        target_ref=target_ref,
        paths=paths,
        governance_scopes=governance_scopes,
        grant_fingerprint=policy.fingerprint,
        changes=changes,
    )
    return IngressDecision(
        True,
        f"authorized connector write by {writer!r} for Issue #{issue}: branch {target_ref}, "
        f"capability {capability}, base {base_ref}@{base_sha[:10]}, {len(paths)} path(s) in scope, "
        f"grant {policy.fingerprint[:12]}, authorization {authorization.authorization_id[:12]}. "
        f"Commit the receipt to {AUTHORIZATION_RECEIPT_PATH}; the candidate remains Draft/unadmitted "
        "until the trusted hosted exact-head canonical preflight and trusted re-evaluation prove it.",
        authorization,
    )


def grant_widening(trusted: ConnectorIngressPolicy, candidate: ConnectorIngressPolicy) -> tuple[str, ...]:
    """Every way `candidate` grants more authority than `trusted`, most specific first.

    Used by the trusted controller to compare the default-branch grant with the
    grant at a candidate's own head. A candidate is free to *propose* a wider
    grant -- that is what a governed pull request is for -- but it may not rely on
    the proposal while the proposal is what is under review. Returning an empty
    tuple means the candidate proposes no additional authority, so evaluating it
    under the trusted grant takes nothing away from it.

    Narrowing is deliberately not reported: tightening the grant cannot escalate
    the candidate that proposes it.
    """

    def covered(entry: str, entries: tuple[str, ...]) -> bool:
        """Whether `entry`'s whole scope is still covered by some entry in `entries`.

        Compared semantically, not by literal text: `.githooks/`, `.githooks/*`
        and `.githooks/**` are the same statement, and reporting a re-spelling as
        widening would block a candidate that changed nothing. Narrowing an entry
        (`.github/` to `.github/workflows/`) is correctly *not* covered, because
        it does leave paths open that were closed before.
        """

        return any(path_matches_scope_entry(entry, existing) for existing in entries)

    reasons: list[str] = []
    if candidate.enabled and not trusted.enabled:
        reasons.append("activates the ingress")

    added_writers = sorted(set(candidate.writers) - set(trusted.writers))
    reasons.extend(f"adds writer grant {login!r} with capability {capability!r}" for login, capability in added_writers)

    added_capabilities = sorted(candidate.granted_capabilities() - trusted.granted_capabilities())
    reasons.extend(f"adds capability {name!r}" for name in added_capabilities)

    for capability in sorted(candidate.granted_capabilities() & trusted.granted_capabilities()):
        new_scope = candidate.capability_scope(capability)
        old_scope = trusted.capability_scope(capability)
        if new_scope is None or old_scope is None:
            continue
        added_allowed = sorted(e for e in new_scope.allowed_paths if not covered(e, old_scope.allowed_paths))
        if added_allowed:
            reasons.append(f"widens {capability!r} allowed paths with " + ", ".join(added_allowed))
        dropped_prohibited = sorted(e for e in old_scope.prohibited_paths if not covered(e, new_scope.prohibited_paths))
        if dropped_prohibited:
            reasons.append(f"drops {capability!r} prohibitions on " + ", ".join(dropped_prohibited))
        if old_scope.requires_issue_authorization and not new_scope.requires_issue_authorization:
            reasons.append(f"drops the Issue-authorization requirement for {capability!r}")

    dropped_root = sorted(e for e in trusted.root_of_trust_paths if not covered(e, candidate.root_of_trust_paths))
    if dropped_root:
        reasons.append("drops root-of-trust protection for " + ", ".join(dropped_root))

    for scope in sorted(candidate.governance_scopes, key=lambda item: item.name):
        existing = trusted.governance_scope(scope.name)
        if existing is None:
            reasons.append(f"adds governance scope {scope.name!r}")
            continue
        added_paths = sorted(e for e in scope.unblocked_paths if not covered(e, existing.unblocked_paths))
        if added_paths:
            reasons.append(f"widens governance scope {scope.name!r} with " + ", ".join(added_paths))

    for entry in sorted(candidate.issue_authorizations, key=lambda item: item.issue):
        existing_scopes = trusted.scopes_authorized_for_issue(entry.issue)
        if existing_scopes is None:
            reasons.append(f"authorizes governance maintenance for Issue #{entry.issue}")
            continue
        added_scopes = sorted(entry.scopes - existing_scopes)
        if added_scopes:
            reasons.append(f"widens Issue #{entry.issue} authorization with " + ", ".join(added_scopes))

    return tuple(reasons)


def confirm_base_unchanged(
    authorization: ConnectorWriteAuthorization,
    resolve_base_tip: BaseTipResolver,
) -> str:
    """Re-check the trusted base tip just before the write is applied.

    Closes the window between authorization and application: an authorization
    minted against an older tip must not be applied after the base advanced.
    Returns "" when the write may proceed, or the reason it may not.
    """

    try:
        current = resolve_base_tip(authorization.base_ref).strip().lower()
    except BaseTipUnavailable as exc:
        return f"trusted base tip is unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 - any resolver failure must fail closed
        return f"trusted base tip is unavailable: {type(exc).__name__}: {exc}"
    if not _SHA.fullmatch(current):
        return "trusted repository state returned no usable base tip"
    if current != authorization.base_sha:
        return (
            f"base {authorization.base_ref} advanced from {authorization.base_sha[:10]} to {current[:10]} "
            "after authorization; re-authorize against the current tip"
        )
    return ""


def authorize(
    request: ConnectorWriteRequest,
    *,
    path: Path | None = None,
    resolve_base_tip: BaseTipResolver | None = None,
) -> IngressDecision:
    """Evaluate one request against the trusted on-disk policy."""

    policy, error = load_policy(path)
    return evaluate_write_request(
        request,
        policy,
        resolve_base_tip=resolve_base_tip if resolve_base_tip is not None else git_base_tip,
        policy_error=error,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Authorize one governed connector code-write request, or reject it fail-closed."
    )
    result.add_argument(
        "--request",
        type=Path,
        metavar="PATH",
        help="JSON write-request document; omit to read the document from stdin.",
    )
    result.add_argument(
        "--emit-receipt",
        type=Path,
        metavar="PATH",
        help=(
            "On authorization, write the commit-bound authorization receipt here. "
            f"It must be committed to the candidate head as {AUTHORIZATION_RECEIPT_PATH}."
        ),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        raw = args.request.read_text(encoding="utf-8") if args.request else sys.stdin.read()
        request = ConnectorWriteRequest.from_dict(json.loads(raw))
    except Exception as exc:
        print(f"[Connector Write Ingress] REJECT: unreadable write request ({type(exc).__name__}: {exc})")
        return 1

    decision = authorize(request)
    verdict = "AUTHORIZE" if decision.authorized else "REJECT"
    print(f"[Connector Write Ingress] {verdict}: {decision.reason}")
    if not decision.authorized or decision.authorization is None:
        return 1

    if args.emit_receipt:
        args.emit_receipt.parent.mkdir(parents=True, exist_ok=True)
        args.emit_receipt.write_text(
            json.dumps(decision.authorization.document(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"[Connector Write Ingress] receipt written to {args.emit_receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
