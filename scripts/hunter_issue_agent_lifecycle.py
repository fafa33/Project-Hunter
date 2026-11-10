"""GitHub-native Issue-agent lifecycle entry point: one subcommand per workflow job (ADR 0037 D1, S5).

Every subcommand loads the repository-pinned trust roots from the ``control_sha`` checkout **first**. While
they are unprovisioned (the S5 state) every job fails closed with ``MISSING_CONFIGURATION`` before it reads a
secret, the ledger or the network, so a label can never execute anything until the owner provisions S6.

Secrets reach a job only through step-scoped environment variables of its own trust domain and are never
accepted from argv. Public trust material (keys, anchor pin, writer) is never taken from the environment:
it is read from the pinned file and exported to the variables the existing constructors read.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hunter.automation import issue_agent_authorize as authorize
from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state

STATE_SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_STATE_SIGNING_KEY"
HANDOFF_KEY_ENV = "HUNTER_ISSUE_AGENT_HANDOFF_KEY"
#: ADR 0041 F3: X_FB, held only by the fallback executor environment on the attested Mac.
FALLBACK_HANDOFF_KEY_ENV = "HUNTER_ISSUE_AGENT_FALLBACK_HANDOFF_KEY"
RESULT_KEY_ENV = "HUNTER_ISSUE_AGENT_RESULT_KEY"
MODEL_KEY_ENV = "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY"
PUSH_TOKEN_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN"
#: Read-only credential (Administration: read + Metadata: read, this repository only) used solely to observe the
#: live ruleset contract. It has no push, PR or other authority and is never a fallback for, or from, any other token.
HGCC_TOKEN_ENV = "HUNTER_HGCC_READ_TOKEN"
#: The validate job's own job-scoped ``GITHUB_TOKEN`` (``actions: read`` only), handed to its trusted intake steps
#: solely for the ledger's run-provenance reads (AT-44). Anonymous reads share a 60/hour per-runner-IP budget that a
#: long ledger exhausts on its own (Issue #577). It is removed from the environment when read, so no subprocess,
#: and never the credential-free isolation uid, inherits it. Absent, the reads stay anonymous exactly as before.
PROVENANCE_TOKEN_ENV = "HUNTER_ISSUE_AGENT_PROVENANCE_TOKEN"
PUBLISHER_SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY"
#: K_AUTH: the issuer key that mints both an Issue authorization and a remediation authorization (ADR 0039 L4).
AUTHORIZATION_SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY"
ISOLATION_USER = "hunter-untrusted"
#: The trusted gate chain the validator runs at ``control_sha`` and the toolchain it runs on; their digests
#: are recorded so a receipt can never be reused after either changes (VALIDATION_STAGE_CONTRACT).
VALIDATION_DEFINITION_FILES = (".githooks/pre-push", "scripts/hunter_pre_push.py")
TOOLCHAIN_FILES = ("requirements/ci-constraints.txt", "pyproject.toml")
EXIT_REFUSED = 2
EXIT_NOOP = 0


class LifecycleRefused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class PublicationContractRefused(LifecycleRefused):
    """The observed HGCC-1 contract definitively fails: terminal and uploaded as the publisher's REFUSED outcome."""

    def __init__(self, message: str) -> None:
        super().__init__("PUBLICATION_CONTRACT_REFUSED", message)


def _secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LifecycleRefused("MISSING_CONFIGURATION", f"{name} is not provisioned for this job")
    return value


def _ed25519(name: str) -> Ed25519PrivateKey:
    try:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_secret(name)))
    except ValueError:
        raise LifecycleRefused("MISSING_CONFIGURATION", f"{name} is not a 32-byte hex key") from None


def _x25519(name: str) -> X25519PrivateKey:
    try:
        return X25519PrivateKey.from_private_bytes(bytes.fromhex(_secret(name)))
    except ValueError:
        raise LifecycleRefused("MISSING_CONFIGURATION", f"{name} is not a 32-byte hex key") from None


def _run_context() -> tuple[int, int, str]:
    try:
        run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    except (KeyError, ValueError):
        raise LifecycleRefused("MISSING_CONFIGURATION", "not running inside a GitHub Actions run") from None
    if attempt != 1:
        raise LifecycleRefused("RERUN_REFUSED", "lifecycle jobs never run on a workflow re-run")
    return run_id, attempt, os.environ.get("GITHUB_SHA", "")


def _output(**values: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _remote(configuration: control.Configuration) -> str:
    return f"https://github.com/{configuration.repository}.git"


def _store(configuration: control.Configuration, *, authenticated: bool) -> state.GitLedgerStore:
    header = None
    if authenticated:
        token = _secret("GITHUB_TOKEN")
        header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return state.GitLedgerStore(_remote(configuration), auth_header=header)


def _github(configuration: control.Configuration, *, authenticated: bool = True) -> control.GitHubRest:
    return control.GitHubRest(configuration.repository, _secret("GITHUB_TOKEN") if authenticated else None)


def _writer(role: str, job: str, workflow: str = control.LIFECYCLE_WORKFLOW) -> control.Writer:
    run_id, attempt, head_sha = _run_context()
    return control.Writer(workflow, job, role, run_id, attempt, head_sha)


def _provenance_github(configuration: control.Configuration) -> control.GitHubRest:
    """The read client for one role step's ledger access: the job's read-only token when provisioned, else anonymous.

    The token is popped from the environment here, before any subprocess starts, so it lives only in this trusted
    process's request headers.
    """

    token = os.environ.pop(PROVENANCE_TOKEN_ENV, "").strip()
    return control.GitHubRest(configuration.repository, token or None)


def _ledger_access(
    configuration: control.Configuration, github: control.GitHubRest | None = None
) -> roles.LedgerAccess:
    """One verified-ledger reader. Callers that verify the chain more than once in a step share one instance, so each
    distinct writer run is observed once per step rather than once per verification (Issue #577)."""

    github = github or _provenance_github(configuration)
    return roles.LedgerAccess(
        _store(configuration, authenticated=False), configuration.trust, control.run_provenance(github, configuration)
    )


def _finding_provenance(
    configuration: control.Configuration, github: control.GitHubRest, finding_id: str
) -> Mapping[str, Any] | None:
    """The anchored, verified provenance of one ingested finding; ``None`` when the ledger does not hold it.

    The provenance is public reviewer-thread metadata the knowledge ledger already stores, so the promotion
    record carries provenance, not evidence bytes. A finding that is not in the verified ledger yields no
    promotion at all: the fix still publishes and the finding stays unresolved.
    """

    findings = _knowledge_view(configuration, github).findings
    item = findings.get(finding_id)
    if item is None:
        return None
    provenance = item.ingested["provenance"]
    return {
        "finding_id": finding_id,
        "path": item.path,
        "pull_request_number": int(provenance["pull_request_number"]),
        "reviewed_head_sha": str(provenance["reviewed_head_sha"]),
        "reviewer": str(provenance["reviewer"]),
        "comment_id": int(provenance["comment_id"]),
    }


def _promotion_port(configuration: control.Configuration, github: control.GitHubRest) -> roles.RemediationPort:
    """ADR 0039 L6 (RD-3): the one trusted promotion service, used by the validator *and* the publisher.

    Its only inputs are the validated result's proposal, the verified anchored finding provenance and the
    canonical registry at the reviewed head, so both roles derive byte-identical promotion bytes or neither
    does. A finding that is not in the verified ledger yields no promotion: the fix still publishes and the
    finding stays known and unresolved.
    """

    def promote(
        *, repo: Path, group: Mapping[str, Any], proposal: Mapping[str, Any], finding_id: str, **_rest: Any
    ) -> Mapping[str, bytes]:
        finding = _finding_provenance(configuration, github, finding_id)
        if finding is None:
            raise remediation.PromotionRefused("the proven finding is not in the verified knowledge ledger")
        return remediation.promote(repo=repo, group=group, proposal=proposal, finding=finding)

    return roles.RemediationPort(promote=promote)


# --- control jobs ------------------------------------------------------------------------------------------


def terminal_outcome_annotation(issue: int, decision: state.Decision) -> str | None:
    """A run-page error annotation for a signed terminal ``FAILED`` (Issue #577), never a job failure.

    The step job's success means only that the signed transition was written; the ledger, not the job
    conclusion, is the canonical outcome. Failing the job would change every downstream ``if:``, so the
    terminal outcome is surfaced as a GitHub ``::error`` annotation instead. Only the closed code is emitted.
    """

    if decision.action != "fail":  # the only decision that writes a terminal FAILED record (freeze raises)
        return None
    code = decision.code if decision.code in state.FAILURE_CODES else "UNKNOWN"  # the FAILED record's closed vocabulary
    return (
        f"::error title=Issue agent FAILED::issue {issue}: FAILED {code}. The signed ledger is the outcome; "
        "this run's job conclusions report orchestration only, not task completion."
    )


def cmd_step(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    workflow = {
        "reconcile": control.RECONCILE_WORKFLOW,
        "candidate-pr-record": control.CANDIDATE_PR_WORKFLOW,
    }.get(arguments.role, control.LIFECYCLE_WORKFLOW)
    job = {"candidate-pr-record": "record"}.get(arguments.role, arguments.role)
    if getattr(arguments, "job", None) == "bind-fallback":
        if arguments.role != "bind":
            raise LifecycleRefused("MISSING_CONFIGURATION", "bind-fallback writes only as the bind role")
        job = "bind-fallback"
    writer = _writer(arguments.role, job, workflow)
    github = _github(configuration)
    issues = [arguments.issue] if arguments.issue else _active_issues(configuration)
    for issue in issues:
        try:
            decision = control.step(
                github=github,
                configuration=configuration,
                store=_store(configuration, authenticated=True),
                signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
                writer=writer,
                issue=issue,
                clock=_now,
                executor_advisory_code=arguments.advisory or None,
                executor_stderr_category=getattr(arguments, "stderr_category", "") or None,
                executor_probe_category=getattr(arguments, "probe_category", "") or None,
                fallback_attestation_sha256=getattr(arguments, "attestation_sha256", "") or None,
                executor_canary_refusal=_canary_refusal(arguments),
                fallback_model=_fallback_model(arguments),
            )
            print(f"issue {issue}: {decision.action} {decision.target_state or decision.code or ''}".rstrip())
            annotation = terminal_outcome_annotation(issue, decision)
            if annotation is not None:
                print(annotation, flush=True)
            if arguments.role == "bind":
                # Job success does not imply a bound result. Only the signed
                # transition permits the downstream validator to start, and only
                # the signed FALLBACK_AUTHORIZED record releases the fallback job.
                _output(
                    result_bound="true" if decision.target_state == state.RESULT_BOUND else "false",
                    fallback_authorized="true" if decision.target_state == state.FALLBACK_AUTHORIZED else "false",
                )
            if job == "record-validation":
                # The job can succeed while the signed transition is terminal FAILED.
                # Only an actual VALIDATED transition may release same-run publication.
                _output(publication_eligible="true" if decision.target_state == state.VALIDATED else "false")
                if decision.target_state == state.VALIDATED:
                    _record_proven_classification(configuration, github, writer, issue)
        except control.FactsUnavailable as error:
            print(f"issue {issue}: no-op ({error})")
    return 0


def _canary_refusal(arguments: argparse.Namespace) -> dict[str, Any] | None:
    """ADR 0041 F8: the primary job's gateway refusal outputs, in the ledger's closed form (untrusted until bound)."""
    primary, digest = getattr(arguments, "canary_primary_refusals", ""), getattr(arguments, "canary_refusal_digest", "")
    if not primary or not digest or not str(primary).isdigit():
        return None
    return {
        "provider_host": state.CANARY_PROVIDER_HOST,
        "primary_refusals": int(primary),
        "probe_refusals": 1,
        "refusal_digest": str(digest),
    }


def _fallback_model(arguments: argparse.Namespace) -> dict[str, Any] | None:
    """ADR 0041 F5a: the fallback job's model report (a JSON object), validated in the ledger's closed form."""
    raw = getattr(arguments, "fallback_model", "") or ""
    if not raw or len(raw) > 1024:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if state.fallback_model_conforms(value) else None


def _record_proven_classification(
    configuration: control.Configuration, github: control.GitHubRest, writer: control.Writer, issue: int
) -> None:
    """ADR 0039 L3.2/L2: make a proven mapping permanent knowledge, from the ledger's own proof group.

    Only the classification is written here. The exact-head ``finding_proven`` proof and the thread resolution
    belong to the reconcile job that can observe the hosted preflight and the re-review at that head, so a
    classification can never imply that a thread was resolved.
    """

    store = _store(configuration, authenticated=True)
    provenance = control.run_provenance(github, configuration)
    _head, view = control.load_ledger(store, configuration, provenance, issue)
    authorization = view.authorizations.get(view.active or "")
    if authorization is None or state.VALIDATED not in authorization.evidence:
        return
    known = _knowledge_view(configuration, github)
    writes = remediation.proof_writes(
        known, authorization_id=authorization.authorization_id, validation=authorization.evidence[state.VALIDATED]
    )
    if not writes:
        return
    _, _, written = knowledge.append(
        store,
        writes,
        trust=configuration.trust,
        provenance=provenance,
        signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
        recorded_by=writer.recorded_by(),
        recorded_at=_timestamp(),
    )
    print(f"issue {issue}: {written} proven-classification knowledge record(s)")


def _active_issues(configuration: control.Configuration) -> list[int]:
    """Every Issue ledger branch (the reconcile set). Only the verified index decides whether one is active."""

    completed = subprocess.run(
        ["git", "ls-remote", _remote(configuration), f"{state.LEDGER_REF_PREFIX}issue-*"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0"},
    )
    issues = []
    for line in completed.stdout.splitlines():
        ref = line.split("\t")[-1]
        suffix = ref.removeprefix(f"{state.LEDGER_REF_PREFIX}issue-")
        if suffix.isdigit():
            issues.append(int(suffix))
    return sorted(issues)


def _registry_applicability(checkout: Path) -> Callable[[str, str], bool | None]:
    """``(family_id, path)`` against the pinned registry: applicable, inapplicable, or no such family."""

    from hunter.evidence_intelligence.engineering_context_authority import _path_intersects

    registry = json.loads((checkout / "docs" / "DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    families = {
        str(family.get("id")): [str(p) for p in (family.get("applicability") or {}).get("changed_paths") or []]
        for family in registry.get("families", [])
        if isinstance(family, dict)
    }

    def applicability(family_id: str, path: str) -> bool | None:
        if family_id not in families:
            return None
        return any(_path_intersects(path, entry) for entry in families[family_id])

    return applicability


def _knowledge_view(configuration: control.Configuration, github: control.GitHubRest) -> Any:
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    try:
        _, view = knowledge.read(
            _store(configuration, authenticated=False),
            trust=configuration.trust,
            provenance=control.run_provenance(github, configuration),
        )
    except state.LedgerCorruptError as error:
        raise control.Frozen("STATE_CORRUPT", str(error)) from None
    return view


def cmd_knowledge_ingest(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0039 L1/L3.1: ingest authenticated inline findings independently of reviewer authority."""

    import hunter_collect_learning_observations as collector
    import hunter_collect_sonar_observations as sonar_collector

    writer = _writer("knowledge-ingest", "knowledge-ingest", control.KNOWLEDGE_WORKFLOW)
    github = _github(configuration)
    token = _secret("GITHUB_TOKEN")
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    if arguments.pr:
        numbers = [arguments.pr]
    else:
        listing = control.definitive(github.get(f"/repos/{configuration.repository}/pulls?state=open&per_page=100"))
        if not isinstance(listing, list):
            raise control.FactsUnavailable("open pull requests are not observable")
        numbers = sorted(int(pr["number"]) for pr in listing)
    trusted = collector._trusted_reviewer_logins()
    applicability = _registry_applicability(Path(arguments.checkout))
    provenance = control.run_provenance(github, configuration)
    store = _store(configuration, authenticated=True)
    for number in numbers:
        pull = control.definitive(github.get(f"/repos/{configuration.repository}/pulls/{number}"))
        if not isinstance(pull, Mapping):
            raise control.FactsUnavailable("pull request metadata is not observable")
        if pull.get("state") != "open" and not (arguments.pr and pull.get("merged_at")):
            continue
        head, base = str(pull["head"]["sha"]), str(pull["base"]["sha"])
        observations = collector.collect(configuration.repository, token, number, head, base)
        observations.extend(sonar_collector.collect("fafa33_Project-Hunter", number, head, base))
        try:
            _, view = knowledge.read(store, trust=configuration.trust, provenance=provenance)
            writes, refusals = knowledge.ingestion_writes(
                view,
                observations,
                repository_id=configuration.repository_id,
                trusted_reviewers=trusted,
                applicability=applicability,
            )
            _, _, written = knowledge.append(
                store,
                writes,
                trust=configuration.trust,
                provenance=provenance,
                signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
                recorded_by=writer.recorded_by(),
                recorded_at=_timestamp(),
            )
        except state.LedgerConflictError:
            raise control.FactsUnavailable("the knowledge ledger moved; the next run re-ingests") from None
        except state.LedgerCorruptError as error:
            raise control.Frozen("STATE_CORRUPT", str(error)) from None
        print(f"PR #{number} at {head[:12]}: {written} knowledge record(s), {len(refusals)} refused observation(s)")
        for refusal in refusals:
            print(f"  refused {refusal}")
    return 0


def cmd_historical_coverage(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """Read live GitHub review threads and the anchored signed ledger; never synthesize proof."""

    import hunter_collect_learning_observations as collector

    from hunter.automation.historical_finding_coverage import coverage

    github = _github(configuration)
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    view = _knowledge_view(configuration, github)
    numbers = sorted(set(arguments.pr))
    if not numbers or len(numbers) > 20 or any(number <= 0 for number in numbers):
        raise LifecycleRefused("INVALID_SCOPE", "historical coverage requires 1..20 positive PR numbers")
    threads: dict[tuple[int, int], dict[str, Any]] = {}
    for number in numbers:
        pull = control.definitive(github.get(f"/repos/{configuration.repository}/pulls/{number}"))
        if not isinstance(pull, Mapping) or pull.get("number") != number:
            raise control.FactsUnavailable(f"PR #{number} metadata unavailable")
        observed = collector._review_threads(configuration.repository, _secret("GITHUB_TOKEN"), number)
        for comment_id, resolved in observed.items():
            threads[(number, comment_id)] = {
                "pull_request_number": number,
                "comment_id": comment_id,
                "resolved": resolved,
            }
    report = coverage(view, pull_requests=numbers, github_threads=threads)
    report["source"] = "live-github-review-threads-and-anchored-signed-knowledge"
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


def _historical_ingestion_writes(
    *,
    view: knowledge.KnowledgeView,
    observations: Sequence[Mapping[str, Any]],
    original_comments: Sequence[Mapping[str, Any]],
    repository_id: int,
    number: int,
    expected_findings: Mapping[int, Mapping[str, Any]],
) -> list[knowledge.Write]:
    """Bind missing signed knowledge identities to authenticated original GitHub review SHAs."""
    comments = {item.get("id"): item for item in original_comments if isinstance(item, Mapping)}
    if len(comments) != len(original_comments) or set(comments) != set(expected_findings):
        raise LifecycleRefused("COVERAGE_GAP", "historical REST comment inventory differs from approved set")
    observed = {item.get("event_id"): item for item in observations}
    writes: list[knowledge.Write] = []
    for comment_id, mapping in sorted(expected_findings.items()):
        source = comments[comment_id]
        original = source.get("original_commit_id")
        if original != mapping["reviewed_head"] or not isinstance(source.get("path"), str):
            raise LifecycleRefused("STALE_HISTORICAL_SOURCE", "original review SHA or path cannot be authenticated")
        observation = observed.get(f"review-comment-{comment_id}")
        if observation is None or observation.get("source_pr") != number or observation.get("path") != source["path"]:
            raise LifecycleRefused("COVERAGE_GAP", "authenticated historical observation is missing")
        authenticated = dict(observation)
        authenticated["reviewed_head_sha"] = original
        authenticated["source_event_head_sha"] = source.get("commit_id")
        try:
            evidence = knowledge.finding_from_observation(
                authenticated, repository_id=repository_id, trusted_reviewers=frozenset()
            )
        except knowledge.FindingRefused as error:
            raise LifecycleRefused("COVERAGE_GAP", f"historical finding refused: {error}") from None
        identity = knowledge.finding_id(repository_id, number, comment_id)
        if evidence["finding_id"] != identity:
            raise LifecycleRefused("COVERAGE_GAP", "historical finding identity differs from signed ledger key")
        existing = view.findings.get(identity)
        if existing is None:
            writes.append(knowledge.Write("finding_ingested", evidence))
        elif (
            existing.ingested["provenance"]["pull_request_number"] != number
            or existing.ingested["provenance"]["comment_id"] != comment_id
            or existing.ingested["provenance"]["path"] != source["path"]
        ):
            raise LifecycleRefused("COVERAGE_GAP", "existing signed finding provenance conflicts with GitHub")
    return writes


def _validate_historical_write_batch(
    writes: Sequence[knowledge.Write], expected_findings: Mapping[int, Mapping[str, Any]]
) -> None:
    """Account for both new ingestions and all historical assessments before CAS."""
    # Missing signed identities add finding_ingested records before assessments.
    # Validate each record class independently; the total is not the assessment count.
    assessments = [write for write in writes if write.kind == "historical_evidence_assessed"]
    ingestions = [write for write in writes if write.kind == "finding_ingested"]
    if (
        len(assessments) != len(expected_findings)
        or len(ingestions) > len(expected_findings)
        or len(assessments) + len(ingestions) != len(writes)
        or {item.evidence["comment_id"] for item in assessments} != set(expected_findings)
        or len({item.evidence["finding_id"] for item in ingestions}) != len(ingestions)
        or not {item.evidence["finding_id"] for item in ingestions}.issubset(
            {item.evidence["finding_id"] for item in assessments}
        )
    ):
        raise LifecycleRefused("COVERAGE_GAP", "historical evidence count or identity is incomplete")


def cmd_historical_assess(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """Write only retrospectively authenticated evidence; never mint exact-head finding proof."""

    import hunter_collect_learning_observations as collector

    from hunter.automation.historical_finding_coverage import coverage

    number = arguments.pr
    if number not in {579, 581, 582}:
        raise LifecycleRefused("INVALID_SCOPE", "only the approved historical PR inventory is eligible")
    writer = _writer("knowledge-ingest", "knowledge-ingest", control.KNOWLEDGE_WORKFLOW)
    github = _github(configuration)
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    view = _knowledge_view(configuration, github)
    pull = control.definitive(github.get(f"/repos/{configuration.repository}/pulls/{number}"))
    if not isinstance(pull, Mapping) or pull.get("number") != number or not pull.get("merged_at"):
        raise control.FactsUnavailable("historical PR is not authentically merged")
    evidence_path = Path(arguments.checkout) / "docs" / "evidence" / "historical-findings-579-581-582.md"
    if not evidence_path.is_file():
        raise LifecycleRefused("MISSING_EVIDENCE", "canonical historical finding inventory is absent")
    # Source commit/test inventory is a finite, reviewed contract. No caller-supplied
    # commit, digest, disposition or tests can cross this privileged writer boundary.
    from hunter.automation.historical_finding_coverage import HISTORICAL_EVIDENCE

    threads = collector._review_threads(configuration.repository, _secret("GITHUB_TOKEN"), number)
    expected = HISTORICAL_EVIDENCE[number]
    if set(threads) != set(expected["findings"]):
        raise LifecycleRefused("COVERAGE_GAP", "historical review-thread inventory differs from the approved set")
    finalized = str(pull["head"]["sha"])
    merged = str(pull["merge_commit_sha"])
    expected_final, expected_merge = expected["final_head"], expected["merge_commit"]
    if finalized != expected_final or merged != expected_merge:
        raise LifecycleRefused("STALE_HISTORICAL_SOURCE", "historical head or merge identity changed")
    runs = control.definitive(
        github.get(f"/repos/{configuration.repository}/commits/{finalized}/check-runs?per_page=100")
    )
    if not isinstance(runs, Mapping) or not isinstance(runs.get("check_runs"), list):
        raise control.FactsUnavailable("historical hosted check runs unavailable")
    checks = [
        item
        for item in runs["check_runs"]
        if isinstance(item, Mapping)
        and item.get("name") == "Hunter Pre-PR Preflight"
        and item.get("conclusion") == "success"
        and item.get("head_sha") == finalized
        and isinstance(item.get("id"), int)
    ]
    if not checks:
        raise LifecycleRefused("HISTORICAL_PROOF_GAP", "successful exact-head preflight metadata is absent")
    if len(checks) != 1 or checks[0]["id"] != expected["preflight_job_id"]:
        raise LifecycleRefused(
            "HISTORICAL_PROOF_GAP", "historical preflight job identity does not match approved evidence"
        )
    run_id = checks[0]["id"]
    # Authenticate the original review SHA from GitHub, not the final PR HEAD.
    # Existing signed identities remain immutable; only missing ones are ingested.
    rest_comments = control.definitive(
        github.get(f"/repos/{configuration.repository}/pulls/{number}/comments?per_page=100")
    )
    if not isinstance(rest_comments, list) or any(not isinstance(item, Mapping) for item in rest_comments):
        raise control.FactsUnavailable("historical REST review comments are unavailable")
    observations = collector.collect(
        configuration.repository, _secret("GITHUB_TOKEN"), number, finalized, str(pull["base"]["sha"])
    )
    writes = _historical_ingestion_writes(
        view=view,
        observations=observations,
        original_comments=rest_comments,
        repository_id=configuration.repository_id,
        number=number,
        expected_findings=expected["findings"],
    )
    for comment_id, mapping in sorted(expected["findings"].items()):
        identity = knowledge.finding_id(configuration.repository_id, number, comment_id)
        if not threads[comment_id]:
            raise LifecycleRefused("HISTORICAL_THREAD_OPEN", "historical review thread was not resolved")
        commit = control.definitive(github.get(f"/repos/{configuration.repository}/commits/{mapping['commit']}"))
        if not isinstance(commit, Mapping) or commit.get("sha") != mapping["commit"]:
            raise control.FactsUnavailable("historical remediation commit cannot be authenticated")
        files = commit.get("files")
        if not isinstance(files, list) or not any(
            isinstance(file, Mapping) and file.get("filename") in mapping["paths"] for file in files
        ):
            raise LifecycleRefused("HISTORICAL_DIFF_GAP", "historical commit does not touch the approved fix path")
        # The original reviewed SHA is GitHub-authenticated above. Legacy signed
        # ingestions may have captured the then-current PR HEAD instead; bind
        # both without rewriting either immutable historical identity.
        signed_finding = view.findings.get(identity)
        signed_head = (
            signed_finding.ingested["provenance"]["reviewed_head_sha"]
            if signed_finding is not None
            else mapping["reviewed_head"]
        )
        writes.append(
            knowledge.Write(
                "historical_evidence_assessed",
                {
                    "finding_id": identity,
                    "pull_request_number": number,
                    "comment_id": comment_id,
                    "reviewed_head_sha": mapping["reviewed_head"],
                    **({"ingested_head_sha": signed_head} if signed_head != mapping["reviewed_head"] else {}),
                    "remediation_commit_sha": mapping["commit"],
                    "final_head_sha": finalized,
                    "merge_commit_sha": merged,
                    "hosted_preflight_run_id": run_id,
                    "regression_tests": list(mapping["tests"]),
                    "disposition": mapping["disposition"],
                    "source": "github-authenticated-historical-review",
                },
            )
        )
    _validate_historical_write_batch(writes, expected["findings"])
    # The single existing CAS, K_STATE signature and trusted-run provenance remain authoritative.
    _, resulting, written = knowledge.append(
        _store(configuration, authenticated=True),
        writes,
        trust=configuration.trust,
        provenance=control.run_provenance(github, configuration),
        signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
        recorded_by=writer.recorded_by(),
        recorded_at=_timestamp(),
    )
    report = coverage(
        resulting,
        pull_requests=[number],
        github_threads={
            (number, key): {"pull_request_number": number, "comment_id": key, "resolved": resolved}
            for key, resolved in threads.items()
        },
    )
    print(
        json.dumps(
            {
                "records_written": written,
                "historical_assessed": report["historical_assessed"],
                "signed_proven": report["signed_proven"],
                "proof_gap": report["proof_gap"],
            },
            sort_keys=True,
        )
    )
    return 0


def cmd_knowledge_context_check(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """Exercise the real Issue-agent engineering context compiler with signed live knowledge."""

    from hunter.evidence_intelligence.engineering_context_authority import (
        ENGINEERING_IMPLEMENT_TASK_KEY,
        EngineeringContextAuthority,
    )
    from hunter.task_scope import TaskScopeContract

    path = arguments.path
    if not path or path.startswith("/") or ".." in Path(path).parts or "\\" in path:
        raise LifecycleRefused("INVALID_SCOPE", "unsafe engineering context path")
    scope = TaskScopeContract(
        task_id="historical-knowledge-consumption-check",
        branch_pattern="issue-586-*",
        base_ref="main",
        base_sha=arguments.base_sha,
        allowed_paths=(path,),
        prohibited_paths=(),
    )
    scope_refusal = scope.incompleteness()
    if scope_refusal:
        raise LifecycleRefused("INVALID_SCOPE", scope_refusal)
    github = _github(configuration)
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    context = EngineeringContextAuthority(
        registry_path=Path(arguments.checkout) / "docs" / "DEFECT_REGISTRY.json",
        knowledge_overlay=_knowledge_overlay(configuration, github),
    ).compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope)
    matches = [item["id"] for item in context["applicable_defect_families"] if str(item["id"]).startswith("KF-")]
    if not matches:
        raise LifecycleRefused("PREVENTION_NOT_MATERIALIZED", "no signed finding reached engineering context")
    print(
        json.dumps(
            {
                "schema_version": "hunter-knowledge-context-check-v1",
                "source": "anchored-signed-knowledge-to-issue-agent-engineering-context",
                "scope_path": path,
                "selected_prevention_rules_sha256": context["selected_prevention_rules_sha256"],
                "finding_prevention_ids": matches,
            },
            sort_keys=True,
        )
    )
    return 0


def _notify_recovery_budget_exhausted(
    github: control.GitHubRest, configuration: control.Configuration, view: state.LedgerView, issue: int
) -> None:
    """Notify the owner once for a terminal, ledger-proven recovery budget exhaustion.

    The reconciler is serialized. On ambiguous reads/writes it fails closed and the next
    reconciliation re-reads the existing comment before attempting a write. No log text,
    model output, or secrets enter the comment.
    """

    if not view.claimed:
        return
    parent = view.authorizations[view.claimed[-1]]
    failure = parent.evidence.get(state.FAILED)
    source = state.validator_failure_source(parent)
    interrupted = (
        state.recovery_unstarted_on_ledger(parent)
        and parent.evidence[state.AUTHORIZED].get("preflight_recovery") is not None
    )
    if (
        source is None
        and not interrupted
        and (
            parent.state != state.FAILED
            or not isinstance(failure, Mapping)
            or failure.get("code") != state.PREFLIGHT_RECOVERY_TRIGGER
            or failure.get("failed_from_state") != state.PUBLISHED
        )
    ):
        return
    branch = str(parent.binding("execution_branch"))
    # The caller decided exhaustion with the run-level execution proof (Issue #716); this is only a lower bound
    # that keeps a notice from ever being posted for a lineage that has not spent its execution budget.
    if len(state.preflight_recoveries(view, branch)) < state.MAX_PREFLIGHT_RECOVERY_ATTEMPTS:
        return
    if source is not None:
        head = str(parent.binding("base_sha"))
    else:
        head = str(
            parent.evidence[state.AUTHORIZED]["preflight_recovery"]["bound_head_sha"]
            if interrupted
            else (parent.evidence.get(state.PUBLISHED) or {}).get("head_sha") or ""
        )
    marker = f"<!-- hunter-preflight-recovery-exhausted:{parent.authorization_id} -->"
    base = f"/repos/{configuration.repository}/issues/{issue}/comments"
    # Complete bounded pagination; an incomplete history is not permission to post.
    for page in range(1, 101):
        comments = control.definitive(github.get(f"{base}?per_page=100&page={page}"))
        if not isinstance(comments, list):
            raise control.FactsUnavailable("Issue comment history is not observable")
        if any(
            isinstance(item, Mapping)
            and isinstance(item.get("user"), Mapping)
            and item["user"].get("login") == "github-actions[bot]"
            and marker in str(item.get("body", ""))
            for item in comments
        ):
            return
        if len(comments) < 100:
            break
    else:
        raise control.FactsUnavailable("Issue comment history exceeds notification inspection bound")
    body = (
        f"{marker}\n"
        "Hunter autonomous Pre-PR recovery is BLOCKED: the approved repair budget "
        f"({state.MAX_PREFLIGHT_RECOVERY_ATTEMPTS} executed attempts, "
        f"{state.MAX_UNSTARTED_PREFLIGHT_RECOVERIES} proven-unstarted authorizations) is exhausted.\n\n"
        f"Issue: #{issue}\nBranch: `{branch}`\nExact failed HEAD: `{head}`\n"
        f"Last failed authorization: `{parent.authorization_id}`\n"
        "Owner action: inspect the signed Issue lifecycle and authoritative Preflight evidence; "
        "approve a new bounded remediation scope only after root-cause review. "
        "No further automatic recovery or PR creation is authorized by this notice."
    )
    posted = control.definitive(github.reply(base, {"body": body}))
    if not isinstance(posted, Mapping) or not isinstance(posted.get("id"), int):
        raise control.FactsUnavailable("Issue notification publication was not confirmed")


def _recover_failed_preflight(
    configuration: control.Configuration,
    github: control.GitHubRest,
    ledger_view: state.LedgerView,
    issue: int,
    signing_key: Ed25519PrivateKey,
) -> bool:
    """ADR 0039 L9: mint and dispatch one bounded recovery of a verified failed exact-head Pre-PR Preflight.

    The decision is durable without a second store: the authorization identity is a pure function of the live
    Issue and the verified failure, ``authorize`` claims it by CAS on the Issue ledger, and once claimed the
    ledger itself (an active authorization, then the recovery's own terminal record) makes every later pass a
    no-op. A lost dispatch is simply re-detected; a duplicate dispatch loses the claim. Nothing here reads Issue
    text for scope or routing, and a non-recoverable failure stays terminal and is only reported.
    """

    found: Any = control.eligible_validator_recovery(github, configuration, ledger_view, issue)
    if found is None:
        found = control.eligible_preflight_recovery(github, configuration, ledger_view, issue, job_log=github.job_log)
    if found is None:
        print(f"issue {issue}: no eligible remediation")
        return False
    if isinstance(found, control.PreflightFailureClass):
        if found.reason == "RECOVERY_BUDGET_EXHAUSTED":
            _notify_recovery_budget_exhausted(github, configuration, ledger_view, issue)
        print(f"issue {issue}: failed candidate stays terminal (BLOCKED): {found.kind}: {found.reason}")
        return False
    live = control.definitive(github.get(f"/repos/{configuration.repository}/issues/{issue}"))
    if not isinstance(live, Mapping):
        raise control.FactsUnavailable("the governing Issue is not observable")
    parent = ledger_view.authorizations[found.parent_authorization_id]
    try:
        builder = (
            remediation.validator_recovery_group
            if isinstance(found, control.ValidatorRecoveryCandidate)
            else remediation.preflight_recovery_group
        )
        group = builder(found, issue_number=issue)
        authorization = remediation.remediation_authorization(
            live, repository=configuration.repository, owner_login=configuration.owner_login, remediation=group
        )
        scope = remediation.remediation_scope(authorization, parent.evidence[state.AUTHORIZED]["task_scope"])
        document = remediation.sign_remediation(authorization, scope, signing_key=signing_key).to_json()
    except remediation.RemediationRefused as refusal:
        print(f"issue {issue}: refused {refusal}")
        return False
    if not github.dispatch(
        Path(control.LIFECYCLE_WORKFLOW).name,
        {control.REMEDIATION_DISPATCH_INPUT: base64.b64encode(document.encode()).decode("ascii")},
    ):
        raise control.FactsUnavailable("the preflight recovery dispatch was not accepted")
    if isinstance(found, control.ValidatorRecoveryCandidate):
        print(
            f"issue {issue}: dispatched validator Ruff recovery {authorization.authorization_id} attempt "
            f"{found.attempt} on {found.branch} at base {found.bound_base_sha[:12]} for validator run "
            f"{found.failure['validator_run_id']} ({len(found.failure['diagnostics'])} finding(s))"
        )
        return True
    print(
        f"issue {issue}: dispatched preflight recovery {authorization.authorization_id} attempt {found.attempt} "
        f"on {found.branch} at {found.bound_head_sha[:12]} for run {found.run_id} job {found.job_id} "
        f"({len(found.failed_tests)} failed test(s))"
    )
    return True


def cmd_remediate(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0039 L4 (RD-4/RD-5): detect an eligible lifecycle PR and dispatch one bounded remediation.

    At most one authorization is minted per Issue per pass, and it is minted from the *same* claims every time,
    so a duplicate pass, a lost dispatch or a crash recomputes the identical identity and loses the claim in
    ``authorize`` instead of starting a second lifecycle. Nothing here reads Issue text for scope or routing:
    the findings come from the verified anchored knowledge ledger and the head from a GitHub observation.
    """

    import hunter_collect_learning_observations as collector

    writer = _writer("reconcile", "remediate", control.RECONCILE_WORKFLOW)
    github = _github(configuration)
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    provenance = control.run_provenance(github, configuration)
    signing_key = _ed25519(AUTHORIZATION_SIGNING_KEY_ENV)
    store = _store(configuration, authenticated=True)
    dispatched = 0
    for issue in ([arguments.issue] if arguments.issue else _active_issues(configuration)):
        try:
            _, ledger_view = control.load_ledger(store, configuration, provenance, issue)
            candidate = control.eligible_remediation(
                github,
                configuration,
                ledger_view,
                _knowledge_view(configuration, github),
                issue,
                trusted_reviewers=collector._trusted_reviewer_logins(),
            )
            if candidate is None:
                if _recover_failed_preflight(configuration, github, ledger_view, issue, signing_key):
                    dispatched += 1
                continue
            live = control.definitive(github.get(f"/repos/{configuration.repository}/issues/{issue}"))
            if not isinstance(live, Mapping):
                raise control.FactsUnavailable("the governing Issue is not observable")
            parent = ledger_view.authorizations[candidate.parent_authorization_id]
            group = remediation.remediation_group(
                parent_authorization_id=candidate.parent_authorization_id,
                issue_number=issue,
                pull_request_number=candidate.pull_request_number,
                bound_head_sha=candidate.bound_head_sha,
                attempt=min(candidate.attempts),
                findings=candidate.findings,
            )
            try:
                authorization = remediation.remediation_authorization(
                    live, repository=configuration.repository, owner_login=configuration.owner_login, remediation=group
                )
                scope = remediation.remediation_scope(authorization, parent.evidence[state.AUTHORIZED]["task_scope"])
                document = remediation.sign_remediation(authorization, scope, signing_key=signing_key).to_json()
            except remediation.RemediationRefused as refusal:
                print(f"issue {issue}: refused {refusal}")
                continue
            # The request record is written before the dispatch, so a lost dispatch is a read-back, not a
            # second mint; the insert-only key makes a duplicate delivery a no-op.
            _, _, written = knowledge.append(
                store,
                remediation.remediation_writes(candidate, authorization_id=authorization.authorization_id),
                trust=configuration.trust,
                provenance=provenance,
                signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
                recorded_by=writer.recorded_by(),
                recorded_at=_timestamp(),
            )
            if not control.dispatch_remediation(github, candidate, document.encode()):
                raise control.FactsUnavailable("the remediation dispatch was not accepted")
            dispatched += 1
            print(
                f"issue {issue}: dispatched {authorization.authorization_id} for PR "
                f"#{candidate.pull_request_number} at {candidate.bound_head_sha[:12]} "
                f"({len(candidate.findings)} finding(s), {written} knowledge record(s))"
            )
        except state.LedgerConflictError:
            print(f"issue {issue}: no-op (a ledger moved; the next pass re-decides)")
        except control.FactsUnavailable as error:
            print(f"issue {issue}: no-op ({error})")
    return 0 if dispatched or arguments.issue else EXIT_NOOP


def cmd_resolve(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0039 L7 (RD-6): answer and resolve one exact review thread, only at an exactly proven head.

    The owner label, the anchored proof, the successful exact-head hosted Pre-PR Preflight and a re-review at
    that head are all required before anything is written. The only writes are one reply on the finding's own
    thread, one ``resolveReviewThread`` on that thread, and the insert-only knowledge records -- never an edit,
    a dismissal, an approval or a merge, and never a second pull request call.
    """

    writer = _writer("reconcile", "resolve", control.RECONCILE_WORKFLOW)
    github = _github(configuration)
    control.require_anchor(github, configuration, knowledge.KNOWLEDGE_LEDGER_REF)
    provenance = control.run_provenance(github, configuration)
    store = _store(configuration, authenticated=True)
    known = _knowledge_view(configuration, github)
    resolved = 0
    resolved_proofs: list[control.ResolutionProof] = []
    for issue in ([arguments.issue] if arguments.issue else _active_issues(configuration)):
        _head, state_view = control.load_ledger(store, configuration, provenance, issue)
        for finding_id in sorted(known.findings):
            try:
                proof = control.exact_head_proof(github, configuration, known, state_view, finding_id)
                if proof is None:
                    continue
                _head, after = knowledge.read(store, trust=configuration.trust, provenance=provenance)
                written = knowledge.append(
                    store,
                    remediation.proven_writes(
                        after,
                        finding_id,
                        authorization_id=proof.authorization_id,
                        remediated_head_sha=proof.remediated_head_sha,
                        receipt_sha256=proof.receipt_sha256,
                        preflight_run_id=proof.preflight_run_id,
                    ),
                    trust=configuration.trust,
                    provenance=provenance,
                    signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
                    recorded_by=writer.recorded_by(),
                    recorded_at=_timestamp(),
                )[2]
                comment_id = control.resolve_thread(
                    github,
                    configuration,
                    proof,
                    remediation.evidence_reply(
                        finding_id,
                        remediated_head_sha=proof.remediated_head_sha,
                        family=proof.family_id or "a new family candidate",
                        tests=proof.regression_tests,
                    ),
                )
                if comment_id is None:
                    print(f"issue {issue}: {finding_id[:12]} proven ({written} record(s)); the thread is not resolved")
                    continue
                _head, after = knowledge.read(store, trust=configuration.trust, provenance=provenance)
                knowledge.append(
                    store,
                    remediation.resolution_writes(
                        after,
                        finding_id,
                        remediated_head_sha=proof.remediated_head_sha,
                        reply_comment_id=comment_id,
                    ),
                    trust=configuration.trust,
                    provenance=provenance,
                    signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
                    recorded_by=writer.recorded_by(),
                    recorded_at=_timestamp(),
                )
                resolved += 1
                resolved_proofs.append(proof)
                print(f"issue {issue}: resolved {finding_id[:12]} at {proof.remediated_head_sha[:12]}")
            except control.FactsUnavailable as error:
                print(f"issue {issue}: no-op ({error})")
            except state.LedgerConflictError:
                print(f"issue {issue}: no-op (the knowledge ledger moved; the next pass re-decides)")
    if resolved:
        # A thread resolution does not wake a privileged workflow. The readiness
        # status refreshes on the event-driven workflow_run chain while checks run,
        # and otherwise on the trusted scheduled sweep (best-effort; GitHub may
        # delay it). An operator can force a refresh with GitHub's own "Re-run all jobs"
        # on a Merge Readiness run. This job deliberately holds no `actions: write` and
        # never dispatches a status-writing workflow.
        print("merge readiness refresh deferred to trusted scheduled sweep")
    return 0 if resolved or arguments.issue else EXIT_NOOP


def cmd_resume_bind(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    control_sha = control.bind_resume(
        github=_github(configuration),
        configuration=configuration,
        store=_store(configuration, authenticated=True),
        signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
        writer=_writer("bind", "resume-bind"),
        issue=arguments.issue,
        authorization_id=arguments.authorization_id,
        stage=arguments.stage,
        nonce=arguments.nonce,
        clock=_now,
    )
    _output(control_sha=control_sha)
    return 0


# --- authorize (T1) ---------------------------------------------------------------------------------------

#: ADR 0041 F8: the Issue event listing is read to its end within this bound, or arming is indefinite.
ISSUE_EVENT_PAGES: Final = 10


def _export_public_trust(configuration: control.Configuration) -> None:
    """Point the existing env-configured constructors at the pinned public trust material only."""

    environment = {
        "HUNTER_ISSUE_AGENT_REPOSITORY": configuration.repository,
        "HUNTER_ISSUE_AGENT_OWNER_LOGIN": configuration.owner_login,
        "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": configuration.authorization_verifying_key,
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": configuration.prompt_verifying_key,
        "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY": configuration.source_handling.verification_key,
        "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256": configuration.source_handling.verification_key_sha256,
        "HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256": configuration.source_handling.genesis_rule_sha256,
    }
    os.environ.update(environment)


def _authorize_dependencies(
    configuration: control.Configuration, github: control.GitHubRest, *, repository_checkout: Path | None = None
) -> Any:
    import hunter_issue_agent_provisioner as provisioner

    from hunter.automation.issue_agent_execution import IssueAgentAuthorizationVerifier
    from hunter.evidence_intelligence import source_handling_provenance
    from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier
    from hunter.evidence_intelligence.source_handling_persistence import (
        SourceHandlingOperatorRoot,
    )

    def provision(signed: Any, database: Path) -> object:
        os.environ[source_handling_provenance.EVIDENCE_DATABASE_ENV] = str(database)
        source_handling_provenance._production_view = None  # bind the resolver to this job's materialized store
        return provisioner.provision_issue_authority(provisioner.ProvisionerConfiguration.from_environment(), signed)

    def open_pull_request(issue: int) -> bool:
        listing = control.definitive(github.get(f"/repos/{configuration.repository}/pulls?state=open&per_page=100"))
        if not isinstance(listing, list):
            raise control.FactsUnavailable("pull request listing missing")
        return any(str((pr.get("head") or {}).get("ref", "")).startswith(f"issue-{issue}-") for pr in listing)

    def active_lifecycles() -> int:
        workflow = Path(control.LIFECYCLE_WORKFLOW).name
        listing = control.definitive(
            github.get(f"/repos/{configuration.repository}/actions/workflows/{workflow}/runs?status=in_progress")
        )
        if not isinstance(listing, Mapping):
            raise control.FactsUnavailable("run listing missing")
        return max(int(listing.get("total_count", 0)) - 1, 0)  # this run is one of them

    def issue_label_events(issue: int) -> list[Mapping[str, Any]]:
        """ADR 0041 F8: every event of the Issue, read to its end; an incomplete listing is indefinite."""
        events: list[Mapping[str, Any]] = []
        for page in range(1, ISSUE_EVENT_PAGES + 1):
            listing = control.definitive(
                github.get(f"/repos/{configuration.repository}/issues/{issue}/events?per_page=100&page={page}")
            )
            if not isinstance(listing, list):
                raise control.FactsUnavailable("issue event listing missing")
            events.extend(item for item in listing if isinstance(item, Mapping))
            if len(listing) < 100:
                return events
        raise control.FactsUnavailable("issue event listing exceeds its bound")

    run_id, _, control_sha = _run_context()
    operator = configuration.source_handling
    return authorize.AuthorizeDependencies(
        repository=configuration.repository,
        repository_id=configuration.repository_id,
        owner_login=configuration.owner_login,
        issuer_verifier=IssueAgentAuthorizationVerifier.from_environment(),
        prompt_verifier=PromptAutomationVerifier.from_environment(),
        source_handling_verification_key=bytes.fromhex(operator.verification_key),
        source_handling_operator_root=SourceHandlingOperatorRoot(
            genesis_rule_sha256=operator.genesis_rule_sha256,
            verification_key_sha256=operator.verification_key_sha256,
        ),
        provenance_resolver=source_handling_provenance.production_provenance_resolver,
        provision=provision,
        trust=configuration.trust,
        ledger_provenance=control.run_provenance(github, configuration),
        state_signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
        handoff_recipient=configuration.handoff_recipient,
        fallback_handoff_recipient=getattr(configuration, "fallback_handoff_recipient", None),
        issue_label_events=issue_label_events,
        open_issue_agent_pull_request=open_pull_request,
        active_lifecycles=active_lifecycles,
        compiler_identity_sha256=authorize.compiler_identity(
            control_sha=control_sha, checkout=repository_checkout or Path.cwd()
        ),
        repository_checkout=repository_checkout or Path.cwd(),
        knowledge_overlay=_knowledge_overlay(configuration, github),
    )


def _knowledge_overlay(configuration: control.Configuration, github: control.GitHubRest) -> list[dict[str, Any]]:
    return knowledge.overlay_families(_knowledge_view(configuration, github))


def _authorize_context() -> Any:
    run_id, attempt, control_sha = _run_context()
    return authorize.RunContext(
        workflow_path=control.LIFECYCLE_WORKFLOW,
        run_id=run_id,
        run_attempt=attempt,
        control_sha=control_sha,
        recorded_at=os.environ.get("HUNTER_ISSUE_AGENT_RECORDED_AT") or _timestamp(),
    )


def _require_anchors(configuration: control.Configuration, github: control.GitHubRest, issue: int) -> None:
    from hunter.automation.issue_agent_source_handling_store import SOURCE_HANDLING_LEDGER_REF

    control.require_anchor(github, configuration, state.ledger_ref(issue))
    control.require_anchor(github, configuration, SOURCE_HANDLING_LEDGER_REF)


def cmd_source_handling_bootstrap(configuration: control.Configuration, _arguments: argparse.Namespace) -> int:
    """One-shot S6 bootstrap of ADR 0038, recovering any valid interrupted prefix.

    Everything this job can observe outside the closed refusal vocabulary is translated here, so the
    owner-dispatched run always ends in a bounded code and never in a traceback.
    """
    from hunter.evidence_intelligence.source_handling_persistence import SourceHandlingBlockedError

    try:
        return _source_handling_bootstrap(configuration)
    except (control.ControlRefused, LifecycleRefused):
        raise
    except (state.LedgerCorruptError, state.LedgerSchemaError) as error:
        raise control.Frozen("STATE_CORRUPT", str(error)) from None
    except state.LedgerError:
        raise control.FactsUnavailable(
            "the Source Handling ledger could not be read or advanced; re-dispatch the bootstrap"
        ) from None
    except OSError:
        # A local workspace creation/storage failure (for example a full or
        # unavailable temporary filesystem) is operational, never a trust
        # decision, so it becomes the same bounded re-dispatch refusal. The
        # OS error payload is deliberately not carried.
        raise control.FactsUnavailable(
            "the Source Handling workspace could not be prepared; re-dispatch the bootstrap"
        ) from None
    except sqlite3.OperationalError:
        # SQLite open/setup/replay storage failures are operational too and
        # must not surface a raw traceback; the error payload is not carried.
        raise control.FactsUnavailable(
            "the Source Handling store could not be read or advanced; re-dispatch the bootstrap"
        ) from None
    except SourceHandlingBlockedError as error:
        raise LifecycleRefused("SOURCE_HANDLING_BLOCKED", str(error)) from None


def _source_handling_bootstrap(configuration: control.Configuration) -> int:
    import tempfile

    import bootstrap_source_handling_authority as bootstrap

    from hunter.automation import issue_agent_source_handling_store as sh
    from hunter.evidence_intelligence.source_handling_persistence import (
        SourceHandlingOperatorRoot,
        SqliteSourceHandlingAuthorityReadView,
    )
    from hunter.evidence_intelligence.source_handling_provenance import production_provenance_resolver

    _export_public_trust(configuration)
    github = _github(configuration)

    def canonical_bootstrap(target: Path) -> tuple[Any, list[Any]]:
        try:
            return sh.capture(target, lambda: bootstrap.bootstrap_authority(str(target)))
        except ValueError as error:
            raise LifecycleRefused("MISSING_CONFIGURATION", str(error)) from None

    store = _store(configuration, authenticated=True)
    # Creation-aware S6 bootstrap: GitHub cannot return branch-applied rules for a ref that does not
    # exist.  Existing refs always take the strict path.  A first creation may proceed only after the
    # pinned namespace ruleset is verified and absence is observed through the authenticated store; the
    # newly-created ref is then required to pass the normal strict branch-applied verification below.
    bootstrap_head = store.ref_head(sh.SOURCE_HANDLING_LEDGER_REF)
    try:
        if bootstrap_head is None:
            control.require_anchor_ruleset(github, configuration, sh.SOURCE_HANDLING_LEDGER_REF)
        else:
            control.require_anchor(github, configuration, sh.SOURCE_HANDLING_LEDGER_REF)
    except ValueError:
        raise control.Frozen(
            "ANCHOR_INTEGRITY_FAILED", "the bootstrap target ref is outside the anchored state namespace"
        ) from None
    provenance = control.run_provenance(github, configuration)
    operator_root = SourceHandlingOperatorRoot(
        genesis_rule_sha256=configuration.source_handling.genesis_rule_sha256,
        verification_key_sha256=configuration.source_handling.verification_key_sha256,
    )
    with tempfile.TemporaryDirectory(prefix="hunter-sh-bootstrap-") as root:
        root = Path(root)
        database = root / "evidence.sqlite"
        head, entries = store.read_files(sh.SOURCE_HANDLING_LEDGER_REF, frozenset({"record.json", "delta.json"}))
        if entries:
            position = sh.materialize(
                store,
                database,
                trust=configuration.trust,
                provenance=provenance,
                verification_public_key=bytes.fromhex(configuration.source_handling.verification_key),
                operator_root=operator_root,
            )
        else:
            position = sh.LedgerPosition()

        # Run the canonical bootstrap against the verified prefix. If a previous run stopped after one
        # transaction, capture emits only the missing canonical transactions; a complete ledger emits none.
        outcome, transactions = canonical_bootstrap(database)
        if outcome.get("status") not in {"bootstrapped", "already-provisioned"}:
            raise LifecycleRefused("SOURCE_HANDLING_BLOCKED", "canonical bootstrap did not reach a complete state")
        if (
            outcome.get(bootstrap.VERIFICATION_KEY_ENV) != configuration.source_handling.verification_key
            or outcome.get(bootstrap.VERIFICATION_KEY_SHA256_ENV)
            != configuration.source_handling.verification_key_sha256
            or outcome.get(bootstrap.GENESIS_RULE_SHA256_ENV) != configuration.source_handling.genesis_rule_sha256
        ):
            raise LifecycleRefused(
                "SOURCE_HANDLING_BLOCKED", "bootstrap signing key or genesis does not match pinned trust roots"
            )
        writer = _writer(
            "source-handling-bootstrap", "source-handling-bootstrap", control.SOURCE_HANDLING_BOOTSTRAP_WORKFLOW
        )
        if transactions:
            signing_key = _ed25519(STATE_SIGNING_KEY_ENV)
            if state.public_key_id(signing_key.public_key()) not in configuration.trust.state_keys:
                # A syntactically valid key that the trust roots do not pin would sign and durably append an
                # unrecoverable first record, and the later replay verification could only report STATE_CORRUPT
                # after the write. Refuse before any durable write so the ref never appears.
                raise LifecycleRefused(
                    "MISSING_CONFIGURATION",
                    f"{STATE_SIGNING_KEY_ENV} is not pinned by the trust roots; refusing to write an unusable "
                    "ledger record",
                )
            first, *remaining = transactions
            position = sh.publish(
                store,
                position,
                [first],
                signing_key=signing_key,
                recorded_by=writer.recorded_by(),
                recorded_at=_timestamp(),
                repository_id=configuration.repository_id,
            )
            # The creation exception is exactly one CAS.  The ref now exists, so branch-applied protection
            # must be proven before any later bootstrap transaction can become durable.
            control.require_anchor(github, configuration, sh.SOURCE_HANDLING_LEDGER_REF)
            if remaining:
                position = sh.publish(
                    store,
                    position,
                    remaining,
                    signing_key=signing_key,
                    recorded_by=writer.recorded_by(),
                    recorded_at=_timestamp(),
                    repository_id=configuration.repository_id,
                )

        replay = root / "replay.sqlite"
        verified = sh.materialize(
            store,
            replay,
            trust=configuration.trust,
            provenance=provenance,
            verification_public_key=bytes.fromhex(configuration.source_handling.verification_key),
            operator_root=operator_root,
        )
        # Completeness is semantic, not merely "non-empty": the canonical bootstrap must now be idempotent.
        check, remaining = canonical_bootstrap(replay)
        if check.get("status") != "already-provisioned" or remaining:
            raise LifecycleRefused("SOURCE_HANDLING_BLOCKED", "anchored bootstrap is valid but incomplete")
        # Force the canonical ADR 0036 read path to authenticate root/history signatures before success.
        SqliteSourceHandlingAuthorityReadView(
            replay,
            verification_public_key=bytes.fromhex(configuration.source_handling.verification_key),
            operator_root=operator_root,
            provenance_resolver=production_provenance_resolver,
        )
        if verified.head != position.head or verified.snapshot_sha256 != position.snapshot_sha256:
            raise LifecycleRefused("SOURCE_HANDLING_BLOCKED", "bootstrap replay does not match the published ledger")
    print(f"source-handling bootstrap: complete and verified at {position.head}")
    return 0


def cmd_authorize_prepare(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    import hunter_issue_agent_trigger as trigger

    _run_context()
    if (arguments.event is None) == (arguments.document is None):
        raise LifecycleRefused("MISSING_CONFIGURATION", "authorize-prepare takes exactly one authorization source")
    if arguments.document is not None:
        # ADR 0039 L4: the remediation document was already minted and signed by the reconcile job with K_AUTH.
        # This job re-verifies that signature and never re-mints, so a dispatch can never be a second issuer.
        from hunter.automation.issue_agent_execution import SignedIssueAgentAuthorization

        document = Path(arguments.document).read_bytes()
        try:
            authorization = SignedIssueAgentAuthorization.from_json(document).authorization
        except Exception:
            raise LifecycleRefused("TRANSPORT_INTEGRITY_FAILED", "the remediation document does not parse") from None
    else:
        event = json.loads(Path(arguments.event).read_bytes())
        authorization = trigger.authorize_event(
            event, expected_repository=configuration.repository, owner_login=configuration.owner_login
        )
        document = (
            trigger.sign_authorization(
                authorization, signing_key=trigger.load_signing_key(_secret(trigger.SIGNING_KEY_ENV))
            )
            .to_json()
            .encode("utf-8")
        )
    _export_public_trust(configuration)
    github = _github(configuration)
    _require_anchors(configuration, github, authorization.issue_number)
    out = Path(arguments.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    context = _authorize_context()
    fallback_path = out / "fallback.sealed"
    fallback_path.unlink(missing_ok=True)
    prepared, sealed = authorize.prepare(
        document,
        dependencies=_authorize_dependencies(
            configuration,
            github,
            repository_checkout=Path(getattr(arguments, "checkout", None) or Path.cwd()).resolve(),
        ),
        context=context,
        state_store=_store(configuration, authenticated=True),
        source_handling_store=_store(configuration, authenticated=True),
        workdir=out / "work",
        fallback_sink=(
            None if getattr(configuration, "fallback_handoff_recipient", None) is None else fallback_path.write_bytes
        ),
    )
    (out / "handoff.sealed").write_bytes(sealed)
    (out / "prepared.json").write_text(prepared.to_json(), encoding="utf-8")
    (out / "recorded_at").write_text(context.recorded_at, encoding="utf-8")
    _output(
        authorization_id=prepared.authorization_id,
        issue=prepared.issue_number,
        handoff_artifact=state.handoff_artifact_name(prepared.authorization_id),
        fallback_handoff_artifact=(
            state.fallback_handoff_artifact_name(prepared.authorization_id) if fallback_path.exists() else ""
        ),
    )
    return 0


def _uploaded(configuration: control.Configuration, github: Any, artifact_id: int) -> authorize.UploadedArtifact:
    item = control.definitive(github.get(f"/repos/{configuration.repository}/actions/artifacts/{artifact_id}"))
    if not isinstance(item, Mapping):
        raise LifecycleRefused("TRANSPORT_INTEGRITY_FAILED", "the uploaded handoff artifact is not readable back")
    return authorize.UploadedArtifact(
        run_id=int((item.get("workflow_run") or {}).get("id", 0)),
        artifact_id=int(item["id"]),
        name=str(item.get("name")),
        artifact_digest=str(item.get("digest") or ""),
        expired=bool(item.get("expired")),
    )


def cmd_authorize_commit(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    run_id, _, _ = _run_context()
    _export_public_trust(configuration)
    out = Path(arguments.out_dir)
    os.environ["HUNTER_ISSUE_AGENT_RECORDED_AT"] = (out / "recorded_at").read_text(encoding="utf-8")
    prepared = authorize.Prepared.from_json((out / "prepared.json").read_bytes())
    github = _github(configuration)
    _require_anchors(configuration, github, prepared.issue_number)
    uploaded = _uploaded(configuration, github, arguments.artifact_id)
    fallback_id = getattr(arguments, "fallback_artifact_id", None)
    head = authorize.commit(
        prepared,
        uploaded=uploaded,
        fallback_uploaded=None if fallback_id is None else _uploaded(configuration, github, fallback_id),
        dependencies=_authorize_dependencies(configuration, github),
        context=_authorize_context(),
        state_store=_store(configuration, authenticated=True),
        source_handling_store=_store(configuration, authenticated=True),
    )
    print(f"AUTHORIZED {prepared.authorization_id} at {head}")
    return 0


# --- role jobs -----------------------------------------------------------------------------------------------


def _view(
    configuration: control.Configuration,
    issue: int,
    authorization_id: str,
    access: roles.LedgerAccess | None = None,
) -> state.AuthorizationView:
    access = access or _ledger_access(configuration)
    _, entries = access.store.read(issue)
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=configuration.repository_id,
        issue_number=issue,
        trust=configuration.trust,
        provenance=access.provenance,
        indexes=[entry.index for entry in entries],
    )
    if view.active != authorization_id:
        raise LifecycleRefused("STATE_CORRUPT", "the authorization is not the Issue's active authorization")
    return view.authorizations[authorization_id]


def cmd_bound(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """Artifact ids and outcome names derived only from the ledger (consumers download by bound id)."""

    view = _view(configuration, arguments.issue, arguments.authorization_id)
    values: dict[str, object] = {
        "handoff_artifact_id": view.binding("handoff_artifact")["artifact_id"],
        "handoff_run_id": view.binding("handoff_artifact")["run_id"],
        "control_sha": view.binding("control_sha"),
    }
    fallback = view.evidence[state.AUTHORIZED].get("fallback")
    if fallback is not None:
        values["fallback_handoff_artifact_id"] = fallback["handoff_artifact"]["artifact_id"]
        values["fallback_handoff_run_id"] = fallback["handoff_artifact"]["run_id"]
    if state.RESULT_BOUND in view.evidence:
        artifact = view.evidence[state.RESULT_BOUND]["result_artifact"]
        values.update(result_artifact_id=artifact["artifact_id"], result_run_id=artifact["run_id"])
    for stage in state.RESUME_STAGES:
        attempt = (view.resume_attempts or {}).get(stage, 0) + 1
        values[f"{stage}_outcome"] = control.outcome_artifact_name(view.authorization_id, stage, attempt)
    _output(**values)
    return 0


MODEL_TAIL_OPTION = "--model-argv"


def _model_command(tail: Sequence[str]) -> tuple[str, ...]:
    """The model command exactly as given after ``--model-argv``.

    One leading ``--`` is the conventional end-of-options separator and is not part of the command; every other word,
    including later ``--`` and options such as ``--model``, is forwarded unchanged.
    """

    command = tuple(tail[1:] if tail[:1] == ["--"] else tail)
    if not command or not command[0].strip():
        raise LifecycleRefused("MISSING_CONFIGURATION", "execute requires a model command after --model-argv")
    return command


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the lifecycle command line; the model command after ``--model-argv`` never reaches argparse.

    The first ``--model-argv`` word splits the line: the head is parsed as lifecycle options, the tail is the downstream
    model command, forwarded verbatim. Argparse therefore cannot reinterpret or abbreviate the tail's own options, and
    ``--model-argv`` is accepted for ``execute`` only. A missing or empty tail is refused.
    """

    words = list(sys.argv[1:] if argv is None else argv)
    tail: list[str] | None = None
    if MODEL_TAIL_OPTION in words:
        boundary = words.index(MODEL_TAIL_OPTION)
        words, tail = words[:boundary], words[boundary + 1 :]
    parser = _parser()
    arguments = parser.parse_args(words)
    if arguments.command == "execute":
        if tail is None:
            parser.error(f"execute requires {MODEL_TAIL_OPTION} COMMAND [ARGS...]")
        try:
            arguments.model_argv = _model_command(tail)
        except LifecycleRefused as refusal:
            parser.error(str(refusal))
    elif tail is not None:
        parser.error(f"{MODEL_TAIL_OPTION} is valid only for execute")
    return arguments


def _executor_model_secrets(model_argv: Sequence[str], model_key_name: str) -> dict[str, str]:
    """Allow keyless execution only for explicitly free Kilo models.

    Never relax the credential requirement for paid, unknown, or legacy providers.
    """
    if (
        len(model_argv) == 4
        and Path(model_argv[0]).name == "kilo"
        and model_argv[1:3] == ("run", "--model")
        and model_argv[3].startswith("kilo/")
        and model_argv[3].endswith(":free")
    ):
        return {}
    if not model_key_name:
        raise LifecycleRefused("MISSING_CONFIGURATION", "a valid model key name is required")
    return {model_key_name: _secret(MODEL_KEY_ENV)}


def cmd_execute(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
    from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

    run_id, attempt, _ = _run_context()
    os.environ["HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY"] = configuration.prompt_verifying_key
    model_secrets = _executor_model_secrets(tuple(arguments.model_argv), arguments.model_key_name)
    probe = None
    if getattr(configuration, "fallback_handoff_recipient", None) is not None and model_secrets:
        from hunter.automation.issue_agent_provider_probe import KILO_PROBE_URL, probe_provider

        key = next(iter(model_secrets.values()))
        probe = lambda: probe_provider(KILO_PROBE_URL, api_key=key)  # noqa: E731 - trusted step, no model input
    outcome = roles.run_executor(
        issue=arguments.issue,
        authorization_id=arguments.authorization_id,
        context=roles.RoleContext(run_id, attempt),
        ledger=_ledger_access(configuration),
        handoff_envelope=Path(arguments.handoff).read_bytes(),
        config=roles.ExecutorConfig(
            remote=_remote(configuration),
            model_argv=tuple(arguments.model_argv),
            model_public_env=roles.MODEL_PUBLIC_ENVIRONMENT,
            model_secret_env=model_secrets,
            handoff_key=_x25519(HANDOFF_KEY_ENV),
            result_recipient=configuration.result_recipient,
            prompt_verifier=PromptAutomationVerifier.from_environment(),
            provider_probe=probe,
        ),
        isolation=(
            KiloContainerIsolation(Path(__file__).resolve().parent / "issue_agent")
            if roles._is_kilo_run(tuple(arguments.model_argv))
            else roles.SudoIsolation(ISOLATION_USER)
        ),
        workroot=Path(arguments.workroot),
    )
    if outcome.sealed_result is not None:
        Path(arguments.out).write_bytes(outcome.sealed_result)
    elif outcome.advisory_code:
        # Emit only an allowlisted, non-secret diagnostic; never model stdout.
        print(f"executor advisory: {outcome.advisory_code}")
    _output(
        advisory_code=outcome.advisory_code or "",
        sealed="true" if outcome.sealed_result else "false",
        result_artifact=state.result_artifact_name(arguments.authorization_id),
        stderr_category=outcome.stderr_category or "",
        probe_category=outcome.probe_category or "",
        canary_primary_refusals=str((outcome.canary_refusal or {}).get("primary_refusals", "")),
        canary_refusal_digest=str((outcome.canary_refusal or {}).get("refusal_digest", "")),
    )
    return 0


FALLBACK_DECISION_LABELS: Final = {
    "hunter-codex-fallback-approve": "approve",
    "hunter-codex-fallback-reject": "reject",
}


def cmd_fallback_decision(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0041 F1: the owner's explicit approval or rejection of a pending fallback, from the label event itself."""

    event = json.loads(Path(arguments.event).read_bytes())
    issue = event.get("issue") or {}
    label = (event.get("label") or {}).get("name")
    sender = (event.get("sender") or {}).get("login")
    if (
        event.get("action") != "labeled"
        or label not in FALLBACK_DECISION_LABELS
        or sender != configuration.owner_login
        or issue.get("pull_request") is not None
        or issue.get("state") != "open"
        or type(issue.get("number")) is not int
    ):
        raise LifecycleRefused("NOT_ELIGIBLE", "only the owner's label on an open Issue decides a fallback")
    decision = control.owner_decision(
        github=_github(configuration),
        configuration=configuration,
        store=_store(configuration, authenticated=True),
        signing_key=_ed25519(STATE_SIGNING_KEY_ENV),
        writer=_writer("owner-decision", "fallback-decision"),
        issue=int(issue["number"]),
        decision=FALLBACK_DECISION_LABELS[str(label)],
        clock=_now,
    )
    print(f"issue {issue['number']}: fallback {decision.action} {decision.code or decision.reason}".rstrip())
    return 0


def cmd_execute_fallback(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0041: the single admitted Codex fallback, on the attested Mac, through the unchanged executor role."""

    import secrets as _secrets

    from hunter.automation.issue_agent_mac_fallback import WORK_ROOT, MacCodexIsolation
    from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

    run_id, attempt, _ = _run_context()
    if getattr(configuration, "fallback_handoff_recipient", None) is None:
        raise LifecycleRefused("MISSING_CONFIGURATION", "no fallback recipient is provisioned")
    access = _ledger_access(configuration)
    view = _view(configuration, arguments.issue, arguments.authorization_id, access)
    if view.state != state.FALLBACK_AUTHORIZED:
        raise roles.RoleRefused("EXECUTION_NOT_STARTED", "no signed FALLBACK_AUTHORIZED record on the ledger")
    os.environ["HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY"] = configuration.prompt_verifying_key
    # ADR 0041 F1: anything refused before Codex is launched (no attestation, no owner sign-in, an expired device
    # code) ran no model. It is reported as such, so the request returns to pending for the owner's next approval.
    try:
        isolation = MacCodexIsolation(Path(arguments.checkout))
        workroot = WORK_ROOT / _secrets.token_hex(16)
        workroot.mkdir(mode=0o700)
        # Attest before the fallback key is even read: pinned bytes, principals, live denials.
        attestation = isolation.attest(workroot)
        outcome = roles.run_executor(
            issue=arguments.issue,
            authorization_id=arguments.authorization_id,
            context=roles.RoleContext(run_id, attempt),
            ledger=access,
            handoff_envelope=Path(arguments.handoff).read_bytes(),
            config=roles.ExecutorConfig(
                remote=_remote(configuration),
                model_argv=("codex", "exec", "--model", arguments.model),
                model_public_env=roles.MODEL_PUBLIC_ENVIRONMENT,
                model_secret_env={},
                handoff_key=_x25519(FALLBACK_HANDOFF_KEY_ENV),
                result_recipient=configuration.result_recipient,
                prompt_verifier=PromptAutomationVerifier.from_environment(),
            ),
            isolation=isolation,
            workroot=workroot,
        )
    except roles.RoleRefused as refusal:
        if refusal.code != "EXECUTION_NOT_STARTED":
            raise
        print("fallback executor advisory: FALLBACK_SESSION_NOT_STARTED", flush=True)
        _output(
            advisory_code="FALLBACK_SESSION_NOT_STARTED",
            sealed="false",
            result_artifact=state.fallback_result_artifact_name(arguments.authorization_id),
            attestation_sha256="",
        )
        return 0
    if outcome.sealed_result is not None:
        Path(arguments.out).write_bytes(outcome.sealed_result)
    elif outcome.advisory_code:
        print(f"fallback executor advisory: {outcome.advisory_code}")
    _output(
        advisory_code=outcome.advisory_code or "",
        sealed="true" if outcome.sealed_result else "false",
        result_artifact=state.fallback_result_artifact_name(arguments.authorization_id),
        attestation_sha256=attestation,
        fallback_model=json.dumps(isolation.model, sort_keys=True, separators=(",", ":")) if isolation.model else "",
    )
    return 0


def _digest_files(paths: Sequence[str], *, extra: str = "") -> str:
    digest = hashlib.sha256()
    for name in paths:
        content = Path(name).read_bytes()
        digest.update(f"{name}\0{len(content)}\0".encode())
        digest.update(content)
    digest.update(extra.encode())
    return digest.hexdigest()


def _write_outcome(path: str, document: Mapping[str, Any]) -> None:
    Path(path).write_bytes(state.canonical_json(dict(document)))


def cmd_validate(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    github = _provenance_github(configuration)
    access = _ledger_access(configuration, github)
    view = _view(configuration, arguments.issue, arguments.authorization_id, access)
    try:
        receipt = roles.run_validator(
            issue=arguments.issue,
            authorization_id=arguments.authorization_id,
            repository=configuration.repository,
            ledger=access,
            result_envelope=Path(arguments.result).read_bytes(),
            result_key=_x25519(RESULT_KEY_ENV),
            trusted_repo=Path(arguments.trusted_repo),
            isolation_user=ISOLATION_USER,
            writer=configuration.writer,
            validation_definition=_digest_files(VALIDATION_DEFINITION_FILES),
            toolchain_sha256=_digest_files(TOOLCHAIN_FILES, extra=sys.version),
            remediation=_promotion_port(configuration, github),
        )
    except roles.RoleRefused as error:
        if error.code not in state.VALIDATION_REFUSAL_CODES:
            raise
        receipt = {
            "schema_version": roles.RECEIPT_SCHEMA_VERSION,
            "authorization_id": arguments.authorization_id,
            "execution_id": view.execution_id,
            "ciphertext_sha256": view.evidence[state.RESULT_BOUND]["result_artifact"]["ciphertext_sha256"],
            "verdict": "REFUSED",
            "code": error.code,
            **error.receipt_detail,
        }
    _write_outcome(arguments.out, receipt)
    print(f"validation {receipt['verdict']}")
    return 0


def _issue_gate(configuration: control.Configuration, github: control.GitHubRest, issue: int) -> Any:
    document = control.definitive(github.get(f"/repos/{configuration.repository}/issues/{issue}"))
    if not isinstance(document, Mapping):
        raise LifecycleRefused("PUBLICATION_UNAVAILABLE", "the Issue is not observable")
    labels = {str(label.get("name")) for label in document.get("labels") or [] if isinstance(label, Mapping)}
    return roles.IssueGate(
        open=document.get("state") == "open",
        is_pull_request="pull_request" in document,
        label_present="hunter-agent-execute" in labels,
        title_sha256=state.sha256_hex(str(document.get("title") or "").encode("utf-8")),
        body_sha256=state.sha256_hex(str(document.get("body") or "").encode("utf-8")),
    )


def _observe_hgcc_contract(repository: str) -> None:
    """HGCC-1 trusted publication boundary: the live ruleset contract must be completely observable, or refuse.

    ``bypass_actors`` is returned only to a credential with ruleset-level visibility, which ``GITHUB_TOKEN`` cannot
    hold. The dedicated read-only credential is read once, removed from this process's environment so no later
    subprocess (git push, PR calls) inherits it, and handed only to the observer subprocess, whose environment holds
    nothing else. There is no fallback to ``GITHUB_TOKEN`` or the publisher push token. Nothing the observer prints is
    forwarded: a refusal carries only a reason from a closed vocabulary.
    """

    from hunter_github_contract import DEFINITIVE_REASONS, REFUSAL_REASONS

    def refuse(reason: str) -> LifecycleRefused:
        message = f"HGCC-1 GitHub contract unavailable: {reason}"
        # Only a verdict on data actually received is definitive; an outage, a missing credential or an unclassified
        # failure stays transient and is bounded by the publication resume budget.
        if reason in DEFINITIVE_REASONS or reason == "OBSERVATION_INCOMPLETE":
            return PublicationContractRefused(message)
        return LifecycleRefused("PUBLICATION_UNAVAILABLE", message)

    token = os.environ.pop(HGCC_TOKEN_ENV, "").strip()
    if not token:
        raise refuse("READ_CREDENTIAL_MISSING")
    script = Path(__file__).resolve().with_name("hunter_github_contract.py")
    with tempfile.TemporaryDirectory(prefix="hunter-hgcc-") as home:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home,
            "GH_CONFIG_DIR": home,
            "GH_TOKEN": token,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, minimal environment
                (sys.executable, str(script), repository),
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise refuse("OBSERVATION_FAILED") from None
    if done.returncode != 0:
        prefix = "[HGCC-1] INDETERMINATE: "
        reasons = [line[len(prefix) :] for line in done.stderr.splitlines() if line.startswith(prefix)]
        raise refuse(reasons[-1] if reasons and reasons[-1] in REFUSAL_REASONS else "UNCLASSIFIED")
    try:
        contract = json.loads(done.stdout)
        rulesets = contract["rulesets"]
        complete = (
            contract["schema"] == "hgcc-1/v1"
            and contract["repository"] == repository
            and isinstance(rulesets, list)
            and bool(rulesets)
            and all(isinstance(item, dict) and isinstance(item.get("bypass_actors"), list) for item in rulesets)
        )
    except (ValueError, KeyError, TypeError):
        complete = False
    if not complete:
        raise refuse("OBSERVATION_INCOMPLETE")


def _refused_outcome(arguments: argparse.Namespace, code: str) -> int:
    _write_outcome(
        arguments.out,
        {
            "schema_version": control.OUTCOME_SCHEMA_VERSION,
            "authorization_id": arguments.authorization_id,
            "verdict": "REFUSED",
            "code": code,
        },
    )
    _output(refused="true")
    print(f"publication refused: {code}")
    return 0


def cmd_publish(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    try:
        _observe_hgcc_contract(configuration.repository)
    except PublicationContractRefused as error:
        return _refused_outcome(arguments, error.code)
    github = _github(configuration)
    open_pr = control.definitive(github.get(f"/repos/{configuration.repository}/pulls?state=open&per_page=100"))
    if not isinstance(open_pr, list):
        raise LifecycleRefused("PUBLICATION_UNAVAILABLE", "pull requests are not observable")
    token = _secret(PUSH_TOKEN_ENV)
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    signing_key = Path(arguments.workroot) / "publisher-signing-key"
    signing_key.parent.mkdir(parents=True, exist_ok=True)
    signing_key.touch(mode=0o600)
    signing_key.write_text(_secret(PUBLISHER_SIGNING_KEY_ENV) + "\n", encoding="utf-8")
    try:
        publication = roles.run_publisher(
            issue=arguments.issue,
            authorization_id=arguments.authorization_id,
            repository=configuration.repository,
            ledger=_ledger_access(configuration, github),
            result_envelope=Path(arguments.result).read_bytes(),
            result_key=_x25519(RESULT_KEY_ENV),
            trusted_repo=Path(arguments.trusted_repo),
            writer=configuration.writer,
            issue_gate=_issue_gate(configuration, github, arguments.issue),
            open_issue_agent_pull_request=any(
                str((pr.get("head") or {}).get("ref", "")).startswith(f"issue-{arguments.issue}-") for pr in open_pr
            ),
            signing_key=str(signing_key),
            push_url=_remote(configuration),
            push_config=(("http.extraheader", header),),
            derive_promotion=_promotion_port(configuration, github).promote,
        )
    except roles.RoleRefused as error:
        if error.code not in state.PUBLICATION_REFUSAL_CODES:
            raise
        return _refused_outcome(arguments, error.code)
    finally:
        signing_key.unlink(missing_ok=True)
    print(f"published {publication.branch} at {publication.head_sha}")
    return 0


def cmd_candidate_gate(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """The candidate-PR workflow may open a Draft PR only for a head the ledger recorded as PUBLISHED."""

    prefix, _, rest = arguments.branch.partition("-")
    issue_text, _, _ = rest.partition("-")
    if prefix != "issue" or not issue_text.isdigit():
        raise LifecycleRefused("NOT_ELIGIBLE", "not an Issue-agent branch")
    access = _ledger_access(configuration)
    issue = int(issue_text)
    _, entries = access.store.read(issue)
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=configuration.repository_id,
        issue_number=issue,
        trust=configuration.trust,
        provenance=access.provenance,
        indexes=[entry.index for entry in entries],
    )
    active = view.authorizations.get(view.active or "")
    if (
        active is None
        or active.state != state.PUBLISHED
        or active.binding("execution_branch") != arguments.branch
        or active.evidence[state.PUBLISHED]["head_sha"] != arguments.head_sha
    ):
        raise LifecycleRefused("NOT_ELIGIBLE", "the head is not the ledger's PUBLISHED head for this branch")
    _output(issue=issue, authorization_id=active.authorization_id)
    return 0


# --- CLI ------------------------------------------------------------------------------------------------------


Command = Callable[[control.Configuration, argparse.Namespace], int]


def _parser() -> argparse.ArgumentParser:
    # No option abbreviation anywhere: a lifecycle option is only ever its full name, so a token meant for the model
    # command can never be mistaken for (or silently resolved to) one of ours.
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_lifecycle", allow_abbrev=False)
    parser.add_argument("--checkout", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, command: Command) -> argparse.ArgumentParser:
        child = sub.add_parser(name, allow_abbrev=False)
        child.set_defaults(handler=command)
        return child

    add("source-handling-bootstrap", cmd_source_handling_bootstrap)
    prepare = add("authorize-prepare", cmd_authorize_prepare)
    prepare.add_argument("--event")
    prepare.add_argument("--document")
    prepare.add_argument("--out-dir", required=True)
    commit = add("authorize-commit", cmd_authorize_commit)
    commit.add_argument("--out-dir", required=True)
    commit.add_argument("--artifact-id", type=int, required=True)
    commit.add_argument("--fallback-artifact-id", type=int)
    step = add("step", cmd_step)
    step.add_argument(
        "--role", choices=["bind", "record-validation", "finalize", "reconcile", "candidate-pr-record"], required=True
    )
    step.add_argument("--issue", type=int)
    step.add_argument("--advisory", default="")
    # ADR 0041 F2: the primary executor job's closed fallback signals, and the fallback job's attestation digest.
    step.add_argument("--stderr-category", default="")
    step.add_argument("--probe-category", default="")
    # ADR 0041 F8: the armed canary's gateway refusal outputs from the primary executor job.
    step.add_argument("--canary-primary-refusals", default="")
    step.add_argument("--canary-refusal-digest", default="")
    # ADR 0041 F5a: the fallback job's requested/observed model report.
    step.add_argument("--fallback-model", default="")
    step.add_argument("--attestation-sha256", default="")
    step.add_argument("--job", choices=["bind-fallback"])
    resume = add("resume-bind", cmd_resume_bind)
    for child in (resume,):
        child.add_argument("--issue", type=int, required=True)
        child.add_argument("--authorization-id", required=True)
    resume.add_argument("--stage", choices=sorted(state.RESUME_STAGES), required=True)
    resume.add_argument("--nonce", required=True)
    bound = add("bound", cmd_bound)
    execute = add("execute", cmd_execute)
    execute_fallback = add("execute-fallback", cmd_execute_fallback)
    fallback_decision = add("fallback-decision", cmd_fallback_decision)
    fallback_decision.add_argument("--event", required=True)
    validate = add("validate", cmd_validate)
    publish = add("publish", cmd_publish)
    for child in (bound, execute, execute_fallback, validate, publish):
        child.add_argument("--issue", type=int, required=True)
        child.add_argument("--authorization-id", required=True)
    execute.add_argument("--handoff", required=True)
    execute.add_argument("--out", required=True)
    execute.add_argument("--workroot", required=True)
    execute.add_argument("--model-key-name", required=True)
    execute_fallback.add_argument("--handoff", required=True)
    execute_fallback.add_argument("--out", required=True)
    execute_fallback.add_argument("--model", required=True)
    # `--model-argv <command> [args...]` is not an argparse option: it is the tail boundary handled by
    # `parse_arguments`, so the model command's own options never reach the lifecycle parser.
    execute.epilog = "The model command follows --model-argv and must be the last words: --model-argv COMMAND [ARGS...]"
    for child in (validate, publish):
        child.add_argument("--result", required=True)
        child.add_argument("--trusted-repo", required=True)
        child.add_argument("--out", required=True)
    publish.add_argument("--workroot", required=True)
    ingest = add("knowledge-ingest", cmd_knowledge_ingest)
    ingest.add_argument("--pr", type=int)
    assess = add("historical-assess", cmd_historical_assess)
    assess.add_argument("--pr", type=int, required=True)
    historical = add("historical-coverage", cmd_historical_coverage)
    historical.add_argument("--pr", type=int, action="append", required=True)
    context_check = add("knowledge-context-check", cmd_knowledge_context_check)
    context_check.add_argument("--path", required=True)
    context_check.add_argument("--base-sha", required=True)
    remediate = add("remediate", cmd_remediate)
    remediate.add_argument("--issue", type=int)
    resolve = add("resolve-finding", cmd_resolve)
    resolve.add_argument("--issue", type=int)
    gate = add("candidate-gate", cmd_candidate_gate)
    gate.add_argument("--branch", required=True)
    gate.add_argument("--head-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        # First, before any secret, ledger or network access: an unprovisioned repository refuses here.
        configuration = control.load_configuration(Path(arguments.checkout))
        return int(arguments.handler(configuration, arguments))
    except (control.ControlRefused, LifecycleRefused, authorize.AuthorizeRefused, roles.RoleRefused) as error:
        print(f"issue-agent lifecycle refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except control.FactsUnavailable as error:
        print(f"issue-agent lifecycle no-op: {error}", file=sys.stderr)
        return (
            EXIT_NOOP
            if arguments.command in ("step", "knowledge-ingest", "remediate", "resolve-finding")
            else EXIT_REFUSED
        )


if __name__ == "__main__":
    raise SystemExit(main())
