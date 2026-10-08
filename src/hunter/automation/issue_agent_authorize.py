"""Canonical ``authorize`` composition of the GitHub-native Issue Agent lifecycle (ADR 0037 T1, D4, D7; ADR 0038).

This adds no authority. It composes the existing ones in their canonical order, then commits the result
to the anchored ledgers:

1. the issuer-signed authorization (``verify_signed_authorization``);
2. the TaskScope and derived execution target;
3. the Source Handling ledger, materialized and canonically provisioned with each ADR 0036 transaction
   captured, then the preflight run **before** any claim (DFF-023);
4. ADR 0036 intake → ``GovernedEngineeringTaskIngress`` → ``SmartPromptMachine`` →
   ``EngineeringContextAuthority``/DPM;
5. verification of the signed handoff.

The model-facing payload is sealed to the executor before it leaves the job. That payload is the signed
lineage handoff plus the exact prompt artifact.

It runs in two phases around the workflow's artifact-upload step:

* ``prepare``: everything above, ending with a sealed envelope file and a non-secret ``Prepared`` record.
  Nothing durable is written.
* ``commit``: binds the uploaded artifact by its GitHub-reported identity, appends the captured Source
  Handling transactions, then appends ``AUTHORIZED`` by compare-and-swap. This single CAS is the claim
  and the model release.

Refusals before ``AUTHORIZED`` write nothing durable. A CAS conflict makes the whole authorization retry
from ``prepare``; provisioning is idempotent and resumes interrupted batches.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from hunter.automation import issue_agent_source_handling_store as sh
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorizationVerifier,
    IssueAgentExecutionConfiguration,
    IssueAgentRemediationAuthorization,
    SignedIssueAgentAuthorization,
    build_production_source_handling_resolver,
    compose_governed_compilation,
    derive_execution_target,
    issue_agent_intake_reference,
    issue_agent_task_request,
    verify_signed_authorization,
)
from hunter.automation.issue_agent_transport import TransportBinding, recipient_key_id, seal
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.engineering_context_authority import (
    ENGINEERING_IMPLEMENT_TASK_KEY,
    EngineeringContextAuthority,
    EngineeringContextAuthorityError,
)
from hunter.evidence_intelligence.intake import evidence_document_id
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier
from hunter.evidence_intelligence.source_handling_persistence import (
    ProvenanceResolver,
    SourceHandlingOperatorRoot,
)
from hunter.execution import Clock, SystemClock

HANDOFF_BUNDLE_SCHEMA_VERSION: Final = "hunter-issue-agent-handoff-bundle-v1"
PREPARED_SCHEMA_VERSION: Final = "hunter-issue-agent-prepared-authorization-v1"
LIFECYCLE_PUBLISH_DEADLINE: Final = timedelta(hours=6)
DEFAULT_ADMISSION_CAP: Final = 2


def _engineering_context_refusal(error: EngineeringContextAuthorityError) -> AuthorizeRefused:
    """Expose only authority-owned machine codes, never untrusted exception prose."""
    return AuthorizeRefused("COMPILATION_REFUSED", f"EngineeringContextAuthorityError/{error.reason_code}")


class AuthorizeRefused(RuntimeError):
    """A pre-authorization refusal. Nothing durable was written (state machine spec section 5)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class RunContext:
    """The trusted lifecycle run executing ``authorize`` (bound into the ledger's provenance)."""

    workflow_path: str
    run_id: int
    run_attempt: int
    control_sha: str
    recorded_at: str
    job: str = "authorize"

    def recorded_by(self) -> dict[str, Any]:
        return {
            "workflow_path": self.workflow_path,
            "job": self.job,
            "role": "authorize",
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "head_sha": self.control_sha,
        }


@dataclass(frozen=True, slots=True)
class AuthorizeDependencies:
    """Everything ``authorize`` needs, injected so no configuration is read from Issue text or the model."""

    repository: str
    repository_id: int
    owner_login: str
    issuer_verifier: IssueAgentAuthorizationVerifier
    prompt_verifier: PromptAutomationVerifier
    source_handling_verification_key: bytes
    source_handling_operator_root: SourceHandlingOperatorRoot
    provenance_resolver: ProvenanceResolver
    #: Canonical per-Issue Source Handling provisioning bound to the given evidence database.
    provision: Callable[[SignedIssueAgentAuthorization, Path], object]
    trust: state.TrustRoots
    ledger_provenance: state.ProvenanceCheck
    state_signing_key: Ed25519PrivateKey
    handoff_recipient: X25519PublicKey
    #: Definitive "is an Issue-Agent PR open for this Issue" fact; raises on an indefinite observation.
    open_issue_agent_pull_request: Callable[[int], bool]
    #: Definitive count of active lifecycles across Issues; raises on an indefinite observation.
    active_lifecycles: Callable[[], int]
    compiler_identity_sha256: str
    admission_cap: int = DEFAULT_ADMISSION_CAP
    #: Verified anchored-knowledge overlay (ADR 0039 L2): prevention knowledge reaches the task before its model.
    knowledge_overlay: Sequence[Mapping[str, Any]] = ()
    clock: Clock = field(default_factory=SystemClock)


@dataclass(frozen=True, slots=True)
class Prepared:
    """Non-secret output of ``prepare``: digests, bindings and captured Source Handling transactions."""

    issue_number: int
    authorization_id: str
    unsigned_authorized: dict[str, Any]
    state_head: str | None
    source_handling_position: dict[str, Any]
    source_handling_transactions: list[dict[str, Any]]
    handoff_binding: dict[str, Any]
    handoff_ciphertext_sha256: str
    schema_version: str = PREPARED_SCHEMA_VERSION

    def to_json(self) -> str:
        return state.canonical_json(asdict(self)).decode("utf-8")

    @classmethod
    def from_json(cls, document: str | bytes) -> Prepared:
        payload = json.loads(document)
        if payload.get("schema_version") != PREPARED_SCHEMA_VERSION:
            raise AuthorizeRefused("MISSING_CONFIGURATION", "prepared authorization has an unknown schema")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class UploadedArtifact:
    """The handoff artifact as **reported by the GitHub API** after upload (never the executor's word)."""

    run_id: int
    artifact_id: int
    name: str
    artifact_digest: str
    expired: bool = False


def compiler_identity(*, control_sha: str, checkout: Path) -> str:
    """D4 compiler identity: control commit, interpreter, pinned constraints and the compiling modules."""

    def digest(relative: str) -> str:
        return hashlib.sha256((checkout / relative).read_bytes()).hexdigest()

    return state.sha256_hex(
        state.canonical_json(
            {
                "control_sha": control_sha,
                "python": ".".join(str(part) for part in sys.version_info[:3]),
                "constraints_sha256": digest("requirements/ci-constraints.txt"),
                "smart_prompt_machine_sha256": digest("src/hunter/evidence_intelligence/smart_prompt_machine.py"),
                "smart_prompt_routing_sha256": digest("src/hunter/evidence_intelligence/smart_prompt_routing.py"),
                "engineering_context_authority_sha256": digest(
                    "src/hunter/evidence_intelligence/engineering_context_authority.py"
                ),
                "defect_registry_sha256": digest("docs/DEFECT_REGISTRY.json"),
            }
        )
    )


def _plus(timestamp: str, delta: timedelta) -> str:
    return (state._instant(timestamp) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _definitive(probe: Callable[[], Any], code: str) -> Any:
    try:
        return probe()
    except Exception as error:  # an indefinite observation never becomes a negative fact
        raise AuthorizeRefused(
            code, f"precondition could not be observed definitively ({type(error).__name__})"
        ) from None


def prepare(
    document: bytes,
    *,
    dependencies: AuthorizeDependencies,
    context: RunContext,
    state_store: state.GitLedgerStore,
    source_handling_store: state.GitLedgerStore,
    workdir: Path,
) -> tuple[Prepared, bytes]:
    """Run the canonical pre-model composition. Returns the prepared record and the sealed handoff."""

    if context.run_attempt != 1:
        raise AuthorizeRefused("RERUN_REFUSED", "authorization never runs on a workflow re-run")
    signed = SignedIssueAgentAuthorization.from_json(document)
    authorization = verify_signed_authorization(
        signed,
        issuer_verifier=dependencies.issuer_verifier,
        repository=dependencies.repository,
        owner_login=dependencies.owner_login,
    )
    scope = signed.implementation_scope
    incomplete = scope.incompleteness()
    if incomplete:
        raise AuthorizeRefused("SCOPE_INCOMPLETE", incomplete)
    try:
        target = derive_execution_target(signed)
    except Exception as error:
        raise AuthorizeRefused("SCOPE_INCOMPLETE", str(error)) from None

    issue = authorization.issue_number
    head, entries = state_store.read(issue)
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=dependencies.repository_id,
        issue_number=issue,
        trust=dependencies.trust,
        provenance=dependencies.ledger_provenance,
        indexes=[entry.index for entry in entries],
    )
    if authorization.authorization_id in view.claimed:
        raise AuthorizeRefused("DUPLICATE_AUTHORIZATION", "this authorization identity was already claimed")
    if view.active is not None:
        raise AuthorizeRefused("ISSUE_EXECUTION_ACTIVE", "another authorization for this Issue is active")
    remediation = authorization.remediation if isinstance(authorization, IssueAgentRemediationAuthorization) else None
    if remediation is not None:
        parent = view.authorizations.get(remediation["parent_authorization_id"])
        # ADR 0039 L4: the open PR *is* the remediation's target, so ISSUE_HAS_ACTIVE_DRAFT_PR does not apply;
        # the parent must be this Issue's completed authorization on exactly that branch.
        if parent is None or parent.state != state.COMPLETED or parent.binding("execution_branch") != target.branch:
            raise AuthorizeRefused("NOT_ELIGIBLE", "the remediation parent is not this Issue's completed authorization")
    elif _definitive(lambda: dependencies.open_issue_agent_pull_request(issue), "ISSUE_HAS_ACTIVE_DRAFT_PR"):
        raise AuthorizeRefused("ISSUE_HAS_ACTIVE_DRAFT_PR", "an Issue-Agent pull request is open for this Issue")
    if _definitive(dependencies.active_lifecycles, "ADMISSION_CAP_REACHED") >= dependencies.admission_cap:
        raise AuthorizeRefused("ADMISSION_CAP_REACHED", "the concurrent lifecycle cap is reached")

    workdir.mkdir(parents=True, exist_ok=True)
    evidence = workdir / "evidence.sqlite"
    try:
        position = sh.materialize(
            source_handling_store,
            evidence,
            trust=dependencies.trust,
            provenance=dependencies.ledger_provenance,
            verification_public_key=dependencies.source_handling_verification_key,
            operator_root=dependencies.source_handling_operator_root,
        )
        _, transactions = sh.capture(evidence, lambda: dependencies.provision(signed, evidence))
        resolver = build_production_source_handling_resolver(
            IssueAgentExecutionConfiguration(
                repository=dependencies.repository,
                owner_login=dependencies.owner_login,
                evidence_database=evidence,
                repository_checkout=workdir,
                source_handling_verification_key=dependencies.source_handling_verification_key,
                source_handling_operator_root=dependencies.source_handling_operator_root,
            ),
            provenance_resolver=dependencies.provenance_resolver,
        )
        # The hosted lifecycle installs the package non-editably.  Module-relative
        # defaults then point into site-packages, not the checked-out repository.
        # The workflow executes from the trusted checkout root; bind DPM to its
        # canonical registry there and retain fail-closed registry validation.
        dpm = EngineeringContextAuthority(
            registry_path=Path.cwd() / "docs" / "DEFECT_REGISTRY.json",
            knowledge_overlay=dependencies.knowledge_overlay,
        )
        composed = compose_governed_compilation(
            repository=EvidenceIntelligenceRepository(evidence),
            source_handling_resolver=resolver,
            clock=dependencies.clock,
            engineering_context_authority=dpm,
        )
        reference = issue_agent_intake_reference(authorization)
        document_id = evidence_document_id(reference)
        request = issue_agent_task_request(authorization)
        if request.document_id != document_id:
            raise AuthorizeRefused("SOURCE_HANDLING_BLOCKED", "task request does not bind the ingested document")
        composed.boundary.preflight(
            reference, processing_run_id=authorization.authorization_id, processed_at=dependencies.clock.now()
        )
        composed.boundary.ingest(
            reference, processing_run_id=authorization.authorization_id, processed_at=dependencies.clock.now()
        )
        compiled = composed.ingress.compile(request, implementation_scope=scope)
    except AuthorizeRefused:
        raise
    except EngineeringContextAuthorityError as error:
        # The authority owns the machine code; never parse attacker-controlled
        # identifiers or echo the raw exception to public Actions logs.
        raise _engineering_context_refusal(error) from None
    except state.LedgerError:
        raise
    except Exception as error:
        code = "SOURCE_HANDLING_BLOCKED" if "SourceHandling" in type(error).__name__ else "COMPILATION_REFUSED"
        raise AuthorizeRefused(code, type(error).__name__) from None
    envelope = compiled.envelope
    envelope.verify_issuer_signature(dependencies.prompt_verifier)
    if envelope.build_record_id != compiled.compilation.manifest.build_record_id:
        raise AuthorizeRefused("COMPILATION_REFUSED", "signed envelope and persisted build refer to different lineage")
    artifact = compiled.compilation.orchestration.build_result.prompt_artifact
    if artifact is None:
        raise AuthorizeRefused("COMPILATION_REFUSED", "no concrete prompt artifact")

    handoff_document = serialize_prompt_automation_handoff(envelope)
    bundle = state.canonical_json(
        {
            "schema_version": HANDOFF_BUNDLE_SCHEMA_VERSION,
            "authorization_id": authorization.authorization_id,
            "handoff_document": handoff_document,
            "prompt_artifact_id": artifact.artifact_id,
            "prompt": artifact.content,
        }
    )
    handoff_sha256 = state.sha256_hex(bundle)
    prompt_sha256 = state.sha256_hex(artifact.content.encode("utf-8"))
    dpm_context_sha256 = state.sha256_hex(
        dpm.canonical_json(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope).encode("utf-8")
    )
    task_scope = {
        "task_id": scope.task_id,
        "branch_pattern": scope.branch_pattern,
        "base_ref": scope.base_ref,
        "base_sha": scope.base_sha,
        "allowed_paths": list(scope.allowed_paths),
        "prohibited_paths": list(scope.prohibited_paths),
    }
    task_scope_sha256 = state.sha256_hex(state.canonical_json(task_scope))
    claims = {
        "owner_login": authorization.authorized_by,
        "label": authorization.authorization_label,
        "issue_updated_at": authorization.issue_updated_at,
        "schema_version": authorization.schema_version,
        "title_sha256": state.sha256_hex(authorization.issue_title.encode("utf-8")),
        "body_sha256": state.sha256_hex(authorization.issue_body.encode("utf-8")),
    }
    source_handling_record_ids = sorted(
        str(row["record_id"])
        for transaction in transactions
        for row in transaction.delta.get("source_handling_authority_records", [])
    )
    manifest = state.sha256_hex(
        state.canonical_json(
            {
                "authorization_id": authorization.authorization_id,
                "claims": claims,
                "task_scope_sha256": task_scope_sha256,
                "base_sha": target.base_sha,
                "document_id": document_id,
                "source_handling_record_ids": source_handling_record_ids,
                "route_registry_identity": envelope.route_registry_identity,
                "profile_registry_identity": envelope.profile_registry_identity,
                "route_identity": envelope.route_identity,
                "profile_identity": envelope.profile_identity,
                "dpm_context_sha256": dpm_context_sha256,
                "cutoff": context.recorded_at,
            }
        )
    )
    execution_id = state.execution_identity(
        authorization_id=authorization.authorization_id,
        authorize_run_id=context.run_id,
        control_sha=context.control_sha,
        handoff_sha256=handoff_sha256,
    )
    binding = TransportBinding(
        payload_kind="handoff",
        repository_id=dependencies.repository_id,
        issue_number=issue,
        authorization_id=authorization.authorization_id,
        base_sha=target.base_sha,
        task_scope_sha256=task_scope_sha256,
        execution_id=execution_id,
        handoff_sha256=handoff_sha256,
        plaintext_sha256=handoff_sha256,
        recipient_key_id=recipient_key_id(dependencies.handoff_recipient),
    )
    envelope_bytes = seal(bundle, recipient=dependencies.handoff_recipient, binding=binding)
    unsigned_evidence = {
        "authorization_envelope_sha256": state.sha256_hex(signed.to_json().encode("utf-8")),
        "claims": claims,
        "task_scope": task_scope,
        "task_scope_sha256": task_scope_sha256,
        "execution_branch": target.branch,
        "base_sha": target.base_sha,
        "control_sha": context.control_sha,
        "authorize_run_id": context.run_id,
        "execution_id": execution_id,
        "prompt_input_manifest_sha256": manifest,
        "compiler_identity_sha256": dependencies.compiler_identity_sha256,
        "deadline_published_at": _plus(context.recorded_at, LIFECYCLE_PUBLISH_DEADLINE),
        "lineage": {
            "document_id": document_id,
            "build_record_id": envelope.build_record_id,
            "envelope_id": envelope.envelope_id,
            "prompt_artifact_id": artifact.artifact_id,
            "prompt_sha256": prompt_sha256,
            "handoff_sha256": handoff_sha256,
            "dpm_context_sha256": dpm_context_sha256,
            "source_handling_record_ids": source_handling_record_ids,
            "reconstruction": "EXACT_RECONSTRUCTION_UNAVAILABLE",
            "reconstruction_reason": "NO_CONFIDENTIAL_DURABLE_STORE",
        },
    }
    if remediation is not None:
        unsigned_evidence["remediation"] = {
            "parent_authorization_id": remediation["parent_authorization_id"],
            "pull_request_number": remediation["pull_request_number"],
            "bound_head_sha": remediation["bound_head_sha"],
            "finding_ids": [item["finding_id"] for item in remediation["findings"]],
            "attempt": remediation["attempt"],
        }
    prepared = Prepared(
        issue_number=issue,
        authorization_id=authorization.authorization_id,
        unsigned_authorized=unsigned_evidence,
        state_head=head,
        source_handling_position={
            "head": position.head,
            "next_seq": position.next_seq,
            "prev_digest": position.prev_digest,
            "snapshot_sha256": position.snapshot_sha256,
        },
        source_handling_transactions=[
            {"delta": t.delta, "snapshot_sha256": t.snapshot_sha256, "history_sequence": t.history_sequence}
            for t in transactions
        ],
        handoff_binding=binding.as_dict(),
        handoff_ciphertext_sha256=state.sha256_hex(envelope_bytes),
    )
    return prepared, envelope_bytes


def commit(
    prepared: Prepared,
    *,
    uploaded: UploadedArtifact,
    dependencies: AuthorizeDependencies,
    context: RunContext,
    state_store: state.GitLedgerStore,
    source_handling_store: state.GitLedgerStore,
) -> str:
    """Bind the uploaded handoff, persist Source Handling, then CAS ``AUTHORIZED`` (claim + model release)."""

    if context.run_attempt != 1:
        raise AuthorizeRefused("RERUN_REFUSED", "authorization never runs on a workflow re-run")
    if (
        uploaded.run_id != context.run_id
        or uploaded.name != state.handoff_artifact_name(prepared.authorization_id)
        or uploaded.expired
        or state._ARTIFACT_DIGEST.fullmatch(uploaded.artifact_digest) is None
    ):
        raise AuthorizeRefused("TRANSPORT_INTEGRITY_FAILED", "uploaded handoff artifact does not bind this run")
    position = sh.LedgerPosition(
        prepared.source_handling_position["head"],
        prepared.source_handling_position["next_seq"],
        prepared.source_handling_position["prev_digest"],
        prepared.source_handling_position["snapshot_sha256"],
    )
    sh.publish(
        source_handling_store,
        position,
        [
            sh.CapturedTransaction(t["delta"], t["snapshot_sha256"], t["history_sequence"])
            for t in prepared.source_handling_transactions
        ],
        signing_key=dependencies.state_signing_key,
        recorded_by=context.recorded_by(),
        recorded_at=context.recorded_at,
        repository_id=dependencies.repository_id,
    )
    head, entries = state_store.read(prepared.issue_number)
    if head != prepared.state_head:
        raise state.LedgerConflictError("the Issue ledger moved since prepare; re-run authorize")
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=dependencies.repository_id,
        issue_number=prepared.issue_number,
        trust=dependencies.trust,
        provenance=dependencies.ledger_provenance,
        indexes=[entry.index for entry in entries],
    )
    evidence = {
        **prepared.unsigned_authorized,
        "handoff_artifact": {
            "run_id": uploaded.run_id,
            "artifact_id": uploaded.artifact_id,
            "artifact_digest": uploaded.artifact_digest,
            "ciphertext_sha256": prepared.handoff_ciphertext_sha256,
            "aad_sha256": TransportBinding(**prepared.handoff_binding).digest(),
            "recipient_key_id": prepared.handoff_binding["recipient_key_id"],
        },
    }
    record = state.sign_record(
        {
            "schema_version": state.RECORD_SCHEMA_VERSION,
            "kind": "transition",
            "record_seq": view.next_seq,
            "prev_record_sha256": view.head_record_digest,
            "recorded_at": context.recorded_at,
            "recorded_by": context.recorded_by(),
            "repository_id": dependencies.repository_id,
            "issue_number": prepared.issue_number,
            "authorization_id": prepared.authorization_id,
            "state": state.AUTHORIZED,
            "evidence": evidence,
        },
        dependencies.state_signing_key,
    )
    state.apply_record(view, record, trust=dependencies.trust, provenance=dependencies.ledger_provenance)
    return state_store.append(prepared.issue_number, head, record, view.index())


__all__: Sequence[str] = [
    "AuthorizeDependencies",
    "AuthorizeRefused",
    "Prepared",
    "RunContext",
    "UploadedArtifact",
    "commit",
    "compiler_identity",
    "prepare",
]
