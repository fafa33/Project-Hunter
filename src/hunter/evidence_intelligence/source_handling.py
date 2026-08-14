from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence


class SourceHandlingBlockedError(RuntimeError):
    """Raised when Source Handling Authority cannot produce a governed result."""


@dataclass(frozen=True)
class PublicationAuthorization:
    publication_kind: str
    governed_subject_scope: str
    authorized_payload_sha256: str
    authorization_rule_id: str
    effective_from: datetime
    recorded_at: datetime
    known_at: datetime


class AuthorityStore:
    """Small deterministic authority store used by the V1 authority surface.

    Publication is append-only per (family, scope).  The compare-and-append
    primitive prevents two canonical successors from being created from the
    same head.  Higher-level publication paths remain responsible for governed
    authorization checks before calling the primitive.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def publish(
        self,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        record: Mapping[str, Any],
    ) -> None:
        if record.get("publication_authorization") is None:
            raise SourceHandlingBlockedError("governed publication authorization required")
        self.compare_and_append(
            family=family,
            scope=scope,
            expected_current_head_id=expected_current_head_id,
            record=record,
        )

    def direct_write(
        self,
        *,
        family: str,
        scope: str,
        record: Mapping[str, Any],
    ) -> None:
        del family, scope, record
        raise SourceHandlingBlockedError("direct repository authority writes are forbidden")

    def compare_and_append(
        self,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        record: Mapping[str, Any],
    ) -> None:
        key = (family, scope)
        records = self._records.setdefault(key, [])
        current = _record_id(records[-1]) if records else None
        if current != expected_current_head_id:
            raise SourceHandlingBlockedError("authority head changed; re-resolution required")
        candidate = dict(record)
        candidate_id = _record_id(candidate)
        if candidate_id is None:
            raise SourceHandlingBlockedError("authority record identity is required")
        if any(_record_id(existing) == candidate_id for existing in records):
            raise SourceHandlingBlockedError("authority record identity must be immutable")
        records.append(candidate)

    def current_head_id(self, family: str, scope: str) -> str | None:
        records = self._records.get((family, scope), [])
        return _record_id(records[-1]) if records else None

    def records(self, family: str, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(dict(record) for record in self._records.get((family, scope), []))


def canonical_publication_digest(
    publication_kind: str,
    governed_subject_scope: str,
    payload: object,
) -> str:
    envelope = {
        "domain": "HUNTER_SOURCE_HANDLING_PUBLICATION_V1",
        "publication_kind": publication_kind,
        "governed_subject_scope": governed_subject_scope,
        "payload": payload,
    }
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publication_authorization(
    *,
    publication_kind: str,
    governed_subject_scope: str,
    authorized_payload_sha256: str,
    authorization_rule_id: str,
    effective_from: datetime,
    recorded_at: datetime,
    known_at: datetime,
) -> PublicationAuthorization:
    return PublicationAuthorization(
        publication_kind=publication_kind,
        governed_subject_scope=governed_subject_scope,
        authorized_payload_sha256=authorized_payload_sha256,
        authorization_rule_id=authorization_rule_id,
        effective_from=effective_from,
        recorded_at=recorded_at,
        known_at=known_at,
    )


def verify_publication(
    authorization: PublicationAuthorization,
    publication_kind: str,
    governed_subject_scope: str,
    payload: object,
) -> None:
    expected = canonical_publication_digest(
        publication_kind,
        governed_subject_scope,
        payload,
    )
    if authorization.publication_kind != publication_kind:
        raise SourceHandlingBlockedError("publication kind does not match authorization")
    if authorization.governed_subject_scope != governed_subject_scope:
        raise SourceHandlingBlockedError("publication scope does not match authorization")
    if authorization.authorized_payload_sha256 != expected:
        raise SourceHandlingBlockedError("publication payload does not match authorization")


def publish_genesis_rule(
    store: AuthorityStore,
    rule: Mapping[str, Any],
    *,
    expected_golden_sha256: str,
) -> None:
    digest = _canonical_object_sha256(rule)
    if digest != expected_golden_sha256:
        raise SourceHandlingBlockedError("authorization-rule bootstrap digest mismatch")
    if rule.get("authorization_rule_id") != "AUTHORIZATION_RULE_V1":
        raise SourceHandlingBlockedError("unexpected authorization-rule bootstrap identity")
    if store.current_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING") is not None:
        raise SourceHandlingBlockedError("a second authorization-rule genesis is forbidden")
    record = dict(rule)
    record["id"] = str(rule["authorization_rule_id"])
    store.compare_and_append(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id=None,
        record=record,
    )


def publish_successor_rule(
    store: AuthorityStore,
    successor: Mapping[str, Any],
    *,
    authorizing_rule_id: str,
    historical_cutoff: datetime | None = None,
) -> None:
    successor_id = str(successor.get("authorization_rule_id", ""))
    if not successor_id:
        raise SourceHandlingBlockedError("successor authorization-rule identity required")
    if successor_id == authorizing_rule_id:
        raise SourceHandlingBlockedError("an authorization rule cannot authorize itself")

    current_id = store.current_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING")
    if current_id is None or current_id != authorizing_rule_id:
        raise SourceHandlingBlockedError("successor must be authorized by the exact current rule")

    current_records = store.records("AUTHORIZATION_RULE", "SOURCE_HANDLING")
    current = current_records[-1]
    if historical_cutoff is not None and not strict_known_eligible(
        current,
        historical_cutoff,
    ):
        raise SourceHandlingBlockedError("authorizing rule was not strict-known at cutoff")

    supersedes = successor.get(
        "supersedes_authorization_rule_id",
        successor.get("supersedes_rule_record_id"),
    )
    if supersedes != current_id:
        raise SourceHandlingBlockedError("successor must supersede the exact current rule")

    record = dict(successor)
    record["id"] = successor_id
    store.compare_and_append(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id=current_id,
        record=record,
    )


def validate_permission_evidence(
    *,
    evidence_strength: str,
    evidence_method: str,
    verifier_type: str,
    requested_change: str,
    authorization_rule: Mapping[str, Any],
    released_restrictions: set[str] | None = None,
) -> None:
    body = authorization_rule.get("rule_body")
    if not isinstance(body, Mapping):
        raise SourceHandlingBlockedError("authorization-rule body unavailable")

    if requested_change == "MORE_RESTRICTIVE":
        if (
            evidence_strength == "OBSERVED_RESTRICTIVE_SIGNAL"
            and evidence_method == "AUTOMATED_RESTRICTIVE_DETECTOR"
            and verifier_type == "DETECTOR"
        ):
            return
        if evidence_strength in {
            "AUTHORITATIVE_SOURCE_EVIDENCE",
            "INDEPENDENT_VERIFIED_EVIDENCE",
        }:
            return
        raise SourceHandlingBlockedError("restrictive change lacks admissible evidence")

    if requested_change not in {
        "PERMISSIVE_GENESIS",
        "LESS_RESTRICTIVE",
        "RESTRICTION_RELEASE",
    }:
        raise SourceHandlingBlockedError("unknown permission change")

    strengths = set(_string_sequence(body.get("permissive_evidence_strengths")))
    forbidden_methods = set(_string_sequence(body.get("forbidden_permission_methods")))
    matrix = body.get("method_to_verifier_types")
    if not isinstance(matrix, Mapping):
        raise SourceHandlingBlockedError("authorization-rule verifier matrix unavailable")
    allowed_verifiers = set(_string_sequence(matrix.get(evidence_method)))

    if evidence_strength not in strengths:
        raise SourceHandlingBlockedError("evidence strength cannot grant permission")
    if evidence_method in forbidden_methods or verifier_type not in allowed_verifiers:
        raise SourceHandlingBlockedError("method/verifier pair cannot grant permission")
    if requested_change in {"LESS_RESTRICTIVE", "RESTRICTION_RELEASE"}:
        if body.get("require_release_restriction_enumeration") is True and not released_restrictions:
            raise SourceHandlingBlockedError("released restrictions must be enumerated")


def strict_known_eligible(record: Mapping[str, Any], cutoff: datetime) -> bool:
    for field in ("effective_from", "recorded_at", "known_at"):
        value = _as_datetime(record.get(field))
        if value is None or value > cutoff:
            return False
    return True


def strict_known_head(
    records: Sequence[Mapping[str, Any]],
    *,
    cutoff: datetime,
    scope: str,
) -> Mapping[str, Any]:
    eligible = [
        record
        for record in records
        if record.get("scope") == scope and strict_known_eligible(record, cutoff)
    ]
    if not eligible:
        raise SourceHandlingBlockedError("no strict-known authority head")

    superseded: set[str] = set()
    for record in eligible:
        predecessor = _supersedes_id(record)
        if predecessor is not None:
            superseded.add(predecessor)
    heads = [record for record in eligible if _record_id(record) not in superseded]
    if len(heads) != 1:
        raise SourceHandlingBlockedError("strict-known authority history has divergent heads")
    return heads[0]


def authority_store() -> AuthorityStore:
    return AuthorityStore()


def restrictive_fact_join(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not facts:
        raise SourceHandlingBlockedError("at least one handling fact is required")

    sensitivity_order = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }
    persistence_order = {
        "FULL_CONTENT_ALLOWED": 0,
        "DERIVED_ONLY": 1,
        "METADATA_ONLY": 2,
        "NO_PERSISTENCE": 3,
    }

    sensitivity = "PUBLIC"
    persistence = "FULL_CONTENT_ALLOWED"
    restrictions: set[str] = set()
    secret_presence: set[str] = set()
    withdrawn = False
    deleted_at_source = False
    historically_unavailable = False

    for fact in facts:
        if fact.get("operation_restrictions_known") is not True:
            raise SourceHandlingBlockedError("operation restrictions are not known")
        if fact.get("secret_presence_known") is not True:
            raise SourceHandlingBlockedError("secret presence is not known")
        if fact.get("availability_known") is not True:
            raise SourceHandlingBlockedError("source availability is not known")

        candidate_sensitivity = str(fact.get("sensitivity"))
        candidate_persistence = str(fact.get("persistence_restriction"))
        if candidate_sensitivity not in sensitivity_order:
            raise SourceHandlingBlockedError("sensitivity is unknown or unsupported")
        if candidate_persistence not in persistence_order:
            raise SourceHandlingBlockedError("persistence restriction is unknown or unsupported")

        if sensitivity_order[candidate_sensitivity] > sensitivity_order[sensitivity]:
            sensitivity = candidate_sensitivity
        if persistence_order[candidate_persistence] > persistence_order[persistence]:
            persistence = candidate_persistence

        restrictions.update(_string_sequence(fact.get("operation_restrictions")))
        secret_presence.update(_string_sequence(fact.get("secret_presence")))
        withdrawn = withdrawn or bool(fact.get("withdrawn"))
        deleted_at_source = deleted_at_source or bool(fact.get("deleted_at_source"))
        historically_unavailable = historically_unavailable or bool(
            fact.get("historically_unavailable")
        )

    return {
        "sensitivity": sensitivity,
        "operation_restrictions": restrictions,
        "persistence_restriction": persistence,
        "secret_presence": secret_presence,
        "operation_restrictions_known": True,
        "secret_presence_known": True,
        "withdrawn": withdrawn,
        "deleted_at_source": deleted_at_source,
        "historically_unavailable": historically_unavailable,
        "availability_known": True,
    }


def lifecycle_join(values: Sequence[str]) -> str:
    order = {"ALLOW": 0, "EXPIRE": 1, "DELETE": 2, "BLOCKED": 3}
    if not values:
        raise SourceHandlingBlockedError("at least one lifecycle disposition is required")
    if any(value not in order for value in values):
        raise SourceHandlingBlockedError("unknown lifecycle disposition")
    return max(values, key=order.__getitem__)


def validate_durable_payload(
    *,
    decision: Mapping[str, Any],
    registry: Mapping[str, Any],
    payload: Mapping[str, Any],
    secret_presence: set[str],
) -> None:
    decision_registry_id = decision.get("field_category_registry_id")
    registry_id = registry.get("field_category_registry_id")
    if not decision_registry_id or decision_registry_id != registry_id:
        raise SourceHandlingBlockedError("exact historical field-category registry required")

    if decision.get("publication_authorization") is not None:
        raise SourceHandlingBlockedError(
            "persistence cannot reverify a publication authorization without its exact subject payload"
        )

    field_map = registry.get("field_map")
    dispositions = decision.get("durable_dispositions")
    if not isinstance(field_map, Mapping) or not isinstance(dispositions, Mapping):
        raise SourceHandlingBlockedError("durable field authority is incomplete")

    protected = bool(secret_presence & {"SECRET_PRESENT", "CREDENTIAL_PRESENT"})
    for field, value in payload.items():
        categories_raw = field_map.get(field)
        categories = _string_sequence(categories_raw)
        if not categories:
            raise SourceHandlingBlockedError("durable field category is unknown or ambiguous")

        for category in categories:
            category_dispositions = dispositions.get(category)
            if not isinstance(category_dispositions, Mapping):
                raise SourceHandlingBlockedError("durable category disposition unavailable")
            if category_dispositions.get("PERSIST") != "ALLOW":
                raise SourceHandlingBlockedError("durable field persistence is not allowed")

        if protected and _derived_from_protected_content(value):
            raise SourceHandlingBlockedError(
                "secret/credential-derived secondary representation cannot persist"
            )


def migrate_legacy(record: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(record)
    migrated.setdefault("publication_authorization", None)
    migrated.setdefault("field_category_registry_id", None)
    migrated.setdefault("authorization_rule_id", None)
    return migrated


def _canonical_object_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def _record_id(record: Mapping[str, Any]) -> str | None:
    for field in (
        "id",
        "authorization_rule_id",
        "source_handling_fact_id",
        "source_handling_policy_id",
        "field_category_registry_id",
    ):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _supersedes_id(record: Mapping[str, Any]) -> str | None:
    for field in (
        "supersedes",
        "supersedes_record_id",
        "supersedes_rule_record_id",
        "supersedes_authorization_rule_id",
    ):
        value = record.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (set, frozenset, list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _derived_from_protected_content(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("derived_from_protected_content") is True
