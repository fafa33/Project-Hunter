from __future__ import annotations

import copy
import hashlib
import json
import threading
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

AUTHORITY_COMPONENT_ID = "EVIDENCE_INTELLIGENCE_SOURCE_HANDLING_AUTHORITY"
GENESIS_RULE_ID = "AUTHORIZATION_RULE_V1"

#: Closed V1 governed durable data categories (design contract section 7).  A
#: registry may not introduce categories outside this vocabulary, and any field
#: whose mapping is absent, malformed, or outside it resolves to
#: ``UNKNOWN_CATEGORY``, which is never persistable.
UNKNOWN_DURABLE_CATEGORY = "UNKNOWN_CATEGORY"
GOVERNED_DURABLE_CATEGORIES = frozenset(
    {
        "SOURCE_BYTES",
        "SOURCE_DERIVED_TEXT",
        "CONTENT_DERIVED_ID",
        "LOCATOR_URL",
        "COORDINATE",
        "OPERATIONAL_METADATA",
        "DIAGNOSTIC",
        "PROVENANCE_ID",
        "AUDIT_FIELD",
        "SAFE_CONTROL_ID",
        "RECONSTRUCTION_METADATA",
        "ACCESS_CONTROLLED_REPRESENTATION",
        "LIFECYCLE_STATE",
        UNKNOWN_DURABLE_CATEGORY,
    }
)

#: Restrictive orders from design contract section 8.  A value that is invalid
#: for its operation is treated as ``BLOCKED``, never as permission.
CONTENT_DISPOSITION_ORDER = {"ALLOW": 0, "REDACT": 1, "OMIT": 2, "DENY": 3, "BLOCKED": 4}
LIFECYCLE_DISPOSITION_ORDER = {"ALLOW": 0, "EXPIRE": 1, "DELETE": 2, "BLOCKED": 3}

#: Lifecycle dispositions that forbid a durable write outright.  ``EXPIRE``
#: establishes a governed expiry obligation rather than a write prohibition, so
#: it is deliberately absent here and matches the top-level lifecycle rule.
LIFECYCLE_WRITE_BLOCKING_DISPOSITIONS = frozenset({"DELETE", "BLOCKED"})

#: Design contract section 10 makes secret/credential exclusion absolute for
#: every canonical secondary durable representation.  The only governed escape
#: is a ``SAFE_CONTROL_ID`` whose construction is proven by the exact historical
#: registry, so the exemption is an allowlist rather than a risk denylist: a
#: category added later is excluded until it is explicitly proven safe.
SECRET_EXCLUSION_EXEMPT_CATEGORIES = frozenset({"SAFE_CONTROL_ID"})
PROTECTED_SECRET_PRESENCE = frozenset({"SECRET_PRESENT", "CREDENTIAL_PRESENT"})


class SourceHandlingBlockedError(RuntimeError):
    """Raised when Source Handling Authority cannot produce a governed result."""


class SourceHandlingAuthorityReadView(typing.Protocol):
    """Read-only record surface consumed by Source Handling resolution."""

    def canonical_records(self, family: str, scope: str) -> tuple[dict[str, typing.Any], ...]: ...

    def canonical_record_by_id(self, family: str, record_id: str) -> dict[str, typing.Any] | None: ...

    def current_canonical_head_id(self, family: str, scope: str) -> str | None: ...

    def verify_canonical_record(
        self,
        *,
        family: str,
        scope: str,
        record: Mapping[str, typing.Any],
        cutoff: datetime,
    ) -> None: ...


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
    evidence_ids: tuple[str, ...] = ()
    evidence_strength: str | None = None
    evidence_method: str | None = None
    verifier_ids: tuple[str, ...] = ()
    verifier_type: str | None = None
    released_restrictions: frozenset[str] = frozenset()
    predecessor_ids: tuple[str, ...] = ()
    expires_at: datetime | None = None
    issuer_signature: str = ""


class AuthorityStore:
    """Append-only storage plus non-forgeable canonical publication bookkeeping."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[dict[str, typing.Any]]] = {}
        self._canonical_keys: set[tuple[str, str, str]] = set()
        self._issued_authorizations: dict[str, PublicationAuthorization] = {}
        self._provenance_resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None] | None = None
        self._lock = threading.RLock()

    def publish(
        self,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        record: Mapping[str, typing.Any],
    ) -> None:
        with self._lock:
            authorization = record.get("publication_authorization")
            if not isinstance(authorization, PublicationAuthorization):
                raise SourceHandlingBlockedError("governed publication authorization required")
            self._require_issued_authorization(authorization)
            self._validate_authorization_provenance(authorization, cutoff=authorization.known_at)

            actual_payload = _publication_payload_from_record(record)
            supplied_payload = record.get("publication_payload")
            if supplied_payload is not None and _canonical_comparable(supplied_payload) != _canonical_comparable(
                actual_payload
            ):
                raise SourceHandlingBlockedError("publication payload does not equal exact candidate record body")

            verify_publication(authorization, family, scope, actual_payload)
            if not strict_known_eligible(_authorization_times(authorization), authorization.known_at):
                raise SourceHandlingBlockedError("publication authorization temporal state is invalid")

            rule = self._resolve_rule_for_authorization(authorization)
            derived_change, derived_releases = _derive_publication_change(
                self, family=family, scope=scope, candidate=actual_payload
            )
            if frozenset(authorization.released_restrictions) != derived_releases:
                raise SourceHandlingBlockedError("released restrictions do not match the candidate history")
            _validate_publication_authorization_evidence(
                authorization=authorization,
                authorization_rule=rule,
                requested_change=derived_change,
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

            candidate = copy.deepcopy(dict(record))
            candidate["publication_payload"] = copy.deepcopy(actual_payload)
            candidate_id = _record_id(candidate)
            if candidate_id is None:
                raise SourceHandlingBlockedError("authority record identity is required")
            self.compare_and_append(
                family=family,
                scope=scope,
                expected_current_head_id=self.current_head_id(family, scope),
                record=candidate,
            )
            self._canonical_keys.add((family, scope, candidate_id))

    def direct_write(self, *, family: str, scope: str, record: Mapping[str, typing.Any]) -> None:
        del family, scope, record
        raise SourceHandlingBlockedError("direct repository authority writes are forbidden")

    def compare_and_append(
        self,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        record: Mapping[str, typing.Any],
    ) -> None:
        with self._lock:
            key = (family, scope)
            records = self._records.setdefault(key, [])
            current = _record_id(records[-1]) if records else None
            if current != expected_current_head_id:
                raise SourceHandlingBlockedError("authority head changed; re-resolution required")
            candidate = copy.deepcopy(dict(record))
            candidate_id = _record_id(candidate)
            if candidate_id is None:
                raise SourceHandlingBlockedError("authority record identity is required")
            if any(_record_id(existing) == candidate_id for existing in records):
                raise SourceHandlingBlockedError("authority record identity must be immutable")
            records.append(candidate)

    def current_head_id(self, family: str, scope: str) -> str | None:
        with self._lock:
            records = self._records.get((family, scope), [])
            return _record_id(records[-1]) if records else None

    def current_canonical_head_id(self, family: str, scope: str) -> str | None:
        records = self.canonical_records(family, scope)
        return _record_id(records[-1]) if records else None

    def records(self, family: str, scope: str) -> tuple[dict[str, typing.Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(record) for record in self._records.get((family, scope), []))

    def canonical_records(self, family: str, scope: str) -> tuple[dict[str, typing.Any], ...]:
        with self._lock:
            canonical: list[dict[str, typing.Any]] = []
            for record in self._records.get((family, scope), []):
                record_id = _record_id(record)
                if record_id is None:
                    continue
                if (family, scope, record_id) in self._canonical_keys:
                    canonical.append(copy.deepcopy(record))
            return tuple(canonical)

    def canonical_record_by_id(self, family: str, record_id: str) -> dict[str, typing.Any] | None:
        with self._lock:
            matches: list[dict[str, typing.Any]] = []
            for (stored_family, scope), records in self._records.items():
                if stored_family != family:
                    continue
                for record in records:
                    if _record_id(record) != record_id:
                        continue
                    if (family, scope, record_id) in self._canonical_keys:
                        matches.append(copy.deepcopy(record))
            if len(matches) > 1:
                raise SourceHandlingBlockedError("canonical authority identity is ambiguous")
            return matches[0] if matches else None

    def verify_canonical_record(
        self,
        *,
        family: str,
        scope: str,
        record: Mapping[str, typing.Any],
        cutoff: datetime,
    ) -> None:
        _reverify_canonical_record(self, family=family, scope=scope, record=record, cutoff=cutoff)

    def _register_authorization(self, authorization: PublicationAuthorization) -> None:
        with self._lock:
            if not authorization.authorization_id:
                raise SourceHandlingBlockedError("publication authorization identity is required")
            existing = self._issued_authorizations.get(authorization.authorization_id)
            if existing is not None and existing != authorization:
                raise SourceHandlingBlockedError("publication authorization identity is immutable")
            self._issued_authorizations[authorization.authorization_id] = authorization

    def _require_issued_authorization(self, authorization: PublicationAuthorization) -> None:
        issued = self._issued_authorizations.get(authorization.authorization_id)
        if not authorization.authorization_id or issued != authorization:
            raise SourceHandlingBlockedError("publication authorization was not issued by source handling authority")

    def _install_provenance_resolver(
        self,
        resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None],
    ) -> None:
        with self._lock:
            if self._provenance_resolver is not None and self._provenance_resolver is not resolver:
                raise SourceHandlingBlockedError("canonical provenance resolver is immutable once installed")
            self._provenance_resolver = resolver

    def _resolve_provenance_record(
        self,
        provenance_id: str,
        provenance_kind: str,
        cutoff: datetime,
    ) -> Mapping[str, typing.Any]:
        resolver = self._provenance_resolver
        if resolver is None:
            raise SourceHandlingBlockedError("canonical provenance resolver is unavailable")
        record = resolver(provenance_id, provenance_kind, cutoff)
        if not isinstance(record, Mapping):
            raise SourceHandlingBlockedError("canonical provenance record is unavailable")
        if record.get("provenance_id") != provenance_id or record.get("provenance_kind") != provenance_kind:
            raise SourceHandlingBlockedError("canonical provenance identity or kind mismatch")
        if not strict_known_eligible(record, cutoff):
            raise SourceHandlingBlockedError("canonical provenance was not strict-known at cutoff")
        return record

    def _validate_authorization_provenance(
        self,
        authorization: PublicationAuthorization,
        *,
        cutoff: datetime,
    ) -> None:
        evidence_records = [
            self._resolve_provenance_record(evidence_id, "EVIDENCE", cutoff)
            for evidence_id in authorization.evidence_ids
        ]
        verifier_records = [
            self._resolve_provenance_record(verifier_id, "VERIFIER", cutoff)
            for verifier_id in authorization.verifier_ids
        ]
        if not evidence_records or not verifier_records:
            raise SourceHandlingBlockedError("canonical publication provenance is incomplete")
        if any(record.get("evidence_strength") != authorization.evidence_strength for record in evidence_records):
            raise SourceHandlingBlockedError(
                "caller evidence-strength classification does not match canonical provenance"
            )
        if any(record.get("evidence_method") != authorization.evidence_method for record in evidence_records):
            raise SourceHandlingBlockedError(
                "caller evidence-method classification does not match canonical provenance"
            )
        if any(record.get("verifier_type") != authorization.verifier_type for record in verifier_records):
            raise SourceHandlingBlockedError("caller verifier classification does not match canonical provenance")

    def _resolve_rule_for_authorization(self, authorization: PublicationAuthorization) -> Mapping[str, typing.Any]:
        records = self.canonical_records("AUTHORIZATION_RULE", "SOURCE_HANDLING")
        if not records:
            raise SourceHandlingBlockedError("authorization-rule history is unavailable")
        rule = strict_known_head(records, cutoff=authorization.known_at, scope="SOURCE_HANDLING")
        if _record_id(rule) != authorization.authorization_rule_id:
            raise SourceHandlingBlockedError("authorization names a stale or non-applicable authorization rule")
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
    evidence_ids: Sequence[str] = (),
    evidence_strength: str | None = None,
    evidence_method: str | None = None,
    verifier_ids: Sequence[str] = (),
    verifier_type: str | None = None,
    released_restrictions: set[str] | frozenset[str] | None = None,
    predecessor_ids: Sequence[str] = (),
    expires_at: datetime | None = None,
    issuer_signature: str = "",
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
        evidence_ids=tuple(evidence_ids),
        evidence_strength=evidence_strength,
        evidence_method=evidence_method,
        verifier_ids=tuple(verifier_ids),
        verifier_type=verifier_type,
        released_restrictions=frozenset(released_restrictions or ()),
        predecessor_ids=tuple(predecessor_ids),
        expires_at=expires_at,
        issuer_signature=issuer_signature,
    )


def issue_publication_authorization(
    store: AuthorityStore,
    *,
    authorization_id: str,
    publication_kind: str,
    governed_subject_scope: str,
    payload: object,
    authorization_rule_id: str,
    evidence_ids: Sequence[str],
    evidence_strength: str,
    evidence_method: str,
    verifier_ids: Sequence[str],
    verifier_type: str,
    effective_from: datetime,
    recorded_at: datetime,
    known_at: datetime,
    requested_change: str = "PERMISSIVE_GENESIS",
    released_restrictions: set[str] | frozenset[str] | None = None,
    predecessor_ids: Sequence[str] = (),
) -> PublicationAuthorization:
    del requested_change
    if not authorization_id or not evidence_ids or not verifier_ids:
        raise SourceHandlingBlockedError("authorization requires immutable evidence and verifier identities")
    derived_change, derived_releases = _derive_publication_change(
        store, family=publication_kind, scope=governed_subject_scope, candidate=payload
    )
    if frozenset(released_restrictions or ()) != derived_releases:
        raise SourceHandlingBlockedError("released restrictions must be derived exactly from candidate history")
    digest = canonical_publication_digest(publication_kind, governed_subject_scope, payload)
    authorization = publication_authorization(
        authorization_id=authorization_id,
        publication_kind=publication_kind,
        governed_subject_scope=governed_subject_scope,
        authorized_payload_sha256=digest,
        authorization_rule_id=authorization_rule_id,
        evidence_ids=evidence_ids,
        evidence_strength=evidence_strength,
        evidence_method=evidence_method,
        verifier_ids=verifier_ids,
        verifier_type=verifier_type,
        effective_from=effective_from,
        recorded_at=recorded_at,
        known_at=known_at,
        released_restrictions=derived_releases,
        predecessor_ids=predecessor_ids,
    )
    store._validate_authorization_provenance(authorization, cutoff=known_at)
    rule = store._resolve_rule_for_authorization(authorization)
    verify_publication(authorization, publication_kind, governed_subject_scope, payload)
    _validate_publication_authorization_evidence(
        authorization=authorization, authorization_rule=rule, requested_change=derived_change
    )
    store._register_authorization(authorization)
    return authorization


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
    rule: Mapping[str, typing.Any],
    *,
    expected_golden_sha256: str,
) -> None:
    with store._lock:
        if _canonical_object_sha256(rule) != expected_golden_sha256:
            raise SourceHandlingBlockedError("authorization-rule bootstrap digest mismatch")
        if rule.get("authorization_rule_id") != GENESIS_RULE_ID:
            raise SourceHandlingBlockedError("unexpected authorization-rule bootstrap identity")
        if store.current_head_id("AUTHORIZATION_RULE", "SOURCE_HANDLING") is not None:
            raise SourceHandlingBlockedError("authorization-rule bootstrap requires empty history")

        record = copy.deepcopy(dict(rule))
        record["id"] = str(rule["authorization_rule_id"])
        record["scope"] = "SOURCE_HANDLING"
        store.compare_and_append(
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            expected_current_head_id=None,
            record=record,
        )
        store._canonical_keys.add(("AUTHORIZATION_RULE", "SOURCE_HANDLING", GENESIS_RULE_ID))


def publish_successor_rule(
    store: AuthorityStore,
    successor: Mapping[str, typing.Any],
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
    authorization_rule: Mapping[str, typing.Any],
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


def strict_known_eligible(record: Mapping[str, typing.Any], cutoff: datetime) -> bool:
    fields = ["effective_from", "recorded_at", "known_at"]
    if "admission_time" in record:
        fields.append("admission_time")
    for field in fields:
        value = _as_datetime(record.get(field))
        if value is None or value > cutoff:
            return False
    return True


def strict_known_head(
    records: Sequence[Mapping[str, typing.Any]],
    *,
    cutoff: datetime,
    scope: str,
) -> Mapping[str, typing.Any]:
    eligible = [record for record in records if record.get("scope") == scope and strict_known_eligible(record, cutoff)]
    if not eligible:
        raise SourceHandlingBlockedError("no strict-known authority head")

    by_id: dict[str, Mapping[str, typing.Any]] = {}
    for record in eligible:
        record_id = _record_id(record)
        if record_id is None or record_id in by_id:
            raise SourceHandlingBlockedError("strict-known authority identity is missing or duplicated")
        by_id[record_id] = record

    predecessor_by_id: dict[str, str | None] = {}
    superseded: set[str] = set()
    for record_id, record in by_id.items():
        predecessor = _supersedes_id(record)
        predecessor_by_id[record_id] = predecessor
        if predecessor is not None:
            if predecessor not in by_id:
                raise SourceHandlingBlockedError("strict-known authority predecessor is missing")
            superseded.add(predecessor)

    heads = [record_id for record_id in by_id if record_id not in superseded]
    if len(heads) != 1:
        raise SourceHandlingBlockedError("strict-known authority history has divergent heads")

    visited: set[str] = set()
    cursor: str | None = heads[0]
    while cursor is not None:
        if cursor in visited:
            raise SourceHandlingBlockedError("strict-known authority history contains a cycle")
        visited.add(cursor)
        cursor = predecessor_by_id[cursor]
    if visited != set(by_id):
        raise SourceHandlingBlockedError("strict-known authority history is not one complete chain")
    return by_id[heads[0]]


def resolve_canonical_head(
    store: SourceHandlingAuthorityReadView,
    *,
    family: str,
    scope: str,
    cutoff: datetime,
) -> Mapping[str, typing.Any]:
    records = store.canonical_records(family, scope)
    if not records:
        raise SourceHandlingBlockedError("canonical historical authority is absent")
    head = strict_known_head(records, cutoff=cutoff, scope=scope)
    store.verify_canonical_record(family=family, scope=scope, record=head, cutoff=cutoff)
    return head


def authority_store(
    *,
    provenance_resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None] | None = None,
) -> AuthorityStore:
    store = AuthorityStore()
    if provenance_resolver is not None:
        store._install_provenance_resolver(provenance_resolver)
    return store


def restrictive_fact_join(facts: Sequence[Mapping[str, typing.Any]]) -> dict[str, typing.Any]:
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
        if fact.get("sensitivity_known") is not True:
            raise SourceHandlingBlockedError("sensitivity is not known")
        if fact.get("operation_restrictions_known") is not True:
            raise SourceHandlingBlockedError("operation restrictions are not known")
        if fact.get("persistence_restriction_known") is not True:
            raise SourceHandlingBlockedError("persistence restriction is not known")
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
        "sensitivity_known": True,
        "operation_restrictions_known": True,
        "persistence_restriction_known": True,
        "secret_presence_known": True,
        "withdrawn": withdrawn,
        "deleted_at_source": deleted_at_source,
        "historically_unavailable": historically_unavailable,
        "availability_known": True,
    }


def _derive_publication_change(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    candidate: object,
) -> tuple[str, frozenset[str]]:
    if family != "FACT":
        return "PERMISSIVE_GENESIS", frozenset()
    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("fact"), Mapping):
        raise SourceHandlingBlockedError("fact publication payload lacks normalized fact")
    candidate_fact = typing.cast(Mapping[str, typing.Any], candidate["fact"])
    current_id = store.current_canonical_head_id("FACT", scope)
    if current_id is None:
        return ("MORE_RESTRICTIVE" if _fact_has_restriction(candidate_fact) else "PERMISSIVE_GENESIS"), frozenset()
    current = store.canonical_record_by_id("FACT", current_id)
    if current is None or not isinstance(current.get("fact"), Mapping):
        raise SourceHandlingBlockedError("canonical predecessor fact is unavailable")
    releases = _released_fact_restrictions(typing.cast(Mapping[str, typing.Any], current["fact"]), candidate_fact)
    return ("LESS_RESTRICTIVE", releases) if releases else ("MORE_RESTRICTIVE", frozenset())


def _derive_persisted_publication_change(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    record: Mapping[str, typing.Any],
) -> tuple[str, frozenset[str]]:
    if family != "FACT":
        return "PERMISSIVE_GENESIS", frozenset()
    candidate_fact = record.get("fact")
    if not isinstance(candidate_fact, Mapping):
        raise SourceHandlingBlockedError("persisted fact publication lacks normalized fact")
    predecessor_id = _supersedes_id(record)
    if predecessor_id is None:
        return (
            "MORE_RESTRICTIVE" if _fact_has_restriction(candidate_fact) else "PERMISSIVE_GENESIS",
            frozenset(),
        )
    predecessor = store.canonical_record_by_id("FACT", predecessor_id)
    if predecessor is None or predecessor.get("scope") != scope:
        raise SourceHandlingBlockedError("persisted fact predecessor is unavailable or out of scope")
    predecessor_fact = predecessor.get("fact")
    if not isinstance(predecessor_fact, Mapping):
        raise SourceHandlingBlockedError("persisted fact predecessor body is unavailable")
    releases = _released_fact_restrictions(predecessor_fact, candidate_fact)
    return ("LESS_RESTRICTIVE", releases) if releases else ("MORE_RESTRICTIVE", frozenset())


def _fact_has_restriction(fact: Mapping[str, typing.Any]) -> bool:
    return bool(
        fact.get("sensitivity") not in {None, "PUBLIC"}
        or _string_sequence(fact.get("operation_restrictions"))
        or fact.get("persistence_restriction") not in {None, "FULL_CONTENT_ALLOWED"}
        or _string_sequence(fact.get("secret_presence"))
        or fact.get("withdrawn") is True
        or fact.get("deleted_at_source") is True
        or fact.get("historically_unavailable") is True
    )


def _released_fact_restrictions(
    predecessor: Mapping[str, typing.Any],
    candidate: Mapping[str, typing.Any],
) -> frozenset[str]:
    sensitivity_order = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3, "UNKNOWN": 4}
    persistence_order = {
        "FULL_CONTENT_ALLOWED": 0,
        "DERIVED_ONLY": 1,
        "METADATA_ONLY": 2,
        "NO_PERSISTENCE": 3,
        "UNKNOWN": 4,
    }
    releases: set[str] = set()
    old_sensitivity, new_sensitivity = str(predecessor.get("sensitivity")), str(candidate.get("sensitivity"))
    if old_sensitivity not in sensitivity_order or new_sensitivity not in sensitivity_order:
        raise SourceHandlingBlockedError("fact sensitivity is not comparable")
    if sensitivity_order[new_sensitivity] < sensitivity_order[old_sensitivity]:
        releases.add(f"SENSITIVITY:{old_sensitivity}")
    old_persistence, new_persistence = str(predecessor.get("persistence_restriction")), str(
        candidate.get("persistence_restriction")
    )
    if old_persistence not in persistence_order or new_persistence not in persistence_order:
        raise SourceHandlingBlockedError("fact persistence restriction is not comparable")
    if persistence_order[new_persistence] < persistence_order[old_persistence]:
        releases.add(f"PERSISTENCE:{old_persistence}")
    old_ops = set(_string_sequence(predecessor.get("operation_restrictions")))
    new_ops = set(_string_sequence(candidate.get("operation_restrictions")))
    releases.update(old_ops - new_ops)
    old_secrets = set(_string_sequence(predecessor.get("secret_presence")))
    new_secrets = set(_string_sequence(candidate.get("secret_presence")))
    releases.update(f"SECRET_PRESENCE:{value}" for value in old_secrets - new_secrets)
    for field, label in (
        ("withdrawn", "WITHDRAWN"),
        ("deleted_at_source", "DELETED_AT_SOURCE"),
        ("historically_unavailable", "HISTORICALLY_UNAVAILABLE"),
    ):
        if predecessor.get(field) is True and candidate.get(field) is not True:
            releases.add(label)
    return frozenset(releases)


def _validate_safe_control_proof(*, registry: Mapping[str, typing.Any], field: str, value: object) -> None:
    proofs = registry.get("safe_control_proofs")
    if not isinstance(proofs, Mapping):
        raise SourceHandlingBlockedError("SAFE_CONTROL_ID construction proof is unavailable")
    proof = proofs.get(field)
    if not isinstance(proof, Mapping):
        raise SourceHandlingBlockedError("SAFE_CONTROL_ID field lacks governed construction proof")
    proof_id = proof.get("proof_id")
    allowed_values = proof.get("allowed_values")
    if not isinstance(proof_id, str) or not proof_id or not isinstance(allowed_values, (list, tuple, set, frozenset)):
        raise SourceHandlingBlockedError("SAFE_CONTROL_ID construction proof is incomplete")
    actual_value = value.get("value") if isinstance(value, Mapping) else value
    if actual_value not in allowed_values:
        raise SourceHandlingBlockedError("SAFE_CONTROL_ID value is not proven by the historical registry")
    if isinstance(value, Mapping) and value.get("proof_id") not in {None, proof_id}:
        raise SourceHandlingBlockedError("SAFE_CONTROL_ID proof identity mismatch")


def lifecycle_join(values: Sequence[str]) -> str:
    order = {"ALLOW": 0, "EXPIRE": 1, "DELETE": 2, "BLOCKED": 3}
    if not values:
        raise SourceHandlingBlockedError("at least one lifecycle disposition is required")
    if any(value not in order for value in values):
        raise SourceHandlingBlockedError("unknown lifecycle disposition")
    return max(values, key=order.__getitem__)


def derive_source_handling_decision(
    *,
    fact_record: Mapping[str, typing.Any],
    policy_record: Mapping[str, typing.Any],
    registry_record: Mapping[str, typing.Any],
    authorization_rule: Mapping[str, typing.Any],
) -> dict[str, typing.Any]:
    fact = fact_record.get("fact")
    policy = policy_record.get("policy_body")
    if not isinstance(fact, Mapping) or not isinstance(policy, Mapping):
        raise SourceHandlingBlockedError("fact or policy body is incomplete")

    normalized_fact = restrictive_fact_join([fact])
    registry_id = registry_record.get("field_category_registry_id")
    if registry_id != policy_record.get("field_category_registry_id"):
        raise SourceHandlingBlockedError("policy is not bound to the resolved registry")

    top_level_allowed = {
        "processing_decision": {"ALLOW", "DENY", "BLOCKED"},
        "retention_decision": {"ALLOW", "DENY", "BLOCKED"},
        "reconstruction_decision": {"ALLOW", "DENY", "BLOCKED"},
        "access_decision": {"ALLOW", "DENY", "BLOCKED"},
        "deletion_lifecycle_decision": {"ALLOW", "EXPIRE", "DELETE", "BLOCKED"},
    }
    for key, allowed in top_level_allowed.items():
        if policy.get(key) not in allowed:
            raise SourceHandlingBlockedError(f"policy decision is missing or invalid: {key}")

    operation_restrictions = set(_string_sequence(normalized_fact.get("operation_restrictions")))
    if "MODEL_PROCESSING_PROHIBITED" in operation_restrictions and policy.get("processing_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override model-processing prohibition")
    if "RECONSTRUCTION_PROHIBITED" in operation_restrictions and policy.get("reconstruction_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override reconstruction prohibition")
    if "ACCESS_RESTRICTED" in operation_restrictions and policy.get("access_decision") == "ALLOW":
        raise SourceHandlingBlockedError("policy cannot override access restriction")
    if (
        normalized_fact.get("persistence_restriction") == "NO_PERSISTENCE"
        and policy.get("retention_decision") == "ALLOW"
    ):
        raise SourceHandlingBlockedError("policy cannot override no-persistence restriction")

    dispositions = policy.get("durable_dispositions")
    if not isinstance(dispositions, Mapping):
        raise SourceHandlingBlockedError("durable dispositions are missing")
    _validate_complete_disposition_map(dispositions, registry_record)

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


def governed_durable_category(value: object) -> str:
    """Resolve one declared mapping entry to a governed V1 category.

    Design contract section 7: an unknown, omitted, or ambiguous mapping is
    ``UNKNOWN_CATEGORY``.  Resolution never invents a category and never treats
    an unrecognized name as safe.
    """

    if isinstance(value, str) and value in GOVERNED_DURABLE_CATEGORIES:
        return value
    return UNKNOWN_DURABLE_CATEGORY


def governed_field_categories(field_map: Mapping[str, typing.Any], field: str) -> tuple[str, ...]:
    """Resolve the governed categories a registry maps one durable field to.

    An absent or empty mapping is ambiguous, so it resolves to a single
    ``UNKNOWN_CATEGORY`` rather than to an empty set that would vacuously
    satisfy an all-categories check.
    """

    declared = field_map.get(field)
    if isinstance(declared, str):
        candidates: tuple[object, ...] = (declared,)
    elif isinstance(declared, (list, tuple, set, frozenset)):
        candidates = tuple(declared)
    else:
        candidates = ()
    if not candidates:
        return (UNKNOWN_DURABLE_CATEGORY,)
    resolved = {governed_durable_category(candidate) for candidate in candidates}
    return tuple(sorted(resolved))


def _disposition_join(values: Sequence[object], order: Mapping[str, int]) -> str:
    """Most restrictive disposition across every applicable category.

    Design contract section 8: the operation-specific join is applied across
    every applicable category, and any value invalid for the operation is
    ``BLOCKED``.  An empty set of applicable categories is ambiguous, so it is
    ``BLOCKED`` as well.
    """

    if not values:
        return "BLOCKED"
    worst = "ALLOW"
    for value in values:
        if not isinstance(value, str) or value not in order:
            return "BLOCKED"
        if order[value] > order[worst]:
            worst = value
    return worst


def effective_persist_disposition(
    dispositions: Mapping[str, typing.Any],
    categories: Sequence[str],
) -> str:
    """Effective ``PERSIST`` disposition across every applicable category."""

    return _disposition_join(
        [_category_disposition(dispositions, category, "PERSIST") for category in categories],
        CONTENT_DISPOSITION_ORDER,
    )


def effective_lifecycle_disposition(
    dispositions: Mapping[str, typing.Any],
    categories: Sequence[str],
) -> str:
    """Effective ``DELETE_OR_EXPIRE`` disposition across every applicable category.

    Design contract section 8: a mandatory deletion or expiry obligation cannot
    be erased by an ``ALLOW`` on another category or axis, so a top-level
    lifecycle ``ALLOW`` never overrides a stricter category-level obligation.
    """

    return _disposition_join(
        [_category_disposition(dispositions, category, "DELETE_OR_EXPIRE") for category in categories],
        LIFECYCLE_DISPOSITION_ORDER,
    )


def _category_disposition(
    dispositions: Mapping[str, typing.Any],
    category: str,
    operation: str,
) -> object:
    category_dispositions = dispositions.get(category)
    if not isinstance(category_dispositions, Mapping):
        return None
    return category_dispositions.get(operation)


def validate_durable_payload(
    *,
    decision: Mapping[str, typing.Any],
    registry: Mapping[str, typing.Any],
    payload: Mapping[str, typing.Any],
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

    protected = bool(set(secret_presence) & PROTECTED_SECRET_PRESENCE)
    for field, value in payload.items():
        categories = governed_field_categories(field_map, field)
        if UNKNOWN_DURABLE_CATEGORY in categories:
            raise SourceHandlingBlockedError("durable field category is unknown or ambiguous")
        for category in categories:
            if category == "SAFE_CONTROL_ID":
                _validate_safe_control_proof(registry=registry, field=field, value=value)
            elif protected:
                raise SourceHandlingBlockedError("protected source cannot persist through a risky secondary category")
        if effective_persist_disposition(dispositions, categories) != "ALLOW":
            raise SourceHandlingBlockedError("durable field persistence is not allowed")
        if effective_lifecycle_disposition(dispositions, categories) in LIFECYCLE_WRITE_BLOCKING_DISPOSITIONS:
            raise SourceHandlingBlockedError("durable field lifecycle disposition forbids a durable write")
        if protected and _derived_from_protected_content(value):
            raise SourceHandlingBlockedError("secret/credential-derived secondary representation cannot persist")


def enforce_persistence(
    store: SourceHandlingAuthorityReadView,
    *,
    fact_scope: str,
    policy_scope: str,
    cutoff: datetime,
    payload: Mapping[str, typing.Any],
    supplied_decision: Mapping[str, typing.Any] | None = None,
) -> dict[str, typing.Any]:
    fact_record = resolve_canonical_head(store, family="FACT", scope=fact_scope, cutoff=cutoff)
    policy_record = resolve_canonical_head(store, family="POLICY", scope=policy_scope, cutoff=cutoff)

    registry_id = policy_record.get("field_category_registry_id")
    if not isinstance(registry_id, str) or not registry_id:
        raise SourceHandlingBlockedError("policy-bound registry identity is missing")
    registry_record = store.canonical_record_by_id("FIELD_CATEGORY_REGISTRY", registry_id)
    if registry_record is None or not strict_known_eligible(registry_record, cutoff):
        raise SourceHandlingBlockedError("exact historical field-category registry is unavailable")
    registry_scope = str(registry_record.get("scope", ""))
    store.verify_canonical_record(
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
    if decision.get("retention_decision") != "ALLOW":
        raise SourceHandlingBlockedError("top-level retention decision forbids persistence")
    if decision.get("deletion_lifecycle_decision") in {"DELETE", "BLOCKED"}:
        raise SourceHandlingBlockedError("top-level lifecycle decision forbids a durable write")

    secret_presence = set(_string_sequence(fact.get("secret_presence")))
    validate_durable_payload(
        decision=decision,
        registry=registry_record,
        payload=payload,
        secret_presence=secret_presence,
    )
    return decision


def migrate_legacy(record: Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    migrated = copy.deepcopy(dict(record))
    migrated.setdefault("publication_authorization", None)
    migrated.setdefault("field_category_registry_id", None)
    migrated.setdefault("authorization_rule_id", None)
    return migrated


def _validate_publication_authorization_evidence(
    *,
    authorization: PublicationAuthorization,
    authorization_rule: Mapping[str, typing.Any],
    requested_change: str,
) -> None:
    if not authorization.evidence_ids or not authorization.verifier_ids:
        raise SourceHandlingBlockedError("publication authorization provenance identities are incomplete")
    if (
        authorization.evidence_strength is None
        or authorization.evidence_method is None
        or authorization.verifier_type is None
    ):
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
    record: Mapping[str, typing.Any],
    cutoff: datetime,
) -> None:
    record_id = _record_id(record)
    if record_id is None or (family, scope, record_id) not in store._canonical_keys:
        raise SourceHandlingBlockedError("record was not published through canonical authority")
    if family == "AUTHORIZATION_RULE" and record_id == GENESIS_RULE_ID:
        return

    authorization = record.get("publication_authorization")
    if not isinstance(authorization, PublicationAuthorization):
        raise SourceHandlingBlockedError("canonical publication authorization is missing")
    store._require_issued_authorization(authorization)
    if not strict_known_eligible(_authorization_times(authorization), cutoff):
        raise SourceHandlingBlockedError("publication authorization was not strict-known at cutoff")

    actual_payload = _publication_payload_from_record(record)
    supplied_payload = record.get("publication_payload")
    if supplied_payload is not None and _canonical_comparable(supplied_payload) != _canonical_comparable(
        actual_payload
    ):
        raise SourceHandlingBlockedError("persisted publication payload does not match canonical record body")
    verify_publication(authorization, family, scope, actual_payload)

    store._validate_authorization_provenance(authorization, cutoff=cutoff)
    rule = store._resolve_rule_for_authorization(authorization)
    derived_change, derived_releases = _derive_persisted_publication_change(
        store,
        family=family,
        scope=scope,
        record=record,
    )
    if frozenset(authorization.released_restrictions) != derived_releases:
        raise SourceHandlingBlockedError("persisted released restrictions do not match historical authority")
    _validate_publication_authorization_evidence(
        authorization=authorization,
        authorization_rule=rule,
        requested_change=derived_change,
    )


def _record_authorization_rule_ids(*records: Mapping[str, typing.Any]) -> set[str]:
    rule_ids: set[str] = set()
    for record in records:
        authorization = record.get("publication_authorization")
        if not isinstance(authorization, PublicationAuthorization):
            raise SourceHandlingBlockedError("authority record lacks publication authorization")
        rule_ids.add(authorization.authorization_rule_id)
    return rule_ids


def validate_field_category_registry_payload(payload: Mapping[str, typing.Any]) -> None:
    """Validate a `FieldCategoryRegistryRecord` body before it becomes authority.

    Design contract sections 4.3 and 7: the registry carries a logical identity
    and an exact governed field-to-category mapping drawn from the closed V1
    vocabulary.  Rejecting an undeclared category at publication keeps an
    unknown category from ever becoming canonical authority; `validate_durable_payload`
    independently refuses to persist through one, so a registry admitted before
    this guard existed still cannot launder content.
    """

    registry_id = payload.get("field_category_registry_id")
    if not isinstance(registry_id, str) or not registry_id.strip():
        raise SourceHandlingBlockedError("field-category registry identity is missing or malformed")

    field_map = payload.get("field_map")
    if not isinstance(field_map, Mapping) or not field_map:
        raise SourceHandlingBlockedError("field-category registry mapping is unavailable")

    for field, mapped in field_map.items():
        if not isinstance(field, str) or not field.strip():
            raise SourceHandlingBlockedError("field-category registry field identity is malformed")
        declared = (mapped,) if isinstance(mapped, str) else mapped
        if not isinstance(declared, (list, tuple)) or not declared:
            raise SourceHandlingBlockedError("field-category registry mapping entry is malformed")
        if any(not isinstance(category, str) for category in declared):
            raise SourceHandlingBlockedError("field-category registry mapping entry is malformed")
        undeclared = {category for category in declared if category not in GOVERNED_DURABLE_CATEGORIES}
        if undeclared:
            raise SourceHandlingBlockedError("field-category registry declares an undeclared durable category")

    proofs = payload.get("safe_control_proofs", {})
    if not isinstance(proofs, Mapping):
        raise SourceHandlingBlockedError("field-category registry safe-control proofs are malformed")


def _validate_complete_disposition_map(
    dispositions: Mapping[str, typing.Any],
    registry: Mapping[str, typing.Any],
) -> None:
    field_map = registry.get("field_map")
    if not isinstance(field_map, Mapping):
        raise SourceHandlingBlockedError("field-category registry mapping is unavailable")
    categories = {category for field in field_map for category in governed_field_categories(field_map, field)}
    if not categories:
        raise SourceHandlingBlockedError("field-category registry has no governed categories")
    if not categories <= GOVERNED_DURABLE_CATEGORIES:
        raise SourceHandlingBlockedError("field-category registry declares an undeclared durable category")

    for category in categories:
        category_dispositions = dispositions.get(category)
        if not isinstance(category_dispositions, Mapping):
            raise SourceHandlingBlockedError("durable category disposition unavailable")
        for operation in ("PERSIST", "READ_ACCESS", "RECONSTRUCT"):
            if category_dispositions.get(operation) not in CONTENT_DISPOSITION_ORDER:
                raise SourceHandlingBlockedError("durable content disposition is missing or invalid")
        if category_dispositions.get("DELETE_OR_EXPIRE") not in LIFECYCLE_DISPOSITION_ORDER:
            raise SourceHandlingBlockedError("durable lifecycle disposition is missing or invalid")


def _publication_payload_from_record(record: Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    excluded = {
        "id",
        "fact_record_id",
        "policy_record_id",
        "source_handling_fact_id",
        "source_handling_policy_id",
        "publication_authorization",
        "publication_payload",
        "admission_time",
    }
    return {
        key: copy.deepcopy(value) for key, value in record.items() if key not in excluded and not key.startswith("_")
    }


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


def _deep_plain_mapping(value: Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    return typing.cast(dict[str, typing.Any], json.loads(_canonical_comparable(value)))


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
            "evidence_ids": value.evidence_ids,
            "evidence_strength": value.evidence_strength,
            "evidence_method": value.evidence_method,
            "verifier_ids": value.verifier_ids,
            "verifier_type": value.verifier_type,
            "released_restrictions": value.released_restrictions,
            "predecessor_ids": value.predecessor_ids,
            "expires_at": value.expires_at,
            "issuer_signature": value.issuer_signature,
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


def _record_id(record: Mapping[str, typing.Any]) -> str | None:
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


def _supersedes_id(record: Mapping[str, typing.Any]) -> str | None:
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
