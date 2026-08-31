"""Governed connector code-write ingress authorization (Issue #403).

This is the repository-owned decision function for the one narrow write path that
lets an explicitly authorized connector writer create code changes without a local
clone. It is an *additional* ingress: `.githooks/pre-push` remains the authoritative
boundary for clone-capable writers and is untouched by this module.

The decision is deliberately narrow and fail-closed. A request is authorized only
when every one of the following holds:

* the trusted policy loads, declares the ingress enabled, and binds at least one
  writer identity;
* the writer login is on the owner-bound allowlist and presents the exact granted
  capability;
* the target is a branch that is neither the base branch nor a forbidden ref, so a
  direct `main` write is unrepresentable;
* the target branch name encodes the one governing Issue the request declares, so
  branch/commit scope is traceable to a single task;
* the base ref matches the policy base and the declared base commit is exactly the
  observed base tip, so a stale base is rejected rather than silently merged later;
* every changed path is inside the granted scope and outside the prohibited scope.

Authorization here is *write* authorization only. It is never pre-push proof and
never admission: a connector-written candidate stays Draft/unadmitted until the
trusted hosted exact-head canonical preflight proves it (enforced separately in
`hunter_governance_review_v2.verify_code_write_ingress_provenance`). Hosted CI,
Hunter Governance Review, independent review, Hunter Merge Readiness, and owner
merge approval all remain mandatory and unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, fields
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from hunter_workflow_state import path_matches_scope_entry

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "CODE_WRITE_POLICY.json"

HEADS_PREFIX = "refs/heads/"
ISSUE_PLACEHOLDER = "{issue}"

_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?\Z")
_ISSUE = re.compile(r"\A[1-9][0-9]{0,9}\Z")


@dataclass(frozen=True)
class IngressDecision:
    """The authorization outcome. `reason` is always populated, including on success."""

    authorized: bool
    reason: str


def _reject(reason: str) -> IngressDecision:
    return IngressDecision(False, reason)


@dataclass(frozen=True)
class ConnectorWriteRequest:
    """One proposed connector write, as machine-readable fields.

    Every field is compared against the trusted policy and against repository
    evidence supplied by the caller. Nothing the connector writes in prose --
    commit message, PR body, comment -- can widen, waive, or substitute for any
    of them.
    """

    writer: str
    capability: str
    issue: str
    target_ref: str
    base_ref: str
    base_sha: str
    observed_base_tip_sha: str
    paths: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Any) -> ConnectorWriteRequest:
        if not isinstance(payload, dict):
            raise ValueError("write request must be a JSON object")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            # An unreadable field is a request claim the gate would silently
            # ignore, so refuse the request rather than evaluate part of it.
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

        return cls(
            writer=text("writer"),
            capability=text("capability"),
            issue=text("issue"),
            target_ref=text("target_ref"),
            base_ref=text("base_ref"),
            base_sha=text("base_sha"),
            observed_base_tip_sha=text("observed_base_tip_sha"),
            paths=path_tuple,
        )


@dataclass(frozen=True)
class ConnectorIngressPolicy:
    """The owner-authored connector ingress grant, as the evaluator consumes it."""

    enabled: bool
    writers: tuple[tuple[str, str], ...]
    required_capability: str
    base_ref: str
    forbidden_target_refs: frozenset[str]
    branch_pattern_template: str
    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    require_exact_base_tip: bool
    local_pre_push_equivalent: bool

    def capability_for(self, login: str) -> str | None:
        for granted_login, capability in self.writers:
            if granted_login == login:
                return capability
        return None


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
        if any(login == existing for existing, _ in writers):
            return None, f"connector write ingress declares duplicate writer {login!r}"
        writers.append((login, capability.strip()))

    base_ref = grant.get("base_ref")
    if not isinstance(base_ref, str) or not base_ref.strip():
        return None, "connector write ingress declares no base ref"

    template = grant.get("branch_pattern_template")
    if not isinstance(template, str) or ISSUE_PLACEHOLDER not in template:
        return None, "connector write ingress branch pattern must bind the governing Issue"

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

    return (
        ConnectorIngressPolicy(
            enabled=enabled,
            writers=tuple(writers),
            required_capability=required_capability.strip(),
            base_ref=base_ref.strip(),
            forbidden_target_refs=frozenset(item.strip() for item in forbidden_raw if item.strip()),
            branch_pattern_template=template,
            allowed_paths=allowed_paths,
            prohibited_paths=prohibited_paths,
            require_exact_base_tip=True,
            local_pre_push_equivalent=False,
        ),
        "",
    )


def evaluate_write_request(
    request: ConnectorWriteRequest,
    policy: ConnectorIngressPolicy | None,
    *,
    policy_error: str = "",
) -> IngressDecision:
    """Authorize or reject one proposed connector write. Fail-closed throughout."""

    if policy is None:
        return _reject(f"connector write ingress is unavailable: {policy_error or 'no usable policy'}")
    if not policy.enabled:
        return _reject("connector write ingress is not enabled")
    if not policy.writers:
        return _reject("connector write ingress binds no writer identity")

    writer = request.writer.strip().lower()
    if not writer:
        return _reject("write request declares no writer identity")
    granted = policy.capability_for(writer)
    if granted is None:
        return _reject(f"unauthorized connector writer {writer!r}")

    capability = request.capability.strip()
    if capability != policy.required_capability or granted != policy.required_capability:
        return _reject(
            f"writer {writer!r} presented capability {capability or '(none)'!r}, "
            f"but the grant requires {policy.required_capability!r}"
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

    issue = request.issue.strip().lstrip("#")
    if not _ISSUE.fullmatch(issue):
        return _reject("write request declares no single governing Issue number")
    expected_pattern = policy.branch_pattern_template.replace(ISSUE_PLACEHOLDER, issue)
    # A branch name is matched as a pattern, not as a path scope: directory-prefix
    # containment would let `connector/issue-403` also admit `connector/issue-403/x`.
    if not fnmatch(target_ref, expected_pattern):
        return _reject(f"branch {target_ref!r} is out of scope for Issue #{issue} (expected {expected_pattern!r})")

    base_sha = request.base_sha.strip().lower()
    observed_tip = request.observed_base_tip_sha.strip().lower()
    if not _SHA.fullmatch(base_sha):
        return _reject("write request declares no full base commit SHA")
    if not _SHA.fullmatch(observed_tip):
        return _reject("write request carries no observed base tip commit SHA")
    if policy.require_exact_base_tip and base_sha != observed_tip:
        return _reject(f"stale base: {base_sha[:10]} is not the current {base_ref} tip {observed_tip[:10]}")

    paths = tuple(path.strip() for path in request.paths)
    if not paths or any(not path for path in paths):
        return _reject("write request declares no changed paths to check against the granted scope")
    for path in paths:
        if path.startswith("/") or ".." in Path(path).parts:
            return _reject(f"path {path!r} is not a repository-relative path")

    prohibited = sorted(
        {path for path in paths if any(path_matches_scope_entry(path, e) for e in policy.prohibited_paths)}
    )
    if prohibited:
        return _reject("prohibited path(s) in write request: " + ", ".join(prohibited))

    outside = sorted(
        {path for path in paths if not any(path_matches_scope_entry(path, e) for e in policy.allowed_paths)}
    )
    if outside:
        return _reject("path(s) outside the granted connector scope: " + ", ".join(outside))

    return IngressDecision(
        True,
        f"authorized connector write by {writer!r} for Issue #{issue}: branch {target_ref}, "
        f"base {base_ref}@{base_sha[:10]}, {len(paths)} path(s) in scope. "
        "The candidate remains Draft/unadmitted until trusted hosted exact-head canonical preflight proves it.",
    )


def authorize(request: ConnectorWriteRequest, *, path: Path | None = None) -> IngressDecision:
    """Evaluate one request against the trusted on-disk policy."""

    policy, error = load_policy(path)
    return evaluate_write_request(request, policy, policy_error=error)


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
    return 0 if decision.authorized else 1


if __name__ == "__main__":
    raise SystemExit(main())
