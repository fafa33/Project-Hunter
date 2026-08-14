from pathlib import Path
import re

source_path = Path('src/hunter/evidence_intelligence/source_handling.py')
text = source_path.read_text()

# Canonical provenance resolver owned by the authority composition, not caller classification.
text = text.replace(
    '        self._issued_authorizations: dict[str, PublicationAuthorization] = {}\n        self._lock = threading.RLock()\n',
    '        self._issued_authorizations: dict[str, PublicationAuthorization] = {}\n        self._provenance_resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None] | None = None\n        self._lock = threading.RLock()\n',
    1,
)
anchor = '''    def _require_issued_authorization(self, authorization: PublicationAuthorization) -> None:\n        issued = self._issued_authorizations.get(authorization.authorization_id)\n        if not authorization.authorization_id or issued != authorization:\n            raise SourceHandlingBlockedError("publication authorization was not issued by source handling authority")\n\n'''
if anchor not in text:
    raise SystemExit('authorization anchor missing')
text = text.replace(
    anchor,
    anchor + '''    def _install_provenance_resolver(\n        self,\n        resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None],\n    ) -> None:\n        with self._lock:\n            if self._provenance_resolver is not None and self._provenance_resolver is not resolver:\n                raise SourceHandlingBlockedError("canonical provenance resolver is immutable once installed")\n            self._provenance_resolver = resolver\n\n    def _resolve_provenance_record(\n        self,\n        provenance_id: str,\n        provenance_kind: str,\n        cutoff: datetime,\n    ) -> Mapping[str, typing.Any]:\n        resolver = self._provenance_resolver\n        if resolver is None:\n            raise SourceHandlingBlockedError("canonical provenance resolver is unavailable")\n        record = resolver(provenance_id, provenance_kind, cutoff)\n        if not isinstance(record, Mapping):\n            raise SourceHandlingBlockedError("canonical provenance record is unavailable")\n        if record.get("provenance_id") != provenance_id or record.get("provenance_kind") != provenance_kind:\n            raise SourceHandlingBlockedError("canonical provenance identity or kind mismatch")\n        if not strict_known_eligible(record, cutoff):\n            raise SourceHandlingBlockedError("canonical provenance was not strict-known at cutoff")\n        return record\n\n    def _validate_authorization_provenance(\n        self,\n        authorization: PublicationAuthorization,\n        *,\n        cutoff: datetime,\n    ) -> None:\n        evidence_records = [\n            self._resolve_provenance_record(evidence_id, "EVIDENCE", cutoff)\n            for evidence_id in authorization.evidence_ids\n        ]\n        verifier_records = [\n            self._resolve_provenance_record(verifier_id, "VERIFIER", cutoff)\n            for verifier_id in authorization.verifier_ids\n        ]\n        if not evidence_records or not verifier_records:\n            raise SourceHandlingBlockedError("canonical publication provenance is incomplete")\n        if any(record.get("evidence_strength") != authorization.evidence_strength for record in evidence_records):\n            raise SourceHandlingBlockedError("caller evidence-strength classification does not match canonical provenance")\n        if any(record.get("evidence_method") != authorization.evidence_method for record in evidence_records):\n            raise SourceHandlingBlockedError("caller evidence-method classification does not match canonical provenance")\n        if any(record.get("verifier_type") != authorization.verifier_type for record in verifier_records):\n            raise SourceHandlingBlockedError("caller verifier classification does not match canonical provenance")\n\n''',
    1,
)

pattern = re.compile(r'    def _resolve_rule_for_authorization\(self, authorization: PublicationAuthorization\) -> Mapping\[str, typing\.Any\]:\n.*?        return rule\n', re.S)
text, n = pattern.subn('''    def _resolve_rule_for_authorization(self, authorization: PublicationAuthorization) -> Mapping[str, typing.Any]:\n        records = self.canonical_records("AUTHORIZATION_RULE", "SOURCE_HANDLING")\n        if not records:\n            raise SourceHandlingBlockedError("authorization-rule history is unavailable")\n        rule = strict_known_head(records, cutoff=authorization.known_at, scope="SOURCE_HANDLING")\n        if _record_id(rule) != authorization.authorization_rule_id:\n            raise SourceHandlingBlockedError("authorization names a stale or non-applicable authorization rule")\n        return rule\n''', text, count=1)
if n != 1:
    raise SystemExit('rule resolver replacement failed')

start = text.index('    def publish(\n')
end = text.index('    def direct_write(', start)
text = text[:start] + '''    def publish(\n        self,\n        *,\n        family: str,\n        scope: str,\n        expected_current_head_id: str | None,\n        record: Mapping[str, typing.Any],\n    ) -> None:\n        with self._lock:\n            authorization = record.get("publication_authorization")\n            if not isinstance(authorization, PublicationAuthorization):\n                raise SourceHandlingBlockedError("governed publication authorization required")\n            self._require_issued_authorization(authorization)\n            self._validate_authorization_provenance(authorization, cutoff=authorization.known_at)\n\n            actual_payload = _publication_payload_from_record(record)\n            supplied_payload = record.get("publication_payload")\n            if supplied_payload is not None and _canonical_comparable(supplied_payload) != _canonical_comparable(actual_payload):\n                raise SourceHandlingBlockedError("publication payload does not equal exact candidate record body")\n\n            verify_publication(authorization, family, scope, actual_payload)\n            if not strict_known_eligible(_authorization_times(authorization), authorization.known_at):\n                raise SourceHandlingBlockedError("publication authorization temporal state is invalid")\n\n            rule = self._resolve_rule_for_authorization(authorization)\n            derived_change, derived_releases = _derive_publication_change(\n                self, family=family, scope=scope, candidate=actual_payload\n            )\n            if frozenset(authorization.released_restrictions) != derived_releases:\n                raise SourceHandlingBlockedError("released restrictions do not match the candidate history")\n            _validate_publication_authorization_evidence(\n                authorization=authorization,\n                authorization_rule=rule,\n                requested_change=derived_change,\n            )\n\n            current = self.current_canonical_head_id(family, scope)\n            if current != expected_current_head_id:\n                raise SourceHandlingBlockedError("canonical authority head changed; re-resolution required")\n            supersedes = _supersedes_id(record)\n            if current is None:\n                if supersedes is not None:\n                    raise SourceHandlingBlockedError("genesis publication cannot supersede a record")\n            elif supersedes != current:\n                raise SourceHandlingBlockedError("successor must supersede the exact canonical head")\n\n            candidate = copy.deepcopy(dict(record))\n            candidate["publication_payload"] = copy.deepcopy(actual_payload)\n            candidate_id = _record_id(candidate)\n            if candidate_id is None:\n                raise SourceHandlingBlockedError("authority record identity is required")\n            self.compare_and_append(\n                family=family,\n                scope=scope,\n                expected_current_head_id=self.current_head_id(family, scope),\n                record=candidate,\n            )\n            self._canonical_keys.add((family, scope, candidate_id))\n\n''' + text[end:]

pattern = re.compile(r'def issue_publication_authorization\(\n.*?\n    return authorization\n', re.S)
text, n = pattern.subn('''def issue_publication_authorization(\n    store: AuthorityStore,\n    *,\n    authorization_id: str,\n    publication_kind: str,\n    governed_subject_scope: str,\n    payload: object,\n    authorization_rule_id: str,\n    evidence_ids: Sequence[str],\n    evidence_strength: str,\n    evidence_method: str,\n    verifier_ids: Sequence[str],\n    verifier_type: str,\n    effective_from: datetime,\n    recorded_at: datetime,\n    known_at: datetime,\n    requested_change: str = "PERMISSIVE_GENESIS",\n    released_restrictions: set[str] | frozenset[str] | None = None,\n    predecessor_ids: Sequence[str] = (),\n) -> PublicationAuthorization:\n    del requested_change\n    if not authorization_id or not evidence_ids or not verifier_ids:\n        raise SourceHandlingBlockedError("authorization requires immutable evidence and verifier identities")\n    derived_change, derived_releases = _derive_publication_change(\n        store, family=publication_kind, scope=governed_subject_scope, candidate=payload\n    )\n    if frozenset(released_restrictions or ()) != derived_releases:\n        raise SourceHandlingBlockedError("released restrictions must be derived exactly from candidate history")\n    digest = canonical_publication_digest(publication_kind, governed_subject_scope, payload)\n    authorization = publication_authorization(\n        authorization_id=authorization_id,\n        publication_kind=publication_kind,\n        governed_subject_scope=governed_subject_scope,\n        authorized_payload_sha256=digest,\n        authorization_rule_id=authorization_rule_id,\n        evidence_ids=evidence_ids,\n        evidence_strength=evidence_strength,\n        evidence_method=evidence_method,\n        verifier_ids=verifier_ids,\n        verifier_type=verifier_type,\n        effective_from=effective_from,\n        recorded_at=recorded_at,\n        known_at=known_at,\n        released_restrictions=derived_releases,\n        predecessor_ids=predecessor_ids,\n    )\n    store._validate_authorization_provenance(authorization, cutoff=known_at)\n    rule = store._resolve_rule_for_authorization(authorization)\n    verify_publication(authorization, publication_kind, governed_subject_scope, payload)\n    _validate_publication_authorization_evidence(\n        authorization=authorization, authorization_rule=rule, requested_change=derived_change\n    )\n    store._register_authorization(authorization)\n    return authorization\n''', text, count=1)
if n != 1:
    raise SystemExit('issue authorization replacement failed')

old = 'def authority_store() -> AuthorityStore:\n    return AuthorityStore()\n'
new = '''def authority_store(\n    *,\n    provenance_resolver: typing.Callable[[str, str, datetime], Mapping[str, typing.Any] | None] | None = None,\n) -> AuthorityStore:\n    store = AuthorityStore()\n    if provenance_resolver is not None:\n        store._install_provenance_resolver(provenance_resolver)\n    return store\n'''
if old not in text:
    raise SystemExit('authority_store anchor missing')
text = text.replace(old, new, 1)

old = '    secret_presence = set(_string_sequence(fact.get("secret_presence")))\n    validate_durable_payload(\n'
new = '''    if decision.get("retention_decision") != "ALLOW":\n        raise SourceHandlingBlockedError("top-level retention decision forbids persistence")\n    if decision.get("deletion_lifecycle_decision") in {"DELETE", "BLOCKED"}:\n        raise SourceHandlingBlockedError("top-level lifecycle decision forbids a durable write")\n\n    secret_presence = set(_string_sequence(fact.get("secret_presence")))\n    validate_durable_payload(\n'''
if old not in text:
    raise SystemExit('persistence gate anchor missing')
text = text.replace(old, new, 1)

old = '''    rule = store.canonical_record_by_id("AUTHORIZATION_RULE", authorization.authorization_rule_id)\n    if rule is None or not strict_known_eligible(rule, cutoff):\n        raise SourceHandlingBlockedError("publication authorization rule was not strict-known at cutoff")\n    _validate_publication_authorization_evidence(\n'''
new = '''    store._validate_authorization_provenance(authorization, cutoff=cutoff)\n    rule = store._resolve_rule_for_authorization(authorization)\n    _validate_publication_authorization_evidence(\n'''
if old not in text:
    raise SystemExit('replay rule anchor missing')
text = text.replace(old, new, 1)

old = '''            if category_dispositions.get("PERSIST") != "ALLOW":\n                raise SourceHandlingBlockedError("durable field persistence is not allowed")\n            if protected and category in protected_risky_categories:\n                raise SourceHandlingBlockedError("protected source cannot persist through a risky secondary category")\n'''
new = '''            if category_dispositions.get("PERSIST") != "ALLOW":\n                raise SourceHandlingBlockedError("durable field persistence is not allowed")\n            if category == "SAFE_CONTROL_ID":\n                _validate_safe_control_proof(registry=registry, field=field, value=value)\n            if protected and category in protected_risky_categories:\n                raise SourceHandlingBlockedError("protected source cannot persist through a risky secondary category")\n'''
if old not in text:
    raise SystemExit('safe control anchor missing')
text = text.replace(old, new, 1)

helper_anchor = 'def lifecycle_join(values: Sequence[str]) -> str:\n'
helpers = '''def _derive_publication_change(\n    store: AuthorityStore,\n    *,\n    family: str,\n    scope: str,\n    candidate: object,\n) -> tuple[str, frozenset[str]]:\n    if family != "FACT":\n        return "PERMISSIVE_GENESIS", frozenset()\n    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("fact"), Mapping):\n        raise SourceHandlingBlockedError("fact publication payload lacks normalized fact")\n    candidate_fact = typing.cast(Mapping[str, typing.Any], candidate["fact"])\n    current_id = store.current_canonical_head_id("FACT", scope)\n    if current_id is None:\n        return ("MORE_RESTRICTIVE" if _fact_has_restriction(candidate_fact) else "PERMISSIVE_GENESIS"), frozenset()\n    current = store.canonical_record_by_id("FACT", current_id)\n    if current is None or not isinstance(current.get("fact"), Mapping):\n        raise SourceHandlingBlockedError("canonical predecessor fact is unavailable")\n    releases = _released_fact_restrictions(typing.cast(Mapping[str, typing.Any], current["fact"]), candidate_fact)\n    return ("LESS_RESTRICTIVE", releases) if releases else ("MORE_RESTRICTIVE", frozenset())\n\n\ndef _fact_has_restriction(fact: Mapping[str, typing.Any]) -> bool:\n    return bool(\n        fact.get("sensitivity") not in {None, "PUBLIC"}\n        or _string_sequence(fact.get("operation_restrictions"))\n        or fact.get("persistence_restriction") not in {None, "FULL_CONTENT_ALLOWED"}\n        or _string_sequence(fact.get("secret_presence"))\n        or fact.get("withdrawn") is True\n        or fact.get("deleted_at_source") is True\n        or fact.get("historically_unavailable") is True\n    )\n\n\ndef _released_fact_restrictions(\n    predecessor: Mapping[str, typing.Any],\n    candidate: Mapping[str, typing.Any],\n) -> frozenset[str]:\n    sensitivity_order = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3, "UNKNOWN": 4}\n    persistence_order = {"FULL_CONTENT_ALLOWED": 0, "DERIVED_ONLY": 1, "METADATA_ONLY": 2, "NO_PERSISTENCE": 3, "UNKNOWN": 4}\n    releases: set[str] = set()\n    old_sensitivity, new_sensitivity = str(predecessor.get("sensitivity")), str(candidate.get("sensitivity"))\n    if old_sensitivity not in sensitivity_order or new_sensitivity not in sensitivity_order:\n        raise SourceHandlingBlockedError("fact sensitivity is not comparable")\n    if sensitivity_order[new_sensitivity] < sensitivity_order[old_sensitivity]:\n        releases.add(f"SENSITIVITY:{old_sensitivity}")\n    old_persistence, new_persistence = str(predecessor.get("persistence_restriction")), str(candidate.get("persistence_restriction"))\n    if old_persistence not in persistence_order or new_persistence not in persistence_order:\n        raise SourceHandlingBlockedError("fact persistence restriction is not comparable")\n    if persistence_order[new_persistence] < persistence_order[old_persistence]:\n        releases.add(f"PERSISTENCE:{old_persistence}")\n    old_ops = set(_string_sequence(predecessor.get("operation_restrictions")))\n    new_ops = set(_string_sequence(candidate.get("operation_restrictions")))\n    releases.update(old_ops - new_ops)\n    old_secrets = set(_string_sequence(predecessor.get("secret_presence")))\n    new_secrets = set(_string_sequence(candidate.get("secret_presence")))\n    releases.update(f"SECRET_PRESENCE:{value}" for value in old_secrets - new_secrets)\n    for field, label in (("withdrawn", "WITHDRAWN"), ("deleted_at_source", "DELETED_AT_SOURCE"), ("historically_unavailable", "HISTORICALLY_UNAVAILABLE")):\n        if predecessor.get(field) is True and candidate.get(field) is not True:\n            releases.add(label)\n    return frozenset(releases)\n\n\ndef _validate_safe_control_proof(*, registry: Mapping[str, typing.Any], field: str, value: object) -> None:\n    proofs = registry.get("safe_control_proofs")\n    if not isinstance(proofs, Mapping):\n        raise SourceHandlingBlockedError("SAFE_CONTROL_ID construction proof is unavailable")\n    proof = proofs.get(field)\n    if not isinstance(proof, Mapping):\n        raise SourceHandlingBlockedError("SAFE_CONTROL_ID field lacks governed construction proof")\n    proof_id = proof.get("proof_id")\n    allowed_values = proof.get("allowed_values")\n    if not isinstance(proof_id, str) or not proof_id or not isinstance(allowed_values, (list, tuple, set, frozenset)):\n        raise SourceHandlingBlockedError("SAFE_CONTROL_ID construction proof is incomplete")\n    actual_value = value.get("value") if isinstance(value, Mapping) else value\n    if actual_value not in allowed_values:\n        raise SourceHandlingBlockedError("SAFE_CONTROL_ID value is not proven by the historical registry")\n    if isinstance(value, Mapping) and value.get("proof_id") not in {None, proof_id}:\n        raise SourceHandlingBlockedError("SAFE_CONTROL_ID proof identity mismatch")\n\n\n'''
if helper_anchor not in text:
    raise SystemExit('helper insertion anchor missing')
text = text.replace(helper_anchor, helpers + helper_anchor, 1)
source_path.write_text(text)

# Test-only canonical provenance adapter.
harness_path = Path('tests/source_handling_runtime_harness.py')
htext = harness_path.read_text().replace('    authority_store,\n', '    authority_store as _runtime_authority_store,\n', 1)
marker = '\nblocked_error = SourceHandlingBlockedError\n'
insertion = '''\n\ndef _test_provenance_resolver(provenance_id: str, provenance_kind: str, cutoff):\n    del cutoff\n    base = {\n        "provenance_id": provenance_id,\n        "provenance_kind": provenance_kind,\n        "effective_from": "2026-08-14T00:00:00Z",\n        "recorded_at": "2026-08-14T01:00:00Z",\n        "known_at": "2026-08-14T02:00:00Z",\n    }\n    if provenance_kind == "EVIDENCE" and provenance_id.startswith("evidence:"):\n        if "detector" in provenance_id:\n            return {**base, "evidence_strength": "OBSERVED_RESTRICTIVE_SIGNAL", "evidence_method": "AUTOMATED_RESTRICTIVE_DETECTOR"}\n        return {**base, "evidence_strength": "AUTHORITATIVE_SOURCE_EVIDENCE", "evidence_method": "SOURCE_TERMS_VERIFIED"}\n    if provenance_kind == "VERIFIER" and provenance_id.startswith("verifier:"):\n        return {**base, "verifier_type": "DETECTOR" if "detector" in provenance_id else "SOURCE_VERIFIER"}\n    return None\n\n\ndef authority_store():\n    return _runtime_authority_store(provenance_resolver=_test_provenance_resolver)\n'''
if marker not in htext:
    raise SystemExit('harness marker missing')
htext = htext.replace(marker, insertion + marker, 1)
harness_path.write_text(htext)

# SAFE_CONTROL_ID positive contract test now carries canonical-registry proof.
contract_path = Path('tests/test_source_handling_authority_contract.py')
ctext = contract_path.read_text()
old = '''    registry = {\n        "field_category_registry_id": "registry-v1",\n        "field_map": {\n            "run_id": ["SAFE_CONTROL_ID"],\n        },\n    }\n'''
new = '''    registry = {\n        "field_category_registry_id": "registry-v1",\n        "field_map": {\n            "run_id": ["SAFE_CONTROL_ID"],\n        },\n        "safe_control_proofs": {\n            "run_id": {\n                "proof_id": "proof:run-id:v1",\n                "allowed_values": ["run-123"],\n            }\n        },\n    }\n'''
if old not in ctext:
    raise SystemExit('safe-control contract test anchor missing')
ctext = ctext.replace(old, new, 1)
ctext = ctext.replace(
    '                "derived_from_protected_content": False,\n            }\n',
    '                "derived_from_protected_content": False,\n                "proof_id": "proof:run-id:v1",\n            }\n',
    1,
)
contract_path.write_text(ctext)

# Enforcement helper and focused Codex regressions.
enforcement_path = Path('tests/test_source_handling_authority_enforcement.py')
etext = enforcement_path.read_text()
etext = etext.replace(
    'def _ready_store(\n    h: ModuleType,\n    *,\n    persist: str = "ALLOW",\n    secret_presence: list[str] | None = None,\n    field_category: str = "AUDIT_FIELD",\n):\n',
    'def _ready_store(\n    h: ModuleType,\n    *,\n    persist: str = "ALLOW",\n    retention: str = "ALLOW",\n    secret_presence: list[str] | None = None,\n    field_category: str = "AUDIT_FIELD",\n):\n',
    1,
)
etext = etext.replace(
    '        "field_map": {"audit": [field_category]},\n        "requested_change": "PERMISSIVE_GENESIS",\n',
    '        "field_map": {"audit": [field_category]},\n        "safe_control_proofs": ({"audit": {"proof_id": "proof:audit-control:v1", "allowed_values": ["safe"]}} if field_category == "SAFE_CONTROL_ID" else {}),\n        "requested_change": "PERMISSIVE_GENESIS",\n',
    1,
)
etext = etext.replace('            "retention_decision": "ALLOW",\n', '            "retention_decision": retention,\n', 1)

etext += '''


def test_codex_p1_caller_cannot_launder_provenance_classification() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {"scope": "doc-x", "fact": {"sensitivity": "PUBLIC", "operation_restrictions": [], "persistence_restriction": "FULL_CONTENT_ALLOWED", "secret_presence": [], "operation_restrictions_known": True, "secret_presence_known": True, "withdrawn": False, "deleted_at_source": False, "historically_unavailable": False, "availability_known": True}, **_times()}
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(store, authorization_id="auth:launder", publication_kind="FACT", governed_subject_scope="doc-x", payload=payload, authorization_rule_id="AUTHORIZATION_RULE_V1", evidence_ids=("not-canonical-evidence",), evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE", evidence_method="SOURCE_TERMS_VERIFIED", verifier_ids=("not-canonical-verifier",), verifier_type="SOURCE_VERIFIER", **_times())


def test_codex_p1_restrictive_label_cannot_hide_permissive_genesis() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {"scope": "doc-permissive", "fact": {"sensitivity": "PUBLIC", "operation_restrictions": [], "persistence_restriction": "FULL_CONTENT_ALLOWED", "secret_presence": [], "operation_restrictions_known": True, "secret_presence_known": True, "withdrawn": False, "deleted_at_source": False, "historically_unavailable": False, "availability_known": True}, **_times()}
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(store, authorization_id="auth:detector-launder", publication_kind="FACT", governed_subject_scope="doc-permissive", payload=payload, authorization_rule_id="AUTHORIZATION_RULE_V1", evidence_ids=("evidence:detector-launder",), evidence_strength="OBSERVED_RESTRICTIVE_SIGNAL", evidence_method="AUTOMATED_RESTRICTIVE_DETECTOR", verifier_ids=("verifier:detector-launder",), verifier_type="DETECTOR", requested_change="MORE_RESTRICTIVE", **_times())


def test_codex_p1_top_level_retention_denial_blocks_persistence() -> None:
    h = _harness()
    store = _ready_store(h, retention="DENY", field_category="AUDIT_FIELD")
    with pytest.raises(h.blocked_error):
        h.enforce_persistence(store, fact_scope="doc-1", policy_scope="policy:source-handling:v1", cutoff=_utc("2026-08-14T12:00:00Z"), payload={"audit": "safe-audit"})


def test_codex_p1_safe_control_requires_canonical_registry_proof() -> None:
    h = _harness()
    decision = {"field_category_registry_id": "registry-v1", "durable_dispositions": {"SAFE_CONTROL_ID": _complete_disposition()}}
    registry = {"field_category_registry_id": "registry-v1", "field_map": {"audit": ["SAFE_CONTROL_ID"]}}
    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(decision=decision, registry=registry, payload={"audit": {"value": "secret", "derived_from_protected_content": False}}, secret_presence={"SECRET_PRESENT"})


def test_codex_p1_stale_authorization_rule_cannot_issue_after_successor_is_applicable() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    v2 = {"id": "AUTHORIZATION_RULE_V2", "authorization_rule_id": "AUTHORIZATION_RULE_V2", "scope": "SOURCE_HANDLING", "rule_schema_version": 1, "rule_body": _rule_fixture()["rule_body"], "effective_from": _utc("2026-08-14T03:00:00Z"), "recorded_at": _utc("2026-08-14T04:00:00Z"), "known_at": _utc("2026-08-14T05:00:00Z"), "supersedes_authorization_rule_id": "AUTHORIZATION_RULE_V1"}
    store.compare_and_append(family="AUTHORIZATION_RULE", scope="SOURCE_HANDLING", expected_current_head_id="AUTHORIZATION_RULE_V1", record=v2)
    store._canonical_keys.add(("AUTHORIZATION_RULE", "SOURCE_HANDLING", "AUTHORIZATION_RULE_V2"))
    payload = {"scope": "doc-stale", "fact": {"sensitivity": "PUBLIC", "operation_restrictions": [], "persistence_restriction": "FULL_CONTENT_ALLOWED", "secret_presence": [], "operation_restrictions_known": True, "secret_presence_known": True, "withdrawn": False, "deleted_at_source": False, "historically_unavailable": False, "availability_known": True}, **_times()}
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(store, authorization_id="auth:stale-rule", publication_kind="FACT", governed_subject_scope="doc-stale", payload=payload, authorization_rule_id="AUTHORIZATION_RULE_V1", evidence_ids=("evidence:stale-rule",), evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE", evidence_method="SOURCE_TERMS_VERIFIED", verifier_ids=("verifier:stale-rule",), verifier_type="SOURCE_VERIFIER", **_times())
'''
enforcement_path.write_text(etext)
