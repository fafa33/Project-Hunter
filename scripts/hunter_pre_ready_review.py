"""Exact-head-bound pre-ready hostile review state.

Issue #412. Before PR #411 the first complete adversarial pass over a candidate
happened *after* it was marked Ready, so predictable defect families were
discovered by an automated reviewer in a loop of Ready -> findings -> fix ->
Ready. This module makes the hostile review a piece of repository-owned state
that exists before Ready and is bound to the exact candidate content.

The binding problem and its solution
------------------------------------
A review cannot name the commit SHA it reviews, because the review is committed
*into* that commit. So the review binds **content**, exactly as the connector
authorization receipt does: it records the canonical base->HEAD change set --
operation, path, previous path and resulting git blob SHA per file -- and
excludes only its own artifact path. Any later content mutation changes some
blob SHA, and the review no longer describes the candidate: it is stale.
Re-recording the review is therefore the only way to make it current again, and
re-recording is what forces a fresh adversarial pass.

The same claim set is verified two ways from one implementation: locally against
``git diff --raw``, and by the trusted controller against GitHub's changed-file
listing for the exact head. Neither trusts the review's own account of what
changed.

Ready is blocked, not merely warned, when the review is absent, stale,
incomplete against the applicable recurring-defect families, or still carries an
unresolved blocking finding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hunter_connector_write_ingress as ingress
from hunter_workflow_state import path_matches_scope_entry

ROOT = Path(__file__).resolve().parents[1]
REVIEW_RELATIVE_PATH = ".hunter/pre-ready-hostile-review.json"
REVIEW_PATH = ROOT / REVIEW_RELATIVE_PATH
REGISTRY_RELATIVE_PATH = "docs/DEFECT_REGISTRY.json"
REGISTRY_PATH = ROOT / REGISTRY_RELATIVE_PATH
CODE_WRITE_POLICY_RELATIVE_PATH = "docs/CODE_WRITE_POLICY.json"
CODE_WRITE_POLICY_PATH = ROOT / CODE_WRITE_POLICY_RELATIVE_PATH
REVIEW_SCHEMA = "hunter.pre-ready-hostile-review.v1"

#: Artifacts excluded from the reviewed change set. The review cannot bind its
#: own bytes without being unsatisfiable, and the connector receipt is by
#: contract the *last* mutation, so binding it would make every valid connector
#: candidate look stale.
EXCLUDED_PATHS = frozenset({REVIEW_RELATIVE_PATH, ingress.AUTHORIZATION_RECEIPT_PATH})

#: The single canonical claim set a review must carry -- nothing more, nothing
#: less. Local/pre-push verification and the hosted trusted controller consume
#: THIS ONE definition, so neither side can drift from the other. The review
#: authority (who reviewed, when, against which exact head) is deliberately NOT
#: a claim: it is document-level review metadata recorded beside the claims, so
#: the claims stay exactly the set the trusted default-branch controller was
#: merged with and no review can smuggle evidence in or out under a claim key.
CANONICAL_CLAIM_SET = frozenset(
    {
        "acceptance_criteria",
        "adversarial_dimensions",
        "base_ref",
        "base_sha",
        "defect_families",
        "findings",
        "issue",
        "review_target",
        "review_target_digest",
    }
)

CRITERION_VERDICTS = frozenset({"satisfied", "not-applicable"})
FAMILY_OUTCOMES = frozenset({"clear", "repaired"})
FINDING_SEVERITIES = frozenset({"blocking", "non-blocking"})
FINDING_RESOLUTIONS = frozenset({"resolved", "unresolved"})

#: Review-authority model (Issue #467 follow-on): the exact-head hostile review
#: remains mandatory, and Codex remains the Tier 1 primary. The reviewer pool is
#: an ordered chain -- Codex (Tier 1), any approved agent reviewers (Tier 2),
#: and the canonical *OpenCode* Hostile-Review guard as the last resort (Tier 3)
#: -- declared by CODE_WRITE_POLICY.json on the trusted default branch, so a
#: candidate can never widen its own pool, invent a reviewer, or relax its own
#: timeout bound. Any authority below the primary must record, in
#: machine-checkable fields, that every higher-priority enabled reviewer was
#: actually attempted and exhausted within the bounded timeout policy; the
#: guard's record must additionally state why those reviewers could not review
#: and the gate state it relied on. Failover is automatic: a review by a
#: lower-tier reviewer without the exhaustion trail, or a guard review that
#: skipped an enabled reviewer, is a bypass and fails closed.
CODEX_REVIEW_AUTHORITY = "codex"
OPENCODE_REVIEW_AUTHORITY = "opencode"
#: The field the canonical reviewer pool is declared under inside
#: ``review_authority`` of CODE_WRITE_POLICY.json.
REVIEWER_POOL_FIELD = "reviewer_pool"
#: The gate states a recorded last-resort guard review must claim at review
#: time. Each field has its own admissible value because the states mean
#: different things: Governance and trusted Preflight report "success"/"failure"
#: while structured evidence completeness is "complete"/"partial".
FALLBACK_REQUIRED_GATE_STATES = {
    "governance_state": "success",
    "trusted_preflight_state": "success",
    "structured_evidence_status": "complete",
}

#: The exact-head correction commit a resolved finding must name: a git commit SHA.
_GIT_SHA = re.compile(r"\A[0-9a-fA-F]{40}\Z")

#: The adversarial dimensions Issue #412 requires a large or high-risk candidate
#: to be swept in one batch rather than discovered one review round at a time.
REQUIRED_ADVERSARIAL_DIMENSIONS = (
    "authorization",
    "replay",
    "cutoff-history",
    "persistence-restrictions",
    "transaction-snapshot-consistency",
    "tamper-resistance",
    "caller-controlled-time-state",
    "monotonicity",
    "malformed-unknown-authority",
    "restart-crash-retry",
    "fail-closed",
)


#: A markdown heading whose text names the governing Issue's acceptance criteria.
_HEADING = re.compile(r"\A(#{1,6})\s+(.*?)\s*#*\s*\Z")
#: A top-level markdown list item. Indented items are sub-detail of a criterion,
#: not criteria in their own right.
_TOP_LEVEL_BULLET = re.compile(r"\A[-*+]\s+(.*)\Z")
ACCEPTANCE_CRITERIA_HEADING = "acceptance criteria"


def normalize_criterion(text: str) -> str:
    """Fold one criterion to its comparison form.

    Markdown emphasis, backticks, checkbox markers, surrounding whitespace and a
    trailing full stop all carry no meaning for *which* criterion this is, so
    they are removed before comparison. What remains is the criterion's words, so
    a review that restates a criterion in the Issue's own terms matches and one
    that names a different criterion does not.
    """

    folded = unicodedata.normalize("NFKC", text).strip()
    folded = re.sub(r"\A\[[ xX]\]\s*", "", folded)
    folded = folded.replace("`", "").replace("**", "").replace("__", "")
    folded = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", folded)
    folded = " ".join(folded.split()).casefold()
    return folded.rstrip(".").strip()


def parse_issue_acceptance_criteria(body: str) -> tuple[str, ...]:
    """The governing Issue's acceptance criteria, normalized, in order.

    Scoped to the section a heading names "acceptance criteria" so the Issue's
    other bullet lists -- required behaviour, regression scenarios -- are not
    mistaken for criteria. An Issue with no such section defines no criteria and
    imposes no coverage requirement; that is the truthful reading, not a bypass,
    because there is nothing for a review to cover.
    """

    criteria: list[str] = []
    in_section = False
    pending: str | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            normalized = normalize_criterion(pending)
            if normalized and normalized not in criteria:
                criteria.append(normalized)
            pending = None

    for raw in body.splitlines():
        heading = _HEADING.match(raw.strip())
        if heading is not None:
            flush()
            in_section = normalize_criterion(heading.group(2)) == ACCEPTANCE_CRITERIA_HEADING
            continue
        if not in_section:
            continue
        bullet = _TOP_LEVEL_BULLET.match(raw.rstrip())
        if bullet is not None:
            flush()
            pending = bullet.group(1)
        elif pending is not None and raw.strip():
            if _TOP_LEVEL_BULLET.match(raw.strip()):
                # An indented bullet is sub-detail of the criterion above it, not
                # part of that criterion's text and not a criterion of its own.
                continue
            # A wrapped continuation line belongs to the criterion above it.
            pending = f"{pending} {raw.strip()}"
        elif not raw.strip():
            flush()
    flush()
    return tuple(criteria)


@dataclass(frozen=True)
class ReviewVerdict:
    """The outcome of evaluating pre-ready review state against exact content."""

    state: str  # "valid" | "missing" | "stale" | "incomplete" | "unresolved"
    reason: str

    @property
    def ok(self) -> bool:
        return self.state == "valid"


def _canonical(claims: dict[str, Any]) -> str:
    return json.dumps(claims, sort_keys=True, separators=(",", ":"))


def review_id(claims: dict[str, Any]) -> str:
    """SHA-256 over exactly the review claims, so a hand-edited review is detectable."""

    return hashlib.sha256(_canonical(claims).encode("utf-8")).hexdigest()


def target_changes(changes: tuple[ingress.ConnectorFileChange, ...]) -> tuple[ingress.ConnectorFileChange, ...]:
    """The reviewed change set: canonical, artifact-free, rename-representation free.

    Two evidence sources describe the same candidate -- local ``git diff --raw``
    and GitHub's changed-file listing -- and they do not have to agree on whether
    a given pair of paths is a rename or an unrelated delete plus add. Rename
    detection is a heuristic with its own thresholds on each side, so binding the
    review to a representation that depends on it would make a locally valid
    review look stale to the trusted controller for no reason at all.

    So a rename is expanded into the two facts both sources always agree on: the
    old path is gone and the new path holds this content. The reviewed subject is
    unchanged -- the same paths and the same resulting bytes -- and the binding
    stops depending on a heuristic.
    """

    expanded: list[ingress.ConnectorFileChange] = []
    for change in changes:
        if change.status == "renamed":
            expanded.append(ingress.ConnectorFileChange("removed", change.previous_path, "", ""))
            expanded.append(ingress.ConnectorFileChange("added", change.path, "", change.blob_sha))
        else:
            expanded.append(change)
    # Exclusion is applied to the expanded facts, never to the original record.
    # Dropping a whole rename because its *destination* is an excluded artifact
    # would carry the disappearance of its source out of the reviewed set with
    # it, so renaming a protected file onto an artifact path would delete it
    # unreviewed. Expanded, the removal of the source survives the exclusion.
    return tuple(sorted(change for change in expanded if change.path not in EXCLUDED_PATHS))


def target_digest(changes: tuple[ingress.ConnectorFileChange, ...]) -> str:
    """A stable digest of the exact reviewed content, independent of ordering."""

    documents = [change.document() for change in target_changes(changes)]
    return hashlib.sha256(json.dumps(documents, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --- Applicable recurring-defect families -----------------------------------


def load_families(path: Path | None = None) -> tuple[tuple[dict[str, Any], ...], str]:
    """Read the recurring-defect family catalog. An unreadable catalog fails closed."""

    target = path or REGISTRY_PATH
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), f"{REGISTRY_RELATIVE_PATH} is missing"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (), f"{REGISTRY_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})"
    families = document.get("families")
    if not isinstance(families, list) or not families:
        return (), f"{REGISTRY_RELATIVE_PATH} declares no recurring-defect families"
    if not all(isinstance(entry, dict) and isinstance(entry.get("id"), str) for entry in families):
        return (), f"{REGISTRY_RELATIVE_PATH} families must be objects carrying a string id"
    return tuple(families), ""


def applicable_family_ids(families: tuple[dict[str, Any], ...], changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Family ids whose declared applicability covers at least one changed path.

    Applicability is a structural property of what the candidate touches, not a
    judgement the candidate makes about itself, so it is re-derived here from the
    trusted change set every time rather than read from the review.
    """

    applicable: list[str] = []
    for family in families:
        scope = family.get("applicability")
        entries = scope.get("changed_paths") if isinstance(scope, dict) else None
        if not isinstance(entries, list):
            continue
        if any(
            path_matches_scope_entry(path, str(entry))
            for path in changed_paths
            for entry in entries
            if isinstance(entry, str) and entry.strip()
        ):
            applicable.append(str(family["id"]))
    return tuple(sorted(applicable))


# --- Claim construction and verification ------------------------------------


def build_claims(
    *,
    issue: str,
    base_ref: str,
    base_sha: str,
    changes: tuple[ingress.ConnectorFileChange, ...],
    acceptance_criteria: tuple[dict[str, Any], ...],
    defect_families: tuple[dict[str, Any], ...],
    findings: tuple[dict[str, Any], ...],
    adversarial_dimensions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "acceptance_criteria": [dict(sorted(item.items())) for item in acceptance_criteria],
        "adversarial_dimensions": sorted(set(adversarial_dimensions)),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "defect_families": [dict(sorted(item.items())) for item in defect_families],
        "findings": [dict(sorted(item.items())) for item in findings],
        "issue": issue,
        "review_target": [change.document() for change in target_changes(changes)],
        "review_target_digest": target_digest(changes),
    }


def document_for(claims: dict[str, Any], authority: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the review document: canonical claims plus document-level metadata.

    ``claims`` is exactly the canonical claim set. The authority is review
    metadata recorded BESIDE the claims (like the review_id) -- not inside them --
    so the claims stay the canonical set the trusted default-branch controller
    verifies while the exact-head authority contract still binds the document.
    """
    document: dict[str, Any] = {"schema": REVIEW_SCHEMA, "claims": claims, "review_id": review_id(claims)}
    if authority is not None:
        document["authority"] = dict(sorted(authority.items()))
    return document


# --- Ordered reviewer pool --------------------------------------------------


def _pool_problems(policy: Mapping[str, Any]) -> list[str]:
    """Structural validation of the reviewer-pool declaration; empty means valid.

    Shared by the local verifier and the Defect Prevention guard through
    ``load_reviewer_pool``, so the two cannot drift into different readings of
    the same pool. The checks are structural rather than spellings, so a valid
    equivalent declaration is never rejected; missing, malformed, or ambiguous
    declarations fail closed.
    """

    problems: list[str] = []
    progression = policy.get("review_progression")
    if not isinstance(progression, dict):
        return ["review_progression must be an object"]
    authority = progression.get("review_authority")
    if not isinstance(authority, dict):
        return ["review_authority must be an object"]
    if REVIEWER_POOL_FIELD not in authority:
        return [f"review_authority must declare a {REVIEWER_POOL_FIELD}"]
    raw = authority[REVIEWER_POOL_FIELD]
    if not isinstance(raw, dict):
        return [f"{REVIEWER_POOL_FIELD} must be an object"]

    for field in ("model", "ordering", "exhaustion_semantics"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{REVIEWER_POOL_FIELD} must document its {field}")

    last_resort = raw.get("last_resort")
    if not isinstance(last_resort, str) or not last_resort.strip():
        problems.append(f"{REVIEWER_POOL_FIELD} must declare a last_resort guard")

    timeout = raw.get("timeout_policy")
    if not isinstance(timeout, dict):
        problems.append(f"{REVIEWER_POOL_FIELD} must declare a timeout_policy")
        timeout = {}
    else:
        if timeout.get("bounded") is not True:
            problems.append(f"{REVIEWER_POOL_FIELD} timeout_policy must be bounded")
        default_seconds = timeout.get("default_seconds")
        max_seconds = timeout.get("max_seconds")
        if isinstance(default_seconds, bool) or not isinstance(default_seconds, int) or default_seconds <= 0:
            problems.append(f"{REVIEWER_POOL_FIELD} timeout_policy default_seconds must be a positive integer")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or max_seconds <= 0:
            problems.append(f"{REVIEWER_POOL_FIELD} timeout_policy max_seconds must be a positive integer")
        if (
            isinstance(default_seconds, int)
            and not isinstance(default_seconds, bool)
            and isinstance(max_seconds, int)
            and not isinstance(max_seconds, bool)
            and max_seconds < default_seconds
        ):
            problems.append(f"{REVIEWER_POOL_FIELD} timeout_policy max_seconds must be at least default_seconds")
        retries = timeout.get("retries_per_agent")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            problems.append(f"{REVIEWER_POOL_FIELD} timeout_policy retries_per_agent must be a non-negative integer")

    agents = raw.get("agents")
    if not isinstance(agents, list) or not agents:
        problems.append(f"{REVIEWER_POOL_FIELD} agents must be a non-empty list")
        return problems
    seen_ids: set[str] = set()
    seen_priorities: set[int] = set()
    codex_primary = False
    for entry in agents:
        if not isinstance(entry, dict):
            problems.append(f"{REVIEWER_POOL_FIELD} agents must be objects")
            continue
        agent_id = entry.get("id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            problems.append(f"{REVIEWER_POOL_FIELD} agents must carry a non-empty id")
        else:
            if agent_id in seen_ids:
                problems.append(f"{REVIEWER_POOL_FIELD} duplicate agent id {agent_id!r}")
            seen_ids.add(agent_id)
        priority = entry.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
            problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must carry a positive-integer priority")
        else:
            if priority in seen_priorities:
                problems.append(f"{REVIEWER_POOL_FIELD} agents must declare distinct priorities")
            seen_priorities.add(priority)
        enabled = entry.get("enabled")
        if not isinstance(enabled, bool):
            problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must declare enabled as a boolean")
        exact_head = entry.get("exact_head_support")
        if not isinstance(exact_head, bool):
            problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must declare exact_head_support as a boolean")
        elif enabled is True and exact_head is not True:
            problems.append(f"{REVIEWER_POOL_FIELD} enabled agent {agent_id!r} must support exact-head binding")
        for field in ("trigger_method", "evidence_parser"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must document its {field}")
        retryable = entry.get("retryable")
        if not isinstance(retryable, bool):
            problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must declare retryable as a boolean")
        fallback_eligibility = entry.get("fallback_eligibility")
        if not isinstance(fallback_eligibility, bool):
            problems.append(f"{REVIEWER_POOL_FIELD} agent {agent_id!r} must declare fallback_eligibility as a boolean")
        elif fallback_eligibility is True:
            problems.append(
                f"{REVIEWER_POOL_FIELD} agent {agent_id!r} cannot be fallback-eligible; only the declared "
                "last_resort guard may close the pool"
            )
        timeout_seconds = entry.get("timeout_seconds")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            if enabled is True:
                problems.append(
                    f"{REVIEWER_POOL_FIELD} enabled agent {agent_id!r} must declare a positive timeout_seconds"
                )
        else:
            max_seconds = timeout.get("max_seconds")
            if isinstance(max_seconds, int) and not isinstance(max_seconds, bool) and timeout_seconds > max_seconds:
                problems.append(
                    f"{REVIEWER_POOL_FIELD} agent {agent_id!r} timeout_seconds cannot exceed the "
                    f"pool max_seconds ({max_seconds})"
                )
        if agent_id == CODEX_REVIEW_AUTHORITY and enabled is True and priority == 1:
            codex_primary = True
    if not codex_primary:
        problems.append(f"{REVIEWER_POOL_FIELD} must declare Codex as the enabled priority-1 primary reviewer")
    if isinstance(last_resort, str) and last_resort in seen_ids:
        problems.append(f"{REVIEWER_POOL_FIELD} last_resort {last_resort!r} must not also be a pool agent")
    return problems


def load_reviewer_pool(source: Path | Mapping[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    """Parse the canonical ordered reviewer pool from the code-write policy.

    ``source`` is either a policy mapping, a path, or ``None`` (the repository
    default branch). Every authority record is validated against the pool the
    trusted default branch declares, so a candidate can never widen its own
    pool, invent a reviewer, or relax its own timeout bound. An unreadable or
    structurally invalid pool fails closed. The same implementation is consumed
    by the local pre-push check, the hosted trusted controller, and the Defect
    Prevention guard, so none of them can drift into a different reading.
    """

    if source is None or isinstance(source, Path):
        target = source if source is not None else CODE_WRITE_POLICY_PATH
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} is missing"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})"
        if not isinstance(loaded, dict):
            return None, f"{CODE_WRITE_POLICY_RELATIVE_PATH} must be a JSON object"
        policy = loaded
    elif isinstance(source, Mapping):
        policy = dict(source)
    else:
        return None, "reviewer pool source must be a policy mapping or a path"

    problems = _pool_problems(policy)
    if problems:
        return None, "CODE_WRITE_POLICY reviewer_pool is invalid: " + "; ".join(problems)

    raw = policy["review_progression"]["review_authority"][REVIEWER_POOL_FIELD]
    agents = tuple(sorted(raw["agents"], key=lambda entry: int(entry["priority"])))
    return (
        {
            "last_resort": str(raw["last_resort"]),
            "last_resort_github_login": str(raw.get("last_resort_github_login") or ""),
            "timeout_policy": dict(raw["timeout_policy"]),
            "agents": agents,
        },
        "",
    )


def enabled_pool_reviewers(pool: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """The enabled reviewers of a normalized pool, already in priority order."""

    return tuple(agent for agent in pool["agents"] if agent.get("enabled") is True)


def _exhaustion_error(pool: Mapping[str, Any], authority: Mapping[str, Any], authority_type: str) -> str | None:
    """Machine-checkable proof that every higher-priority enabled reviewer was exhausted.

    Failover is a deterministic decision, never a human-noticed skip. A recorded
    authority from any reviewer below the primary must cover every enabled
    reviewer above it (the guard must cover every enabled reviewer), each with an
    ``exhausted`` status, a reason, an ``attempt_count`` honouring the retry
    policy, a ``failure_class``, a machine-verifiable ``invocation_reference``,
    and a ``timeout_seconds`` exactly equal to the configured reviewer timeout.
    Only then is a lower-tier review admissible.
    """

    message, _, unproven = _exhaustion_error_impl(pool, authority, authority_type)
    return message if unproven else None


def _exhaustion_error_impl(
    pool: Mapping[str, Any], authority: Mapping[str, Any], authority_type: str
) -> tuple[str, str, bool]:
    """Pure verdict over recorded exhaustion evidence: (message, kind, unproven).

    ``kind`` is one of ``"POOL_NOT_EXHAUSTED"`` (a required enabled reviewer was
    never attempted or recorded as anything but exhausted -- a skip) or
    ``"EXHAUSTION_UNPROVEN"`` (attempts exist but cannot be believed: the
    recorded timeout differs from the configured reviewer timeout, the retry
    policy was not honoured, or there is no machine-verifiable invocation
    reference). ``unproven`` is ``True`` only for a genuine failure.
    """

    enabled = enabled_pool_reviewers(pool)
    priorities = {str(agent["id"]): int(agent["priority"]) for agent in enabled}
    if authority_type == str(pool["last_resort"]):
        required = enabled
    else:
        own_priority = priorities.get(authority_type)
        if own_priority is None:
            return "", "", False
        required = [agent for agent in enabled if int(agent["priority"]) < own_priority]
    if not required:
        return "", "", False

    attempts = authority.get("reviewer_attempts")
    if attempts is None:
        return (
            "review authority must record reviewer exhaustion evidence for every higher-priority "
            "enabled reviewer before a lower-tier review authority is admissible",
            "POOL_NOT_EXHAUSTED",
            True,
        )
    if not isinstance(attempts, list) or not attempts:
        return (
            "reviewer exhaustion evidence must be a non-empty list of per-agent attempt records",
            "POOL_NOT_EXHAUSTED",
            True,
        )

    covered: set[str] = set()
    problems: list[str] = []
    pool_not_exhausted = False
    for attempt in attempts:
        if not isinstance(attempt, dict):
            problems.append("reviewer exhaustion evidence contains a malformed attempt record")
            continue
        agent_id = attempt.get("agent_id")
        if not isinstance(agent_id, str) or agent_id not in priorities:
            problems.append(f"reviewer exhaustion attempt names {agent_id!r}, which is not an enabled pool reviewer")
            continue
        if agent_id in covered:
            problems.append(f"reviewer exhaustion attempt for {agent_id} is recorded more than once")
            continue
        if attempt.get("status") != "exhausted":
            pool_not_exhausted = True
            problems.append(
                f"reviewer attempt for {agent_id} records status {attempt.get('status')!r}; a higher-priority "
                "reviewer is bypassed unless it was actually attempted and exhausted"
            )
            continue
        # Agent was attempted and exhausted - mark as covered regardless of
        # evidence quality issues (those are EXHAUSTION_UNPROVEN, not POOL_NOT_EXHAUSTED)
        covered.add(agent_id)

        reason = attempt.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"reviewer attempt for {agent_id} must record why it was exhausted")
        configured_timeout = next(
            (agent.get("timeout_seconds") for agent in required if str(agent.get("id")) == agent_id), None
        )
        timeout = attempt.get("timeout_seconds")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not isinstance(configured_timeout, int)
            or timeout != configured_timeout
        ):
            problems.append(
                f"reviewer attempt for {agent_id} must record timeout_seconds equal to the configured "
                f"reviewer timeout ({configured_timeout!r})"
            )
        failure_class = attempt.get("failure_class")
        if failure_class not in ("transient", "permanent"):
            problems.append(
                f"reviewer attempt for {agent_id} must record a machine-checkable failure_class "
                "(transient/permanent)"
            )
        invocation = attempt.get("invocation_reference")
        if not isinstance(invocation, str) or not invocation.strip():
            problems.append(
                f"reviewer attempt for {agent_id} must record a machine-verifiable invocation_reference "
                "to the exhausted attempt evidence"
            )
        count = attempt.get("attempt_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            problems.append(f"reviewer attempt for {agent_id} must record a positive attempt_count")
        elif failure_class == "transient" and next(
            (agent.get("retryable") for agent in required if agent["id"] == agent_id), False
        ):
            allowed = 1 + int((pool.get("timeout_policy") or {}).get("retries_per_agent") or 0)
            if count < allowed:
                problems.append(
                    f"reviewer attempt for {agent_id} records {count} attempt(s); a retryable transient "
                    f"failure requires {allowed} attempts (policy retries_per_agent={allowed - 1})"
                )

    missing = sorted({str(agent["id"]) for agent in required} - covered)
    if missing:
        pool_not_exhausted = True
        problems.append(
            "reviewer exhaustion evidence is incomplete; missing exhausted attempts for " + ", ".join(missing)
        )
    if not problems:
        return "", "", False
    message = "reviewer exhaustion evidence fails closed: " + "; ".join(problems)
    return message, "POOL_NOT_EXHAUSTED" if pool_not_exhausted else "EXHAUSTION_UNPROVEN", True


def exhaustion_failure_kind(pool: Mapping[str, Any], authority: Mapping[str, Any], authority_type: str) -> str | None:
    """Classification of unproven exhaustion: POOL_NOT_EXHAUSTED or EXHAUSTION_UNPROVEN.

    ``None`` means the recorded exhaustion evidence satisfies the trusted
    contract. The classification is consumed by the merge-readiness authority
    resolver so a guard review's failure surfaces as an explicit state, never as
    a silently accepted last resort.
    """

    message, kind, unproven = _exhaustion_error_impl(pool, authority, authority_type)
    return kind if unproven else None


def authority_problem(document: Mapping[str, Any]) -> str | None:
    """Public validation of the recorded review-authority record, or ``None``."""

    return _authority_error(document)


def _authority_error(document: dict[str, Any]) -> str | None:
    """Validate the recorded review-authority record before believing it.

    The admissible authorities are the reviewer pool the trusted default branch
    declares: the enabled pool reviewers in priority order plus the last-resort
    guard, all sharing one structural contract -- type, tool identity, the exact
    head reviewed, when, and which artifact carries the evidence. Any reviewer
    below the primary must record the machine-checkable exhaustion of every
    higher-priority enabled reviewer (the guard must record the whole enabled
    pool), and must do so within the bounded timeout policy; the guard must
    additionally state why the pool reviewers could not review and record that
    every snapshot gate it relied on was green. `verify_claims` cannot tell the
    authorities apart, so neither can lengthen its own reach: a lower-tier
    review is admitted on the credibility of the recorded exhaustion trail and,
    for the guard, the recorded reason and gates -- never on the reviewer's
    name. A review that bypasses an enabled reviewer, or a guard review whose
    exhaustion cannot be proven, fails closed with no human-noticed skip needed.

    The authority is document-level review metadata, recorded beside the claims
    rather than inside them, so the claims stay the canonical claim set the
    trusted controller verifies.
    """

    pool, pool_error = load_reviewer_pool()
    if pool_error or pool is None:
        return pool_error or "reviewer pool is unavailable"
    authority = document.get("authority")
    if not isinstance(authority, dict):
        return "review document must carry a review-authority record"
    authority_type = authority.get("type")
    admissible = {str(agent["id"]) for agent in enabled_pool_reviewers(pool)} | {str(pool["last_resort"])}
    if authority_type not in admissible:
        return f"review authority type must be one of {sorted(admissible)}"
    if not isinstance(authority.get("tool"), str) or not authority["tool"].strip():
        return "review authority must name its reviewer/tool identity"
    head_sha = authority.get("head_sha")
    if not isinstance(head_sha, str) or _GIT_SHA.fullmatch(head_sha) is None:
        return "review authority must record the exact 40-character head SHA it reviewed"
    if not isinstance(authority.get("reviewed_at"), str) or not authority["reviewed_at"].strip():
        return "review authority must record the reviewed_at timestamp"
    if not isinstance(authority.get("artifact"), str) or not authority["artifact"].strip():
        return "review authority must reference the hostile-review artifact it verifies"

    if authority_type == str(pool["last_resort"]):
        reason = authority.get("fallback_reason")
        if not isinstance(reason, str) or not reason.strip():
            return (
                "opencode fallback authority must record why Codex could not review; "
                "skipping Codex without a reason is forbidden"
            )
        # A guard review is legitimate only when nothing is waiting on it: the
        # exact head (held by the recorded head_sha), no open threads, and green
        # gates.
        unresolved = authority.get("unresolved_thread_count")
        # bool is an int subclass; a True is a claim that something waited.
        if isinstance(unresolved, bool) or not isinstance(unresolved, int) or unresolved != 0:
            return "opencode fallback authority must record a zero unresolved-thread count at review time"
        readable_gates = {
            "governance_state": "Governance",
            "trusted_preflight_state": "trusted Preflight",
            "structured_evidence_status": "complete structured evidence",
        }
        for gate in sorted(FALLBACK_REQUIRED_GATE_STATES):
            required = FALLBACK_REQUIRED_GATE_STATES[gate]
            if authority.get(gate) != required:
                return (
                    f"opencode fallback authority requires recorded {readable_gates[gate]} "
                    f"== {required!r} at review time"
                )

    exhaustion_problem = _exhaustion_error(pool, authority, authority_type)
    if exhaustion_problem is not None:
        return exhaustion_problem
    return None


def _structural_error(claims: dict[str, Any]) -> str | None:
    """Validate the review's own shape before comparing it to anything."""

    if set(claims) != CANONICAL_CLAIM_SET:
        return "review claims must carry exactly the canonical claim set"
    for name in ("base_ref", "base_sha", "issue", "review_target_digest"):
        if not isinstance(claims.get(name), str) or not claims[name].strip():
            return f"review claim {name!r} must be a non-empty string"
    if not str(claims["issue"]).strip().lstrip("#").isdigit():
        return "review claim 'issue' must name the governing Issue number"

    criteria = claims.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        return "review must record the governing Issue acceptance criteria"
    for item in criteria:
        if not isinstance(item, dict):
            return "each acceptance criterion must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "criterion", "evidence")):
            return "each acceptance criterion must carry a non-empty id, criterion and evidence"
        if item.get("verdict") not in CRITERION_VERDICTS:
            return f"acceptance criterion {item.get('id')!r} must be verdicted {sorted(CRITERION_VERDICTS)}"
    if not any(item.get("verdict") == "satisfied" for item in criteria):
        return "a review in which no acceptance criterion is satisfied is not a completed review"

    families = claims.get("defect_families")
    if not isinstance(families, list):
        return "review defect_families must be a list"
    for item in families:
        if not isinstance(item, dict):
            return "each defect family record must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("family", "evidence")):
            return "each defect family record must carry a non-empty family and evidence"
        if item.get("outcome") not in FAMILY_OUTCOMES:
            return f"defect family {item.get('family')!r} must record an outcome in {sorted(FAMILY_OUTCOMES)}"

    findings = claims.get("findings")
    if not isinstance(findings, list):
        return "review findings must be a list"
    for item in findings:
        if not isinstance(item, dict):
            return "each finding must be an object"
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "evidence")):
            return "each finding must carry a non-empty id and evidence"
        if item.get("severity") not in FINDING_SEVERITIES:
            return f"finding {item.get('id')!r} must declare a severity in {sorted(FINDING_SEVERITIES)}"
        if item.get("resolution") not in FINDING_RESOLUTIONS:
            return f"finding {item.get('id')!r} must declare a resolution in {sorted(FINDING_RESOLUTIONS)}"
        if item.get("resolution") == "resolved":
            # Issue #467: a finding is fixed only when the resolution is backed by
            # structured evidence -- the exact-head correction commit and a
            # regression test committed in the change set -- so "resolved" is a
            # real disposition, not a thread that was closed without proof.
            evidence = item.get("resolution_evidence")
            if not isinstance(evidence, dict):
                return (
                    f"finding {item.get('id')!r} resolved without structured resolution evidence; "
                    "a fixed finding must name its exact-head correction commit and committed regression test"
                )
            correction = evidence.get("correction")
            if not isinstance(correction, str) or _GIT_SHA.fullmatch(correction) is None:
                return (
                    f"finding {item.get('id')!r} structured resolution evidence must name "
                    "the exact-head correction commit SHA"
                )
            regression_test = evidence.get("regression_test")
            if not isinstance(regression_test, str) or not regression_test.strip():
                return f"finding {item.get('id')!r} structured resolution evidence must name " "a regression test path"

    dimensions = claims.get("adversarial_dimensions")
    if not isinstance(dimensions, list) or not all(isinstance(item, str) for item in dimensions):
        return "review adversarial_dimensions must be an array of dimension names"
    missing = sorted(set(REQUIRED_ADVERSARIAL_DIMENSIONS) - set(dimensions))
    if missing:
        return "the single adversarial batch is incomplete; missing dimensions: " + ", ".join(missing)

    if ingress.normalize_changes(claims.get("review_target")) is None:
        return "review_target must be canonical exact file transitions"
    return None


def verify_claims(
    document: Any,
    *,
    base_sha: str,
    changes: tuple[ingress.ConnectorFileChange, ...],
    families: tuple[dict[str, Any], ...],
    issue_criteria: tuple[str, ...] | None = None,
    resolution_corrections: frozenset[str] | None = None,
    head_sha: str | None = None,
) -> ReviewVerdict:
    """Compare repository-owned review state against the exact candidate content.

    ``changes`` and ``base_sha`` must come from trusted evidence -- the local git
    range, or GitHub's changed-file listing for the exact head -- never from the
    review document, which is the thing being checked.

    ``issue_criteria`` is the governing Issue's acceptance criteria, normalized and
    derived by the caller from trusted evidence. When supplied, every one of them
    must be addressed by the review: a review is otherwise free to declare a
    single self-authored criterion satisfied and call itself complete, which
    proves nothing about the Issue it claims to have been reviewed against.
    Extra entries beyond the derived set stay allowed -- a reviewer may record
    more than the Issue asks -- because coverage is measured against the trusted
    set, never against what the review lists.

    The binding is deliberately to content rather than to a pull-request number:
    a review is a statement about a diff, so two candidates with a byte-identical
    diff from the same fork point have been reviewed by the same pass, and the
    only way to reuse a review is to reproduce exactly the content it covers.
    Every mutation, however small, changes a blob SHA and invalidates it.
    """

    if document is None:
        return ReviewVerdict("missing", "no pre-ready hostile review exists for this candidate")
    if not isinstance(document, dict) or document.get("schema") != REVIEW_SCHEMA:
        return ReviewVerdict("missing", f"pre-ready hostile review must use schema {REVIEW_SCHEMA}")
    if "review_request" in document:
        return ReviewVerdict("missing", "a review request is not completed exact-head review authority")
    claims = document.get("claims")
    if not isinstance(claims, dict):
        return ReviewVerdict("missing", "pre-ready hostile review claims must be an object")
    if document.get("review_id") != review_id(claims):
        return ReviewVerdict("stale", "pre-ready hostile review identifier does not match its own claims")

    # Issue #467: the authority is document-level review metadata recorded beside
    # the canonical claims. It must exist and satisfy the authority contract
    # (Codex primary, recorded-reason OpenCode fallback) before anything that
    # binds the candidate is trusted.
    authority_problem = _authority_error(document)
    if authority_problem is not None:
        return ReviewVerdict("incomplete", authority_problem)

    problem = _structural_error(claims)
    if problem is not None:
        return ReviewVerdict("incomplete", problem)

    expected_target = [change.document() for change in target_changes(changes)]
    if claims["review_target"] != expected_target:
        return ReviewVerdict(
            "stale",
            "the pre-ready hostile review describes different content than this candidate head; "
            "the candidate was mutated after it was reviewed",
        )
    if claims["review_target_digest"] != target_digest(changes):
        return ReviewVerdict("stale", "the pre-ready hostile review digest does not bind this candidate's content")
    if claims["base_sha"].strip().lower() != base_sha.strip().lower():
        return ReviewVerdict(
            "stale",
            f"the pre-ready hostile review was taken against base {claims['base_sha'][:10]}, "
            f"not this candidate's base {base_sha[:10]}",
        )

    # Issue #467: the review is a statement about an exact commit, so the head it
    # claims to have reviewed is part of its evidence, not decoration. Content
    # equality alone cannot detect an amended commit whose diff is byte-identical,
    # and an artifact that records an ancestor head must never legitimise a later
    # head: the authority is positive review at *this* exact head, not review of
    # anything on the candidate's history. The self-inserted artifact commit is
    # therefore deliberately inert -- it records the pre-commit HEAD and can never
    # be treated as authority for the commit that records it.
    recorded_head = document["authority"]["head_sha"]
    if head_sha is not None and recorded_head.strip().lower() != head_sha.strip().lower():
        return ReviewVerdict(
            "stale",
            f"the pre-ready hostile review was recorded for exact head {recorded_head[:10]}, "
            f"not the evaluated exact head {head_sha[:10]}",
        )

    changed_paths = tuple(sorted({path for change in target_changes(changes) for path in change.affected_paths()}))
    required = set(applicable_family_ids(families, changed_paths))
    reviewed = {str(item.get("family")) for item in claims["defect_families"]}
    # A family this catalog does not know is deliberately not an error. The
    # trusted controller reads the catalog from the default branch, so the very
    # candidate that *adds* a family would otherwise be blocked for reviewing the
    # family it introduces. Naming an extra family cannot make an incomplete
    # review look complete either: what is required is derived from the trusted
    # catalog and the trusted changed paths, never from what the review lists.
    missing = sorted(required - reviewed)
    if missing:
        return ReviewVerdict(
            "incomplete",
            "applicable recurring-defect prevention checks are incomplete: " + ", ".join(missing),
        )

    if issue_criteria is not None:
        reviewed_criteria = {normalize_criterion(str(item.get("criterion"))) for item in claims["acceptance_criteria"]}
        uncovered = [criterion for criterion in issue_criteria if criterion not in reviewed_criteria]
        if uncovered:
            preview = "; ".join(criterion[:70] for criterion in uncovered[:3])
            return ReviewVerdict(
                "incomplete",
                f"the review does not cover {len(uncovered)} of the {len(issue_criteria)} acceptance criteria "
                f"the governing Issue defines: {preview}",
            )

    findings = claims.get("findings")
    resolved = [item for item in findings if isinstance(item, dict) and item.get("resolution") == "resolved"]
    untracked = sorted(
        str(item.get("id"))
        for item in resolved
        if str((item.get("resolution_evidence") or {}).get("regression_test") or "") not in changed_paths
    )
    if untracked:
        return ReviewVerdict(
            "incomplete",
            "resolved findings do not commit their regression test in the change set: " + ", ".join(untracked),
        )
    if resolution_corrections is not None:
        staged = sorted(
            str(item.get("id"))
            for item in resolved
            if str((item.get("resolution_evidence") or {}).get("correction") or "") not in resolution_corrections
        )
        if staged:
            return ReviewVerdict(
                "stale",
                "resolved finding correction commits are not part of this candidate's commit range: "
                + ", ".join(staged),
            )

    unresolved = sorted(
        str(item.get("id"))
        for item in claims["findings"]
        if item.get("severity") == "blocking" and item.get("resolution") != "resolved"
    )
    if unresolved:
        return ReviewVerdict("unresolved", "substantive review findings remain unresolved: " + ", ".join(unresolved))

    return ReviewVerdict(
        "valid",
        f"complete base->HEAD hostile review for Issue #{claims['issue']} covers "
        f"{len(required)} applicable recurring-defect famil{'y' if len(required) == 1 else 'ies'} "
        f"and {len(claims['acceptance_criteria'])} acceptance criteria"
        + (f", including all {len(issue_criteria)} the Issue defines" if issue_criteria else ""),
    )


# --- Local git evidence -----------------------------------------------------


#: GitHub's changed-file vocabulary is wider than the canonical one. Mapping is
#: explicit and total: a status not named here is unrecognised evidence and fails
#: closed rather than being silently dropped from the reviewed set.
GITHUB_STATUS_MAP = {
    "added": "added",
    "modified": "modified",
    "removed": "removed",
    "renamed": "renamed",
    "changed": "modified",
    "copied": "added",
}


def canonical_status(status: str) -> str | None:
    """The canonical change status for one GitHub status, or ``None`` if unknown."""

    return GITHUB_STATUS_MAP.get(status.strip().lower())


class GitEvidenceUnavailable(RuntimeError):
    """The local change set could not be derived, so the review cannot be checked."""


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


def _is_ancestor(possible_ancestor: str, commit: str, *, cwd: Path | None = None) -> bool:
    """Whether ``possible_ancestor`` is ``commit`` or an ancestor of it."""

    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", possible_ancestor, commit),
        check=False,
        capture_output=True,
        text=True,
        cwd=None if cwd is None else str(cwd),
    )
    return completed.returncode == 0


def parse_raw_diff(raw: str) -> tuple[ingress.ConnectorFileChange, ...]:
    """Parse ``git diff --raw -z -M`` into the canonical change vocabulary.

    Rename detection is on and copy detection is off, so every record maps onto
    exactly one of added/modified/removed/renamed -- the same vocabulary GitHub
    reports and the connector receipt already binds. An unrecognised record is an
    unreadable change set, not an ignorable one.
    """

    fields = [item for item in raw.split("\0")]
    changes: list[ingress.ConnectorFileChange] = []
    index = 0
    while index < len(fields):
        meta = fields[index]
        if not meta.strip():
            index += 1
            continue
        if not meta.startswith(":"):
            raise GitEvidenceUnavailable(f"unrecognised raw diff record: {meta!r}")
        parts = meta.split()
        if len(parts) != 5:
            raise GitEvidenceUnavailable(f"unrecognised raw diff record: {meta!r}")
        destination_blob = parts[3].lower()
        status = parts[4]
        code = status[0]
        if code in {"A", "M", "T"}:
            path = fields[index + 1]
            changes.append(
                ingress.ConnectorFileChange("added" if code == "A" else "modified", path, "", destination_blob)
            )
            index += 2
        elif code == "D":
            path = fields[index + 1]
            changes.append(ingress.ConnectorFileChange("removed", path, "", ""))
            index += 2
        elif code == "R":
            previous_path = fields[index + 1]
            path = fields[index + 2]
            changes.append(ingress.ConnectorFileChange("renamed", path, previous_path, destination_blob))
            index += 3
        else:
            raise GitEvidenceUnavailable(f"unsupported raw diff status {status!r}")
    normalized = ingress.normalize_changes(tuple(changes))
    if normalized is None:
        raise GitEvidenceUnavailable("the local change set is ambiguous or malformed")
    return normalized


def local_changes(base: str, head: str, *, cwd: Path | None = None) -> tuple[ingress.ConnectorFileChange, ...]:
    """The canonical change set of ``base..head`` from local git evidence."""

    # --abbrev=40 is mandatory, not cosmetic: `git diff --raw` abbreviates blob
    # SHAs by default, and an abbreviated digest cannot be compared with the full
    # blob SHA GitHub reports for the same file.
    raw = _run_git("diff", "--raw", "-z", "-M", "--abbrev=40", "--no-color", base, head, cwd=cwd)
    return parse_raw_diff(raw)


def read_review_document(path: Path | None = None) -> Any:
    target = path or REVIEW_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitEvidenceUnavailable(f"{REVIEW_RELATIVE_PATH} is unreadable ({type(exc).__name__}: {exc})") from exc


def verify_local(
    base: str,
    head: str = "HEAD",
    *,
    cwd: Path | None = None,
    issue_criteria: tuple[str, ...] | None = None,
) -> ReviewVerdict:
    """Verify the review against local git evidence, optionally against the Issue.

    ``issue_criteria`` is the governing Issue's normalized acceptance criteria,
    derived by the caller from trusted evidence exactly as hosted Candidate
    Admission derives it. When supplied, every criterion must be covered -- so
    the local push boundary enforces the same acceptance-criteria completeness
    the hosted gate enforces, instead of echoing READY-ELIGIBLE on a review that
    only looks structurally complete. ``None`` means no coverage claim is made.
    """
    families, error = load_families()
    if error:
        return ReviewVerdict("incomplete", error)
    try:
        changes = local_changes(base, head, cwd=cwd)
        document = read_review_document()
    except GitEvidenceUnavailable as exc:
        return ReviewVerdict("incomplete", f"pre-ready hostile review evidence is unavailable ({exc})")
    # Issue #467: the recorded authority head is the exact commit SHA, so the
    # evaluated head must be resolved the same way or a "HEAD" ref would never
    # bind. A full SHA is already exact and passes through. Verification is
    # strict-exact only: a document recorded for any other head -- including an
    # ancestor such as the self-inserted artifact commit's parent -- is stale and
    # must never legitimise this candidate.
    exact_head = (
        head if _GIT_SHA.fullmatch(head) else _run_git("rev-parse", "--verify", f"{head}^{{commit}}", cwd=cwd).strip()
    )
    return verify_claims(
        document,
        base_sha=base,
        changes=changes,
        families=families,
        issue_criteria=issue_criteria,
        head_sha=exact_head,
    )


JUDGEMENT_KEYS = (
    "acceptance_criteria",
    "adversarial_dimensions",
    "authority",
    "defect_families",
    "findings",
)


def record(
    *,
    issue: str,
    base: str,
    head: str,
    base_ref: str,
    judgement: dict[str, Any],
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Mint review state for the exact current content of ``base..head``.

    The reviewer supplies only the judgement -- criteria verdicts, family
    outcomes, findings, the adversarial dimensions swept, and the review
    authority record. Everything that binds the review to the candidate is
    derived here from git, so a reviewer cannot record a review of content that
    does not exist, and the recorded authority head must equal ``head`` for the
    minted review to verify.
    """

    missing = [key for key in JUDGEMENT_KEYS if key not in judgement]
    if missing:
        raise ValueError("review judgement is missing " + ", ".join(missing))
    authority = judgement["authority"]
    recorded_head = str(authority.get("head_sha", "")).strip()
    exact_head = _run_git("rev-parse", "--verify", f"{head}^{{commit}}").strip()
    if recorded_head.lower() != exact_head.lower():
        raise ValueError(
            f"review authority head_sha {recorded_head or '(empty)'} does not equal the exact "
            f"candidate head {exact_head}; a review cannot be minted for a head it did not review"
        )
    changes = local_changes(base, head, cwd=cwd)
    claims = build_claims(
        issue=issue,
        base_ref=base_ref,
        base_sha=base,
        changes=changes,
        acceptance_criteria=tuple(judgement["acceptance_criteria"]),
        defect_families=tuple(judgement["defect_families"]),
        findings=tuple(judgement["findings"]),
        adversarial_dimensions=tuple(judgement["adversarial_dimensions"]),
    )
    return document_for(claims, authority=authority)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record or verify exact-head-bound pre-ready hostile review state for the governed candidate range."
        )
    )
    parser.add_argument("--base", required=True, help="Exact base (fork point) SHA of the governed candidate range.")
    parser.add_argument("--head", default="HEAD", help="Candidate head revision (default: HEAD).")
    parser.add_argument("--base-ref", default="main", help="Trusted base branch name (default: main).")
    parser.add_argument("--record", metavar="JUDGEMENT", help="Mint review state from this judgement JSON file.")
    parser.add_argument("--issue", help="Governing Issue number; required with --record.")
    args = parser.parse_args()

    if args.record:
        if not args.issue:
            print("[Pre-Ready Hostile Review] FAIL: --record requires --issue", file=sys.stderr)
            return 2
        try:
            judgement = json.loads(Path(args.record).read_text(encoding="utf-8"))
            if not isinstance(judgement, dict):
                raise ValueError("review judgement must be a JSON object")
            document = record(
                issue=str(args.issue),
                base=args.base,
                head=args.head,
                base_ref=args.base_ref,
                judgement=judgement,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, GitEvidenceUnavailable) as exc:
            print(f"[Pre-Ready Hostile Review] FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[Pre-Ready Hostile Review] RECORDED: {REVIEW_RELATIVE_PATH} ({document['review_id'][:12]})")

    verdict = verify_local(args.base, args.head)
    if not verdict.ok:
        print(f"[Pre-Ready Hostile Review] {verdict.state.upper()}: {verdict.reason}", file=sys.stderr)
        return 1
    print(f"[Pre-Ready Hostile Review] PASS: {verdict.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
