"""ADR 0037 Slice 3b: the canonical authorize composition over the anchored ledgers (T1, D4, D7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hunter_issue_agent_provisioner as provisioner
import hunter_issue_agent_trigger as trigger
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from issue_agent_wire import issue_body_with_scope
from test_issue_agent_source_handling_store import (  # shared real-SH helpers
    OWNER,
    REPOSITORY,
    STATE_KEY,
    TRUST,
    UPDATED_AT,
    bootstrap_ledger,
    build_world,
    store,
    trusted,
)

from hunter.automation import issue_agent_authorize as authorize
from hunter.automation import issue_agent_state as state
from hunter.automation import issue_agent_transport as transport
from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
)
from hunter.evidence_intelligence import source_handling_provenance
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier, PromptTaskAuthorityError
from hunter.evidence_intelligence.source_handling_provenance import production_provenance_resolver

ROOT = Path(__file__).resolve().parents[1]
ISSUER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("33" * 32))
RECIPIENT = X25519PrivateKey.generate()
CONTROL = "c" * 40


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return build_world(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
    )


def document(
    number: int = 520, body: str = "Create docs/ISSUE_AGENT_CANARY.md.", signing_key: Any = ISSUER_KEY
) -> bytes:
    event = {
        "action": "labeled",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "label": {"name": ISSUE_AGENT_AUTHORIZATION_LABEL},
        "issue": {
            "number": number,
            "state": "open",
            "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
            "title": "Canary",
            "body": issue_body_with_scope(body),
            "updated_at": UPDATED_AT,
        },
    }
    authorization = trigger.authorize_event(
        event, expected_repository=REPOSITORY, owner_login=OWNER, authorization_label=ISSUE_AGENT_AUTHORIZATION_LABEL
    )
    return trigger.sign_authorization(authorization, signing_key=signing_key).to_json().encode()


def context(attempt: int = 1, run_id: int = 100) -> authorize.RunContext:
    return authorize.RunContext(
        workflow_path=".github/workflows/hunter-issue-agent-trigger.yml",
        run_id=run_id,
        run_attempt=attempt,
        control_sha=CONTROL,
        recorded_at="2026-10-04T12:00:00Z",
    )


def dependencies(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, **overrides: Any
) -> authorize.AuthorizeDependencies:
    def provision(signed: Any, database: Path) -> object:
        monkeypatch.setenv(EVIDENCE_DATABASE_ENV, str(database))
        monkeypatch.setattr(source_handling_provenance, "_production_view", None)
        return provisioner.provision_issue_authority(provisioner.ProvisionerConfiguration.from_environment(), signed)

    options: dict[str, Any] = dict(
        repository=REPOSITORY,
        repository_id=1,
        owner_login=OWNER,
        issuer_verifier=IssueAgentAuthorizationVerifier.from_environment(),
        prompt_verifier=PromptAutomationVerifier.from_environment(),
        source_handling_verification_key=world["verification_key"],
        source_handling_operator_root=world["operator_root"],
        provenance_resolver=production_provenance_resolver,
        provision=provision,
        trust=TRUST,
        ledger_provenance=trusted,
        state_signing_key=STATE_KEY,
        handoff_recipient=RECIPIENT.public_key(),
        open_issue_agent_pull_request=lambda _issue: False,
        active_lifecycles=lambda: 0,
        compiler_identity_sha256=authorize.compiler_identity(control_sha=CONTROL, checkout=ROOT),
        repository_checkout=ROOT,
    )
    options.update(overrides)
    return authorize.AuthorizeDependencies(**options)


def run_prepare(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, name: str, doc: bytes | None = None, **kw: Any):
    deps = kw.pop("deps", None) or dependencies(world, monkeypatch)
    return (
        authorize.prepare(
            doc or document(),
            dependencies=deps,
            context=kw.pop("ctx", context()),
            state_store=store(world, f"{name}-state"),
            source_handling_store=store(world, f"{name}-sh"),
            workdir=world["tmp"] / f"work-{name}",
        ),
        deps,
    )


def uploaded(prepared: authorize.Prepared, **overrides: Any) -> authorize.UploadedArtifact:
    fields: dict[str, Any] = dict(
        run_id=100,
        artifact_id=55,
        name=state.handoff_artifact_name(prepared.authorization_id),
        artifact_digest="sha256:" + "e" * 64,
    )
    fields.update(overrides)
    return authorize.UploadedArtifact(**fields)


def heads(world: dict[str, Any], issue: int = 520) -> tuple[Any, Any]:
    reader = store(world, "heads")
    return reader.remote_head(issue), reader.ref_head("refs/heads/hunter-state/v1/source-handling")


def test_authorize_composes_the_canonical_path_and_releases_the_model_atomically(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    (prepared, sealed), deps = run_prepare(world, monkeypatch, "ok")
    before = heads(world)
    assert before[0] is None, "prepare writes nothing durable"

    binding = transport.TransportBinding(**prepared.handoff_binding)
    bundle = json.loads(transport.open_sealed(sealed, recipient=RECIPIENT, expected=binding))
    assert bundle["prompt"] and bundle["handoff_document"] and bundle["authorization_id"] == prepared.authorization_id
    lineage = prepared.unsigned_authorized["lineage"]
    assert lineage["prompt_sha256"] == state.sha256_hex(bundle["prompt"].encode())
    assert lineage["reconstruction"] == "EXACT_RECONSTRUCTION_UNAVAILABLE"
    assert lineage["source_handling_record_ids"], "Source Handling records are bound before model release"
    assert b"ISSUE_AGENT_CANARY" not in sealed, "the public envelope carries no plaintext"

    head = authorize.commit(
        prepared,
        uploaded=uploaded(prepared),
        dependencies=deps,
        context=context(),
        state_store=store(world, "ok-commit"),
        source_handling_store=store(world, "ok-commit-sh"),
    )
    _, entries = store(world, "verify").read(520)
    view = state.verify_chain(
        [e.record for e in entries],
        repository_id=1,
        issue_number=520,
        trust=TRUST,
        provenance=trusted,
        indexes=[e.index for e in entries],
    )
    assert head and view.active == prepared.authorization_id
    assert view.authorizations[prepared.authorization_id].state == state.AUTHORIZED
    assert heads(world)[1] != before[1], "the captured Source Handling transactions were appended first"


def test_a_rerun_never_authorizes(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_ledger(world)
    with pytest.raises(authorize.AuthorizeRefused, match="RERUN_REFUSED"):
        run_prepare(world, monkeypatch, "rerun", ctx=context(attempt=2))


def test_a_foreign_issuer_cannot_authorize(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_ledger(world)
    with pytest.raises(IssueAgentAuthorizationError):
        run_prepare(world, monkeypatch, "forged", doc=document(signing_key=Ed25519PrivateKey.generate()))
    assert heads(world)[0] is None


def _authorize_once(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> authorize.Prepared:
    (prepared, _), deps = run_prepare(world, monkeypatch, "first")
    authorize.commit(
        prepared,
        uploaded=uploaded(prepared),
        dependencies=deps,
        context=context(),
        state_store=store(world, "first-commit"),
        source_handling_store=store(world, "first-commit-sh"),
    )
    return prepared


def test_replays_and_concurrent_authorizations_are_refused_before_any_write(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    _authorize_once(world, monkeypatch)
    before = heads(world)
    with pytest.raises(authorize.AuthorizeRefused, match="DUPLICATE_AUTHORIZATION"):
        run_prepare(world, monkeypatch, "replay")
    with pytest.raises(authorize.AuthorizeRefused, match="ISSUE_EXECUTION_ACTIVE"):
        run_prepare(world, monkeypatch, "second", doc=document(body="A different request."))
    assert heads(world) == before


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"open_issue_agent_pull_request": lambda _issue: True}, "ISSUE_HAS_ACTIVE_DRAFT_PR"),
        ({"open_issue_agent_pull_request": lambda _issue: 1 / 0}, "ISSUE_HAS_ACTIVE_DRAFT_PR"),  # indefinite
        ({"active_lifecycles": lambda: 2}, "ADMISSION_CAP_REACHED"),
        ({"active_lifecycles": lambda: (_ for _ in ()).throw(TimeoutError())}, "ADMISSION_CAP_REACHED"),
    ],
)
def test_admission_preconditions_fail_closed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, override: dict[str, Any], code: str
) -> None:
    bootstrap_ledger(world)
    with pytest.raises(authorize.AuthorizeRefused, match=code):
        run_prepare(world, monkeypatch, "pre", deps=dependencies(world, monkeypatch, **override))
    assert heads(world)[0] is None


def test_an_unbootstrapped_source_handling_ledger_blocks(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(authorize.AuthorizeRefused, match="SOURCE_HANDLING_BLOCKED"):
        run_prepare(world, monkeypatch, "noboot")


@pytest.mark.parametrize(
    "override",
    [{"run_id": 999}, {"name": "hunter-ia-handoff-" + "0" * 64}, {"artifact_digest": "md5:abc"}, {"expired": True}],
)
def test_commit_refuses_an_artifact_that_does_not_bind_this_run(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, override: dict[str, Any]
) -> None:
    bootstrap_ledger(world)
    (prepared, _), deps = run_prepare(world, monkeypatch, "art")
    before = heads(world)
    with pytest.raises(authorize.AuthorizeRefused, match="TRANSPORT_INTEGRITY_FAILED"):
        authorize.commit(
            prepared,
            uploaded=uploaded(prepared, **override),
            dependencies=deps,
            context=context(),
            state_store=store(world, "art-c"),
            source_handling_store=store(world, "art-c-sh"),
        )
    assert heads(world) == before


def test_commit_loses_the_cas_when_the_issue_ledger_moved(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    (stale, _), deps = run_prepare(world, monkeypatch, "stale")
    (winner, _), _ = run_prepare(world, monkeypatch, "winner", doc=document(body="Winner request."))
    authorize.commit(
        winner,
        uploaded=uploaded(winner),
        dependencies=deps,
        context=context(),
        state_store=store(world, "w-c"),
        source_handling_store=store(world, "w-c-sh"),
    )
    with pytest.raises(state.LedgerConflictError):
        authorize.commit(
            stale,
            uploaded=uploaded(stale),
            dependencies=deps,
            context=context(),
            state_store=store(world, "s-c"),
            source_handling_store=store(world, "s-c-sh"),
        )


def test_a_handoff_not_signed_by_the_pinned_prompt_issuer_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", "77" * 32)
    foreign_verifier = PromptAutomationVerifier.from_environment()
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
    )
    with pytest.raises(PromptTaskAuthorityError, match="issuer signature mismatch"):
        run_prepare(world, monkeypatch, "spm", deps=dependencies(world, monkeypatch, prompt_verifier=foreign_verifier))
    assert heads(world)[0] is None


def test_commit_refuses_when_only_the_issue_ledger_moved(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolates the Issue-ledger CAS from the Source Handling CAS."""

    from test_issue_agent_state import authorized_evidence

    bootstrap_ledger(world)
    (prepared, _), deps = run_prepare(world, monkeypatch, "iso")
    other = "hunter-issue-agent-authorization:" + "b" * 64
    evidence = authorized_evidence(other, 102)
    by_other = {
        "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
        "job": "authorize",
        "role": "authorize",
        "run_id": 102,
        "run_attempt": 1,
        "head_sha": "c" * 40,
    }
    record = state.sign_record(
        {
            "schema_version": state.RECORD_SCHEMA_VERSION,
            "kind": "transition",
            "record_seq": 0,
            "prev_record_sha256": None,
            "recorded_at": "2026-10-04T12:00:00Z",
            "recorded_by": by_other,
            "repository_id": 1,
            "issue_number": 520,
            "authorization_id": other,
            "state": state.AUTHORIZED,
            "evidence": evidence,
        },
        STATE_KEY,
    )
    view = state.empty_view(1, 520)
    state.apply_record(view, record, trust=TRUST, provenance=trusted)
    store(world, "mover").append(520, None, record, view.index())
    with pytest.raises(state.LedgerConflictError, match="moved since prepare"):
        authorize.commit(
            prepared,
            uploaded=uploaded(prepared),
            dependencies=deps,
            context=context(),
            state_store=store(world, "iso-c"),
            source_handling_store=store(world, "iso-c-sh"),
        )


# --- ADR 0039 L4: finding-driven remediation through the same composition ------------------------------


def _append(
    world: dict[str, Any], prepared: authorize.Prepared, target: str, evidence: dict[str, Any], role: str
) -> None:
    reader = store(world, f"append-{target}")
    head, entries = reader.read(520)
    view = state.verify_chain(
        [e.record for e in entries], repository_id=1, issue_number=520, trust=TRUST, provenance=trusted,
        indexes=[e.index for e in entries],
    )  # fmt: skip
    record = state.sign_record(
        {
            "schema_version": state.RECORD_SCHEMA_VERSION,
            "kind": "transition",
            "record_seq": view.next_seq,
            "prev_record_sha256": view.head_record_digest,
            "recorded_at": "2026-10-04T12:00:00Z",
            "recorded_by": {**context().recorded_by(), "job": role, "role": role},
            "repository_id": 1,
            "issue_number": 520,
            "authorization_id": prepared.authorization_id,
            "state": target,
            "evidence": evidence,
        },
        STATE_KEY,
    )
    state.apply_record(view, record, trust=TRUST, provenance=trusted)
    reader.append(520, head, record, view.index())


def _complete(world: dict[str, Any], prepared: authorize.Prepared, published_head: str) -> None:
    bound = prepared.unsigned_authorized
    artifact = {"run_id": 100, "artifact_id": 22, "artifact_digest": "sha256:" + "e" * 64,
                "ciphertext_sha256": "1" * 64, "aad_sha256": "2" * 64, "recipient_key_id": "3" * 64}  # fmt: skip
    _append(world, prepared, state.RESULT_BOUND, {"result_artifact": artifact, "result_plaintext_sha256": "4" * 64,
            "executor_job_id": 9, "executor_conclusion": "success", "executor_advisory_code": None}, "bind")  # fmt: skip
    validated = {"receipt_sha256": "5" * 64, "result_sha256": "4" * 64, "tree_sha": "6" * 40,
                 "unsigned_commit_sha": "7" * 40, "validation_definition": "8" * 64, "toolchain_sha256": "9" * 64,
                 "validator_run_id": 100, "validation_attempts": 1}  # fmt: skip
    _append(world, prepared, state.VALIDATED, validated, "record-validation")
    identity = state.publication_identity(
        repository_id=1, issue_number=520, authorization_id=prepared.authorization_id, base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"], execution_id=bound["execution_id"], result_sha256="4" * 64,
        tree_sha="6" * 40, unsigned_commit_sha="7" * 40, control_sha=bound["control_sha"], writer_login=OWNER,
    )  # fmt: skip
    _append(world, prepared, state.PUBLISHED, {"writer_login": OWNER, "publication_identity": identity,
            "head_sha": published_head, "commit_verified": True, "publish_attempts": 1,
            "deadline_completed_at": "2026-10-05T12:00:00Z"}, "finalize")  # fmt: skip
    _append(world, prepared, state.COMPLETED, {"pull_request_number": 600, "pull_request_node_id": "PR_x",
            "pull_request_head_sha": published_head, "draft": True, "preflight_run_id": 77,
            "preflight_conclusion": "success"}, "reconcile")  # fmt: skip


def _remediation_document(prepared: authorize.Prepared, bound_head: str, signing_key: Any = ISSUER_KEY) -> bytes:
    from hunter.automation import issue_agent_remediation as remediation

    issue = {
        "number": 520, "state": "open", "html_url": f"https://github.com/{REPOSITORY}/issues/520", "title": "Canary",
        "body": issue_body_with_scope("Create docs/ISSUE_AGENT_CANARY.md."), "updated_at": UPDATED_AT,
        "labels": [{"name": ISSUE_AGENT_AUTHORIZATION_LABEL}],
    }  # fmt: skip
    group = remediation.remediation_group(
        parent_authorization_id=prepared.authorization_id, issue_number=520, pull_request_number=600,
        bound_head_sha=bound_head, attempt=1,
        findings=[{"finding_id": "f" * 64, "path": "docs/ISSUE_AGENT_CANARY.md", "claim": "the canary must say canary."}],
    )  # fmt: skip
    authorization = remediation.remediation_authorization(
        issue, repository=REPOSITORY, owner_login=OWNER, remediation=group
    )
    scope = remediation.remediation_scope(authorization, prepared.unsigned_authorized["task_scope"])
    return remediation.sign_remediation(authorization, scope, signing_key=signing_key).to_json().encode()


def test_a_finding_remediation_authorizes_on_the_completed_parents_open_pr(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    parent = _authorize_once(world, monkeypatch)
    published = "a1" * 20
    _complete(world, parent, published)
    deps = dependencies(world, monkeypatch, open_issue_agent_pull_request=lambda _issue: True)  # the PR is open
    (prepared, sealed), _ = run_prepare(
        world, monkeypatch, "remediate", doc=_remediation_document(parent, published), deps=deps
    )
    evidence = prepared.unsigned_authorized
    assert evidence["execution_branch"] == parent.unsigned_authorized["execution_branch"]
    assert evidence["base_sha"] == published and evidence["task_scope"]["base_sha"] == published
    assert evidence["remediation"] == {
        "parent_authorization_id": parent.authorization_id, "pull_request_number": 600, "bound_head_sha": published,
        "finding_ids": ["f" * 64], "attempt": 1,
    }  # fmt: skip
    bundle = json.loads(
        transport.open_sealed(
            sealed, recipient=RECIPIENT, expected=transport.TransportBinding(**prepared.handoff_binding)
        )
    )
    assert "the canary must say canary." in bundle["prompt"], "the bounded task carries the finding"
    authorize.commit(
        prepared, uploaded=uploaded(prepared), dependencies=deps, context=context(),
        state_store=store(world, "remediate-commit"), source_handling_store=store(world, "remediate-commit-sh"),
    )  # fmt: skip
    _, entries = store(world, "remediate-verify").read(520)
    view = state.verify_chain(
        [e.record for e in entries], repository_id=1, issue_number=520, trust=TRUST, provenance=trusted,
        indexes=[e.index for e in entries],
    )  # fmt: skip
    assert view.active == prepared.authorization_id
    assert view.authorizations[parent.authorization_id].state == state.COMPLETED


def test_a_remediation_of_an_unfinished_parent_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_ledger(world)
    parent = _authorize_once(world, monkeypatch)
    with pytest.raises(authorize.AuthorizeRefused, match="ISSUE_EXECUTION_ACTIVE|NOT_ELIGIBLE"):
        run_prepare(world, monkeypatch, "early", doc=_remediation_document(parent, "a1" * 20))


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("PREVENTION_CONTEXT_BUDGET_EXCEEDED", "PREVENTION_CONTEXT_BUDGET_EXCEEDED"),
        ("DUPLICATE_DEFECT_FAMILY", "DUPLICATE_DEFECT_FAMILY"),
        ("INVALID_KNOWLEDGE_OVERLAY_NAMESPACE", "INVALID_KNOWLEDGE_OVERLAY_NAMESPACE"),
        ("DEFECT_REGISTRY_INVALID", "DEFECT_REGISTRY_INVALID"),
        ("INVALID_SCOPE_CONTRACT", "INVALID_SCOPE_CONTRACT"),
        ("INVALID_ENGINEERING_CONTEXT_RECORD", "INVALID_ENGINEERING_CONTEXT_RECORD"),
    ],
)
def test_authorize_diagnostic_is_stable_and_never_leaks_raw_authority_detail(reason: str, expected: str) -> None:
    from hunter.evidence_intelligence.engineering_context_authority import EngineeringContextAuthorityError

    private_detail = "secret-record-id defect registry duplicate defect family"
    error = EngineeringContextAuthorityError(private_detail, reason_code=reason)
    refusal = authorize._engineering_context_refusal(error)
    assert str(refusal) == f"COMPILATION_REFUSED: EngineeringContextAuthorityError/{expected}"
    assert private_detail not in str(refusal)


def test_prepare_reads_configured_checkout_registry_not_ambient_cwd(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bootstrap_ledger(world)
    checkout = tmp_path / "trusted-control"
    registry = checkout / "docs" / "DEFECT_REGISTRY.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes((ROOT / "docs" / "DEFECT_REGISTRY.json").read_bytes())
    ambient = tmp_path / "untrusted-ambient"
    ambient.mkdir()
    monkeypatch.chdir(ambient)
    deps = dependencies(world, monkeypatch, repository_checkout=checkout)
    (prepared, _sealed), _deps = run_prepare(world, monkeypatch, "trusted-registry", deps=deps)
    assert prepared.issue_number == 520


def test_prepare_missing_configured_checkout_registry_fails_closed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bootstrap_ledger(world)
    checkout = tmp_path / "missing-registry"
    checkout.mkdir()
    deps = dependencies(world, monkeypatch, repository_checkout=checkout)
    with pytest.raises(authorize.AuthorizeRefused, match="EngineeringContextAuthorityError/DEFECT_REGISTRY_INVALID"):
        run_prepare(world, monkeypatch, "missing-registry", deps=deps)
