"""Control-domain lifecycle recorder (ADR 0037 D1, D2, D2a, D8; state machine spec sections 3, 5 and 6).

Every control job after ``authorize`` (``bind``, ``record-validation``, ``finalize``, the scheduled
reconcile and the candidate-PR record job) runs one :func:`step`:

1. verify the anchor ruleset with authenticated reads; any mismatch is the global freeze;
2. read and verify the Issue ledger, including the provenance of every record's writing run (AT-44);
3. observe definitive GitHub facts for the active authorization; an indefinite read is a no-op;
4. :func:`hunter.automation.issue_agent_state.advance` decides at most one step;
5. the decision becomes exactly one signed record, CAS-appended, or a nonce-bound resume dispatch.

Nothing here executes the model, candidate content or a publication credential. The configuration is
repository-pinned (read at ``control_sha``, never from the environment); while it is unprovisioned every
entry point fails closed with ``MISSING_CONFIGURATION`` before any read or write.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_roles import RECEIPT_SCHEMA_VERSION, WriterIdentity
from hunter.automation.issue_agent_transport import (
    MAX_PLAINTEXT_BYTES,
    TransportBinding,
    TransportIntegrityError,
    header,
    recipient_key_id,
)

TRUST_ROOTS_PATH: Final = Path("config/issue_agent_trust_roots.json")
TRUST_ROOTS_SCHEMA_VERSION: Final = "hunter-issue-agent-trust-roots-v1"

LIFECYCLE_WORKFLOW: Final = ".github/workflows/hunter-issue-agent-trigger.yml"
RECONCILE_WORKFLOW: Final = ".github/workflows/hunter-issue-agent-reconcile.yml"
CANDIDATE_PR_WORKFLOW: Final = ".github/workflows/hunter-issue-agent-candidate-pr.yml"
PREFLIGHT_WORKFLOW_FILE: Final = "hunter-pre-pr-preflight.yml"


@dataclass(frozen=True, slots=True)
class WriterWorkflow:
    events: frozenset[str]
    #: ledger role -> the job ids of this workflow allowed to write as that role
    jobs: Mapping[str, frozenset[str]]


#: The only runs whose records are valid (ADR 0037 D2 check 5, AT-44). Anything else is ``STATE_CORRUPT``.
WRITERS: Final[Mapping[str, WriterWorkflow]] = {
    LIFECYCLE_WORKFLOW: WriterWorkflow(
        frozenset({"issues", "workflow_dispatch"}),
        {
            "authorize": frozenset({"authorize"}),
            "bind": frozenset({"bind", "resume-bind"}),
            "record-validation": frozenset({"record-validation"}),
            "finalize": frozenset({"finalize"}),
        },
    ),
    RECONCILE_WORKFLOW: WriterWorkflow(
        frozenset({"schedule", "workflow_dispatch"}), {"reconcile": frozenset({"reconcile"})}
    ),
    CANDIDATE_PR_WORKFLOW: WriterWorkflow(frozenset({"workflow_run"}), {"candidate-pr-record": frozenset({"record"})}),
}

#: The lifecycle job that owns each non-terminal stage (state machine spec section 4).
STAGE_JOBS: Final[Mapping[str, str]] = {
    state.AUTHORIZED: "execute",
    state.RESULT_BOUND: "validate",
    state.VALIDATED: "publish",
}
OUTCOME_SCHEMA_VERSION: Final = "hunter-issue-agent-stage-outcome-v1"
COMPLETION_DEADLINE: Final = timedelta(hours=24)
MAX_ARTIFACT_BYTES: Final = MAX_PLAINTEXT_BYTES * 2 + 64 * 1024
_ACTIVE_RUN_STATUSES: Final = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")


class ControlRefused(RuntimeError):
    """A control job refused before writing anything. ``code`` is from the closed vocabulary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class Frozen(ControlRefused):
    """A freeze (``STATE_CORRUPT``, ``STATE_ROLLBACK_SUSPECTED``, ``ANCHOR_INTEGRITY_FAILED``): no automated write."""

    def __init__(self, code: str, message: str) -> None:
        if code not in state.FREEZE_CODES:
            raise ValueError(f"{code} is not a freeze code")
        super().__init__(code, message)


class FactsUnavailable(RuntimeError):
    """An indefinite observation (5xx, 429, timeout, partial response). The step is a no-op."""


# --- repository-pinned configuration --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceHandlingRoot:
    """ADR 0036/0038 public operator root material (hex)."""

    verification_key: str
    verification_key_sha256: str
    genesis_rule_sha256: str


@dataclass(frozen=True, slots=True)
class Configuration:
    repository: str
    repository_id: int
    owner_login: str
    state_keys: Mapping[str, Ed25519PublicKey]
    anchor: state.AnchorPin
    handoff_recipient: X25519PublicKey
    result_recipient: X25519PublicKey
    writer: WriterIdentity
    authorization_verifying_key: str
    prompt_verifying_key: str
    source_handling: SourceHandlingRoot

    @property
    def trust(self) -> state.TrustRoots:
        return state.TrustRoots(state_keys=self.state_keys, repository_id=self.repository_id)


def _missing(message: str) -> ControlRefused:
    return ControlRefused("MISSING_CONFIGURATION", message)


def _field(document: Mapping[str, Any], name: str, kind: type) -> Any:
    value = document.get(name)
    if type(value) is not kind:
        raise _missing(f"trust roots field {name} is missing or malformed")
    return value


def _raw_key(value: object, name: str) -> bytes:
    if not isinstance(value, str) or _SHA64.fullmatch(value) is None:
        raise _missing(f"trust roots key {name} must be 32 bytes of lowercase hex")
    return bytes.fromhex(value)


def load_configuration(checkout: Path) -> Configuration:
    """Read the trust roots pinned in the ``control_sha`` checkout. Unprovisioned → ``MISSING_CONFIGURATION``."""

    path = Path(checkout) / TRUST_ROOTS_PATH
    try:
        document = json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise _missing("the repository-pinned trust roots are absent or unreadable") from None
    if not isinstance(document, dict) or document.get("schema_version") != TRUST_ROOTS_SCHEMA_VERSION:
        raise _missing("the trust roots document has an unknown schema")
    if document.get("provisioned") is not True:
        raise _missing("the GitHub-native Issue agent is not provisioned (S6 owner action)")
    expected = {
        "schema_version",
        "provisioned",
        "repository",
        "repository_id",
        "owner_login",
        "state_keys",
        "anchor",
        "handoff_recipient",
        "result_recipient",
        "writer",
        "authorization_verifying_key",
        "prompt_verifying_key",
        "source_handling",
    }
    if set(document) != expected:
        raise _missing("the trust roots document carries unknown or missing fields")
    repository = _field(document, "repository", str)
    if _REPOSITORY.fullmatch(repository) is None:
        raise _missing("trust roots repository is malformed")
    repository_id = _field(document, "repository_id", int)
    if repository_id < 1:
        raise _missing("trust roots repository_id must be positive")
    keys = _field(document, "state_keys", list)
    if not keys:
        raise _missing("trust roots must pin at least one K_STATE public key")
    state_keys: dict[str, Ed25519PublicKey] = {}
    for value in keys:
        public = Ed25519PublicKey.from_public_bytes(_raw_key(value, "state_keys"))
        state_keys[state.public_key_id(public)] = public
    anchor = _field(document, "anchor", dict)
    if set(anchor) != {"ruleset_id", "updated_at"} or type(anchor["ruleset_id"]) is not int:
        raise _missing("trust roots anchor must pin exactly a ruleset id and its updated_at")
    if not isinstance(anchor["updated_at"], str) or not anchor["updated_at"]:
        raise _missing("trust roots anchor updated_at is malformed")
    writer = _field(document, "writer", dict)
    if set(writer) != {"login", "name", "email"} or not all(isinstance(v, str) and v for v in writer.values()):
        raise _missing("trust roots writer must be exactly a login, a name and an email")
    for name in ("authorization_verifying_key", "prompt_verifying_key"):
        Ed25519PublicKey.from_public_bytes(_raw_key(document[name], name))
    operator = _field(document, "source_handling", dict)
    if set(operator) != {"verification_key", "verification_key_sha256", "genesis_rule_sha256"}:
        raise _missing("trust roots source_handling must pin exactly its key, key digest and genesis digest")
    for name, value in operator.items():
        _raw_key(value, f"source_handling.{name}")
    if hashlib.sha256(bytes.fromhex(operator["verification_key"])).hexdigest() != operator["verification_key_sha256"]:
        raise _missing("trust roots source_handling key digest does not match its key")
    return Configuration(
        repository=repository,
        repository_id=repository_id,
        owner_login=_field(document, "owner_login", str),
        state_keys=state_keys,
        anchor=state.AnchorPin(ruleset_id=anchor["ruleset_id"], updated_at=anchor["updated_at"]),
        handoff_recipient=X25519PublicKey.from_public_bytes(_raw_key(document["handoff_recipient"], "handoff")),
        result_recipient=X25519PublicKey.from_public_bytes(_raw_key(document["result_recipient"], "result")),
        writer=WriterIdentity(writer["login"], writer["name"], writer["email"]),
        authorization_verifying_key=document["authorization_verifying_key"],
        prompt_verifying_key=document["prompt_verifying_key"],
        source_handling=SourceHandlingRoot(**operator),
    )


# --- GitHub reads: definitive facts only ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Read:
    status: Literal["ok", "absent", "unknown"]
    value: Any = None


class GitHub(Protocol):
    def get(self, path: str) -> Read: ...

    def download_artifact(self, artifact_id: int) -> Read: ...

    def dispatch(self, workflow_file: str, inputs: Mapping[str, str]) -> bool: ...


def definitive(read: Read) -> Any:
    """``ok`` → the value, ``absent`` → ``None``; anything indefinite stops the step."""

    if read.status == "ok":
        return read.value
    if read.status == "absent":
        return None
    raise FactsUnavailable("an observation was indefinite")


Opener = Callable[[urllib.request.Request, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


class GitHubRest:
    """Authenticated REST reads (anonymous reads were proven CDN-stale in S0). The token never leaves headers."""

    API = "https://api.github.com"

    def __init__(
        self, repository: str, token: str | None, *, opener: Opener | None = None, timeout: float = 30.0
    ) -> None:
        """``token=None`` is an anonymous client for the role jobs whose ``GITHUB_TOKEN`` is ``{}``."""

        if _REPOSITORY.fullmatch(repository) is None or token == "":
            raise _missing("a repository and, when authenticated, a non-empty token are required")
        self.repository = repository
        self._token = token
        self._open = opener or _default_opener
        self._timeout = timeout

    def _request(self, method: str, url: str, body: bytes | None = None, *, auth: bool = True) -> tuple[int, bytes]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if auth and self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._open(request, self._timeout) as response:
                return int(response.status), response.read(MAX_ARTIFACT_BYTES + 1)
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            if error.code in (301, 302, 303, 307, 308) and location:
                return error.code, location.encode()
            return error.code, b""
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, b""

    def get(self, path: str) -> Read:
        if not path.startswith("/repos/"):
            raise ValueError("only repository-scoped reads are allowed")
        status, body = self._request("GET", self.API + path)
        if status == 404:
            return Read("absent")
        if status != 200 or len(body) > MAX_ARTIFACT_BYTES:
            return Read("unknown")
        try:
            return Read("ok", json.loads(body))
        except ValueError:
            return Read("unknown")

    def download_artifact(self, artifact_id: int) -> Read:
        status, body = self._request("GET", f"{self.API}/repos/{self.repository}/actions/artifacts/{artifact_id}/zip")
        if status == 404 or status == 410:
            return Read("absent")
        if status in (302, 307):
            location = body.decode()
            if urllib.parse.urlsplit(location).scheme != "https":
                return Read("unknown")
            status, body = self._request("GET", location, auth=False)  # signed blob URL: never send the token
        if status != 200 or len(body) > MAX_ARTIFACT_BYTES:
            return Read("unknown")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = archive.infolist()
                if len(members) != 1 or members[0].file_size > MAX_ARTIFACT_BYTES:
                    return Read("ok", None)  # definitive but not a single-file artifact: refused by the caller
                return Read("ok", archive.read(members[0]))
        except zipfile.BadZipFile:
            return Read("unknown")

    def dispatch(self, workflow_file: str, inputs: Mapping[str, str]) -> bool:
        body = json.dumps({"ref": "main", "inputs": dict(inputs)}).encode()
        url = f"{self.API}/repos/{self.repository}/actions/workflows/{workflow_file}/dispatches"
        status, _ = self._request("POST", url, body)
        return status == 204


# --- provenance and anchor ------------------------------------------------------------------------------


def run_provenance(github: GitHub, configuration: Configuration) -> state.ProvenanceCheck:
    """A record is valid only if attempt 1 of a trusted ``main`` run of an allowlisted job wrote it (AT-44).

    Run metadata is memoized within this job only, never persisted as authority. An absent run is a
    definitive negative; an indefinite read raises :class:`FactsUnavailable` (a no-op, never a freeze).
    """

    memo: dict[int, Any] = {}

    def check(recorded_by: Mapping[str, Any], _record: Mapping[str, Any]) -> bool:
        workflow = WRITERS.get(recorded_by["workflow_path"])
        if workflow is None or recorded_by["job"] not in workflow.jobs.get(recorded_by["role"], frozenset()):
            return False
        if recorded_by["run_attempt"] != 1:
            return False
        run_id = recorded_by["run_id"]
        if run_id not in memo:
            memo[run_id] = definitive(github.get(f"/repos/{configuration.repository}/actions/runs/{run_id}/attempts/1"))
        run = memo[run_id]
        if not isinstance(run, Mapping):
            return False
        return (
            run.get("id") == run_id
            and run.get("run_attempt") == 1
            and run.get("path") == recorded_by["workflow_path"]
            and run.get("event") in workflow.events
            and run.get("head_branch") == "main"
            and run.get("head_sha") == recorded_by["head_sha"]
            and (run.get("repository") or {}).get("id") == configuration.repository_id
            and (run.get("head_repository") or {}).get("id") == configuration.repository_id
        )

    return check


def require_anchor(github: GitHub, configuration: Configuration, ref: str) -> None:
    """ADR 0037 D2a: authenticated reads of the pinned ruleset and of the rules applied to ``ref``."""

    if not ref.startswith(state.LEDGER_REF_PREFIX):
        raise ValueError("the anchor covers only the state namespace")
    branch = ref.removeprefix("refs/heads/")
    repository = configuration.repository
    ruleset = definitive(github.get(f"/repos/{repository}/rulesets/{configuration.anchor.ruleset_id}"))
    rules = definitive(github.get(f"/repos/{repository}/rules/branches/{urllib.parse.quote(branch, safe='/')}"))
    try:
        state.verify_anchor_integrity(
            configuration.anchor,
            ruleset if isinstance(ruleset, Mapping) else None,
            rules if isinstance(rules, list) else [],
        )
    except state.AnchorIntegrityError as error:
        raise Frozen("ANCHOR_INTEGRITY_FAILED", str(error)) from None


def load_ledger(
    store: state.GitLedgerStore, configuration: Configuration, provenance: state.ProvenanceCheck, issue: int
) -> tuple[str | None, state.LedgerView]:
    try:
        head, entries = store.read(issue)
        view = state.verify_chain(
            [entry.record for entry in entries],
            repository_id=configuration.repository_id,
            issue_number=issue,
            trust=configuration.trust,
            provenance=provenance,
            indexes=[entry.index for entry in entries],
        )
    except state.LedgerCorruptError as error:
        raise Frozen("STATE_CORRUPT", str(error)) from None
    except state.LedgerError:
        raise FactsUnavailable("the ledger could not be read") from None
    return head, view


# --- observation ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    facts: state.Facts
    executor_job_id: int | None = None
    receipt_run_id: int | None = None
    result_ciphertext: bytes | None = None
    pull_request: Mapping[str, Any] | None = None
    preflight_run_id: int | None = None


def _attempt(view: state.AuthorizationView, stage: str) -> int:
    return (view.resume_attempts or {}).get(stage, 0) + 1


def outcome_artifact_name(authorization_id: str, stage: str, attempt: int) -> str:
    """The plain (digest-only) receipt or refusal a trusted validator or publisher step uploads."""

    if stage not in state.RESUME_STAGES or type(attempt) is not int or attempt < 1:
        raise ValueError("unknown stage or attempt")
    return f"hunter-ia-{stage}-{state.authorization_digest(authorization_id)}-{attempt}"


def _owning_run(view: state.AuthorizationView) -> int:
    pending = view.pending_resume
    if pending is not None and pending["bound_run_id"] is not None:
        return int(pending["bound_run_id"])
    return int(view.binding("authorize_run_id"))


def _job(github: GitHub, repository: str, run_id: int, name: str) -> tuple[Mapping[str, Any] | None, bool]:
    """``(job, run_completed)`` for one job of one run; absence of the job is definitive only once the run ended."""

    run = definitive(github.get(f"/repos/{repository}/actions/runs/{run_id}"))
    jobs = definitive(github.get(f"/repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"))
    if not isinstance(run, Mapping) or not isinstance(jobs, Mapping) or not isinstance(jobs.get("jobs"), list):
        raise FactsUnavailable("run or job listing missing")
    if jobs.get("total_count") != len(jobs["jobs"]):
        raise FactsUnavailable("partial job listing")
    matches = [job for job in jobs["jobs"] if isinstance(job, Mapping) and job.get("name") == name]
    if len(matches) > 1:
        raise FactsUnavailable("duplicate job names")
    return (matches[0] if matches else None), run.get("status") == "completed"


def _artifact_fact(item: Mapping[str, Any]) -> state.ArtifactFact:
    return state.ArtifactFact(
        artifact_id=int(item["id"]),
        name=str(item["name"]),
        digest=str(item.get("digest") or ""),
        size=int(item.get("size_in_bytes", 0)),
        expired=bool(item.get("expired")),
    )


def _named_artifacts(github: GitHub, repository: str, run_id: int, name: str) -> tuple[state.ArtifactFact, ...]:
    query = urllib.parse.urlencode({"name": name, "per_page": 100})
    listing = definitive(github.get(f"/repos/{repository}/actions/runs/{run_id}/artifacts?{query}"))
    if not isinstance(listing, Mapping) or not isinstance(listing.get("artifacts"), list):
        raise FactsUnavailable("artifact listing missing")
    if listing.get("total_count") != len(listing["artifacts"]):
        raise FactsUnavailable("partial artifact listing")
    return tuple(_artifact_fact(item) for item in listing["artifacts"] if item.get("name") == name)


def _single_json(github: GitHub, artifact: state.ArtifactFact) -> Mapping[str, Any] | None:
    content = definitive(github.download_artifact(artifact.artifact_id))
    if content is None:
        return None
    try:
        document = json.loads(content)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _stage_outcome(
    github: GitHub, configuration: Configuration, view: state.AuthorizationView, stage: str
) -> tuple[Mapping[str, Any] | None, int | None]:
    """The trusted stage outcome of the current attempt, from the run that owns it (never another run)."""

    run_id = _owning_run(view)
    name = outcome_artifact_name(view.authorization_id, stage, _attempt(view, stage))
    found = _named_artifacts(github, configuration.repository, run_id, name)
    if len(found) != 1 or found[0].expired:
        return None, None
    return _single_json(github, found[0]), run_id


def _receipt_matches(view: state.AuthorizationView, receipt: Mapping[str, Any]) -> bool:
    bound, result = view.evidence[state.AUTHORIZED], view.evidence[state.RESULT_BOUND]
    common = (
        receipt.get("schema_version") == RECEIPT_SCHEMA_VERSION
        and receipt.get("authorization_id") == view.authorization_id
        and receipt.get("execution_id") == bound["execution_id"]
        and receipt.get("ciphertext_sha256") == result["result_artifact"]["ciphertext_sha256"]
    )
    if not common:
        return False
    if receipt.get("verdict") == "REFUSED":
        return set(receipt) == {*_REFUSAL_FIELDS} and receipt.get("code") in state.VALIDATION_REFUSAL_CODES
    return (
        receipt.get("verdict") == "PASS"
        and set(receipt) == _RECEIPT_FIELDS
        and receipt.get("result_sha256") == result["result_plaintext_sha256"]
        and receipt.get("base_sha") == bound["base_sha"]
        and receipt.get("task_scope_sha256") == bound["task_scope_sha256"]
        and all(_SHA40.fullmatch(str(receipt.get(f))) for f in ("tree_sha", "unsigned_commit_sha"))
        and all(_SHA64.fullmatch(str(receipt.get(f))) for f in ("validation_definition", "toolchain_sha256"))
    )


_REFUSAL_FIELDS: Final = ("schema_version", "authorization_id", "execution_id", "ciphertext_sha256", "verdict", "code")
_RECEIPT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "authorization_id",
        "execution_id",
        "ciphertext_sha256",
        "result_sha256",
        "tree_sha",
        "unsigned_commit_sha",
        "base_sha",
        "task_scope_sha256",
        "validation_definition",
        "toolchain_sha256",
        "verdict",
    }
)


def _unsigned_commit_sha(payload: str) -> str:
    data = payload.encode("utf-8")
    return hashlib.sha1(b"commit %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def remote_head_conforms(
    commit: Mapping[str, Any], view: state.AuthorizationView, configuration: Configuration
) -> bool:
    """T4: exactly one verified commit over ``base_sha`` whose unsigned form is the validated commit."""

    bound, validated = view.evidence[state.AUTHORIZED], view.evidence[state.VALIDATED]
    body = commit.get("commit") if isinstance(commit.get("commit"), Mapping) else {}
    verification = body.get("verification") if isinstance(body.get("verification"), Mapping) else {}
    parents = [p.get("sha") for p in commit.get("parents") or [] if isinstance(p, Mapping)]
    payload = verification.get("payload")
    return (
        parents == [bound["base_sha"]]
        and (body.get("tree") or {}).get("sha") == validated["tree_sha"]
        and verification.get("verified") is True
        and verification.get("reason") == "valid"
        and isinstance(payload, str)
        and _unsigned_commit_sha(payload) == validated["unsigned_commit_sha"]
        and (commit.get("author") or {}).get("login") == configuration.writer.login
        and (commit.get("committer") or {}).get("login") == configuration.writer.login
    )


def _pull_requests(github: GitHub, configuration: Configuration, branch: str) -> list[Mapping[str, Any]]:
    owner = configuration.repository.split("/")[0]
    query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "per_page": 100})
    listing = definitive(github.get(f"/repos/{configuration.repository}/pulls?{query}"))
    if not isinstance(listing, list):
        raise FactsUnavailable("pull request listing missing")
    return [pr for pr in listing if isinstance(pr, Mapping) and (pr.get("head") or {}).get("ref") == branch]


def _preflight(github: GitHub, configuration: Configuration, head_sha: str) -> tuple[str | None, int | None]:
    query = urllib.parse.urlencode({"head_sha": head_sha, "event": "push", "per_page": 100})
    path = f"/repos/{configuration.repository}/actions/workflows/{PREFLIGHT_WORKFLOW_FILE}/runs?{query}"
    listing = definitive(github.get(path))
    if not isinstance(listing, Mapping) or not isinstance(listing.get("workflow_runs"), list):
        raise FactsUnavailable("preflight listing missing")
    runs = [
        run
        for run in listing["workflow_runs"]
        if isinstance(run, Mapping) and run.get("head_sha") == head_sha and run.get("head_branch") is not None
    ]
    if not runs:
        return None, None
    latest = max(runs, key=lambda run: (int(run.get("run_number", 0)), int(run.get("id", 0))))
    if latest.get("status") != "completed":
        return None, None
    return str(latest.get("conclusion")), int(latest["id"])


def _resume_run_status(github: GitHub, configuration: Configuration, view: state.AuthorizationView) -> str | None:
    pending = view.pending_resume
    assert pending is not None
    if pending["bound_run_id"] is not None:
        run = definitive(github.get(f"/repos/{configuration.repository}/actions/runs/{pending['bound_run_id']}"))
        if not isinstance(run, Mapping):
            return "concluded"  # the bound run was deleted: it can never produce the stage output
        return "active" if run.get("status") in _ACTIVE_RUN_STATUSES else "concluded"
    query = urllib.parse.urlencode({"event": "workflow_dispatch", "branch": "main", "per_page": 100})
    workflow = Path(LIFECYCLE_WORKFLOW).name
    listing = definitive(github.get(f"/repos/{configuration.repository}/actions/workflows/{workflow}/runs?{query}"))
    if not isinstance(listing, Mapping) or not isinstance(listing.get("workflow_runs"), list):
        raise FactsUnavailable("resume run listing missing")
    carrying = [run for run in listing["workflow_runs"] if pending["nonce"] in str(run.get("display_title", ""))]
    if not carrying:
        return None
    return "active" if any(run.get("status") in _ACTIVE_RUN_STATUSES for run in carrying) else "concluded"


def observe(
    github: GitHub,
    configuration: Configuration,
    view: state.AuthorizationView,
    *,
    now: str,
    executor_advisory_code: str | None = None,
) -> Observation:
    """Definitive GitHub facts for one active authorization. Any indefinite read raises ``FactsUnavailable``."""

    repository = configuration.repository
    branch = view.binding("execution_branch")
    current = view.state
    ref = definitive(github.get(f"/repos/{repository}/git/ref/heads/{branch}"))
    remote_head = None
    if isinstance(ref, Mapping):
        remote_head = (ref.get("object") or {}).get("sha")
        if not isinstance(remote_head, str) or _SHA40.fullmatch(remote_head) is None:
            raise FactsUnavailable("malformed ref")
    pulls = _pull_requests(github, configuration, branch)
    stage_active = False
    executor_job_id: int | None = None
    executor_conclusion: str | None = None
    job_name = STAGE_JOBS.get(current)
    if job_name is not None:
        job, run_completed = _job(github, repository, _owning_run(view), job_name)
        if job is None:
            stage_active = not run_completed
        else:
            stage_active = job.get("status") != "completed"
            if current == state.AUTHORIZED:
                executor_job_id = int(job["id"])
                executor_conclusion = str(job.get("conclusion"))
    fields: dict[str, Any] = {
        "stage_run_active": stage_active,
        "remote_branch_head": remote_head,
        "open_draft_pr": pulls[0] if pulls else None,
        "now": now,
    }
    observation: dict[str, Any] = {}
    if current == state.AUTHORIZED:
        name = state.result_artifact_name(view.authorization_id)
        fields["result_artifacts"] = _named_artifacts(github, repository, int(view.binding("authorize_run_id")), name)
        fields["executor_conclusion"] = executor_conclusion
        if executor_advisory_code in state.ADVISORY_CODES:
            fields["executor_advisory_code"] = executor_advisory_code
        observation["executor_job_id"] = executor_job_id
    if current in (state.RESULT_BOUND, state.VALIDATED):
        bound_id = view.evidence[state.RESULT_BOUND]["result_artifact"]["artifact_id"]
        item = definitive(github.get(f"/repos/{repository}/actions/artifacts/{bound_id}"))
        fields["result_artifacts"] = (_artifact_fact(item),) if isinstance(item, Mapping) else ()
        if view.pending_resume is not None:
            fields["resume_run_status"] = _resume_run_status(github, configuration, view)
    if current == state.RESULT_BOUND:
        receipt, run_id = _stage_outcome(github, configuration, view, "validation")
        fields["receipt"] = receipt if receipt is not None and _receipt_matches(view, receipt) else None
        observation["receipt_run_id"] = run_id if fields["receipt"] is not None else None
    if current == state.VALIDATED:
        if remote_head is not None:
            commit = definitive(github.get(f"/repos/{repository}/commits/{remote_head}"))
            fields["remote_head_conforms"] = isinstance(commit, Mapping) and remote_head_conforms(
                commit, view, configuration
            )
        outcome, _ = _stage_outcome(github, configuration, view, "publication")
        if (
            outcome is not None
            and set(outcome) == {"schema_version", "authorization_id", "verdict", "code"}
            and outcome.get("schema_version") == OUTCOME_SCHEMA_VERSION
            and outcome.get("authorization_id") == view.authorization_id
            and outcome.get("verdict") == "REFUSED"
            and outcome.get("code") in state.PUBLICATION_REFUSAL_CODES
        ):
            fields["publication_refusal_code"] = outcome["code"]
    if current == state.PUBLISHED:
        head = view.evidence[state.PUBLISHED]["head_sha"]
        conforming = [
            pr
            for pr in pulls
            if pr.get("draft") is True
            and (pr.get("base") or {}).get("ref") == "main"
            and (pr.get("head") or {}).get("sha") == head
        ]
        fields["open_draft_pr"] = conforming[0] if len(conforming) == 1 else None
        conclusion, preflight_run = _preflight(github, configuration, head)
        fields["preflight_conclusion"] = conclusion
        observation["pull_request"] = fields["open_draft_pr"]
        observation["preflight_run_id"] = preflight_run
    return Observation(state.Facts(**fields), **observation)


# --- recording ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Writer:
    """The control job writing a record (bound to its own run; checked again by every later reader)."""

    workflow_path: str
    job: str
    role: str
    run_id: int
    run_attempt: int
    head_sha: str

    def recorded_by(self) -> dict[str, Any]:
        return {
            "workflow_path": self.workflow_path,
            "job": self.job,
            "role": self.role,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "head_sha": self.head_sha,
        }


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class Ledger:
    store: state.GitLedgerStore
    configuration: Configuration
    provenance: state.ProvenanceCheck
    signing_key: Ed25519PrivateKey
    writer: Writer
    issue: int

    def append(self, authorization_id: str, *, kind: str, target: str, evidence: Mapping[str, Any], now: str) -> str:
        """Sign and CAS-append one record on top of a freshly verified chain."""

        head, view = load_ledger(self.store, self.configuration, self.provenance, self.issue)
        record = state.sign_record(
            {
                "schema_version": state.RECORD_SCHEMA_VERSION,
                "kind": kind,
                "record_seq": view.next_seq,
                "prev_record_sha256": view.head_record_digest,
                "recorded_at": now,
                "recorded_by": self.writer.recorded_by(),
                "repository_id": self.configuration.repository_id,
                "issue_number": self.issue,
                "authorization_id": authorization_id,
                "state": target,
                "evidence": dict(evidence),
            },
            self.signing_key,
        )
        state.apply_record(view, record, trust=self.configuration.trust, provenance=self.provenance)
        try:
            return self.store.append(self.issue, head, record, view.index())
        except state.LedgerConflictError:
            raise FactsUnavailable("another writer advanced the ledger; the next step re-decides") from None


def _bound_result(
    github: GitHub, configuration: Configuration, view: state.AuthorizationView, artifact: state.ArtifactFact
) -> tuple[dict[str, Any] | None, str | None]:
    """T2 evidence from the sealed header, or the terminal code when the artifact is not the expected one."""

    ciphertext = definitive(github.download_artifact(artifact.artifact_id))
    if not isinstance(ciphertext, bytes) or artifact.size > MAX_ARTIFACT_BYTES or not artifact.digest:
        return None, "TRANSPORT_INTEGRITY_FAILED"
    bound = view.evidence[state.AUTHORIZED]
    try:
        declared = header(ciphertext)
        expected = TransportBinding(
            payload_kind="result",
            repository_id=configuration.repository_id,
            issue_number=view.records[0]["issue_number"],
            authorization_id=view.authorization_id,
            base_sha=bound["base_sha"],
            task_scope_sha256=bound["task_scope_sha256"],
            execution_id=bound["execution_id"],
            handoff_sha256=bound["lineage"]["handoff_sha256"],
            plaintext_sha256=declared.plaintext_sha256,
            recipient_key_id=recipient_key_id(configuration.result_recipient),
        )
    except TransportIntegrityError:
        return None, "TRANSPORT_INTEGRITY_FAILED"
    if declared != expected:
        return None, "TRANSPORT_INTEGRITY_FAILED"
    return {
        "result_artifact": {
            "run_id": int(bound["authorize_run_id"]),
            "artifact_id": artifact.artifact_id,
            "artifact_digest": artifact.digest,
            "ciphertext_sha256": state.sha256_hex(ciphertext),
            "aad_sha256": declared.digest(),
            "recipient_key_id": declared.recipient_key_id,
        },
        "result_plaintext_sha256": declared.plaintext_sha256,
    }, None


def _evidence(
    decision: state.Decision,
    view: state.AuthorizationView,
    observation: Observation,
    *,
    github: GitHub,
    configuration: Configuration,
    now: datetime,
) -> tuple[str, dict[str, Any]] | None:
    """``(target_state, evidence)`` for a transition, or ``None`` when its evidence is not (yet) conforming."""

    facts, target = observation.facts, decision.target_state
    bound = view.evidence[state.AUTHORIZED]
    if target == state.RESULT_BOUND:
        assert decision.evidence_hint is not None and not isinstance(facts.result_artifacts, type(state.UNKNOWN))
        artifact = next(a for a in facts.result_artifacts if a.artifact_id == decision.evidence_hint["artifact_id"])
        evidence, failure = _bound_result(github, configuration, view, artifact)
        if failure is not None or evidence is None or observation.executor_job_id is None:
            return state.FAILED, {"code": failure or "TRANSPORT_INTEGRITY_FAILED", "failed_from_state": view.state}
        return target, {
            **evidence,
            "executor_job_id": observation.executor_job_id,
            "executor_conclusion": facts.executor_conclusion,
            "executor_advisory_code": facts.executor_advisory_code,
        }
    if target == state.VALIDATED:
        receipt = facts.receipt
        assert isinstance(receipt, Mapping) and observation.receipt_run_id is not None
        return target, {
            "receipt_sha256": state.sha256_hex(state.canonical_json(receipt)),
            "result_sha256": receipt["result_sha256"],
            "tree_sha": receipt["tree_sha"],
            "unsigned_commit_sha": receipt["unsigned_commit_sha"],
            "validation_definition": receipt["validation_definition"],
            "toolchain_sha256": receipt["toolchain_sha256"],
            "validator_run_id": observation.receipt_run_id,
            "validation_attempts": _attempt(view, "validation"),
        }
    if target == state.PUBLISHED:
        validated = view.evidence[state.VALIDATED]
        return target, {
            "writer_login": configuration.writer.login,
            "publication_identity": state.publication_identity(
                repository_id=configuration.repository_id,
                issue_number=view.records[0]["issue_number"],
                authorization_id=view.authorization_id,
                base_sha=bound["base_sha"],
                task_scope_sha256=bound["task_scope_sha256"],
                execution_id=bound["execution_id"],
                result_sha256=validated["result_sha256"],
                tree_sha=validated["tree_sha"],
                unsigned_commit_sha=validated["unsigned_commit_sha"],
                control_sha=bound["control_sha"],
                writer_login=configuration.writer.login,
            ),
            "head_sha": facts.remote_branch_head,
            "commit_verified": True,
            "publish_attempts": _attempt(view, "publication"),
            "deadline_completed_at": _timestamp(now + COMPLETION_DEADLINE),
        }
    if target == state.COMPLETED:
        pr = observation.pull_request
        if pr is None or observation.preflight_run_id is None:
            return None
        return target, {
            "pull_request_number": int(pr["number"]),
            "pull_request_node_id": str(pr["node_id"]),
            "pull_request_head_sha": str((pr.get("head") or {}).get("sha")),
            "draft": True,
            "preflight_run_id": observation.preflight_run_id,
            "preflight_conclusion": "success",
        }
    raise ValueError(f"no evidence builder for {target}")


def step(
    *,
    github: GitHub,
    configuration: Configuration,
    store: state.GitLedgerStore,
    signing_key: Ed25519PrivateKey,
    writer: Writer,
    issue: int,
    clock: Callable[[], datetime],
    executor_advisory_code: str | None = None,
    new_nonce: Callable[[], str] = lambda: secrets.token_hex(32),
) -> state.Decision:
    """Run ``advance`` once for the Issue's active authorization and record its decision."""

    require_anchor(github, configuration, state.ledger_ref(issue))
    provenance = run_provenance(github, configuration)
    _, ledger_view = load_ledger(store, configuration, provenance, issue)
    if ledger_view.active is None:
        return state.Decision("noop", reason="no active authorization")
    view = ledger_view.authorizations[ledger_view.active]
    moment = clock()
    now = _timestamp(moment)
    observation = observe(github, configuration, view, now=now, executor_advisory_code=executor_advisory_code)
    decision = state.advance(view, observation.facts)
    ledger = Ledger(store, configuration, provenance, signing_key, writer, issue)
    role = writer.role
    if decision.action == "freeze":
        raise Frozen(decision.code or "STATE_ROLLBACK_SUSPECTED", decision.reason)
    if decision.action == "fail":
        assert decision.code is not None
        ledger.append(
            view.authorization_id,
            kind="transition",
            target=state.FAILED,
            evidence={"code": decision.code, "failed_from_state": view.state},
            now=now,
        )
        return decision
    if decision.action == "transition":
        built = _evidence(decision, view, observation, github=github, configuration=configuration, now=moment)
        if built is None:
            return state.Decision("noop", reason="transition evidence not conforming yet")
        target, evidence = built
        if role not in state.WRITER_ROLES[target]:
            return state.Decision("noop", reason=f"{role} does not write {target}")
        ledger.append(view.authorization_id, kind="transition", target=target, evidence=evidence, now=now)
        return decision if target == decision.target_state else state.Decision("fail", code=evidence["code"])
    if decision.action in {"resume", "redispatch", "abandon_resume"} and role not in state.RESUME_ROLES:
        return state.Decision("noop", reason=f"{role} does not manage resumes")
    if decision.action == "resume":
        assert decision.stage is not None
        nonce = new_nonce()
        ledger.append(
            view.authorization_id,
            kind="resume_requested",
            target=view.state,
            evidence={
                "stage": decision.stage,
                "nonce": nonce,
                "attempt": _attempt(view, decision.stage),
                "dispatched_at": now,
            },
            now=now,
        )
        _dispatch_resume(github, issue, view.authorization_id, decision.stage, nonce)
        return decision
    if decision.action == "redispatch":
        assert view.pending_resume is not None and decision.stage is not None
        _dispatch_resume(github, issue, view.authorization_id, decision.stage, view.pending_resume["nonce"])
        return decision
    if decision.action == "abandon_resume":
        assert view.pending_resume is not None
        ledger.append(
            view.authorization_id,
            kind="resume_abandoned",
            target=view.state,
            evidence={"nonce": view.pending_resume["nonce"], "reason": "run_concluded_without_output"},
            now=now,
        )
        return decision
    return decision


def _dispatch_resume(github: GitHub, issue: int, authorization_id: str, stage: str, nonce: str) -> None:
    # A lost dispatch is recovered by the grace-period redispatch with the same nonce; never by a new nonce.
    github.dispatch(
        Path(LIFECYCLE_WORKFLOW).name,
        {"issue": str(issue), "authorization_id": authorization_id, "stage": stage, "nonce": nonce},
    )


def bind_resume(
    *,
    github: GitHub,
    configuration: Configuration,
    store: state.GitLedgerStore,
    signing_key: Ed25519PrivateKey,
    writer: Writer,
    issue: int,
    authorization_id: str,
    stage: str,
    nonce: str,
    clock: Callable[[], datetime],
) -> str:
    """The resume run claims its nonce by CAS and returns the bound ``control_sha`` it must check out.

    A duplicate dispatch, a stale nonce or a run for another authorization refuses without writing.
    """

    require_anchor(github, configuration, state.ledger_ref(issue))
    provenance = run_provenance(github, configuration)
    _, ledger_view = load_ledger(store, configuration, provenance, issue)
    view = ledger_view.authorizations.get(authorization_id)
    pending = None if view is None else view.pending_resume
    if (
        view is None
        or ledger_view.active != authorization_id
        or pending is None
        or pending["nonce"] != nonce
        or pending["stage"] != stage
        or pending["bound_run_id"] is not None
    ):
        raise ControlRefused("RERUN_REFUSED", "no unbound pending resume carries this nonce")
    control_sha = str(view.binding("control_sha"))
    compare = definitive(github.get(f"/repos/{configuration.repository}/compare/{control_sha}...main"))
    if not isinstance(compare, Mapping) or compare.get("status") not in ("ahead", "identical"):
        Ledger(store, configuration, provenance, signing_key, writer, issue).append(
            authorization_id,
            kind="transition",
            target=state.FAILED,
            evidence={"code": "CONTROL_SHA_NOT_ON_MAIN", "failed_from_state": view.state},
            now=_timestamp(clock()),
        )
        raise ControlRefused("CONTROL_SHA_NOT_ON_MAIN", "the bound control commit is no longer on main")
    Ledger(store, configuration, provenance, signing_key, writer, issue).append(
        authorization_id,
        kind="resume_bound",
        target=view.state,
        evidence={"nonce": nonce, "run_id": writer.run_id},
        now=_timestamp(clock()),
    )
    return control_sha


__all__ = [
    "COMPLETION_DEADLINE",
    "Configuration",
    "ControlRefused",
    "SourceHandlingRoot",
    "FactsUnavailable",
    "Frozen",
    "GitHub",
    "GitHubRest",
    "Observation",
    "Read",
    "Writer",
    "WRITERS",
    "bind_resume",
    "load_configuration",
    "observe",
    "outcome_artifact_name",
    "remote_head_conforms",
    "require_anchor",
    "run_provenance",
    "step",
]
