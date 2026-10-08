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
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
RESULT_KEY_ENV = "HUNTER_ISSUE_AGENT_RESULT_KEY"
MODEL_KEY_ENV = "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY"
PUSH_TOKEN_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN"
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


def _ledger_access(configuration: control.Configuration) -> roles.LedgerAccess:
    github = _github(configuration, authenticated=False)
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


def cmd_step(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    workflow = {
        "reconcile": control.RECONCILE_WORKFLOW,
        "candidate-pr-record": control.CANDIDATE_PR_WORKFLOW,
    }.get(arguments.role, control.LIFECYCLE_WORKFLOW)
    job = {"candidate-pr-record": "record"}.get(arguments.role, arguments.role)
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
            )
            print(f"issue {issue}: {decision.action} {decision.target_state or decision.code or ''}".rstrip())
            if decision.target_state == state.VALIDATED and job == "record-validation":
                _record_proven_classification(configuration, github, writer, issue)
        except control.FactsUnavailable as error:
            print(f"issue {issue}: no-op ({error})")
    return 0


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
    """ADR 0039 L1/L3.1 fast path: record every trusted-reviewer finding once, with its deterministic mapping."""

    import hunter_collect_learning_observations as collector

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
        if not isinstance(pull, Mapping) or pull.get("state") != "open":
            continue
        head, base = str(pull["head"]["sha"]), str(pull["base"]["sha"])
        observations = collector.collect(configuration.repository, token, number, head, base)
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


def cmd_remediate(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    """ADR 0039 L4 (RD-4/RD-5): detect an eligible lifecycle PR and dispatch one bounded remediation.

    At most one authorization is minted per Issue per pass, and it is minted from the *same* claims every time,
    so a duplicate pass, a lost dispatch or a crash recomputes the identical identity and loses the claim in
    ``authorize`` instead of starting a second lifecycle. Nothing here reads Issue text for scope or routing:
    the findings come from the verified anchored knowledge ledger and the head from a GitHub observation.
    """

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
                github, configuration, ledger_view, _knowledge_view(configuration, github), issue
            )
            if candidate is None:
                print(f"issue {issue}: no eligible remediation")
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
                print(f"issue {issue}: resolved {finding_id[:12]} at {proof.remediated_head_sha[:12]}")
            except control.FactsUnavailable as error:
                print(f"issue {issue}: no-op ({error})")
            except state.LedgerConflictError:
                print(f"issue {issue}: no-op (the knowledge ledger moved; the next pass re-decides)")
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
    prepared, sealed = authorize.prepare(
        document,
        dependencies=_authorize_dependencies(
            configuration, github, repository_checkout=Path(arguments.checkout).resolve()
        ),
        context=context,
        state_store=_store(configuration, authenticated=True),
        source_handling_store=_store(configuration, authenticated=True),
        workdir=out / "work",
    )
    (out / "handoff.sealed").write_bytes(sealed)
    (out / "prepared.json").write_text(prepared.to_json(), encoding="utf-8")
    (out / "recorded_at").write_text(context.recorded_at, encoding="utf-8")
    _output(
        authorization_id=prepared.authorization_id,
        issue=prepared.issue_number,
        handoff_artifact=state.handoff_artifact_name(prepared.authorization_id),
    )
    return 0


def cmd_authorize_commit(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    run_id, _, _ = _run_context()
    _export_public_trust(configuration)
    out = Path(arguments.out_dir)
    os.environ["HUNTER_ISSUE_AGENT_RECORDED_AT"] = (out / "recorded_at").read_text(encoding="utf-8")
    prepared = authorize.Prepared.from_json((out / "prepared.json").read_bytes())
    github = _github(configuration)
    _require_anchors(configuration, github, prepared.issue_number)
    item = control.definitive(
        github.get(f"/repos/{configuration.repository}/actions/artifacts/{arguments.artifact_id}")
    )
    if not isinstance(item, Mapping):
        raise LifecycleRefused("TRANSPORT_INTEGRITY_FAILED", "the uploaded handoff artifact is not readable back")
    uploaded = authorize.UploadedArtifact(
        run_id=int((item.get("workflow_run") or {}).get("id", 0)),
        artifact_id=int(item["id"]),
        name=str(item.get("name")),
        artifact_digest=str(item.get("digest") or ""),
        expired=bool(item.get("expired")),
    )
    head = authorize.commit(
        prepared,
        uploaded=uploaded,
        dependencies=_authorize_dependencies(configuration, github),
        context=_authorize_context(),
        state_store=_store(configuration, authenticated=True),
        source_handling_store=_store(configuration, authenticated=True),
    )
    print(f"AUTHORIZED {prepared.authorization_id} at {head}")
    return 0


# --- role jobs -----------------------------------------------------------------------------------------------


def _view(configuration: control.Configuration, issue: int, authorization_id: str) -> state.AuthorizationView:
    access = _ledger_access(configuration)
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


def cmd_execute(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
    from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

    run_id, attempt, _ = _run_context()
    os.environ["HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY"] = configuration.prompt_verifying_key
    model_key = _secret(MODEL_KEY_ENV)
    outcome = roles.run_executor(
        issue=arguments.issue,
        authorization_id=arguments.authorization_id,
        context=roles.RoleContext(run_id, attempt),
        ledger=_ledger_access(configuration),
        handoff_envelope=Path(arguments.handoff).read_bytes(),
        config=roles.ExecutorConfig(
            remote=_remote(configuration),
            model_argv=tuple(arguments.model_argv),
            model_public_env={"LANG": "C.UTF-8"},
            model_secret_env={arguments.model_key_name: model_key},
            handoff_key=_x25519(HANDOFF_KEY_ENV),
            result_recipient=configuration.result_recipient,
            prompt_verifier=PromptAutomationVerifier.from_environment(),
        ),
        isolation=roles.SudoIsolation(ISOLATION_USER),
        workroot=Path(arguments.workroot),
    )
    if outcome.sealed_result is not None:
        Path(arguments.out).write_bytes(outcome.sealed_result)
    _output(
        advisory_code=outcome.advisory_code or "",
        sealed="true" if outcome.sealed_result else "false",
        result_artifact=state.result_artifact_name(arguments.authorization_id),
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
    view = _view(configuration, arguments.issue, arguments.authorization_id)
    try:
        receipt = roles.run_validator(
            issue=arguments.issue,
            authorization_id=arguments.authorization_id,
            repository=configuration.repository,
            ledger=_ledger_access(configuration),
            result_envelope=Path(arguments.result).read_bytes(),
            result_key=_x25519(RESULT_KEY_ENV),
            trusted_repo=Path(arguments.trusted_repo),
            isolation_user=ISOLATION_USER,
            writer=configuration.writer,
            validation_definition=_digest_files(VALIDATION_DEFINITION_FILES),
            toolchain_sha256=_digest_files(TOOLCHAIN_FILES, extra=sys.version),
            remediation=_promotion_port(configuration, _github(configuration, authenticated=False)),
        )
    except roles.RoleRefused as error:
        if error.code not in state.VALIDATION_REFUSAL_CODES:
            raise
        bound = view.evidence[state.AUTHORIZED]
        receipt = {
            "schema_version": roles.RECEIPT_SCHEMA_VERSION,
            "authorization_id": arguments.authorization_id,
            "execution_id": bound["execution_id"],
            "ciphertext_sha256": view.evidence[state.RESULT_BOUND]["result_artifact"]["ciphertext_sha256"],
            "verdict": "REFUSED",
            "code": error.code,
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


def cmd_publish(configuration: control.Configuration, arguments: argparse.Namespace) -> int:
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
            ledger=_ledger_access(configuration),
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
            derive_promotion=_promotion_port(configuration, _github(configuration, authenticated=False)).promote,
        )
    except roles.RoleRefused as error:
        if error.code not in state.PUBLICATION_REFUSAL_CODES:
            raise
        _write_outcome(
            arguments.out,
            {
                "schema_version": control.OUTCOME_SCHEMA_VERSION,
                "authorization_id": arguments.authorization_id,
                "verdict": "REFUSED",
                "code": error.code,
            },
        )
        _output(refused="true")
        print(f"publication refused: {error.code}")
        return 0
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
    step = add("step", cmd_step)
    step.add_argument(
        "--role", choices=["bind", "record-validation", "finalize", "reconcile", "candidate-pr-record"], required=True
    )
    step.add_argument("--issue", type=int)
    step.add_argument("--advisory", default="")
    resume = add("resume-bind", cmd_resume_bind)
    for child in (resume,):
        child.add_argument("--issue", type=int, required=True)
        child.add_argument("--authorization-id", required=True)
    resume.add_argument("--stage", choices=sorted(state.RESUME_STAGES), required=True)
    resume.add_argument("--nonce", required=True)
    bound = add("bound", cmd_bound)
    execute = add("execute", cmd_execute)
    validate = add("validate", cmd_validate)
    publish = add("publish", cmd_publish)
    for child in (bound, execute, validate, publish):
        child.add_argument("--issue", type=int, required=True)
        child.add_argument("--authorization-id", required=True)
    execute.add_argument("--handoff", required=True)
    execute.add_argument("--out", required=True)
    execute.add_argument("--workroot", required=True)
    execute.add_argument("--model-key-name", required=True)
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
