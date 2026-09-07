#!/usr/bin/env python3
"""Provision the per-Issue Source Handling authority records for Railway E2E.

This operator-only command realises step ``(b)`` of the Railway operator order:
after ``bootstrap_source_handling_authority.py`` has pinned the operator root
and genesis rule, it publishes the exact ``FACT``, ``FIELD_CATEGORY_REGISTRY``
and ``POLICY`` authority records for ONE real authorized Issue document, bound
to the exact ``github-issue:<repository>#<number>`` document identity that the
issuer runtime derives from the same authorization claims.

Every record is derived from repository-owned contracts plus the supplied Issue
document. The ``EVIDENCE`` and ``VERIFIER`` provenance records these records'
publication authorizations require are provisioned first into the same database
under the same signing key and pinned operator root, because authority
publication validates provenance antecedents as strict-known before issuing.

Authority records carry the provisioning as-of instant (``--as-of``, default:
the provisioning moment). The Source Handling contract forbids backdating a
record before its physical admission, so an ``issue_updated_at`` from the past
cannot by itself time the records; the operator pins ``--as-of`` to the exact
first-run value (printed by ``--json``) for an idempotent re-run. The command is
idempotent only when every existing record exactly matches the derived content;
any mismatch fails closed before it writes, and no provisioning run ever
supersedes or replaces authority state.

The Source Handling private signing key follows the same mutual-exclusion
contract as the bootstrap script (``HUNTER_SOURCE_HANDLING_SIGNING_KEY`` xor
``--signing-key-file``). If any required record's exact canonical content cannot
be derived, the command stops with the specific missing contract instead of
inventing data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bootstrap_source_handling_authority as bootstrap

from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    IssueAgentAuthorization,
    IssueAgentAuthorizationError,
    issue_agent_document_id,
)
from hunter.evidence_intelligence.source_handling import (
    AUTHORITY_COMPONENT_ID,
    resolve_canonical_head,
)
from hunter.evidence_intelligence.source_handling_persistence import (
    PERMISSIVE_EVIDENCE_STRENGTHS,
    SOURCE_HANDLING_RULE_SCOPE,
    SUPPORTED_EVIDENCE_METHODS,
    SUPPORTED_VERIFIER_TYPES,
    SourceHandlingAuthorityService,
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
    _aware_utc,
    _canonical_json,
    _parse_time,
    _plain_mapping,
    _time_text,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    EVIDENCE_DATABASE_ENV,
    GENESIS_RULE_SHA256_ENV,
    SOURCE_HANDLING_PROVENANCE_HEADS,
    SOURCE_HANDLING_PROVENANCE_RECORDS,
    VERIFICATION_KEY_ENV,
    VERIFICATION_KEY_SHA256_ENV,
    SourceHandlingProvenanceAuthorityRepository,
    production_provenance_resolver,
)

SIGNING_KEY_ENV = bootstrap.SIGNING_KEY_ENV

EVIDENCE_STRENGTH = "AUTHORITATIVE_SOURCE_EVIDENCE"
EVIDENCE_METHOD = "SOURCE_TERMS_VERIFIED"
VERIFIER_TYPE = "SOURCE_VERIFIER"
AUTHORIZATION_EXPIRY = timedelta(minutes=10)

# The exact read-only provenance resolver the issuer runtime uses, so the
# operator path validates the same strict-known antecedents the live edge would.
PROVENANCE_RESOLVER = production_provenance_resolver

# Exactly the four durable payload categories ``_issue_intake_durable_payload``
# emits for the Issue Source path, each mapped to its governed category code.
FIELD_CATEGORY_REGISTRY_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "content_derived_ids": ("CONTENT_DERIVED_ID",),
    "locator_urls": ("LOCATOR_URL",),
    "source_derived_text": ("SOURCE_DERIVED_TEXT",),
    "intake_metadata": ("OPERATIONAL_METADATA",),
}

_REPOSITORY_DEFAULTS: Mapping[str, str] = {
    "sensitivity": "PUBLIC",
    "persistence_restriction": "FULL_CONTENT_ALLOWED",
    "processing_decision": "ALLOW",
    "retention_decision": "ALLOW",
    "reconstruction_decision": "ALLOW",
    "access_decision": "ALLOW",
    "deletion_lifecycle_decision": "ALLOW",
}


def _assert_canonical_provenance_values() -> None:
    """Fail fast if the canonical E2E evidence values leave the governed vocabularies."""
    if EVIDENCE_STRENGTH not in PERMISSIVE_EVIDENCE_STRENGTHS:
        raise SourceHandlingBlockedError(
            f"evidence strength {EVIDENCE_STRENGTH!r} is not permitted by the production rule"
        )
    if EVIDENCE_METHOD not in SUPPORTED_EVIDENCE_METHODS:
        raise SourceHandlingBlockedError(f"evidence method {EVIDENCE_METHOD!r} is unsupported")
    if VERIFIER_TYPE not in SUPPORTED_VERIFIER_TYPES:
        raise SourceHandlingBlockedError(f"verifier type {VERIFIER_TYPE!r} is unsupported")


def _authorization_from_arguments(arguments: argparse.Namespace) -> IssueAgentAuthorization:
    if not arguments.issue_body.strip():
        raise ValueError("an authorized Issue must carry body content to execute")
    return IssueAgentAuthorization(
        repository=arguments.repository,
        issue_number=arguments.issue_number,
        issue_url=arguments.issue_url,
        issue_title=arguments.issue_title,
        issue_body=arguments.issue_body,
        authorized_by=arguments.authorized_by,
        authorization_label=arguments.authorization_label,
        issue_updated_at=arguments.issue_updated_at,
        authorization_id=arguments.authorization_id,
    )


def _validate_operator_configuration(
    environ: Mapping[str, str],
    database: str,
    signing_key: bytes,
    rule: Mapping[str, Any],
) -> None:
    """Fail closed unless the runtime provenance environment matches this exact provisioning."""
    verification_key_hex, verification_key_sha256, genesis_rule_sha256 = bootstrap._derived_digests(signing_key, rule)
    configured_database = environ.get(EVIDENCE_DATABASE_ENV)
    if not configured_database or os.path.abspath(configured_database) != os.path.abspath(database):
        raise SourceHandlingBlockedError(
            f"{EVIDENCE_DATABASE_ENV} must resolve to the exact provisioning database; "
            "export it and the three bootstrap-derived HUNTER_SOURCE_HANDLING_* outputs for the issuer runtime"
        )
    observed = {
        VERIFICATION_KEY_ENV: (environ.get(VERIFICATION_KEY_ENV) or "").strip(),
        VERIFICATION_KEY_SHA256_ENV: (environ.get(VERIFICATION_KEY_SHA256_ENV) or "").strip(),
        GENESIS_RULE_SHA256_ENV: (environ.get(GENESIS_RULE_SHA256_ENV) or "").strip(),
    }
    if not all(observed.values()):
        raise SourceHandlingBlockedError("Source Handling operator configuration is incomplete")
    if observed[VERIFICATION_KEY_ENV] != verification_key_hex:
        raise SourceHandlingBlockedError("configured verification key does not match this provisioning signing key")
    if observed[VERIFICATION_KEY_SHA256_ENV] != verification_key_sha256:
        raise SourceHandlingBlockedError("configured verification key sha256 does not match the pinned operator root")
    if observed[GENESIS_RULE_SHA256_ENV] != genesis_rule_sha256:
        raise SourceHandlingBlockedError("configured genesis rule sha256 does not match the pinned operator root")


def _issue_updated_at(authorization: IssueAgentAuthorization) -> datetime:
    return _aware_utc("Issue updated_at", _parse_time(authorization.issue_updated_at))


@dataclass(frozen=True)
class _FactOptions:
    sensitivity: str
    operation_restrictions: tuple[str, ...]
    persistence_restriction: str
    secret_presence: tuple[str, ...]


@dataclass(frozen=True)
class _PolicyOptions:
    processing_decision: str
    retention_decision: str
    reconstruction_decision: str
    access_decision: str
    deletion_lifecycle_decision: str
    persist_disposition: str
    read_access_disposition: str
    reconstruct_disposition: str
    delete_or_expire_disposition: str


def _fact_payload(
    document_id: str,
    at: datetime,
    *,
    options: _FactOptions,
) -> dict[str, Any]:
    return {
        "scope": document_id,
        "fact": {
            "sensitivity": options.sensitivity,
            "operation_restrictions": list(options.operation_restrictions),
            "persistence_restriction": options.persistence_restriction,
            "secret_presence": list(options.secret_presence),
            "sensitivity_known": True,
            "operation_restrictions_known": True,
            "persistence_restriction_known": True,
            "secret_presence_known": True,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        "effective_from": at,
        "recorded_at": at,
        "known_at": at,
    }


def _registry_payload(document_id: str, at: datetime, *, registry_id: str) -> dict[str, Any]:
    return {
        "scope": f"registry:{document_id}:v1",
        "field_category_registry_id": registry_id,
        "field_map": {name: list(categories) for name, categories in FIELD_CATEGORY_REGISTRY_FIELD_MAP.items()},
        "safe_control_proofs": {},
        "effective_from": at,
        "recorded_at": at,
        "known_at": at,
    }


def _policy_payload(
    document_id: str,
    at: datetime,
    *,
    registry_id: str,
    options: _PolicyOptions,
) -> dict[str, Any]:
    categories = sorted({category for names in FIELD_CATEGORY_REGISTRY_FIELD_MAP.values() for category in names})
    durable_dispositions: dict[str, dict[str, str]] = {
        category: {
            "PERSIST": options.persist_disposition,
            "READ_ACCESS": options.read_access_disposition,
            "RECONSTRUCT": options.reconstruct_disposition,
            "DELETE_OR_EXPIRE": options.delete_or_expire_disposition,
        }
        for category in categories
    }
    return {
        "scope": f"policy:{document_id}:v1",
        "field_category_registry_id": registry_id,
        "policy_body": {
            "processing_decision": options.processing_decision,
            "retention_decision": options.retention_decision,
            "reconstruction_decision": options.reconstruction_decision,
            "access_decision": options.access_decision,
            "deletion_lifecycle_decision": options.deletion_lifecycle_decision,
            "durable_dispositions": durable_dispositions,
        },
        "effective_from": at,
        "recorded_at": at,
        "known_at": at,
    }


def _provenance_record_id(
    *,
    provenance_id: str,
    provenance_kind: str,
    authority_identity: str,
    at: datetime,
    evidence_strength: str | None,
    evidence_method: str | None,
    verifier_type: str | None,
) -> str:
    """Content-addressed provenance identity, byte-for-byte as ``record_provenance`` derives it."""
    timestamp_bound = {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "evidence_strength": evidence_strength,
        "evidence_method": evidence_method,
        "verifier_type": verifier_type,
        "authority_identity": authority_identity,
        "effective_from": _time_text(at),
        "recorded_at": _time_text(at),
        "known_at": _time_text(at),
    }
    return hashlib.sha256(_canonical_json(timestamp_bound).encode("utf-8")).hexdigest()


def _provenance_plans(
    *,
    document_id: str,
    authority_identity: str,
    at: datetime,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "provenance_id": provenance_id,
            "provenance_kind": provenance_kind,
            "authority_identity": authority_identity,
            "evidence_strength": EVIDENCE_STRENGTH if provenance_kind == "EVIDENCE" else None,
            "evidence_method": EVIDENCE_METHOD if provenance_kind == "EVIDENCE" else None,
            "verifier_type": VERIFIER_TYPE if provenance_kind == "VERIFIER" else None,
        }
        for provenance_id, provenance_kind in (
            (f"evidence:auth:fact:{document_id}", "EVIDENCE"),
            (f"verifier:auth:fact:{document_id}", "VERIFIER"),
            (f"evidence:auth:registry:{document_id}", "EVIDENCE"),
            (f"verifier:auth:registry:{document_id}", "VERIFIER"),
            (f"evidence:auth:policy:{document_id}", "EVIDENCE"),
            (f"verifier:auth:policy:{document_id}", "VERIFIER"),
        )
    )


def _check_provenance_heads_exact(
    database: str,
    plans: Sequence[Mapping[str, Any]],
    at: datetime,
) -> None:
    """Fail closed before any write if an identity already heads other content."""
    connection = sqlite3.connect(database)
    try:
        for plan in plans:
            row = connection.execute(
                f"SELECT current_record_id FROM {SOURCE_HANDLING_PROVENANCE_HEADS} "
                "WHERE provenance_id = ? AND provenance_kind = ?",
                (plan["provenance_id"], plan["provenance_kind"]),
            ).fetchone()
            if row is None:
                continue
            expected = _provenance_record_id(
                provenance_id=plan["provenance_id"],
                provenance_kind=plan["provenance_kind"],
                authority_identity=plan["authority_identity"],
                at=at,
                evidence_strength=plan["evidence_strength"],
                evidence_method=plan["evidence_method"],
                verifier_type=plan["verifier_type"],
            )
            if row[0] != expected:
                raise SourceHandlingBlockedError(
                    f"provenance identity {plan['provenance_id']} already heads different content; "
                    "fix the inputs or pin --as-of to the original provisioning instant instead of superseding"
                )
    finally:
        connection.close()


def _max_provenance_admission(database: str) -> datetime | None:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(f"SELECT MAX(admission_time) FROM {SOURCE_HANDLING_PROVENANCE_RECORDS}").fetchone()
        if row is None or row[0] is None:
            return None
        return _parse_time(str(row[0]))
    finally:
        connection.close()


def _family_plans(
    *,
    document_id: str,
    at: datetime,
    rule_id: str,
    fact_options: _FactOptions,
    policy_options: _PolicyOptions,
) -> tuple[dict[str, Any], ...]:
    registry_id = f"registry:{document_id}:v1"
    return (
        {
            "family": "FACT",
            "scope": document_id,
            "rule_id": rule_id,
            "payload": _fact_payload(document_id, at, options=fact_options),
            "authorization_id": f"auth:fact:{document_id}",
            "evidence_id": f"evidence:auth:fact:{document_id}",
            "verifier_id": f"verifier:auth:fact:{document_id}",
        },
        {
            "family": "FIELD_CATEGORY_REGISTRY",
            "scope": registry_id,
            "rule_id": rule_id,
            "payload": _registry_payload(document_id, at, registry_id=registry_id),
            "authorization_id": f"auth:registry:{document_id}",
            "evidence_id": f"evidence:auth:registry:{document_id}",
            "verifier_id": f"verifier:auth:registry:{document_id}",
        },
        {
            "family": "POLICY",
            "scope": f"policy:{document_id}:v1",
            "rule_id": rule_id,
            "payload": _policy_payload(document_id, at, registry_id=registry_id, options=policy_options),
            "authorization_id": f"auth:policy:{document_id}",
            "evidence_id": f"evidence:auth:policy:{document_id}",
            "verifier_id": f"verifier:auth:policy:{document_id}",
        },
    )


def _require_rule_strict_known(
    database: str,
    signing_key: bytes,
    operator_root: SourceHandlingOperatorRoot,
    at: datetime,
) -> None:
    """Fail closed unless the genesis rule is strict-known at the as-of instant."""
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=signing_key,
        operator_root=operator_root,
        provenance_resolver=PROVENANCE_RESOLVER,
    )
    view = service.resolver()("source-handling-rule-check", datetime.now(UTC)).store
    try:
        resolve_canonical_head(view, family="AUTHORIZATION_RULE", scope=SOURCE_HANDLING_RULE_SCOPE, cutoff=at)
    except SourceHandlingBlockedError as error:
        raise SourceHandlingBlockedError(
            "the as-of instant is earlier than the bootstrap admission of the genesis authorization rule, "
            "so the derived authority records could not be strict-known; provision as of a later instant"
        ) from error


def _expected_authority_record_id(plan: Mapping[str, Any]) -> str:
    """The content-addressed record id the would-be publication must produce, byte-for-byte as ``_publish`` derives it."""
    return hashlib.sha256(_canonical_json(_plain_mapping(plan["payload"])).encode("utf-8")).hexdigest()


def _check_authority_heads_exact(
    *,
    database: str,
    signing_key: bytes,
    operator_root: SourceHandlingOperatorRoot,
    plans: Sequence[Mapping[str, Any]],
) -> None:
    """Fail closed before any provenance write if an authority head already heads other content.

    Reads each current canonical head through the repository's own resolver store
    (``current_canonical_head_id``), never hand-written SQL. An existing head that
    differs from the derived content proves this run would have to replace
    provisioned state, so it fails closed with zero writes instead of superseding.
    """
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=signing_key,
        operator_root=operator_root,
        provenance_resolver=PROVENANCE_RESOLVER,
    )
    store = service.resolver()("provision-authority-precheck", datetime.now(UTC)).store
    for plan in plans:
        current_head = store.current_canonical_head_id(plan["family"], plan["scope"])
        if current_head is None:
            continue
        if current_head != _expected_authority_record_id(plan):
            raise SourceHandlingBlockedError(
                f"existing {plan['family']} head for scope {plan['scope']!r} does not match the derived content; "
                "refusing to replace provisioned authority state"
            )


def _provision_authority_record(
    *,
    database: str,
    signing_key: bytes,
    operator_root: SourceHandlingOperatorRoot,
    at: datetime,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    service = SourceHandlingAuthorityService(
        database,
        signing_private_key=signing_key,
        operator_root=operator_root,
        provenance_resolver=PROVENANCE_RESOLVER,
    )
    now = datetime.now(UTC)
    store = service.resolver()(f"provision-{plan['family']}", now).store
    payload = plan["payload"]
    expected_record_id = _expected_authority_record_id(plan)
    current_head = store.current_canonical_head_id(plan["family"], plan["scope"])
    if current_head == expected_record_id:
        return {"record_id": expected_record_id, "status": "already-provisioned"}
    if current_head is not None:
        raise SourceHandlingBlockedError(
            f"existing {plan['family']} head for scope {plan['scope']!r} does not match the derived content; "
            "refusing to replace provisioned authority state"
        )
    authorization = service.issue_authorization(
        publication_kind=plan["family"],
        governed_subject_scope=plan["scope"],
        payload=payload,
        authorization_rule_id=plan["rule_id"],
        expected_current_head_id=None,
        evidence_ids=(plan["evidence_id"],),
        evidence_strength=EVIDENCE_STRENGTH,
        evidence_method=EVIDENCE_METHOD,
        verifier_ids=(plan["verifier_id"],),
        verifier_type=VERIFIER_TYPE,
        effective_from=at,
        recorded_at=at,
        known_at=at,
        expires_at=now + AUTHORIZATION_EXPIRY,
        authorization_id=plan["authorization_id"],
    )
    result = service.publish(
        family=plan["family"],
        scope=plan["scope"],
        expected_current_head_id=None,
        payload=payload,
        authorization=authorization,
    )
    if result.record_id != expected_record_id:
        raise SourceHandlingBlockedError(
            f"provisioned {plan['family']} record id does not match the derived content identity"
        )
    return {"record_id": result.record_id, "status": "provisioned"}


def _options_from_arguments(arguments: argparse.Namespace) -> tuple[_FactOptions, _PolicyOptions]:
    fact_options = _FactOptions(
        sensitivity=arguments.sensitivity,
        operation_restrictions=tuple(arguments.operation_restriction),
        persistence_restriction=arguments.persistence_restriction,
        secret_presence=tuple(arguments.secret_presence),
    )
    policy_options = _PolicyOptions(
        processing_decision=arguments.processing_decision,
        retention_decision=arguments.retention_decision,
        reconstruction_decision=arguments.reconstruction_decision,
        access_decision=arguments.access_decision,
        deletion_lifecycle_decision=arguments.deletion_lifecycle_decision,
        persist_disposition=arguments.persist_disposition,
        read_access_disposition=arguments.read_access_disposition,
        reconstruct_disposition=arguments.reconstruct_disposition,
        delete_or_expire_disposition=arguments.delete_or_expire_disposition,
    )
    return fact_options, policy_options


def _run(
    database: str,
    signing_key: bytes,
    rule: Mapping[str, Any],
    *,
    authorization: IssueAgentAuthorization,
    fact_options: _FactOptions,
    policy_options: _PolicyOptions,
    provenance_authority_identity: str,
    as_of: datetime | None,
) -> dict[str, Any]:
    _assert_canonical_provenance_values()
    _, verification_key_sha256, genesis_rule_sha256 = bootstrap._derived_digests(signing_key, rule)
    operator_root = SourceHandlingOperatorRoot(
        genesis_rule_sha256=genesis_rule_sha256,
        verification_key_sha256=verification_key_sha256,
    )
    authorization_rule_id = bootstrap._expected_genesis_record_id(rule)
    document_id = issue_agent_document_id(authorization)
    issued_at = _issue_updated_at(authorization)
    rule_known_at = _parse_time(rule["known_at"])
    on_or_after = as_of if as_of is not None else datetime.now(UTC)
    on_or_after = _aware_utc("provisioning as-of", on_or_after)
    if on_or_after > datetime.now(UTC):
        raise SourceHandlingBlockedError("the provisioning as-of must not be in the future")
    if on_or_after < rule_known_at:
        raise SourceHandlingBlockedError(
            f"the provisioning as-of {_time_text(on_or_after)} predates the genesis authorization rule "
            f"({_time_text(rule_known_at)}); no derived authority record could be strict-known before it"
        )
    if on_or_after < issued_at:
        raise SourceHandlingBlockedError(
            f"the provisioning as-of {_time_text(on_or_after)} predates the Issue's updated_at "
            f"({_time_text(issued_at)}); a classification fact cannot be known before the Issue state it describes"
        )

    provenance_plans = _provenance_plans(
        document_id=document_id,
        authority_identity=provenance_authority_identity,
        at=on_or_after,
    )
    # Construction initializes the provenance schema (idempotently), so the
    # exact-match head pre-check below can read the current heads deterministically.
    provenance_repository = SourceHandlingProvenanceAuthorityRepository(
        database,
        signing_private_key=signing_key,
        operator_root=operator_root,
    )
    _check_provenance_heads_exact(database, provenance_plans, on_or_after)
    _require_rule_strict_known(database, signing_key, operator_root, on_or_after)

    # Fail closed on an existing but mismatched authority head BEFORE any
    # provenance record is written. The would-be authority records are derived
    # at the later of the operator as-of and the provenance admission already in
    # the database; on an exactly-pinned re-run no provenance write changes that
    # admission, so this preview coincides with the records the write phase
    # derives and an exact-match head stays on the idempotent path. A mismatch
    # here therefore proves the run would have to replace provisioned authority
    # state, and it stops with zero writes.
    preview_admission = _max_provenance_admission(database)
    preview_at = max(on_or_after, preview_admission) if preview_admission is not None else on_or_after
    preview_plans = _family_plans(
        document_id=document_id,
        at=preview_at,
        rule_id=authorization_rule_id,
        fact_options=fact_options,
        policy_options=policy_options,
    )
    _check_authority_heads_exact(
        database=database,
        signing_key=signing_key,
        operator_root=operator_root,
        plans=preview_plans,
    )

    for plan in provenance_plans:
        provenance_repository.record_provenance(
            provenance_id=plan["provenance_id"],
            provenance_kind=plan["provenance_kind"],
            authority_identity=plan["authority_identity"],
            effective_from=on_or_after,
            recorded_at=on_or_after,
            known_at=on_or_after,
            evidence_strength=plan["evidence_strength"],
            evidence_method=plan["evidence_method"],
            verifier_type=plan["verifier_type"],
        )

    # The Source Handling contract admits records at physical wall-clock time, so
    # an authority record's claimed known_at can never backdate past admission.
    # The provenance antecedents are claimed at the operator as-of (exactly as
    # the genesis rule's provenance predates its bootstrap admission); each
    # derived authority record's known_at is therefore the later of the operator
    # as-of and the highest provenance admission just written. That bound is a
    # deterministic function of the pinned as-of, so an exactly-pinned re-run
    # derives identical records and stays already-provisioned.
    predefined = _max_provenance_admission(database)
    authority_at = max(on_or_after, predefined) if predefined is not None else on_or_after

    plans = _family_plans(
        document_id=document_id,
        at=authority_at,
        rule_id=authorization_rule_id,
        fact_options=fact_options,
        policy_options=policy_options,
    )
    records: dict[str, Any] = {}
    for plan in plans:
        records[plan["family"]] = _provision_authority_record(
            database=database,
            signing_key=signing_key,
            operator_root=operator_root,
            at=authority_at,
            plan=plan,
        )
    status = (
        "provisioned" if any(entry["status"] == "provisioned" for entry in records.values()) else "already-provisioned"
    )
    return {
        "database": database,
        "document_id": document_id,
        "status": status,
        "as_of": _time_text(on_or_after),
        "records": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/provision_source_handling_issue_authority.py",
        description="Provision the per-Issue Source Handling authority records for Railway E2E.",
    )
    parser.add_argument(
        "--database", required=True, help="explicit evidence database path (e.g. /data/evidence.sqlite)"
    )
    parser.add_argument(
        "--signing-key-file",
        default=None,
        help=f"path to a file containing the hex-encoded Ed25519 signing key (mutually exclusive with ${SIGNING_KEY_ENV})",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="provisioning instant (ISO-8601 UTC) claimed by the EVIDENCE/VERIFIER provenance records; "
        "pin the first run's printed as_of value for an exactly idempotent re-run",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable non-secret JSON on success")
    parser.add_argument(
        "--repository", required=True, help="the authorized repository owner/name, e.g. fafa33/Project-Hunter"
    )
    parser.add_argument("--issue-number", required=True, type=int, help="the authorized Issue number")
    parser.add_argument("--issue-url", required=True, help="the authorized Issue URL")
    parser.add_argument("--issue-title", required=True, help="the authorized Issue title")
    parser.add_argument("--issue-body", required=True, help="the authorized Issue body content")
    parser.add_argument("--authorized-by", required=True, help="the GitHub login that authorized the Issue")
    parser.add_argument(
        "--issue-updated-at", required=True, help="the Issue updated_at ISO-8601 timestamp from the trigger"
    )
    parser.add_argument(
        "--authorization-id", required=True, help="the trigger's hunter-issue-agent-authorization-v1 authorization_id"
    )
    parser.add_argument(
        "--authorization-label", default=ISSUE_AGENT_AUTHORIZATION_LABEL, help="authorization label (governed constant)"
    )
    parser.add_argument(
        "--sensitivity", default=_REPOSITORY_DEFAULTS["sensitivity"], help="FACT sensitivity classification"
    )
    parser.add_argument(
        "--operation-restriction", action="append", default=[], help="FACT operation restriction (repeatable)"
    )
    parser.add_argument(
        "--persistence-restriction",
        default=_REPOSITORY_DEFAULTS["persistence_restriction"],
        help="FACT persistence restriction",
    )
    parser.add_argument(
        "--secret-presence", action="append", default=[], help="FACT secret presence marker (repeatable)"
    )
    for field in (
        "processing_decision",
        "retention_decision",
        "reconstruction_decision",
        "access_decision",
        "deletion_lifecycle_decision",
    ):
        parser.add_argument(f"--{field}", default=_REPOSITORY_DEFAULTS[field], help=f"top-level policy {field}")
    for axis in ("PERSIST", "READ_ACCESS", "RECONSTRUCT", "DELETE_OR_EXPIRE"):
        parser.add_argument(f"--{axis.lower()}-disposition", default="ALLOW", help=f"durable disposition for {axis}")
    parser.add_argument(
        "--authority-identity",
        default=AUTHORITY_COMPONENT_ID,
        help="provenance authority_identity recorded with each EVIDENCE/VERIFIER record",
    )
    arguments = parser.parse_args(argv)

    try:
        signing_key = bootstrap._load_signing_key(environ=os.environ, signing_key_file=arguments.signing_key_file)
        rule = bootstrap._load_production_rule()
        _validate_operator_configuration(os.environ, arguments.database, signing_key, rule)
        authorization = _authorization_from_arguments(arguments)
        fact_options, policy_options = _options_from_arguments(arguments)
        as_of = _parse_time(arguments.as_of) if arguments.as_of else None
        outcome = _run(
            arguments.database,
            signing_key,
            rule,
            authorization=authorization,
            fact_options=fact_options,
            policy_options=policy_options,
            provenance_authority_identity=arguments.authority_identity,
            as_of=as_of,
        )
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
        SourceHandlingBlockedError,
        IssueAgentAuthorizationError,
    ) as error:
        parser.error(str(error))
    if arguments.json:
        print(json.dumps(outcome, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        print(f"[source-handling-issue-provisioning] {outcome['status']}")
        print(f"database={outcome['database']}")
        print(f"document_id={outcome['document_id']}")
        print(f"as_of={outcome['as_of']}")
        for family, entry in outcome["records"].items():
            print(f"{family}={entry['record_id']} ({entry['status']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
