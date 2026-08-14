from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


AUTHORITY_COMPONENT_ID = "EVIDENCE_INTELLIGENCE_SOURCE_HANDLING_AUTHORITY"
GENESIS_RULE_ID = "AUTHORIZATION_RULE_V1"


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
    authorization_id: str = ""
    authority_component_id: str = AUTHORITY_COMPONENT_ID
    evidence_strength: str | None = None
    evidence_method: str | None = None
    verifier_type: str | None = None
    released_restrictions: frozenset[str] = frozenset()


class AuthorityStore:
    """Append-only store that separates physical append from canonical authority publication.

    ``compare_and_append`` is deliberately only a concurrency primitive. Records written
    through it are not canonical authority unless the Source Handling Authority publication
    path has independently verified and stamped the exact publication authorization.
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
        authorization = record.get("publication_authorization")
        if not isinstance(authorization, PublicationAuthorization):
            raise SourceHandlingBlockedError("governed publication authorization required")

        payload = record.get("publication_payload")
        if payload is None:
            payload = _publication_payload_from_record(record)

        verify_publication(authorization, family, scope, payload)
        if not strict_known_eligible(_authorization_times(authorization), authorization.known_at):
            raise SourceHandlingBlockedError("publication authorization temporal state is invalid")

        rule = self._resolve_rule_for_authorization(authorization)
        _validate_publication_authorization_evidence(
            authorization=authorization,
            authorization_rule=rule,
            requested_change=str(record.get("requested_change", "PERMISSIVE_GENESIS")),
        )

        current = self.current_canonical_head_id(family, scope)
        if current != expected_current_head_id:
            raise SourceHandlingBlockedError("canonical authority head changed; re-resolution required")

        supersedes = _supersedes_id(record)
        if current is None:
            if supersedes is not None:
                raise SourceHandlingBlockedError("genesis publication cannot supersede a record")
        elif supersedes != current:
            raise SourceHandlingBlockedError("successor must supersede the exact canonical head")

        candidate = dict(record)
        candidate["publication_payload"] = payload
        candidate["_publication_verified"] = True
        self.compare_and_append(
            family=family,
            scope=scope,
            expected_current_head_id=self.current_head_id(family, scope),
            record=candidate,
        )

    def direct_write(self, *, family: str, scope: str, record: Mapping[str, Any]) -> None:
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

    def current_canonical_head_id(self, family: str, scope: str) -> str | None:
        records = self.canonical_records(family, scope)
        return _record_id(records[-1]) if records else None

    def records(self, family: str, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(dict(record) for record in self._records.get((family, scope), []))

    def canonical_records(self, family: str, scope: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(record)
            for record in self._records.get((family, scope), [])
            if record.get("_publication_verified") is True
        )

    def canonical_record_by_id(self, family: str, record_id: str) -> dict[str, Any] | None:
        matches: list[dict[str, Any]] = []
        for (stored_family, _scope), records in self._records.items():
            if stored_family != family:
                continue
            matches.extend(
                dict(record)
                for record in records
                if record.get("_publication_verified") is True and _record_id(record) == record_id
            )
        if len(matches) > 1:
            raise SourceHandlingBlockedError("canonical authority identity is ambiguous")
        return matches[0] if matches else None

    def _resolve_rule_for_authorization(self, authorization: PublicationAuthorization) -> Mapping[str, Any]:
        rule = self.canonical_record_by_id("AUTHORIZATION_RULE", authorization.authorization_rule_id)
        if rule is None:
            raise SourceHandlingBlockedError("authorization rule is not canonical")
        if not strict_known_eligible(rule, authorization.known_at):
            raise SourceHandlingBlockedError("authorization rule was not strict-known for publication")
        return rule


def canonical_publication_digest(publication_kind: str, governed_subject_scope: str, payload: object) -> str:
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
    authorization_id: str = "",
    authority_component_id: str = AUTHORITY_COMPONENT_ID,
    evidence_strength: str | None = None,
    evidence_method: str | None = None,
    verifier_type: str | None = None,
    released_restrictions: set[str] | frozenset[str] | None = None,
) -> PublicationAuthorization:
    return PublicationAuthorization(
        publication_kind=publication_kind,
        governed_subject_scope=governed_subject_scope,
        authorized_payload_sha256=authorized_payload_sha256,
        authorization_rule_id=authorization_rule_id,
        effective_from=effective_from,
        recorded_at=recorded_at,
        known_at=known_at,
        authorization_id=authorization_id,
        authority_component_id=authority_component_id,
        evidence_strength=evidence_strength,
        evidence_method=evidence_method,
        verifier_type=verifier_type,
        released_restrictions=frozenset(released_restrictions or ()),
    )


def verify_publication(
    authorization: PublicationAuthorization,
    publication_kind: str,
    governed_subject_scope: str,
    payload: object,
) -> None:
    expected = canonical_publication_digest(publication_kind, governed_subject_scope, payload)
    if authorization.authority_component_id != AUTHORITY_COMPONENT_ID:
        raise SourceHandlingBlockedError("publication authorization authority component mismatch")
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
    if _canonical_object_sha256(rule) != expected_golden_sha256:
        raise SourceHandlingBlockedError("authorization-rule bootstrap digest mismatch")
    if rule.get("authorization_rule_id") != GENESIS_RULE_ID:
        raise SourceHandlingBlockedError("unexpected authorization-rule bootstrap identity")
    if store.current_canonical_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING") is not None:
        raise SourceHandlingBlockedError("a second authorization-rule genesis is forbidden")

    record = dict(rule)
    record["id"] = str(rule["authorization_rule_id"])
    record["scope"] = "SOURCE_HANDLING"
    record["_publication_verified"] = True
    record["_bootstrap_verified"] = True
    store.compare_and_append(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id=store.current_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING"),
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

    current_id = store.current_canonical_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING")
    if current_id is None or current_id != authorizing_rule_id:
        raise SourceHandlingBlockedError("successor must be authorized by the exact current rule")
    current = store.canonical_records("AUTHORIZATION_RULE", "SOURCE_HANDLING")[-1]
    if historical_cutoff is not None and not strict_known_eligible(current, historical_cutoff):
        raise SourceHandlingBlockedError("authorizing rule was not strict-known at cutoff")

    supersedes = successor.get("supersedes_authorization_rule_id", successor.get("supersedes_rule_record_id"))
    if supersedes != current_id:
        raise SourceHandlingBlockedError("successor must supersede the exact current rule")

    authorization = successor.get("publication_authorization")
    if not isinstance(authorization, PublicationAuthorization):
        raise SourceHandlingBlockedError("successor authorization rule requires publication authorization")

    store.publish(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id=current_id,
        record={**successor, "id": successor_id, "scope": "SOURCE_HANDLING"},
    )


def validate_permission_evidence(
    *,
    evidence_strength: str,
    evidence_method: str,
    verifier_type: str,
    requested_change: str,
    authorization_rule: Mapping[str, Any],
    released_restrictions: set[str] | frozenset[str] | None = None,
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
        if evidence_strength in {"AUTHORITATIVE_SOURCE_EVIDENCE", "INDEPENDENT_VERIFIED_EVIDENCE"}:
            return
        raise SourceHandlingBlockedError("restrictive change lacks admissible evidence")

    if requested_change not in {"PERMISSIVE_GENESIS", "LESS_RESTRICTIVE", "RESTRICTION_RELEASE"}:
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
    if (
        requested_change in {"LESS_RESTRICTIVE", "RESTRICTION_RELEASE"}
        and body.get("require_release_restriction_enumeration") is True
        and not released_restrictions
    ):
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
    eligible = [record for record in records if record.get("scope") == scope and strict_known_eligible(record, cutoff)]
    if not eligible:
        raise SourceHandlingBlockedError("no strict-known authority head")
    superseded = {predecessor for record in eligible if (predecessor := _supersedes_id(record)) is not None}
    heads = [record for record in eligible if _record_id(record) not in superseded]
    if len(heads) != 1:
        raise SourceHandlingBlockedError("strict-known authority history has divergent heads")
    return heads[0]


def resolve_canonical_head(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    cutoff: datetime,
) -> Mapping[str, Any]:
    records = store.canonical_records(family, scope)
    if not records:
        raise SourceHandlingBlockedError("canonical historical authority is absent")
    head = strict_known_head(records, cutoff=cutoff, scope=scope)
    _reverify_canonical_record(store, family=family, scope=scope, record=head, cutoff=cutoff)
    return head


def authority_store() -> AuthorityStore:
    return AuthorityStore()


def restrictive_fact_join(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not facts:
        raise SourceHandlingBlockedError("at least one handling fact is required")

    sensitivity_order = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
    persistence_order = {"FULL_CONTENT_ALLOWED": 0, "DERIVED_ONLY": 1, "METADATA_ONLY": 2, "NO_PERSISTENCE": 3}
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
        historically_unavailable = historically_unavailable or bool(fact.get("historically_unavailable"))

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


def derive_source_handling_decision(
    *,
    fact_record: Mapping[str, Any],
    policy_record: Mapping[str, Any],
    registry_record: Mapping[str, Any],
    authorization_rule: Mapping[str, Any],
) -> dict[str, Any]:
    fact = fact_record.get("fact")
    policy = policy_record.get("policy_body")
    if not isinstance(fact, Mapping) or not isinstance(policy, Mapping):
        raise SourceHandlingBlockedError("fact or policy body is incomplete")

    normalized_fact = restrictive_fact_join([fact])
    registry_id = registry_record.get("field_category_registry_id")
    if registry_id != policy_record.get("field_category_registry_id"):
        raise SourceHandlingBlockedError("policy is not bound to the resolved registry")

    required_decisions = (
        "processing_decision",
        "retention_decision",
        "reconstruction_decision",
        "access_decision",
        "deletion_lifecycle_decision",
    )
    for key in required_decisions:
        if policy.get(key) not in {"ALLOW", "DENY", "BLOCKED", "EXPIRE", "DELETE"}:
            raise SourceHandlingBlockedError(f"policy decision is missing or invalid: {key}")

    operation_restrictions = set(_string_sequence(normalized_fact.get("operation_restrictions")))
    if "MODEL_PROCESSING_PROHIBITED" in operation_restrictions and policy.get("processing_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override model-processing prohibition")
    if "RECONSTRUCTION_PROHIBITED" in operation_restrictions and policy.get("reconstruction_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override reconstruction prohibition")
    if "ACCESS_RESTRICTED" in operation_restrictions and policy.get("access_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override access restriction")
    if normalized_fact.get("persistence_restriction") == "NO_PERSISTENCE" and policy.get("retention_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override no-persistence restriction")

    dispositions = policy.get("durable_dispositions")
    if not isinstance(dispositions, Mapping):
        raise SourceHandlingBlockedError("durable dispositions are missing")

    return {
        "fact_record_id": _record_id(fact_record),
        "policy_record_id": _record_id(policy_record),
        "field_category_registry_id": registry_id,
        "authorization_rule_id": _record_id(authorization_rule),
        "processing_decision": policy["processing_decision"],
        "retention_decision": policy["retention_decision"],
        "reconstruction_decision": policy["reconstruction_decision"],
        "access_decision": policy["access_decision"],
        "deletion_lifecycle_decision": policy["deletion_lifecycle_decision"],
        "durable_dispositions": _deep_plain_mapping(dispositions),
    }


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
        raise SourceHandlingBlockedError("caller-supplied publication authorization cannot satisfy persistence")

    field_map = registry.get("field_map")
    dispositions = decision.get("durable_dispositions")
    if not isinstance(field_map, Mapping) or not isinstance(dispositions, Mapping):
        raise SourceHandlingBlockedError("durable field authority is incomplete")

    protected = bool(secret_presence & {"SECRET_PRESENT", "CREDENTIAL_PRESENT"})
    for field, value in payload.items():
        categories = _string_sequence(field_map.get(field))
        if not categories:
            raise SourceHandlingBlockedError("durable field category is unknown or ambiguous")
        for category in categories:
            category_dispositions = dispositions.get(category)
            if not isinstance(category_dispositions, Mapping):
                raise SourceHandlingBlockedError("durable category disposition unavailable")
            if category_dispositions.get("PERSIST") != "ALLOW":
                raise SourceHandlingBlockedError("durable field persistence is not allowed")
        if protected and _derived_from_protected_content(value):
            raise SourceHandlingBlockedError("secret/credential-derived secondary representation cannot persist")


def enforce_persistence(
    store: AuthorityStore,
    *,
    fact_scope: str,
    policy_scope: str,
    cutoff: datetime,
    payload: Mapping[str, Any],
    supplied_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fact_record = resolve_canonical_head(store, family="FACT", scope=fact_scope, cutoff=cutoff)
    policy_record = resolve_canonical_head(store, family="POLICY", scope=policy_scope, cutoff=cutoff)

    registry_id = policy_record.get("field_category_registry_id")
    if not isinstance(registry_id, str) or not registry_id:
        raise SourceHandlingBlockedError("policy-bound registry identity is missing")
    registry_record = store.canonical_record_by_id("FIELD_CATEGORY_REGISTRY", registry_id)
    if registry_record is None or not strict_known_eligible(registry_record, cutoff):
        raise SourceHandlingBlockedError("exact historical field-category registry is unavailable")
    registry_scope = str(registry_record.get("scope", ""))
    _reverify_canonical_record(
        store,
        family="FIELD_CATEGORY_REGISTRY",
        scope=registry_scope,
        record=registry_record,
        cutoff=cutoff,
    )

    rule_ids = _record_authorization_rule_ids(fact_record, policy_record, registry_record)
    if len(rule_ids) != 1:
        raise SourceHandlingBlockedError("authority families do not resolve to one exact authorization rule")
    rule_id = next(iter(rule_ids))
    authorization_rule = store.canonical_record_by_id("AUTHORIZATION_RULE", rule_id)
    if authorization_rule is None or not strict_known_eligible(authorization_rule, cutoff):
        raise SourceHandlingBlockedError("exact historical authorization rule is unavailable")

    decision = derive_source_handling_decision(
        fact_record=fact_record,
        policy_record=policy_record,
        registry_record=registry_record,
        authorization_rule=authorization_rule,
    )
    if supplied_decision is not None and _canonical_comparable(supplied_decision) != _canonical_comparable(decision):
        raise SourceHandlingBlockedError("caller-supplied source-handling decision does not match rederived authority")

    fact = fact_record.get("fact")
    if not isinstance(fact, Mapping):
        raise SourceHandlingBlockedError("resolved fact product is missing")
    secret_presence = set(_string_sequence(fact.get("secret_presence")))
    validate_durable_payload(
        decision=decision,
        registry=registry_record,
        payload=payload,
        secret_presence=secret_presence,
    )
    return decision


def migrate_legacy(record: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(record)
    migrated.setdefault("publication_authorization", None)
    migrated.setdefault("field_category_registry_id", None)
    migrated.setdefault("authorization_rule_id", None)
    return migrated


def _validate_publication_authorization_evidence(
    *,
    authorization: PublicationAuthorization,
    authorization_rule: Mapping[str, Any],
    requested_change: str,
) -> None:
    if authorization.evidence_strength is None or authorization.evidence_method is None or authorization.verifier_type is None:
        raise SourceHandlingBlockedError("publication authorization evidence provenance is incomplete")
    validate_permission_evidence(
        evidence_strength=authorization.evidence_strength,
        evidence_method=authorization.evidence_method,
        verifier_type=authorization.verifier_type,
        requested_change=requested_change,
        authorization_rule=authorization_rule,
        released_restrictions=authorization.released_restrictions,
    )


def _reverify_canonical_record(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    record: Mapping[str, Any],
    cutoff: datetime,
) -> None:
    if record.get("_publication_verified") is not True:
        raise SourceHandlingBlockedError("record was not published through canonical authority")
    if family == "AUTHORIZATION_RULE" and record.get("_bootstrap_verified") is True:
        if _record_id(record) != GENESIS_RULE_ID:
            raise SourceHandlingBlockedError("invalid authorization-rule bootstrap identity")
        return

    authorization = record.get("publication_authorization")
    if not isinstance(authorization, PublicationAuthorization):
        raise SourceHandlingBlockedError("canonical publication authorization is missing")
    if not strict_known_eligible(_authorization_times(authorization), cutoff):
        raise SourceHandlingBlockedError("publication authorization was not strict-known at cutoff")

    payload = record.get("publication_payload")
    if payload is None:
        payload = _publication_payload_from_record(record)
    verify_publication(authorization, family, scope, payload)

    rule = store.canonical_record_by_id("AUTHORIZATION_RULE", authorization.authorization_rule_id)
    if rule is None or not strict_known_eligible(rule, cutoff):
        raise SourceHandlingBlockedError("publication authorization rule was not strict-known at cutoff")
    _validate_publication_authorization_evidence(
        authorization=authorization,
        authorization_rule=rule,
        requested_change=str(record.get("requested_change", "PERMISSIVE_GENESIS")),
    )


def _record_authorization_rule_ids(*records: Mapping[str, Any]) -> set[str]:
    rule_ids: set[str] = set()
    for record in records:
        authorization = record.get("publication_authorization")
        if not isinstance(authorization, PublicationAuthorization):
            raise SourceHandlingBlockedError("authority record lacks publication authorization")
        rule_ids.add(authorization.authorization_rule_id)
    return rule_ids


def _publication_payload_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "id",
        "fact_record_id",
        "policy_record_id",
        "source_handling_fact_id",
        "source_handling_policy_id",
        "publication_authorization",
        "publication_payload",
        "_publication_verified",
        "_bootstrap_verified",
    }
    return {key: value for key, value in record.items() if key not in excluded}


def _authorization_times(authorization: PublicationAuthorization) -> dict[str, datetime]:
    return {
        "effective_from": authorization.effective_from,
        "recorded_at": authorization.recorded_at,
        "known_at": authorization.known_at,
    }


def _canonical_object_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_comparable(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _deep_plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(_canonical_comparable(value))


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, PublicationAuthorization):
        return {
            "publication_kind": value.publication_kind,
            "governed_subject_scope": value.governed_subject_scope,
            "authorized_payload_sha256": value.authorized_payload_sha256,
            "authorization_rule_id": value.authorization_rule_id,
            "effective_from": value.effective_from,
            "recorded_at": value.recorded_at,
            "known_at": value.known_at,
            "authorization_id": value.authorization_id,
            "authority_component_id": value.authority_component_id,
            "evidence_strength": value.evidence_strength,
            "evidence_method": value.evidence_method,
            "verifier_type": value.verifier_type,
            "released_restrictions": value.released_restrictions,
        }
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
        "fact_record_id",
        "policy_record_id",
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
        "supersedes_fact_record_id",
        "supersedes_policy_record_id",
        "supersedes_field_category_registry_id",
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
