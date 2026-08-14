from pathlib import Path

source_path = Path('src/hunter/evidence_intelligence/source_handling.py')
text = source_path.read_text()

old = '''    store._validate_authorization_provenance(authorization, cutoff=cutoff)
    rule = store._resolve_rule_for_authorization(authorization)
    _validate_publication_authorization_evidence(
        authorization=authorization,
        authorization_rule=rule,
        requested_change=str(record.get("requested_change", "PERMISSIVE_GENESIS")),
    )
'''
new = '''    store._validate_authorization_provenance(authorization, cutoff=cutoff)
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
'''
if old not in text:
    raise SystemExit('reverify requested-change anchor missing')
text = text.replace(old, new, 1)

anchor = '''def _fact_has_restriction(fact: Mapping[str, typing.Any]) -> bool:
'''
helper = '''def _derive_persisted_publication_change(
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


'''
if anchor not in text:
    raise SystemExit('persisted derivation helper anchor missing')
text = text.replace(anchor, helper + anchor, 1)
source_path.write_text(text)

test_path = Path('tests/test_source_handling_authority_enforcement.py')
tests = test_path.read_text()
tests += '''


def test_replay_rederives_change_type_instead_of_trusting_persisted_label() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-replay-derived-change",
        "fact": {
            "sensitivity": "INTERNAL",
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": [],
            "operation_restrictions_known": True,
            "secret_presence_known": True,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        "requested_change": "PERMISSIVE_GENESIS",
        **_times(),
    }
    authorization = h.issue_publication_authorization(
        store,
        authorization_id="auth:replay-derived-change",
        publication_kind="FACT",
        governed_subject_scope="doc-replay-derived-change",
        payload=payload,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        evidence_ids=("evidence:detector:replay-derived-change",),
        evidence_strength="OBSERVED_RESTRICTIVE_SIGNAL",
        evidence_method="AUTOMATED_RESTRICTIVE_DETECTOR",
        verifier_ids=("verifier:detector:replay-derived-change",),
        verifier_type="DETECTOR",
        requested_change="PERMISSIVE_GENESIS",
        **_times(),
    )
    store.publish(
        family="FACT",
        scope="doc-replay-derived-change",
        expected_current_head_id=None,
        record={
            "id": "fact-replay-derived-change",
            **payload,
            "publication_authorization": authorization,
        },
    )
    resolved = h.resolve_canonical_head(
        store,
        family="FACT",
        scope="doc-replay-derived-change",
        cutoff=_utc("2026-08-14T12:00:00Z"),
    )
    assert resolved["id"] == "fact-replay-derived-change"


def test_default_authority_store_cannot_authorize_without_canonical_provenance() -> None:
    h = _harness()
    store = h.runtime_module.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-no-provenance",
        "fact": {
            "sensitivity": "PUBLIC",
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": [],
            "operation_restrictions_known": True,
            "secret_presence_known": True,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        **_times(),
    }
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(
            store,
            authorization_id="auth:no-provenance",
            publication_kind="FACT",
            governed_subject_scope="doc-no-provenance",
            payload=payload,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            evidence_ids=("evidence:invented",),
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_ids=("verifier:invented",),
            verifier_type="SOURCE_VERIFIER",
            **_times(),
        )
'''
test_path.write_text(tests)
