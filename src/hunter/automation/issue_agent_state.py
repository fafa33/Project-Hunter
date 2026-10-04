"""Signed, anchored durable ledger for the GitHub-native Issue Agent lifecycle (ADR 0037 D2/D2a/D5).

The ledger is the relocated execution ledger: one forward-only branch per Issue,
``refs/heads/hunter-state/v1/issue-<n>``, under a no-bypass ``deletion`` + ``non_fast_forward`` ruleset.
Every state change is one commit whose tree holds the transition record (``record.json``) and the derived
Issue index (``index.json``).

A record is authority only when the whole chain verifies (ADR 0037 D2 checks 1-7):

1. its Ed25519 signature by a pinned K_STATE key;
2. the hash chain and contiguous sequence;
3. a legal transition carrying its required, cross-bound evidence;
4. immutable bindings;
5. trusted workflow provenance;
6. compare-and-swap lineage;
7. anchor integrity.

Nothing in process memory, the runner filesystem, the Actions cache or an artifact is authority.
Unknown fields and free text are refused by a closed schema, so the public ledger can hold only non-secret
identities, digests, enums and timestamps.

``advance`` is the single pure decision function (state machine spec section 6). It never treats an
indefinite observation as a negative fact, and it never re-dispatches the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

RECORD_SCHEMA_VERSION: Final = "hunter-issue-agent-execution-record-v1"
INDEX_SCHEMA_VERSION: Final = "hunter-issue-agent-index-v1"
STATE_SIGNATURE_DOMAIN: Final = "hunter-issue-agent-state-v1"
LEDGER_REF_PREFIX: Final = "refs/heads/hunter-state/v1/"
MAX_RECORD_BYTES: Final = 64 * 1024
_MAX_DEPTH: Final = 6

AUTHORIZED: Final = "AUTHORIZED"
RESULT_BOUND: Final = "RESULT_BOUND"
VALIDATED: Final = "VALIDATED"
PUBLISHED: Final = "PUBLISHED"
COMPLETED: Final = "COMPLETED"
FAILED: Final = "FAILED"
STATES: Final = (AUTHORIZED, RESULT_BOUND, VALIDATED, PUBLISHED, COMPLETED, FAILED)
TERMINAL_STATES: Final = frozenset({COMPLETED, FAILED})
LEGAL_TRANSITIONS: Final[Mapping[str | None, frozenset[str]]] = {
    None: frozenset({AUTHORIZED}),
    AUTHORIZED: frozenset({RESULT_BOUND, FAILED}),
    RESULT_BOUND: frozenset({VALIDATED, FAILED}),
    VALIDATED: frozenset({PUBLISHED, FAILED}),
    PUBLISHED: frozenset({COMPLETED, FAILED}),
}
#: Which control role may write which target state (state machine spec section 3).
WRITER_ROLES: Final[Mapping[str, frozenset[str]]] = {
    AUTHORIZED: frozenset({"authorize"}),
    RESULT_BOUND: frozenset({"bind", "reconcile"}),
    VALIDATED: frozenset({"record-validation", "reconcile"}),
    PUBLISHED: frozenset({"finalize", "reconcile"}),
    COMPLETED: frozenset({"candidate-pr-record", "reconcile"}),
    FAILED: frozenset({"authorize", "bind", "record-validation", "finalize", "reconcile", "candidate-pr-record"}),
}
RESUME_ROLES: Final = frozenset({"bind", "record-validation", "finalize", "reconcile"})
#: Model-free stages that may resume, with their attempt caps. The model never resumes.
RESUME_STAGES: Final[Mapping[str, tuple[str, int]]] = {"validation": (RESULT_BOUND, 2), "publication": (VALIDATED, 3)}
#: An unbound resume whose dispatch produced no run within this grace is re-dispatched with the same nonce.
RESUME_DISPATCH_GRACE_SECONDS: Final = 600

FAILURE_CODES: Final = frozenset(
    {
        "BASE_NOT_ON_MAIN",
        "REMOTE_BRANCH_CONFLICT",
        "EXECUTOR_RESULT_TIMEOUT",
        "EXECUTOR_RESULT_REJECTED",
        "EXECUTION_NOT_STARTED",
        "EXECUTION_NOT_COMPLETED",
        "SECRET_IN_RESULT",
        "TRANSPORT_INTEGRITY_FAILED",
        "PRE_PUSH_SAFETY_FAILED",
        "VALIDATION_UNAVAILABLE",
        "RESULT_TRANSPORT_EXPIRED",
        "PUBLICATION_UNAVAILABLE",
        "PUBLICATION_REJECTED_BY_PLATFORM",
        "OWNER_WITHDREW",
        "ISSUE_CLOSED",
        "ISSUE_CHANGED_AFTER_AUTHORIZATION",
        "LIFECYCLE_DEADLINE_EXCEEDED",
        "CANDIDATE_PREFLIGHT_FAILED",
        "CANDIDATE_PREFLIGHT_TIMEOUT",
        "CONTROL_SHA_NOT_ON_MAIN",
    }
)
FREEZE_CODES: Final = frozenset({"STATE_CORRUPT", "STATE_ROLLBACK_SUSPECTED", "ANCHOR_INTEGRITY_FAILED"})
ADVISORY_CODES: Final = frozenset(
    {"PROVIDER_UNAVAILABLE", "PROVIDER_QUOTA", "MODEL_TIMEOUT", "NO_CHANGES", "MISSING_CONFIGURATION"}
)
EXECUTOR_CONCLUSIONS: Final = frozenset({"success", "failure", "cancelled", "timed_out", "skipped"})


class LedgerError(RuntimeError):
    """Base class. Every subclass is a fail-closed refusal, never a permissive default."""


class LedgerSchemaError(LedgerError):
    """A record or index is outside the closed schema."""


class LedgerCorruptError(LedgerError):
    """The chain does not verify; the Issue freezes as ``STATE_CORRUPT``."""

    freeze_code = "STATE_CORRUPT"


class AnchorIntegrityError(LedgerError):
    """The anti-rollback anchor ruleset is not provably intact; every ledger freezes."""

    freeze_code = "ANCHOR_INTEGRITY_FAILED"


class LedgerConflictError(LedgerError):
    """The compare-and-swap lost: another writer advanced the ledger first. Re-read and re-decide."""


# --- canonical encoding and identities ---------------------------------------------------------------


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_digest(record: Mapping[str, Any]) -> str:
    """The digest the next record's ``prev_record_sha256`` must carry (signature included)."""

    return sha256_hex(canonical_json(record))


def public_key_id(public_key: Ed25519PublicKey) -> str:
    return sha256_hex(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def execution_identity(*, authorization_id: str, authorize_run_id: int, control_sha: str, handoff_sha256: str) -> str:
    """``execution_id`` (state machine spec section 2): run attempt is always 1 for a fresh lifecycle."""

    return sha256_hex(
        canonical_json(
            {
                "authorization_id": authorization_id,
                "authorize_run_id": authorize_run_id,
                "run_attempt": 1,
                "control_sha": control_sha,
                "handoff_sha256": handoff_sha256,
            }
        )
    )


def publication_identity(
    *,
    repository_id: int,
    issue_number: int,
    authorization_id: str,
    base_sha: str,
    task_scope_sha256: str,
    execution_id: str,
    result_sha256: str,
    tree_sha: str,
    unsigned_commit_sha: str,
    control_sha: str,
    writer_login: str,
) -> str:
    """Deterministic idempotency identity of one publication (ADR 0037 D6)."""

    return sha256_hex(
        canonical_json(
            {
                "repository_id": repository_id,
                "issue_number": issue_number,
                "authorization_id": authorization_id,
                "base_sha": base_sha,
                "task_scope_sha256": task_scope_sha256,
                "execution_id": execution_id,
                "result_sha256": result_sha256,
                "tree_sha": tree_sha,
                "unsigned_commit_sha": unsigned_commit_sha,
                "control_sha": control_sha,
                "writer_login": writer_login,
            }
        )
    )


# --- closed schema -------------------------------------------------------------------------------------

Validator = Callable[[object, str], None]

_SHA40 = re.compile(r"[0-9a-f]{40}")
#: The canonical Issue authorization identity (``issue_agent_execution.ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX``).
AUTHORIZATION_ID_PATTERN: Final = re.compile(r"hunter-issue-agent-authorization:([0-9a-f]{64})")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_SIG = re.compile(r"[0-9a-f]{128}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#+@=-]{0,199}")
_NODE_ID = re.compile(r"[A-Za-z0-9_=-]{1,128}")
_WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9._-]{1,100}\.ya?ml")
_JOB = re.compile(r"[a-z][a-z0-9-]{0,63}")
_BRANCH = re.compile(r"issue-[1-9][0-9]{0,9}-[0-9a-f]{16}")
_ARTIFACT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SCOPE_PATH = re.compile(r"[A-Za-z0-9._@+*/-]{1,512}")
_BRANCH_GLOB = re.compile(r"[A-Za-z0-9._/*-]{1,200}")
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,49}")


def _fail(where: str, why: str) -> LedgerSchemaError:
    return LedgerSchemaError(f"{where}: {why}")


def _pattern(regex: re.Pattern[str]) -> Validator:
    def check(value: object, where: str) -> None:
        if not isinstance(value, str) or regex.fullmatch(value) is None:
            raise _fail(where, "malformed value")

    return check


def _int(minimum: int) -> Validator:
    def check(value: object, where: str) -> None:
        if type(value) is not int or value < minimum:
            raise _fail(where, f"must be an integer >= {minimum}")

    return check


def _exact(expected: object) -> Validator:
    def check(value: object, where: str) -> None:
        if type(value) is not type(expected) or value != expected:
            raise _fail(where, f"must be exactly {expected!r}")

    return check


def _enum(values: Iterable[str]) -> Validator:
    allowed = frozenset(values)

    def check(value: object, where: str) -> None:
        if not isinstance(value, str) or value not in allowed:
            raise _fail(where, "value outside the closed vocabulary")

    return check


def _optional(inner: Validator) -> Validator:
    def check(value: object, where: str) -> None:
        if value is not None:
            inner(value, where)

    return check


def _scope_path(value: object, where: str) -> None:
    if not isinstance(value, str) or _SCOPE_PATH.fullmatch(value) is None or value.startswith("/"):
        raise _fail(where, "not a canonical repository path")
    if any(part in {"", ".", ".."} for part in value.rstrip("/").split("/")):
        raise _fail(where, "not a canonical repository path")


def _list(inner: Validator, *, maximum: int, unique: bool = True) -> Validator:
    def check(value: object, where: str) -> None:
        if not isinstance(value, list) or len(value) > maximum:
            raise _fail(where, f"must be a list of at most {maximum}")
        for index, item in enumerate(value):
            inner(item, f"{where}[{index}]")
        if unique and len({canonical_json(item) for item in value}) != len(value):
            raise _fail(where, "duplicate entries")

    return check


def _object(spec: Mapping[str, Validator]) -> Validator:
    def check(value: object, where: str) -> None:
        if not isinstance(value, dict):
            raise _fail(where, "must be an object")
        if set(value) != set(spec):
            raise _fail(where, "fields differ from the closed schema")
        for key, validator in spec.items():
            validator(value[key], f"{where}.{key}")

    return check


SHA40, SHA64 = _pattern(_SHA40), _pattern(_SHA64)
AUTHORIZATION_ID = _pattern(AUTHORIZATION_ID_PATTERN)


def authorization_digest(authorization_id: str) -> str:
    """The 64-hex digest of a canonical authorization identity (artifact names cannot contain ``:``)."""

    match = AUTHORIZATION_ID_PATTERN.fullmatch(authorization_id)
    if match is None:
        raise LedgerSchemaError("not a canonical authorization identity")
    return match.group(1)


def result_artifact_name(authorization_id: str) -> str:
    return f"hunter-ia-result-{authorization_digest(authorization_id)}"


def handoff_artifact_name(authorization_id: str) -> str:
    return f"hunter-ia-handoff-{authorization_digest(authorization_id)}"


TIMESTAMP = _pattern(_TIMESTAMP)
POSITIVE = _int(1)

_ARTIFACT = _object(
    {
        "run_id": POSITIVE,
        "artifact_id": POSITIVE,
        "artifact_digest": _pattern(_ARTIFACT_DIGEST),
        "ciphertext_sha256": SHA64,
        "aad_sha256": SHA64,
        "recipient_key_id": SHA64,
    }
)
_RECORDED_BY = _object(
    {
        "workflow_path": _pattern(_WORKFLOW),
        "job": _pattern(_JOB),
        "role": _enum(
            {
                "authorize",
                "bind",
                "record-validation",
                "finalize",
                "reconcile",
                "candidate-pr-record",
                "source-handling-bootstrap",
            }
        ),
        "run_id": POSITIVE,
        "run_attempt": POSITIVE,
        "head_sha": SHA40,
    }
)
#: Public alias: the closed ``recorded_by`` schema shared by every anchored ledger.
RECORDED_BY_VALIDATOR: Final = _RECORDED_BY
_SIGNATURE = _object(
    {
        "alg": _exact("ed25519"),
        "key_id": SHA64,
        "domain": _exact(STATE_SIGNATURE_DOMAIN),
        "value": _pattern(_SIG),
    }
)
EVIDENCE_SCHEMAS: Final[Mapping[str, Validator]] = {
    AUTHORIZED: _object(
        {
            "authorization_envelope_sha256": SHA64,
            "claims": _object(
                {
                    "owner_login": _pattern(_LOGIN),
                    "label": _pattern(_LABEL),
                    "issue_updated_at": TIMESTAMP,
                    "schema_version": _pattern(_IDENT),
                    "title_sha256": SHA64,
                    "body_sha256": SHA64,
                }
            ),
            "task_scope": _object(
                {
                    "task_id": _pattern(_IDENT),
                    "branch_pattern": _pattern(_BRANCH_GLOB),
                    "base_ref": _exact("main"),
                    "base_sha": SHA40,
                    "allowed_paths": _list(_scope_path, maximum=256),
                    "prohibited_paths": _list(_scope_path, maximum=256),
                }
            ),
            "task_scope_sha256": SHA64,
            "execution_branch": _pattern(_BRANCH),
            "base_sha": SHA40,
            "control_sha": SHA40,
            "authorize_run_id": POSITIVE,
            "execution_id": SHA64,
            "prompt_input_manifest_sha256": SHA64,
            "compiler_identity_sha256": SHA64,
            "deadline_published_at": TIMESTAMP,
            "lineage": _object(
                {
                    "document_id": _pattern(_IDENT),
                    "build_record_id": _pattern(_IDENT),
                    "envelope_id": _pattern(_IDENT),
                    "prompt_artifact_id": _pattern(_IDENT),
                    "prompt_sha256": SHA64,
                    "handoff_sha256": SHA64,
                    "dpm_context_sha256": SHA64,
                    "source_handling_record_ids": _list(_pattern(_IDENT), maximum=64),
                    "reconstruction": _exact("EXACT_RECONSTRUCTION_UNAVAILABLE"),
                    "reconstruction_reason": _exact("NO_CONFIDENTIAL_DURABLE_STORE"),
                }
            ),
            "handoff_artifact": _ARTIFACT,
        }
    ),
    RESULT_BOUND: _object(
        {
            "result_artifact": _ARTIFACT,
            "result_plaintext_sha256": SHA64,
            "executor_job_id": POSITIVE,
            "executor_conclusion": _enum(EXECUTOR_CONCLUSIONS),
            "executor_advisory_code": _optional(_enum(ADVISORY_CODES)),
        }
    ),
    VALIDATED: _object(
        {
            "receipt_sha256": SHA64,
            "result_sha256": SHA64,
            "tree_sha": SHA40,
            "unsigned_commit_sha": SHA40,
            "validation_definition": SHA64,
            "toolchain_sha256": SHA64,
            "validator_run_id": POSITIVE,
            "validation_attempts": POSITIVE,
        }
    ),
    PUBLISHED: _object(
        {
            "writer_login": _pattern(_LOGIN),
            "publication_identity": SHA64,
            "head_sha": SHA40,
            "commit_verified": _exact(True),
            "publish_attempts": POSITIVE,
            "deadline_completed_at": TIMESTAMP,
        }
    ),
    COMPLETED: _object(
        {
            "pull_request_number": POSITIVE,
            "pull_request_node_id": _pattern(_NODE_ID),
            "pull_request_head_sha": SHA40,
            "draft": _exact(True),
            "preflight_run_id": POSITIVE,
            "preflight_conclusion": _exact("success"),
        }
    ),
    FAILED: _object(
        {
            "code": _enum(FAILURE_CODES),
            "failed_from_state": _enum({AUTHORIZED, RESULT_BOUND, VALIDATED, PUBLISHED}),
        }
    ),
}
_RESUME_REQUESTED = _object(
    {"stage": _enum(RESUME_STAGES), "nonce": SHA64, "attempt": POSITIVE, "dispatched_at": TIMESTAMP}
)
_RESUME_BOUND = _object({"nonce": SHA64, "run_id": POSITIVE})
_RESUME_ABANDONED = _object({"nonce": SHA64, "reason": _enum({"run_concluded_without_output"})})
_INDEX = _object(
    {
        "schema_version": _exact(INDEX_SCHEMA_VERSION),
        "repository_id": POSITIVE,
        "issue_number": POSITIVE,
        "claimed_authorization_ids": _list(AUTHORIZATION_ID, maximum=10_000),
        "active_authorization_id": _optional(AUTHORIZATION_ID),
        "pending_resume": _optional(
            _object(
                {
                    "authorization_id": AUTHORIZATION_ID,
                    "stage": _enum(RESUME_STAGES),
                    "nonce": SHA64,
                    "attempt": POSITIVE,
                    "dispatched_at": TIMESTAMP,
                    "bound_run_id": _optional(POSITIVE),
                }
            )
        ),
    }
)
_RECORD_BASE: Final[Mapping[str, Validator]] = {
    "schema_version": _exact(RECORD_SCHEMA_VERSION),
    "kind": _enum({"transition", "resume_requested", "resume_bound", "resume_abandoned"}),
    "record_seq": _int(0),
    "prev_record_sha256": _optional(SHA64),
    "recorded_at": TIMESTAMP,
    "recorded_by": _RECORDED_BY,
    "repository_id": POSITIVE,
    "issue_number": POSITIVE,
    "authorization_id": AUTHORIZATION_ID,
    "state": _enum(STATES),
    "evidence": lambda _value, _where: None,  # validated per kind/state in validate_record_schema
    "signature": _SIGNATURE,
}


def _depth(value: object, level: int = 0) -> int:
    if isinstance(value, dict):
        return max([level, *(_depth(item, level + 1) for item in value.values())])
    if isinstance(value, list):
        return max([level, *(_depth(item, level + 1) for item in value)])
    return level


def validate_record_schema(record: object) -> dict[str, Any]:
    """Refuse anything outside the closed record schema (types, vocabulary, size, depth)."""

    if not isinstance(record, dict):
        raise LedgerSchemaError("record must be an object")
    if len(canonical_json(record)) > MAX_RECORD_BYTES or _depth(record) > _MAX_DEPTH:
        raise LedgerSchemaError("record exceeds the size or depth bound")
    _object(_RECORD_BASE)(record, "record")
    kind, state, evidence = record["kind"], record["state"], record["evidence"]
    if kind == "transition":
        EVIDENCE_SCHEMAS[state](evidence, f"record.evidence[{state}]")
    elif kind == "resume_requested":
        _RESUME_REQUESTED(evidence, "record.evidence[resume_requested]")
    elif kind == "resume_bound":
        _RESUME_BOUND(evidence, "record.evidence[resume_bound]")
    else:
        _RESUME_ABANDONED(evidence, "record.evidence[resume_abandoned]")
    return record


def validate_index_schema(index: object) -> dict[str, Any]:
    if not isinstance(index, dict):
        raise LedgerSchemaError("index must be an object")
    _INDEX(index, "index")
    return index


# --- signing -------------------------------------------------------------------------------------------


def _unsigned(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "signature"}


def sign_record(
    unsigned: Mapping[str, Any], private_key: Ed25519PrivateKey, *, domain: str = STATE_SIGNATURE_DOMAIN
) -> dict[str, Any]:
    """Return the record with its K_STATE signature over the canonical unsigned bytes."""

    body = _unsigned(unsigned)
    value = private_key.sign(_signature_prefix(domain) + canonical_json(body)).hex()
    signature = {
        "alg": "ed25519",
        "key_id": public_key_id(private_key.public_key()),
        "domain": domain,
        "value": value,
    }
    return {**body, "signature": signature}


def _signature_prefix(domain: str) -> bytes:
    return (domain + "\x00").encode("utf-8")


def verify_record_signature(
    record: Mapping[str, Any],
    pinned_keys: Mapping[str, Ed25519PublicKey],
    *,
    domain: str = STATE_SIGNATURE_DOMAIN,
) -> None:
    signature = record.get("signature")
    if not isinstance(signature, dict):
        raise LedgerCorruptError("record is unsigned")
    if signature.get("domain") != domain:
        raise LedgerCorruptError("record is signed for another ledger domain")
    key = pinned_keys.get(str(signature.get("key_id")))
    if key is None:
        raise LedgerCorruptError("record signer key is not pinned")
    try:
        key.verify(
            bytes.fromhex(str(signature.get("value"))), _signature_prefix(domain) + canonical_json(_unsigned(record))
        )
    except (InvalidSignature, ValueError):
        raise LedgerCorruptError("record signature does not verify") from None


# --- trust roots, provenance, anchor -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnchorPin:
    """The pinned anti-rollback ruleset (ADR 0037 D2a)."""

    ruleset_id: int
    updated_at: str
    required_rules: frozenset[str] = frozenset({"deletion", "non_fast_forward"})


def verify_anchor_integrity(
    pin: AnchorPin, ruleset: Mapping[str, Any] | None, rules_for_branch: Sequence[Mapping[str, Any]]
) -> None:
    """Fail closed unless the anchor ruleset is provably intact for this branch.

    ``ruleset`` is an *authenticated* ``GET /repos/{r}/rulesets/{id}`` and ``rules_for_branch`` an
    authenticated ``GET /repos/{r}/rules/branches/{branch}``. Anonymous reads were proven CDN-stale in S0.
    """

    if ruleset is None or ruleset.get("id") != pin.ruleset_id:
        raise AnchorIntegrityError("anchor ruleset missing or replaced")
    if ruleset.get("enforcement") != "active":
        raise AnchorIntegrityError("anchor ruleset not active")
    if ruleset.get("updated_at") != pin.updated_at:
        raise AnchorIntegrityError("anchor ruleset modified since it was pinned")
    declared = {str(rule.get("type")) for rule in ruleset.get("rules") or [] if isinstance(rule, dict)}
    if not pin.required_rules <= declared:
        raise AnchorIntegrityError("anchor ruleset rules weakened")
    applied = {
        str(rule.get("type"))
        for rule in rules_for_branch
        if isinstance(rule, Mapping) and rule.get("ruleset_id") == pin.ruleset_id
    }
    if not pin.required_rules <= applied:
        raise AnchorIntegrityError("anchor ruleset does not cover this ledger branch")


@dataclass(frozen=True, slots=True)
class TrustRoots:
    """Repository-pinned public trust roots, read at ``control_sha`` (never from the environment)."""

    state_keys: Mapping[str, Ed25519PublicKey]
    repository_id: int


#: ``provenance(recorded_by, record) -> bool``: the writing run is a trusted lifecycle run (ADR 0037 D2 check 5).
ProvenanceCheck = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


# --- chain verification and fold ------------------------------------------------------------------------


@dataclass(slots=True)
class AuthorizationView:
    authorization_id: str
    state: str
    records: list[dict[str, Any]]
    evidence: dict[str, dict[str, Any]]
    pending_resume: dict[str, Any] | None = None
    resume_attempts: dict[str, int] | None = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def binding(self, field: str) -> Any:
        return self.evidence[AUTHORIZED][field]


@dataclass(slots=True)
class LedgerView:
    repository_id: int
    issue_number: int
    authorizations: dict[str, AuthorizationView]
    claimed: list[str]
    active: str | None
    head_record_digest: str | None
    next_seq: int

    def index(self) -> dict[str, Any]:
        pending = None
        if self.active is not None:
            view = self.authorizations[self.active]
            if view.pending_resume is not None:
                pending = {"authorization_id": self.active, **view.pending_resume}
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "repository_id": self.repository_id,
            "issue_number": self.issue_number,
            "claimed_authorization_ids": list(self.claimed),
            "active_authorization_id": self.active,
            "pending_resume": pending,
        }


def empty_view(repository_id: int, issue_number: int) -> LedgerView:
    return LedgerView(repository_id, issue_number, {}, [], None, None, 0)


def _check_authorized_bindings(record: Mapping[str, Any]) -> None:
    evidence = record["evidence"]
    authorization_id = record["authorization_id"]
    if evidence["execution_branch"] != f"issue-{record['issue_number']}-{authorization_digest(authorization_id)[:16]}":
        raise LedgerCorruptError("execution branch is not derived from the Issue and authorization identity")
    if evidence["base_sha"] != evidence["task_scope"]["base_sha"]:
        raise LedgerCorruptError("bound base differs from the signed TaskScope base")
    if record["recorded_by"]["run_id"] != evidence["authorize_run_id"] or record["recorded_by"]["run_attempt"] != 1:
        raise LedgerCorruptError("AUTHORIZED is not bound to attempt 1 of its own authorize run")
    if record["recorded_by"]["head_sha"] != evidence["control_sha"]:
        raise LedgerCorruptError("AUTHORIZED was not written by the bound control commit")
    expected = execution_identity(
        authorization_id=authorization_id,
        authorize_run_id=evidence["authorize_run_id"],
        control_sha=evidence["control_sha"],
        handoff_sha256=evidence["lineage"]["handoff_sha256"],
    )
    if evidence["execution_id"] != expected:
        raise LedgerCorruptError("execution identity does not derive from the bound run and handoff")


def _check_cross_bindings(view: AuthorizationView, record: Mapping[str, Any]) -> None:
    state, evidence = record["state"], record["evidence"]
    if state == PUBLISHED:
        bound, validated = view.evidence[AUTHORIZED], view.evidence[VALIDATED]
        expected = publication_identity(
            repository_id=record["repository_id"],
            issue_number=record["issue_number"],
            authorization_id=view.authorization_id,
            base_sha=bound["base_sha"],
            task_scope_sha256=bound["task_scope_sha256"],
            execution_id=bound["execution_id"],
            result_sha256=validated["result_sha256"],
            tree_sha=validated["tree_sha"],
            unsigned_commit_sha=validated["unsigned_commit_sha"],
            control_sha=bound["control_sha"],
            writer_login=evidence["writer_login"],
        )
        if evidence["publication_identity"] != expected:
            raise LedgerCorruptError("publication identity does not derive from the bound evidence")
    if state == VALIDATED and evidence["result_sha256"] != view.evidence[RESULT_BOUND]["result_plaintext_sha256"]:
        raise LedgerCorruptError("validated result digest is not the bound result")
    if state == COMPLETED and evidence["pull_request_head_sha"] != view.evidence[PUBLISHED]["head_sha"]:
        raise LedgerCorruptError("completed pull request is not at the published head")


def apply_record(
    view: LedgerView,
    record: Mapping[str, Any],
    *,
    trust: TrustRoots,
    provenance: ProvenanceCheck,
    index: Mapping[str, Any] | None = None,
) -> LedgerView:
    """Verify one record against the verified prefix and fold it in. Any failure is ``STATE_CORRUPT``."""

    try:
        record = validate_record_schema(json.loads(canonical_json(record)))
    except LedgerSchemaError as error:
        raise LedgerCorruptError(f"schema: {error}") from None
    verify_record_signature(record, trust.state_keys)
    if record["record_seq"] != view.next_seq:
        raise LedgerCorruptError("record sequence is not contiguous")
    if record["prev_record_sha256"] != view.head_record_digest:
        raise LedgerCorruptError("record does not chain to the previous record")
    if record["repository_id"] != view.repository_id or record["issue_number"] != view.issue_number:
        raise LedgerCorruptError("record binds a different repository or Issue")
    if record["repository_id"] != trust.repository_id:
        raise LedgerCorruptError("record binds a foreign repository")
    if not provenance(record["recorded_by"], record):
        raise LedgerCorruptError("record was not written by a trusted lifecycle run")

    authorization_id, state, kind = record["authorization_id"], record["state"], record["kind"]
    role = record["recorded_by"]["role"]
    current = view.authorizations.get(authorization_id)
    if kind == "transition":
        previous_state = None if current is None else current.state
        if state not in LEGAL_TRANSITIONS.get(previous_state, frozenset()):
            raise LedgerCorruptError(f"illegal transition {previous_state} -> {state}")
        if role not in WRITER_ROLES[state]:
            raise LedgerCorruptError(f"role {role} may not write {state}")
        if state == AUTHORIZED:
            if authorization_id in view.claimed:
                raise LedgerCorruptError("authorization replayed")
            if view.active is not None:
                raise LedgerCorruptError("a second authorization became active for one Issue")
            _check_authorized_bindings(record)
            current = AuthorizationView(authorization_id, state, [], {}, None, {})
            view.authorizations[authorization_id] = current
            view.claimed.append(authorization_id)
            view.active = authorization_id
        else:
            assert current is not None
            if state == FAILED and record["evidence"]["failed_from_state"] != current.state:
                raise LedgerCorruptError("failure does not name the state it left")
            if state != FAILED:
                _check_cross_bindings(current, record)
            current.state = state
            current.pending_resume = None
            if state in TERMINAL_STATES:
                view.active = None
        current.evidence[state] = dict(record["evidence"])
    else:
        if current is None or current.terminal or view.active != authorization_id:
            raise LedgerCorruptError("resume record for an inactive authorization")
        if role not in RESUME_ROLES or state != current.state:
            raise LedgerCorruptError("resume record does not match the current state or role")
        evidence = record["evidence"]
        if kind == "resume_requested":
            stage = evidence["stage"]
            stage_state, cap = RESUME_STAGES[stage]
            attempts = (current.resume_attempts or {}).get(stage, 0) + 1
            if current.state != stage_state or current.pending_resume is not None:
                raise LedgerCorruptError("resume requested outside its stage or while one is pending")
            if evidence["attempt"] != attempts or attempts > cap - 1:
                raise LedgerCorruptError("resume attempt is not the next attempt within the cap")
            current.resume_attempts = {**(current.resume_attempts or {}), stage: attempts}
            current.pending_resume = {
                "stage": stage,
                "nonce": evidence["nonce"],
                "attempt": evidence["attempt"],
                "dispatched_at": evidence["dispatched_at"],
                "bound_run_id": None,
            }
        elif kind == "resume_abandoned":
            pending = current.pending_resume
            if pending is None or pending["nonce"] != evidence["nonce"]:
                raise LedgerCorruptError("resume abandonment does not name the pending nonce")
            current.pending_resume = None  # the attempt stays consumed; the next attempt needs a new nonce
        else:
            pending = current.pending_resume
            if pending is None or pending["nonce"] != evidence["nonce"] or pending["bound_run_id"] is not None:
                raise LedgerCorruptError("resume bind does not match one unbound pending nonce")
            if evidence["run_id"] != record["recorded_by"]["run_id"]:
                raise LedgerCorruptError("resume bound by a different run")
            pending["bound_run_id"] = evidence["run_id"]
    current.records.append(dict(record))
    view.head_record_digest = record_digest(record)
    view.next_seq += 1
    if index is not None and canonical_json(validate_index_schema(dict(index))) != canonical_json(view.index()):
        raise LedgerCorruptError("stored index differs from the verified chain")
    return view


def verify_chain(
    records: Sequence[Mapping[str, Any]],
    *,
    repository_id: int,
    issue_number: int,
    trust: TrustRoots,
    provenance: ProvenanceCheck,
    indexes: Sequence[Mapping[str, Any] | None] | None = None,
) -> LedgerView:
    view = empty_view(repository_id, issue_number)
    for position, record in enumerate(records):
        index = None if indexes is None else indexes[position]
        apply_record(view, record, trust=trust, provenance=provenance, index=index)
    return view


# --- git-backed compare-and-swap store ------------------------------------------------------------------

_LEDGER_IDENTITY = {
    "GIT_AUTHOR_NAME": "Hunter State Ledger",
    "GIT_AUTHOR_EMAIL": "state-ledger@hunter.invalid",
    "GIT_COMMITTER_NAME": "Hunter State Ledger",
    "GIT_COMMITTER_EMAIL": "state-ledger@hunter.invalid",
}
_HARDENED_CONFIG = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=always")


def ledger_ref(issue_number: int) -> str:
    if type(issue_number) is not int or issue_number < 1:
        raise LedgerError("Issue number must be a positive integer")
    return f"{LEDGER_REF_PREFIX}issue-{issue_number}"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    commit: str
    record: dict[str, Any]
    index: dict[str, Any]


class GitLedgerStore:
    """Lease-CAS ledger over a private, hook-free git directory (never a candidate worktree).

    ``auth_header`` (for example a GitHub token as an ``AUTHORIZATION: basic ...`` header) is passed per
    command with ``-c http.extraheader`` and is never placed in the environment, argv logs or a URL.
    """

    def __init__(self, remote: str, *, auth_header: str | None = None, workdir: Path | None = None) -> None:
        self._remote = remote
        self._auth = auth_header
        self._dir = Path(workdir or tempfile.mkdtemp(prefix="hunter-ledger-"))
        self._git("init", "--quiet", "--bare", str(self._dir), cwd=None)

    def _git(
        self, *args: str, cwd: Path | None | str = "", stdin: bytes | None = None, env: Mapping[str, str] | None = None
    ) -> bytes:
        command = ["git", *_HARDENED_CONFIG]
        if self._auth is not None:
            command += ["-c", f"http.extraheader={self._auth}"]
        command += list(args)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(self._dir),
            **(env or {}),
        }
        directory = None if cwd is None else (self._dir if cwd == "" else Path(cwd))
        completed = subprocess.run(
            command, cwd=directory, input=stdin, env=environment, capture_output=True, check=False, timeout=120
        )
        if completed.returncode != 0:
            raise LedgerError(f"git {args[0]} failed with exit status {completed.returncode}")
        return completed.stdout

    def ref_head(self, ref: str) -> str | None:
        output = self._git("ls-remote", self._remote, ref).decode().split()
        if not output:
            return None
        if _SHA40.fullmatch(output[0]) is None:
            raise LedgerError("remote returned a malformed head")
        return output[0]

    def remote_head(self, issue_number: int) -> str | None:
        return self.ref_head(ledger_ref(issue_number))

    def read_files(self, ref: str, names: frozenset[str]) -> tuple[str | None, list[tuple[str, dict[str, bytes]]]]:
        """Read a single first-parent ledger chain whose every commit tree is exactly ``names``."""

        head = self.ref_head(ref)
        if head is None:
            return None, []
        self._git("fetch", "--quiet", "--no-tags", self._remote, f"+{ref}:refs/ledger/read")
        if self._git("rev-parse", "refs/ledger/read").decode().strip() != head:
            raise LedgerError("ledger head moved during read; retry")
        commits = self._git("rev-list", "--reverse", "--first-parent", head).decode().split()
        entries: list[tuple[str, dict[str, bytes]]] = []
        for position, commit in enumerate(commits):
            parents = self._git("rev-list", "--parents", "-n", "1", commit).decode().split()[1:]
            if len(parents) != (0 if position == 0 else 1) or (position and parents[0] != commits[position - 1]):
                raise LedgerCorruptError("ledger commit lineage is not a single first-parent chain")
            if set(self._git("ls-tree", "--name-only", commit).decode().split()) != set(names):
                raise LedgerCorruptError("ledger commit tree differs from the closed layout")
            entries.append((commit, {name: self._git("cat-file", "blob", f"{commit}:{name}") for name in names}))
        return head, entries

    def read(self, issue_number: int) -> tuple[str | None, list[LedgerEntry]]:
        head, raw = self.read_files(ledger_ref(issue_number), frozenset({"record.json", "index.json"}))
        entries: list[LedgerEntry] = []
        for commit, files in raw:
            try:
                entries.append(LedgerEntry(commit, json.loads(files["record.json"]), json.loads(files["index.json"])))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise LedgerCorruptError("ledger blob is not canonical JSON") from None
        return head, entries

    def append_files(
        self, ref: str, expected_head: str | None, files: Mapping[str, bytes], *, message: str, timestamp: str
    ) -> str:
        """Compare-and-swap one commit holding exactly ``files``.

        Returns the new head, or raises ``LedgerConflictError`` if another writer won. A push whose
        acknowledgement is lost is resolved by reading the ledger back. If the head is exactly our commit
        (or a byte-identical write already landed), the write succeeded; otherwise the CAS was lost and is
        never retried blindly.
        """

        entries = []
        for name in sorted(files):
            if "/" in name or "\t" in name or "\n" in name or not name:
                raise LedgerError("ledger file names must be flat")
            blob = self._git("hash-object", "-w", "--stdin", stdin=files[name]).decode().strip()
            entries.append(f"100644 blob {blob}\t{name}\n")
        tree = self._git("mktree", stdin="".join(entries).encode()).decode().strip()
        parents = [] if expected_head is None else ["-p", expected_head]
        observed_head = self.ref_head(ref)
        if observed_head is not None:
            self._git("fetch", "--quiet", "--no-tags", self._remote, f"+{ref}:refs/ledger/base")
        try:
            commit = (
                self._git(
                    "commit-tree",
                    tree,
                    *parents,
                    "-m",
                    message,
                    env={**_LEDGER_IDENTITY, "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp},
                )
                .decode()
                .strip()
            )
        except LedgerError:
            raise LedgerConflictError("expected head is not part of the ledger; re-read and re-decide") from None
        if observed_head == commit:
            return commit  # byte-identical write already applied (idempotent)
        # A moved head is refused by the server-side lease below (S0 A-1); no client-side pre-check is relied on.
        try:
            self._git(
                "push", "--quiet", f"--force-with-lease={ref}:{expected_head or ''}", self._remote, f"{commit}:{ref}"
            )
        except LedgerError:
            pass  # resolved by the read-back below
        if self.ref_head(ref) == commit:
            return commit
        raise LedgerConflictError("another writer advanced the ledger; re-read and re-decide")

    def append(
        self, issue_number: int, expected_head: str | None, record: Mapping[str, Any], index: Mapping[str, Any]
    ) -> str:
        """Compare-and-swap one Issue ledger record (see ``append_files``)."""

        return self.append_files(
            ledger_ref(issue_number),
            expected_head,
            {"record.json": canonical_json(record), "index.json": canonical_json(index)},
            message=f"{record['state']} {record['authorization_id']} {record['kind']} {record['record_seq']}",
            timestamp=str(record["recorded_at"]),
        )


# --- advance: the single pure decision function (state machine spec section 6) --------------------------


class _Unknown:
    _instance: _Unknown | None = None

    def __new__(cls) -> _Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"


#: An indefinite observation (5xx, 429, timeout, partial response). Never a negative fact.
UNKNOWN: Final = _Unknown()


@dataclass(frozen=True, slots=True)
class ArtifactFact:
    artifact_id: int
    name: str
    digest: str
    size: int
    expired: bool


@dataclass(frozen=True, slots=True)
class Facts:
    """Definitive GitHub observations for one authorization. ``UNKNOWN`` makes ``advance`` a no-op."""

    stage_run_active: bool | _Unknown = UNKNOWN
    executor_conclusion: str | None | _Unknown = UNKNOWN
    result_artifacts: tuple[ArtifactFact, ...] | _Unknown = UNKNOWN
    receipt: Mapping[str, Any] | None | _Unknown = UNKNOWN
    remote_branch_head: str | None | _Unknown = UNKNOWN
    remote_head_conforms: bool | _Unknown = UNKNOWN
    open_draft_pr: Mapping[str, Any] | None | _Unknown = UNKNOWN
    preflight_conclusion: str | None | _Unknown = UNKNOWN
    #: The workflow run carrying the pending resume nonce: ``None`` (no such run), "active" or "concluded".
    resume_run_status: str | None | _Unknown = UNKNOWN
    now: str = "1970-01-01T00:00:00Z"


@dataclass(frozen=True, slots=True)
class Decision:
    action: str  # "noop" | "transition" | "fail" | "resume" | "redispatch" | "abandon_resume" | "freeze"
    target_state: str | None = None
    code: str | None = None
    stage: str | None = None
    reason: str = ""
    evidence_hint: Mapping[str, Any] | None = None


def _instant(text: str) -> datetime:
    if _TIMESTAMP.fullmatch(text) is None:
        raise LedgerSchemaError("malformed UTC timestamp")
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)


def _deadline_passed(deadline: str, now: str) -> bool:
    return _instant(now) >= _instant(deadline)


def advance(view: AuthorizationView, facts: Facts) -> Decision:
    """Decide at most one step for an authorization whose stage run has concluded.

    The model stage is never resumed: an AUTHORIZED authorization without a bound result fails closed.
    Forward facts never skip a state whose evidence is missing; a contradiction freezes the ledger.
    """

    if view.terminal:
        return Decision("noop", reason="terminal")
    if facts.stage_run_active is UNKNOWN:
        return Decision("noop", reason="stage run status unknown")
    if facts.stage_run_active is True:
        return Decision("noop", reason="owning run still active")
    state = view.state
    branch = view.binding("execution_branch")
    if state in {AUTHORIZED, RESULT_BOUND} and facts.remote_branch_head not in (None, UNKNOWN):
        return Decision("freeze", code="STATE_ROLLBACK_SUSPECTED", reason=f"{branch} exists before VALIDATED")
    if state in {AUTHORIZED, RESULT_BOUND, VALIDATED} and facts.open_draft_pr not in (None, UNKNOWN):
        return Decision("freeze", code="STATE_ROLLBACK_SUSPECTED", reason="a Draft PR exists before PUBLISHED")

    if state == AUTHORIZED:
        if facts.result_artifacts is UNKNOWN or facts.executor_conclusion is UNKNOWN:
            return Decision("noop", reason="executor facts unknown")
        artifacts = [a for a in facts.result_artifacts if a.name == result_artifact_name(view.authorization_id)]
        if len(artifacts) > 1:
            return Decision("fail", code="TRANSPORT_INTEGRITY_FAILED", reason="duplicate result artifacts at bind")
        if len(artifacts) == 1:
            if artifacts[0].expired:
                return Decision("fail", code="RESULT_TRANSPORT_EXPIRED")
            return Decision(
                "transition", target_state=RESULT_BOUND, evidence_hint={"artifact_id": artifacts[0].artifact_id}
            )
        if facts.executor_conclusion in (None, "skipped"):
            return Decision("fail", code="EXECUTION_NOT_STARTED")
        return Decision("fail", code="EXECUTION_NOT_COMPLETED")

    if state == RESULT_BOUND:
        if facts.receipt is UNKNOWN:
            return Decision("noop", reason="receipt unknown")
        if facts.receipt is not None:
            return Decision("transition", target_state=VALIDATED)
        return _resume_or_fail(view, "validation", facts, "VALIDATION_UNAVAILABLE")

    if state == VALIDATED:
        if facts.remote_branch_head is UNKNOWN:
            return Decision("noop", reason="remote branch unknown")
        if facts.remote_branch_head is not None:
            if facts.remote_head_conforms is UNKNOWN:
                return Decision("noop", reason="remote head conformance unknown")
            if facts.remote_head_conforms:
                return Decision("transition", target_state=PUBLISHED)
            return Decision("fail", code="REMOTE_BRANCH_CONFLICT")
        if _deadline_passed(view.binding("deadline_published_at"), facts.now):
            return Decision("fail", code="LIFECYCLE_DEADLINE_EXCEEDED")
        return _resume_or_fail(view, "publication", facts, "PUBLICATION_UNAVAILABLE")

    # PUBLISHED: terminal success needs BOTH a definitive successful exact-head preflight and the Draft PR.
    if facts.open_draft_pr is UNKNOWN or facts.preflight_conclusion is UNKNOWN:
        return Decision("noop", reason="pull request or preflight facts unknown")
    if facts.preflight_conclusion not in (None, "success"):
        return Decision("fail", code="CANDIDATE_PREFLIGHT_FAILED")
    if facts.open_draft_pr is not None and facts.preflight_conclusion == "success":
        return Decision("transition", target_state=COMPLETED)
    if _deadline_passed(view.evidence[PUBLISHED]["deadline_completed_at"], facts.now):
        return Decision("fail", code="CANDIDATE_PREFLIGHT_TIMEOUT")
    return Decision("noop", reason="awaiting exact-head Pre-PR Preflight and Draft PR")


def _resume_or_fail(view: AuthorizationView, stage: str, facts: Facts, exhausted_code: str) -> Decision:
    if facts.result_artifacts is UNKNOWN:
        return Decision("noop", reason="transport facts unknown")
    bound_id = view.evidence[RESULT_BOUND]["result_artifact"]["artifact_id"]
    bound = [a for a in facts.result_artifacts if a.artifact_id == bound_id]
    if not bound or bound[0].expired:
        return Decision("fail", code="RESULT_TRANSPORT_EXPIRED")
    _state, cap = RESUME_STAGES[stage]
    pending = view.pending_resume
    if pending is not None:
        status = facts.resume_run_status
        if status is UNKNOWN or status == "active":
            return Decision("noop", reason="pending resume run unknown or still active")
        if status == "concluded":
            return Decision("abandon_resume", stage=stage, reason="run_concluded_without_output")
        if pending["bound_run_id"] is not None:
            return Decision("noop", reason="bound resume run not observable yet")
        elapsed = (_instant(facts.now) - _instant(pending["dispatched_at"])).total_seconds()
        if elapsed < RESUME_DISPATCH_GRACE_SECONDS:
            return Decision("noop", reason="resume dispatch within its grace period")
        return Decision("redispatch", stage=stage, reason="dispatch produced no run; same nonce, no attempt consumed")
    attempts = (view.resume_attempts or {}).get(stage, 0)
    if attempts + 1 >= cap:
        return Decision("fail", code=exhausted_code)
    return Decision("resume", stage=stage)
