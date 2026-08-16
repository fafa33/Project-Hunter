"""Hunter Merge Readiness: a current-state reconciler.

Architecture
------------

Readiness is a deterministic function of the *current canonical GitHub state*
of one pull request::

    trigger -> identify PR -> read current canonical state -> compute semantic
    revision -> resolve matching Governance evidence -> evaluate blockers ->
    publish exactly one current readiness result

The core invariant is::

    same current canonical state  =>  same readiness result

regardless of which event triggered reconciliation, event delivery order,
duplicate delivery, delayed delivery, cancelled predecessor runs, replayed
``workflow_run`` payloads, prior published readiness states, or scheduler
timing.

Events are hints. Events are not authority. A trigger adapter's only job is to
answer "which pull request number(s) might need reconciling?"; every decision
input is then re-read live from GitHub inside :func:`reconcile_pr`. No payload
field ever reaches :func:`decide`.

The module is split into three strictly separated layers:

1. **I/O** -- :func:`read_current_state` performs every GitHub read and returns
   an immutable :class:`CurrentState` snapshot.
2. **Decision** -- :func:`decide` is a pure function of that snapshot. It has no
   access to the event name, to timestamps, to run identifiers, or to the
   network. This is what makes convergence testable rather than merely asserted:
   the same snapshot cannot produce two different results.
3. **Reconciliation** -- :func:`reconcile_pr` composes the two and publishes or
   confirms exactly one readiness status.

Why timestamps are gone
-----------------------

Earlier generations of this controller derived freshness from a comparison
between "when did Governance Review start" and "when did the pull request last
semantically change", reconstructed from event payloads, run start times, status
publication times, and durable invalidation markers. Every one of those inputs is
a property of *delivery history*, not of current state, so readiness depended on
how events happened to arrive. Freshness is now an *identity* comparison instead:
Hunter Governance Review stamps the pull request number and a digest of exactly
the state it evaluated into its status description (see
``hunter_governance_revision``), and this controller recomputes that digest from
current state. Equal digest means the verdict describes the pull request as it is
now; anything else -- missing marker, different pull request, different revision
-- is unusable evidence and fails closed.

Feedback (unresolved review threads, CHANGES_REQUESTED reviews, unacknowledged
comments) is not part of the Governance authority boundary and never was:
Governance Review does not read it. It is therefore evaluated *live* here on
every reconciliation. That is why resolving a thread or applying an owner
acknowledgement converges on the very next reconciliation without requiring a
Governance re-run, and why no acknowledgement bookkeeping is needed at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import hunter_controller_admission as admission
from hunter_governance_revision import (
    GovernanceInputs,
    conflicting_from_rest,
    governance_revision,
    parse_marker,
)

# Configuration
context = "Hunter Merge Readiness"
governance_context = "Hunter Governance Review"
required_checks = ("Quality Gates", "dependency-review", "CodeQL")
hard_failures = {"failure", "timed_out", "action_required", "startup_failure"}

# How many times a run will re-read and re-publish after writing green before it
# gives up and withholds green instead. See reconcile_pr().
SUCCESS_CONVERGENCE_ATTEMPTS = 3

# Written over a `success` this run published when the run goes on to decide
# against green. See _retract_greens().
RETRACTED_DESCRIPTION = "Waiting: this readiness result was superseded while it was being published."

TRUSTED_BOT_LOGIN = "github-actions[bot]"
DEPENDENCY_REVIEW_MARKER = "<!-- dependency-review-pr-comment-marker -->"
DRAFT_PROMOTION_MARKER_PREFIX = "<!-- hunter-draft-promotion:"
OWNER_NONBLOCKING_STATUS_MARKER = "<!-- hunter-owner-status:nonblocking -->"
OWNER_NONBLOCKING_STATUS_RE = re.compile(
    rf"\A\s*{re.escape(OWNER_NONBLOCKING_STATUS_MARKER)}\s*\n" r"\s*Status:\s*nonblocking\s*\n\s*Detail:\s*\S.+?\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
GOVERNANCE_PREFLIGHT_PATH = Path(__file__).with_name("hunter_governance_preflight.py")
GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS = 120
GOVERNANCE_PREFLIGHT_FAILURE_DESCRIPTION = "trusted governance preflight failed; see workflow logs"
GOVERNANCE_PREFLIGHT_TIMEOUT_DESCRIPTION = "trusted governance preflight timed out; see workflow logs"
ISSUE_276_BOOTSTRAP_PR = 277
PREFLIGHT_ENFORCEMENT_ENV = "HUNTER_ENFORCE_GOVERNANCE_PREFLIGHT"

# Global configuration, populated from the environment by init_globals().
repo: str = ""
repo_owner: str = ""
token: str = ""
event_name: str = ""
run_url: str = ""


def init_globals() -> None:
    global repo, repo_owner, token, event_name, run_url
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_owner = repo.split("/", 1)[0] if "/" in repo else ""
    token = os.environ.get("GH_TOKEN", "")
    event_name = os.environ.get("EVENT_NAME", "")
    run_url = os.environ.get("RUN_URL", "")


# --- GitHub transport -------------------------------------------------------


def request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_body = response.read().decode("utf-8")
            if not raw_body:
                return None
            return json.loads(raw_body)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            print(f"HTTP Error {exc.code} for {method} {path}: {body}")
        except Exception as read_exc:
            print(f"HTTP Error {exc.code} for {method} {path} (could not read response body: {read_exc})")
        raise


def graphql_json(query: str, variables: dict[str, Any]) -> Any:
    data = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            print(f"GraphQL HTTP Error {exc.code}: {body}")
        except Exception as read_exc:
            print(f"GraphQL HTTP Error {exc.code} (could not read response body: {read_exc})")
        raise
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL query failed: {payload['errors']}")
    return payload["data"]


def paged(path: str) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        batch = request_json("GET", f"{path}{separator}per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


# --- Canonical current state ------------------------------------------------


@dataclass(frozen=True)
class RequiredCheckState:
    """The current state of one required check run on the exact head SHA."""

    name: str
    present: bool
    completed: bool
    conclusion: str | None

    def fingerprint(self) -> list[str]:
        return [self.name, str(self.present), str(self.completed), self.conclusion or ""]


@dataclass(frozen=True)
class GovernanceEvidence:
    """A Hunter Governance Review verdict proven to describe this exact state.

    An instance only ever exists when the published status carried an identity
    marker naming *this* pull request and *this* governance revision. Evidence
    that fails either proof is never represented here; it is recorded as
    :attr:`CurrentState.unusable_governance_reasons` instead, so the controller
    can explain why it is waiting without ever treating the verdict as current.
    """

    state: str
    pull_request_number: int
    revision: str


@dataclass(frozen=True)
class CurrentState:
    """An immutable snapshot of everything readiness depends on, read live.

    Deliberately absent: the triggering event name, event payload fields, run
    identifiers, wall-clock times, and any record of previously published
    readiness *history*. The single readiness field present,
    :attr:`published_readiness`, is the controller's own most recent write; it
    is excluded from :meth:`semantic_revision` so that publishing can never
    change the state that decides what to publish.
    """

    pull_request_number: int
    open: bool
    draft: bool
    head_sha: str
    base_sha: str
    base_ref: str
    title: str
    body: str
    author: str
    conflicting: bool
    changed_paths: tuple[str, ...]
    previous_paths: tuple[str, ...]
    shared_head_pull_requests: tuple[int, ...]
    required: tuple[RequiredCheckState, ...]
    governance: GovernanceEvidence | None
    unusable_governance_reasons: tuple[str, ...]
    unresolved_thread_ids: tuple[str, ...]
    changes_requested: tuple[str, ...]
    unacknowledged_comments: tuple[int, ...]
    published_readiness: tuple[str, str] | None

    @property
    def governance_inputs(self) -> GovernanceInputs:
        return GovernanceInputs(
            pull_request_number=self.pull_request_number,
            head_sha=self.head_sha,
            base_sha=self.base_sha,
            base_ref=self.base_ref,
            title=self.title,
            body=self.body,
            draft=self.draft,
            conflicting=self.conflicting,
            changed_paths=self.changed_paths,
        )

    @property
    def governance_revision(self) -> str:
        """The revision Governance evidence must name to be usable here."""
        return governance_revision(self.governance_inputs)

    @property
    def controller_upgrade_candidate(self) -> bool:
        """Whether this pull request changes the files defining the trust boundary.

        Renames are included from both sides. GitHub reports a renamed file with
        the destination in ``filename`` and the controller-owned source in
        ``previous_filename``, so a pull request that renames a controller-owned
        path *away* from itself would otherwise escape admission entirely.
        ``previous_paths`` is deliberately kept out of
        :attr:`governance_inputs`: the Governance Review engine resolves changed
        paths as ``frozenset(f.filename ...)``, so adding previous filenames
        there would fingerprint state no validator reads and no evaluator could
        reproduce.
        """
        return admission.touches_trust_boundary(self.changed_paths + self.previous_paths)

    def semantic_revision(self) -> str:
        """Full-width fingerprint of the complete readiness-relevant current state.

        Two snapshots with equal semantic revisions are the same state as far as
        readiness is concerned, so :func:`decide` must return the same result for
        both. This is the identity used to detect that state moved underneath a
        reconciliation, and the identity convergence tests assert against.

        Excluded on purpose, because none of them changes what readiness *is*:
        :attr:`published_readiness` (the controller's own writes), trusted
        repository-automation advisory comments (filtered out upstream in
        :func:`unacknowledged_top_level_comments`), every timestamp, and every
        run/status/check identifier.
        """
        parts: list[str] = [
            "hunter-merge-readiness-semantic-revision/v1",
            str(self.pull_request_number),
            str(self.open),
            str(self.draft),
            self.head_sha,
            self.base_sha,
            self.base_ref,
            self.title,
            self.body,
            self.author,
            str(self.conflicting),
            "|".join(sorted(set(self.changed_paths))),
            "|".join(sorted(set(self.previous_paths))),
            "|".join(str(number) for number in sorted(self.shared_head_pull_requests)),
            "|".join(sorted(self.unresolved_thread_ids)),
            "|".join(sorted(self.changes_requested)),
            "|".join(str(item) for item in sorted(self.unacknowledged_comments)),
        ]
        for check in self.required:
            parts.extend(check.fingerprint())
        if self.governance is None:
            parts.append("governance=none")
        else:
            parts.extend(
                [
                    "governance",
                    self.governance.state,
                    str(self.governance.pull_request_number),
                    self.governance.revision,
                ]
            )
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# --- Current-state readers --------------------------------------------------

_PULL_REQUEST_FACTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      baseRefOid
      headRefOid
    }
  }
}
"""

_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes { id isResolved }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def base_head_oids(pr_number: int) -> tuple[str, str]:
    """Current ``(base_sha, head_sha)`` for a pull request, read via GraphQL."""
    owner, name = repo.split("/", 1)
    data = graphql_json(_PULL_REQUEST_FACTS_QUERY, {"owner": owner, "name": name, "number": int(pr_number)})
    pull = ((data.get("repository") or {}).get("pullRequest")) or {}
    return (pull.get("baseRefOid") or "").strip(), (pull.get("headRefOid") or "").strip()


def unresolved_review_thread_ids(pr_number: int) -> tuple[str, ...]:
    owner, name = repo.split("/", 1)
    ids: list[str] = []
    cursor = None
    while True:
        data = graphql_json(
            _REVIEW_THREADS_QUERY,
            {"owner": owner, "name": name, "number": int(pr_number), "after": cursor},
        )
        pull = (data.get("repository") or {}).get("pullRequest") or {}
        threads = pull.get("reviewThreads") or {}
        for index, node in enumerate(threads.get("nodes") or []):
            if node.get("isResolved"):
                continue
            ids.append(str(node.get("id") or f"thread:{len(ids)}:{index}"))
        page_info = threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return tuple(ids)
        cursor = page_info.get("endCursor")


def current_changes_requested_reviewers(pr_number: int) -> tuple[str, ...]:
    reviews = paged(f"pulls/{pr_number}/reviews")
    latest_terminal: dict[str, dict[str, Any]] = {}
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "").strip()
        state = (review.get("state") or "").upper()
        if not login or state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        previous = latest_terminal.get(login)
        if previous is None or int(review.get("id", 0)) > int(previous.get("id", 0)):
            latest_terminal[login] = review
    return tuple(
        sorted(
            login
            for login, review in latest_terminal.items()
            if (review.get("state") or "").upper() == "CHANGES_REQUESTED"
        )
    )


def is_exempt_status_comment(comment: dict[str, Any]) -> bool:
    login = ((comment.get("user") or {}).get("login") or "").strip()
    if login != TRUSTED_BOT_LOGIN:
        return False
    body = comment.get("body") or ""
    if DEPENDENCY_REVIEW_MARKER in body:
        return True
    return DRAFT_PROMOTION_MARKER_PREFIX in body


def owner_acknowledged_comment(comment: dict[str, Any]) -> bool:
    comment_id = int(comment["id"])
    updated_at = (comment.get("updated_at") or comment.get("created_at") or "").strip()
    if not updated_at:
        return False
    reactions = paged(f"issues/comments/{comment_id}/reactions")
    for reaction in reactions:
        login = ((reaction.get("user") or {}).get("login") or "").strip()
        if login != repo_owner or reaction.get("content") != "+1":
            continue
        reacted_at = (reaction.get("created_at") or "").strip()
        if reacted_at and reacted_at >= updated_at:
            return True
    return False


def owner_authored_comment(comment: dict[str, Any]) -> bool:
    """Return whether the repository owner authored this top-level comment."""
    login = ((comment.get("user") or {}).get("login") or "").strip()
    return bool(repo_owner) and login == repo_owner


def owner_nonblocking_status_comment(comment: dict[str, Any]) -> bool:
    body = str(comment.get("body") or "")
    return owner_authored_comment(comment) and OWNER_NONBLOCKING_STATUS_RE.fullmatch(body) is not None


def unacknowledged_top_level_comments(pr_number: int) -> tuple[int, ...]:
    comments = paged(f"issues/{pr_number}/comments")
    return tuple(
        int(comment["id"])
        for comment in comments
        if not is_exempt_status_comment(comment)
        and (
            (owner_authored_comment(comment) and not owner_nonblocking_status_comment(comment))
            or (not owner_authored_comment(comment) and not owner_acknowledged_comment(comment))
        )
    )


def _log_governance_preflight_output(stdout: str | None, stderr: str | None) -> None:
    """Keep subprocess diagnostics in workflow logs, never in commit status text."""
    for name, value in (("stdout", stdout), ("stderr", stderr)):
        detail = (value or "").strip()
        if detail:
            print(f"Trusted governance preflight {name}:\n{detail}")


def trusted_governance_preflight_error(pr_number: int) -> str | None:
    """Run resident trusted governance preflight before canonical green publication."""
    if os.environ.get(PREFLIGHT_ENFORCEMENT_ENV) != "1":
        return None
    if not GOVERNANCE_PREFLIGHT_PATH.is_file():
        if int(pr_number) == ISSUE_276_BOOTSTRAP_PR:
            return None
        return "trusted governance preflight is not installed on the default branch"

    env = os.environ.copy()
    env["GH_REPO"] = repo
    env["GITHUB_REPOSITORY"] = repo
    if token:
        env["GH_TOKEN"] = token
    try:
        completed = subprocess.run(
            (
                sys.executable,
                str(GOVERNANCE_PREFLIGHT_PATH),
                "live-pr",
                "--repo",
                repo,
                "--pr",
                str(pr_number),
            ),
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        _log_governance_preflight_output(exc.stdout, exc.stderr)
        print(
            f"Trusted governance preflight timed out after {GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS}s "
            f"for PR #{pr_number}."
        )
        return GOVERNANCE_PREFLIGHT_TIMEOUT_DESCRIPTION
    if completed.returncode == 0:
        return None
    _log_governance_preflight_output(completed.stdout, completed.stderr)
    return GOVERNANCE_PREFLIGHT_FAILURE_DESCRIPTION


def open_pull_requests() -> list[dict[str, Any]]:
    pulls: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = request_json("GET", f"pulls?state=open&base=main&per_page=100&page={page}")
        pulls.extend(batch)
        if len(batch) < 100:
            return pulls
        page += 1


def open_pull_requests_for_head(sha: str) -> list[dict[str, Any]]:
    if not sha:
        return []
    return [
        pr
        for pr in paged(f"commits/{sha}/pulls")
        if isinstance(pr, dict)
        and (pr.get("state") or "").strip() == "open"
        and ((pr.get("head") or {}).get("sha") or "").strip() == sha
    ]


def all_check_runs(sha: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = request_json("GET", f"commits/{sha}/check-runs?per_page=100&page={page}&filter=all")
        batch = payload["check_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def all_commit_statuses(sha: str) -> list[dict[str, Any]]:
    return paged(f"commits/{sha}/statuses")


def latest_check(runs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [run for run in runs if run.get("name") == name]
    if not matches:
        return None
    return max(matches, key=lambda run: int(run.get("id", 0)))


def latest_commit_status(statuses: list[dict[str, Any]], status_context: str) -> dict[str, Any] | None:
    matches = [status for status in statuses if status.get("context") == status_context]
    if not matches:
        return None
    return max(matches, key=lambda status: int(status.get("id", 0)))


def resolve_governance_evidence(
    statuses: list[dict[str, Any]],
    pr_number: int,
    revision: str,
) -> tuple[GovernanceEvidence | None, tuple[str, ...]]:
    reasons: list[str] = []
    qualified: list[GovernanceEvidence] = []
    for status in statuses:
        if status.get("context") != governance_context:
            continue
        marker = parse_marker(status.get("description"))
        if marker is None:
            reasons.append("a Governance Review status on this head carries no revision marker")
            continue
        marked_pr, marked_revision = marker
        if marked_pr != int(pr_number):
            reasons.append(f"a Governance Review status on this head was produced for PR #{marked_pr}")
            continue
        if marked_revision != revision:
            reasons.append(f"Governance Review evidence names superseded revision {marked_revision}")
            continue
        qualified.append(
            GovernanceEvidence(
                state=(status.get("state") or "pending").strip(),
                pull_request_number=marked_pr,
                revision=marked_revision,
            )
        )

    if not qualified:
        return None, tuple(sorted(set(reasons)))
    for preferred in ("failure", "error"):
        for evidence in qualified:
            if evidence.state == preferred:
                return evidence, tuple(sorted(set(reasons)))
    for evidence in qualified:
        if evidence.state != "success":
            return evidence, tuple(sorted(set(reasons)))
    return qualified[0], tuple(sorted(set(reasons)))


def read_current_state(pr_number: int) -> CurrentState | None:
    pr = request_json("GET", f"pulls/{pr_number}")
    if not pr or pr.get("state") != "open":
        return None

    base_sha, head_sha = base_head_oids(pr_number)
    if not head_sha:
        head_sha = ((pr.get("head") or {}).get("sha") or "").strip()
    if not head_sha:
        raise RuntimeError(f"PR #{pr_number} head SHA unavailable")
    if not base_sha:
        raise RuntimeError(f"PR #{pr_number} base SHA unavailable; cannot compute a governance revision")

    shared_head_pull_requests = tuple(
        sorted(
            int(other["number"])
            for other in open_pull_requests_for_head(head_sha)
            if int(other["number"]) != int(pr_number)
        )
    )

    changed_files = [entry for entry in paged(f"pulls/{pr_number}/files") if isinstance(entry, dict)]
    changed_paths = tuple(str(entry.get("filename") or "") for entry in changed_files)
    previous_paths = tuple(str(entry["previous_filename"]) for entry in changed_files if entry.get("previous_filename"))
    statuses = all_commit_statuses(head_sha)
    runs = all_check_runs(head_sha)

    required: list[RequiredCheckState] = []
    for name in required_checks:
        run = latest_check(runs, name)
        if run is None:
            required.append(RequiredCheckState(name, present=False, completed=False, conclusion=None))
        else:
            required.append(
                RequiredCheckState(
                    name,
                    present=True,
                    completed=run.get("status") == "completed",
                    conclusion=run.get("conclusion"),
                )
            )

    partial = CurrentState(
        pull_request_number=int(pr_number),
        open=True,
        draft=bool(pr.get("draft")),
        head_sha=head_sha,
        base_sha=base_sha,
        base_ref=((pr.get("base") or {}).get("ref") or "").strip(),
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        author=((pr.get("user") or {}).get("login") or "").strip(),
        conflicting=conflicting_from_rest(pr.get("mergeable")),
        changed_paths=changed_paths,
        previous_paths=previous_paths,
        shared_head_pull_requests=shared_head_pull_requests,
        required=tuple(required),
        governance=None,
        unusable_governance_reasons=(),
        unresolved_thread_ids=unresolved_review_thread_ids(pr_number),
        changes_requested=current_changes_requested_reviewers(pr_number),
        unacknowledged_comments=unacknowledged_top_level_comments(pr_number),
        published_readiness=_published_readiness(statuses),
    )

    evidence, reasons = resolve_governance_evidence(statuses, int(pr_number), partial.governance_revision)
    return replace(partial, governance=evidence, unusable_governance_reasons=reasons)


def _published_readiness(statuses: list[dict[str, Any]]) -> tuple[str, str] | None:
    status = latest_commit_status(statuses, context)
    if status is None:
        return None
    return (status.get("state") or "").strip(), (status.get("description") or "").strip()


# --- Pure decision ----------------------------------------------------------


@dataclass(frozen=True)
class ReadinessDecision:
    state: str
    description: str


def dependency_only_pull_request(state: CurrentState) -> bool:
    if state.author not in {"dependabot[bot]", "renovate[bot]"}:
        return False
    if not state.changed_paths:
        return False
    allowed_root = {"pyproject.toml", "poetry.lock", "package-lock.json", "pnpm-lock.yaml"}
    return all(path.startswith("requirements/") or path in allowed_root for path in state.changed_paths)


def metadata_error(state: CurrentState) -> str | None:
    if dependency_only_pull_request(state):
        return None
    body = state.body
    rows = []
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
        if len(cells) >= 2 and cells[1].lower() in {"pass", "fail", "blocked", "not applicable"}:
            rows.append((cells[0], cells[1].lower()))
    if not rows:
        return "Acceptance-criteria matrix is missing or unparseable."
    unresolved = [name for name, status in rows if status in {"fail", "blocked"}]
    if unresolved:
        return "FAIL/BLOCKED acceptance criteria remain: " + ", ".join(unresolved)

    checked = []
    allowed = ("ready for review", "changes required", "blocked")
    for match in re.finditer(r"(?im)^\s*[-*+]\s*\[([ xX])\]\s*(.+)", body):
        marker, text = match.group(1), match.group(2).strip().lower()
        if marker.lower() != "x":
            continue
        normalized = text.replace("`", "").replace("*", "")
        for declaration in allowed:
            if normalized.startswith(declaration):
                checked.append(declaration)
                break
    if len(checked) != 1:
        return "Exactly one implementer readiness declaration must be checked."
    if checked[0] != "ready for review":
        return f"Implementer readiness is {checked[0].upper()}, not READY FOR REVIEW."
    return None


def feedback_error(state: CurrentState) -> str | None:
    if state.unresolved_thread_ids:
        return f"Unresolved review threads remain: {len(state.unresolved_thread_ids)}."
    if state.changes_requested:
        return "Changes requested by: " + ", ".join(state.changes_requested)
    if state.unacknowledged_comments:
        preview = ", ".join(str(item) for item in state.unacknowledged_comments[:4])
        suffix = "…" if len(state.unacknowledged_comments) > 4 else ""
        return f"Unacknowledged PR comments need owner 👍: {preview}{suffix}"
    return None


def decide(state: CurrentState) -> ReadinessDecision:
    if state.draft:
        return ReadinessDecision("pending", "Waiting for Ready for Review (PR is Draft).")

    problem = metadata_error(state)
    if problem:
        return ReadinessDecision("failure", problem)

    problem = feedback_error(state)
    if problem:
        return ReadinessDecision("failure", problem)

    missing: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    for check in state.required:
        if not check.present:
            missing.append(check.name)
        elif not check.completed:
            pending.append(check.name)
        elif check.conclusion == "success":
            continue
        elif check.conclusion in hard_failures:
            failed.append(f"{check.name}={check.conclusion}")
        else:
            pending.append(check.name)

    revision = state.governance_revision
    evidence = state.governance
    if evidence is None:
        missing.append(f"{governance_context} (revision {revision})")
    elif evidence.state == "success":
        pass
    elif evidence.state in {"failure", "error"}:
        failed.append(f"{governance_context}={evidence.state}")
    else:
        pending.append(f"{governance_context} ({evidence.state})")

    if failed:
        return ReadinessDecision("failure", "Exact-head prerequisite failed: " + ", ".join(failed))
    if missing or pending:
        return ReadinessDecision("pending", "Waiting for exact-head checks: " + ", ".join(missing + pending))

    if state.shared_head_pull_requests:
        others = ", ".join(f"#{number}" for number in state.shared_head_pull_requests)
        return ReadinessDecision(
            "pending",
            f"Head is shared with open PR {others}; readiness cannot be attributed to one PR.",
        )

    description = "Ready to merge: fresh checks passed and every PR comment is resolved/acknowledged."
    if state.controller_upgrade_candidate:
        description = "Ready to merge: controller-upgrade PR admitted by the current trusted generation. " + description
    return ReadinessDecision("success", description)


# --- Publication ------------------------------------------------------------


def publish(sha: str, state: str, description: str, published: tuple[str, str] | None) -> None:
    description = description.replace("👍", "+1")
    description = "".join(character for character in description if ord(character) <= 0xFFFF)
    description = description[:140]

    if published is not None and published[0] == state and published[1].strip() == description.strip():
        print(f"{sha[:10]} {context}: confirmed already-current {state} — {description}")
        return

    request_json(
        "POST",
        f"statuses/{sha}",
        {"state": state, "context": context, "description": description, "target_url": run_url},
    )
    print(f"{sha[:10]} {context}: {state} — {description}")


# --- Canonical reconciliation entry point -----------------------------------


def reconcile_pr(pr_number: int) -> ReadinessDecision | None:
    state = read_current_state(pr_number)
    if state is None:
        print(f"PR #{pr_number} is not open; nothing to reconcile.")
        return None

    decision = decide(state)
    if decision.state == "success":
        decision = _confirm_success(pr_number, state, decision)

    if state.unusable_governance_reasons and decision.state != "success":
        print(f"PR #{pr_number} unusable Governance evidence: " + "; ".join(state.unusable_governance_reasons))

    publish(state.head_sha, decision.state, decision.description, state.published_readiness)
    if decision.state != "success":
        return decision
    return _converge_after_publishing_success(pr_number, state, decision, {state.head_sha})


def _confirm_success(pr_number: int, state: CurrentState, decision: ReadinessDecision) -> ReadinessDecision:
    confirmation = read_current_state(pr_number)
    if confirmation is None:
        return ReadinessDecision("pending", "Waiting: pull request closed while readiness was being confirmed.")
    if confirmation.semantic_revision() != state.semantic_revision():
        return ReadinessDecision("pending", "Waiting: pull-request state changed while readiness was being confirmed.")
    if state.controller_upgrade_candidate:
        result = admission.evaluate_admission(confirmation)
        if not result.admitted:
            return ReadinessDecision("pending", f"Controller-upgrade admission pending: {result.reason}")
    preflight_error = trusted_governance_preflight_error(pr_number)
    if preflight_error:
        return ReadinessDecision("failure", f"Governance preflight blocked readiness: {preflight_error}")
    return decision


def _converge_after_publishing_success(
    pr_number: int,
    state: CurrentState,
    decision: ReadinessDecision,
    greened_heads: set[str],
) -> ReadinessDecision:
    for _ in range(SUCCESS_CONVERGENCE_ATTEMPTS):
        observed = read_current_state(pr_number)
        if observed is None:
            print(f"PR #{pr_number} closed after publishing readiness; leaving the published status as is.")
            return decision
        if observed.semantic_revision() == state.semantic_revision():
            return decision
        print(
            f"PR #{pr_number}: state changed while readiness was being published; "
            "re-deciding from the observed current state."
        )
        state = observed
        decision = decide(observed)
        if decision.state == "success":
            decision = _confirm_success(pr_number, observed, decision)
        publish(observed.head_sha, decision.state, decision.description, observed.published_readiness)
        if decision.state == "success":
            greened_heads.add(observed.head_sha)
            continue
        greened_heads.discard(observed.head_sha)
        _retract_greens(greened_heads)
        return decision

    print(f"PR #{pr_number}: state is changing faster than readiness can be confirmed; withholding green.")
    return _withhold_green(pr_number, greened_heads, decision)


def _retract_greens(greened_heads: set[str]) -> None:
    retracted: set[str] = set()
    failures: list[str] = []
    for head in sorted(greened_heads):
        try:
            publish(head, "pending", RETRACTED_DESCRIPTION, None)
            retracted.add(head)
        except Exception as exc:
            failures.append(f"{head[:10]} ({type(exc).__name__})")
    greened_heads.difference_update(retracted)
    if failures:
        print(
            "Could not retract this run's readiness success on: "
            + ", ".join(failures)
            + "; the next reconciliation of those commits will correct them."
        )


def _withhold_green(
    pr_number: int,
    greened_heads: set[str],
    decision: ReadinessDecision,
) -> ReadinessDecision:
    exhausted = ReadinessDecision(
        "pending",
        "Waiting: pull-request state is changing faster than readiness can be confirmed.",
    )
    _retract_greens(greened_heads)
    final = read_current_state(pr_number)
    if final is None:
        print(f"PR #{pr_number} closed while withholding green; this run's greens were retracted.")
        return exhausted
    publish(final.head_sha, exhausted.state, exhausted.description, final.published_readiness)
    return exhausted


# --- Trigger adapters -------------------------------------------------------


def candidate_pull_requests(name: str, event: dict[str, Any]) -> list[int]:
    if name == "workflow_run":
        workflow_run = event.get("workflow_run") or {}
        numbers = [
            int(entry["number"])
            for entry in (workflow_run.get("pull_requests") or [])
            if isinstance(entry, dict) and entry.get("number") is not None
        ]
        if numbers:
            return sorted(set(numbers))
        head_sha = (workflow_run.get("head_sha") or "").strip()
        recovered = sorted(int(pr["number"]) for pr in open_pull_requests_for_head(head_sha))
        if not recovered:
            print(
                "Governance workflow completion carried no pull request and no open pull request "
                f"matches head {head_sha[:10] or 'unknown'}; nothing to reconcile."
            )
        return recovered

    if name == "schedule":
        return sorted(int(pr["number"]) for pr in open_pull_requests())

    if name == "workflow_dispatch":
        raw = (event.get("inputs") or {}).get("pr_number")
        if not raw:
            print("workflow_dispatch requires a pr_number input; skipping.")
            raise SystemExit(1)
        return [int(raw)]

    if name == "issue_comment":
        issue = event.get("issue") or {}
        if not issue.get("pull_request"):
            print("Issue comment is not on a pull request; skipping.")
            return []
        return [int(issue["number"])]

    pull_request = event.get("pull_request") or {}
    number = pull_request.get("number") or ((event.get("issue") or {}).get("number"))
    if not number:
        print("Event has no pull request number; skipping.")
        return []
    return [int(number)]


def main() -> None:
    init_globals()

    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)

    candidates = candidate_pull_requests(event_name, event)
    print(f"Trigger '{event_name}' identified {len(candidates)} pull request(s) to reconcile: {candidates}")

    failures: list[int] = []
    for pr_number in candidates:
        try:
            reconcile_pr(pr_number)
        except Exception as exc:
            failures.append(pr_number)
            print(f"Readiness reconciliation failed for PR #{pr_number}: {type(exc).__name__}: {exc}")
            _publish_controller_failure(pr_number, exc)

    if failures:
        raise SystemExit(1)


def _publish_controller_failure(pr_number: int, exc: BaseException) -> None:
    try:
        pr = request_json("GET", f"pulls/{pr_number}")
        sha = ((pr.get("head") or {}).get("sha") or "").strip()
        if not sha:
            return
        publish(sha, "failure", f"Readiness controller error: {type(exc).__name__}.", None)
    except Exception as publish_exc:
        print(f"Could not publish terminal controller failure: {type(publish_exc).__name__}: {publish_exc}")


if __name__ == "__main__":
    main()
