"""Hostile regressions for the canonical ADR 0036 Source Handling seam.

Each test pins one invariant that a previously shipped revision did not hold.
The design-contract section that governs the invariant is named in the test so a
later reader can check the guard against its authority rather than against an
earlier implementation.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_source_handling_production_runtime import (
    INTAKE_FIELD_MAP,
    INTAKE_TABLES,
    _complete_authority,
    _fact_payload,
    _policy_payload,
    _publish,
    _reference,
    _registry_payload,
    _service,
)

from hunter.evidence_intelligence.intake import (
    EvidenceIntelligenceIntakeService,
    evidence_document_id,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import (
    GOVERNED_DURABLE_CATEGORIES,
    SourceHandlingBlockedError,
    effective_lifecycle_disposition,
    effective_persist_disposition,
    governed_field_categories,
    validate_durable_payload,
)
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
)

ALL_OPERATIONS_ALLOWED = {
    "PERSIST": "ALLOW",
    "READ_ACCESS": "ALLOW",
    "RECONSTRUCT": "ALLOW",
    "DELETE_OR_EXPIRE": "ALLOW",
}


def _authority_with_field_map(
    service: Any,
    clock: Any,
    rule_id: str,
    *,
    document_id: str,
    field_map: dict[str, list[str]],
    secrets: tuple[str, ...] = (),
    disposition_overrides: dict[str, dict[str, str]] | None = None,
    deletion: str = "ALLOW",
) -> None:
    """Publish a complete FACT/REGISTRY/POLICY authority set for one field map."""

    _publish(
        service,
        family="FACT",
        scope=document_id,
        payload=_fact_payload(document_id, clock.now(), secrets=secrets),
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:fact:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    registry_logical_id = f"registry:{document_id}:v1"
    registry_payload = _registry_payload(document_id, clock.now(), registry_id=registry_logical_id)
    registry_payload["field_map"] = field_map
    _publish(
        service,
        family="FIELD_CATEGORY_REGISTRY",
        scope=f"registry:{document_id}:v1",
        payload=registry_payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:registry:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    dispositions = {
        category: dict(ALL_OPERATIONS_ALLOWED) for categories in field_map.values() for category in categories
    }
    for category, override in (disposition_overrides or {}).items():
        dispositions[category] = {**dispositions.get(category, dict(ALL_OPERATIONS_ALLOWED)), **override}
    policy_payload = _policy_payload(document_id, clock.now(), registry_id=registry_logical_id, deletion=deletion)
    policy_payload["policy_body"]["durable_dispositions"] = dispositions
    _publish(
        service,
        family="POLICY",
        scope=f"policy:{document_id}:v1",
        payload=policy_payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:policy:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )


def _boundary(service: Any, clock: Any) -> tuple[EvidenceIntelligenceRepository, Any]:
    repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=service.resolver(),
        clock=clock,
    )
    return repository, boundary


def _assert_no_durable_intake(repository: EvidenceIntelligenceRepository) -> None:
    assert {table: repository.count(table) for table in INTAKE_TABLES} == {table: 0 for table in INTAKE_TABLES}


# --- Governed category vocabulary (design contract section 7) -----------------


def test_registry_declaring_an_undeclared_category_cannot_become_authority(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _registry_payload("doc-vocab", clock.now(), registry_id="registry:doc-vocab:v1")
    payload["field_map"] = {"source_derived_text": ["MADE_UP_SAFE_CATEGORY"]}

    with pytest.raises(SourceHandlingBlockedError, match="undeclared durable category"):
        _publish(
            service,
            family="FIELD_CATEGORY_REGISTRY",
            scope="registry:doc-vocab:v1",
            payload=payload,
            rule_id=rule_id,
            expected_head=None,
            authorization_id="auth:registry:doc-vocab",
            expires_at=clock.now() + timedelta(minutes=10),
        )


def test_governed_field_categories_resolves_unknown_mappings_to_unknown_category() -> None:
    field_map = {
        "declared": ["SOURCE_DERIVED_TEXT"],
        "invented": ["MADE_UP_SAFE_CATEGORY"],
        "empty": [],
        "malformed": 7,
        "mixed": ["AUDIT_FIELD", "MADE_UP_SAFE_CATEGORY"],
    }
    assert governed_field_categories(field_map, "declared") == ("SOURCE_DERIVED_TEXT",)
    assert governed_field_categories(field_map, "invented") == ("UNKNOWN_CATEGORY",)
    assert governed_field_categories(field_map, "empty") == ("UNKNOWN_CATEGORY",)
    assert governed_field_categories(field_map, "malformed") == ("UNKNOWN_CATEGORY",)
    assert governed_field_categories(field_map, "absent") == ("UNKNOWN_CATEGORY",)
    assert governed_field_categories(field_map, "mixed") == ("AUDIT_FIELD", "UNKNOWN_CATEGORY")


@pytest.mark.parametrize("category", ["MADE_UP_SAFE_CATEGORY", "UNKNOWN_CATEGORY", "safe_control_id"])
def test_unknown_or_undeclared_category_is_never_persistable(category: str) -> None:
    """A registry admitted before the publication guard still cannot launder content."""

    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {"excerpt": [category]},
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {category: dict(ALL_OPERATIONS_ALLOWED)},
    }
    with pytest.raises(SourceHandlingBlockedError, match="unknown or ambiguous"):
        validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"excerpt": {"value": "source text"}},
            secret_presence=set(),
        )


@pytest.mark.parametrize("marker", ["SECRET_PRESENT", "CREDENTIAL_PRESENT"])
def test_secret_content_cannot_persist_through_an_undeclared_category(marker: str) -> None:
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {"excerpt": ["MADE_UP_SAFE_CATEGORY"]},
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {"MADE_UP_SAFE_CATEGORY": dict(ALL_OPERATIONS_ALLOWED)},
    }
    with pytest.raises(SourceHandlingBlockedError):
        validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"excerpt": {"value": "token=super-secret"}},
            secret_presence={marker},
        )


@pytest.mark.parametrize(
    "category",
    sorted(GOVERNED_DURABLE_CATEGORIES - {"SAFE_CONTROL_ID", "UNKNOWN_CATEGORY"}),
)
def test_secret_exclusion_covers_every_governed_category_except_proven_safe_control(category: str) -> None:
    """Design contract section 10 is absolute, so the exemption is an allowlist.

    ``LIFECYCLE_STATE`` in particular is a governed category that an earlier
    risk denylist omitted, which let a secret excerpt persist through it.
    """

    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {"excerpt": [category]},
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {category: dict(ALL_OPERATIONS_ALLOWED)},
    }
    with pytest.raises(SourceHandlingBlockedError, match="risky secondary category"):
        validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"excerpt": {"value": "token=super-secret"}},
            secret_presence={"SECRET_PRESENT"},
        )


def test_secret_bearing_source_cannot_persist_through_an_undeclared_category_end_to_end(
    tmp_path: Path,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("credential=super-secret-value")
    document_id = evidence_document_id(reference)
    with pytest.raises(SourceHandlingBlockedError, match="undeclared durable category"):
        _authority_with_field_map(
            service,
            clock,
            rule_id,
            document_id=document_id,
            field_map={field: ["MADE_UP_SAFE_CATEGORY"] for field in INTAKE_FIELD_MAP},
            secrets=("SECRET_PRESENT",),
        )
    repository = EvidenceIntelligenceRepository(service.path)
    _assert_no_durable_intake(repository)


def test_secret_bearing_source_cannot_persist_through_lifecycle_state_end_to_end(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("credential=super-secret-value")
    document_id = evidence_document_id(reference)
    _authority_with_field_map(
        service,
        clock,
        rule_id,
        document_id=document_id,
        field_map={field: ["LIFECYCLE_STATE"] for field in INTAKE_FIELD_MAP},
        secrets=("SECRET_PRESENT",),
    )
    repository, boundary = _boundary(service, clock)
    with pytest.raises(SourceHandlingBlockedError, match="risky secondary category"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())
    _assert_no_durable_intake(repository)


# --- Effective disposition joins (design contract section 8) ------------------


def test_effective_dispositions_take_the_most_restrictive_value_across_categories() -> None:
    dispositions = {
        "AUDIT_FIELD": {**ALL_OPERATIONS_ALLOWED, "DELETE_OR_EXPIRE": "EXPIRE"},
        "SOURCE_DERIVED_TEXT": {**ALL_OPERATIONS_ALLOWED, "PERSIST": "OMIT", "DELETE_OR_EXPIRE": "DELETE"},
    }
    categories = ("AUDIT_FIELD", "SOURCE_DERIVED_TEXT")
    assert effective_persist_disposition(dispositions, categories) == "OMIT"
    # DELETE dominates EXPIRE, so an ALLOW elsewhere cannot erase the obligation.
    assert effective_lifecycle_disposition(dispositions, categories) == "DELETE"
    assert effective_lifecycle_disposition(dispositions, ("AUDIT_FIELD",)) == "EXPIRE"
    # Missing or invalid values are BLOCKED, never permission.
    assert effective_lifecycle_disposition({"AUDIT_FIELD": {}}, ("AUDIT_FIELD",)) == "BLOCKED"
    assert effective_persist_disposition({"AUDIT_FIELD": {"PERSIST": "MAYBE"}}, ("AUDIT_FIELD",)) == "BLOCKED"
    assert effective_lifecycle_disposition(dispositions, ()) == "BLOCKED"


@pytest.mark.parametrize("lifecycle", ["DELETE", "BLOCKED"])
def test_category_lifecycle_obligation_blocks_intake_despite_top_level_allow(
    tmp_path: Path,
    lifecycle: str,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _authority_with_field_map(
        service,
        clock,
        rule_id,
        document_id=document_id,
        field_map=dict(INTAKE_FIELD_MAP),
        disposition_overrides={"SOURCE_DERIVED_TEXT": {"DELETE_OR_EXPIRE": lifecycle}},
        deletion="ALLOW",
    )
    repository, boundary = _boundary(service, clock)

    with pytest.raises(SourceHandlingBlockedError, match="lifecycle disposition forbids"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_no_durable_intake(repository)


def test_mixed_category_lifecycle_dispositions_resolve_to_the_dominant_obligation(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _authority_with_field_map(
        service,
        clock,
        rule_id,
        document_id=document_id,
        field_map=dict(INTAKE_FIELD_MAP),
        disposition_overrides={
            "CONTENT_DERIVED_ID": {"DELETE_OR_EXPIRE": "EXPIRE"},
            "SOURCE_DERIVED_TEXT": {"DELETE_OR_EXPIRE": "DELETE"},
        },
    )
    repository, boundary = _boundary(service, clock)

    with pytest.raises(SourceHandlingBlockedError, match="lifecycle disposition forbids"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_no_durable_intake(repository)


def test_category_expiry_obligation_alone_does_not_block_a_durable_write(tmp_path: Path) -> None:
    """EXPIRE establishes a governed obligation, not a write prohibition.

    Blocking it would reject canonically valid authority, so the guard is pinned
    against over-restriction as well as under-restriction.
    """

    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _authority_with_field_map(
        service,
        clock,
        rule_id,
        document_id=document_id,
        field_map=dict(INTAKE_FIELD_MAP),
        disposition_overrides={"SOURCE_DERIVED_TEXT": {"DELETE_OR_EXPIRE": "EXPIRE"}},
    )
    repository, boundary = _boundary(service, clock)

    boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    assert repository.count("evidence_documents") == 1


# --- Registry schema completeness (design contract section 4.3) ---------------


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"field_map": None}, "mapping is unavailable"),
        ({"field_map": {}}, "mapping is unavailable"),
        ({"field_map": []}, "mapping is unavailable"),
        ({"field_category_registry_id": None}, "identity is missing or malformed"),
        ({"field_category_registry_id": 7}, "identity is missing or malformed"),
        ({"field_category_registry_id": "  "}, "identity is missing or malformed"),
        ({"field_map": {"excerpt": []}}, "mapping entry is malformed"),
        ({"field_map": {"excerpt": [123]}}, "mapping entry is malformed"),
        ({"field_map": {"excerpt": "SOURCE_BYTES", "": ["AUDIT_FIELD"]}}, "field identity is malformed"),
        ({"safe_control_proofs": []}, "safe-control proofs are malformed"),
    ],
)
def test_incomplete_registry_payload_cannot_become_authority(
    tmp_path: Path,
    mutation: dict[str, Any],
    expected: str,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _registry_payload("doc-schema", clock.now(), registry_id="registry:doc-schema:v1")
    payload.update(mutation)

    with pytest.raises(SourceHandlingBlockedError, match=expected):
        _publish(
            service,
            family="FIELD_CATEGORY_REGISTRY",
            scope="registry:doc-schema:v1",
            payload=payload,
            rule_id=rule_id,
            expected_head=None,
            authorization_id="auth:registry:doc-schema",
            expires_at=clock.now() + timedelta(minutes=10),
        )


# --- Repository admission time (design contract section 5) --------------------


def test_admission_timestamps_are_fixed_width_so_sql_ordering_is_chronological(tmp_path: Path) -> None:
    """Variable-width ISO text made ``MAX``/``ORDER BY`` disagree with time.

    ``"...:00Z" > "...:00.000001Z"`` lexically because ``"Z" > "."``, so the
    repository's own backwards-clock guard read the wrong row.
    """

    service, clock, _key, rule_id = _service(tmp_path)
    _complete_authority(service, clock, rule_id, document_id="doc-order")

    connection = sqlite3.connect(service.path)
    try:
        stored = [
            str(row[0])
            for row in connection.execute("SELECT admission_time FROM source_handling_authority_records ORDER BY rowid")
        ]
        maximum = str(
            connection.execute("SELECT MAX(admission_time) FROM source_handling_authority_records").fetchone()[0]
        )
    finally:
        connection.close()

    assert len(stored) >= 4
    assert all(len(value) == len(stored[0]) for value in stored), stored
    assert stored == sorted(stored), stored
    assert len(set(stored)) == len(stored), stored
    assert maximum == stored[-1]


def test_frozen_clock_still_yields_strictly_increasing_repository_admission_times(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    frozen = clock.value
    _complete_authority(service, clock, rule_id, document_id="doc-frozen")
    clock.value = frozen  # a clock that never advances must not stall admission

    _publish(
        service,
        family="FACT",
        scope="doc-frozen-2",
        payload=_fact_payload("doc-frozen-2", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:fact:doc-frozen-2",
        expires_at=clock.now() + timedelta(minutes=10),
    )

    connection = sqlite3.connect(service.path)
    try:
        stored = [
            str(row[0])
            for row in connection.execute("SELECT admission_time FROM source_handling_authority_records ORDER BY rowid")
        ]
    finally:
        connection.close()
    assert len(set(stored)) == len(stored), stored
    assert stored == sorted(stored), stored


def test_historical_cutoff_stays_stable_after_a_later_successor_is_published(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    document_id = "doc-replay"
    ids = _complete_authority(service, clock, rule_id, document_id=document_id)
    resolver = service.resolver()
    cutoff = clock.now()
    before = resolver(document_id, cutoff).store.current_canonical_head_id("FACT", document_id)
    assert before == ids["fact"]

    clock.value = clock.value + timedelta(minutes=5)
    successor = _fact_payload(document_id, clock.now(), supersedes=ids["fact"], sensitivity="RESTRICTED")
    _publish(
        service,
        family="FACT",
        scope=document_id,
        payload=successor,
        rule_id=rule_id,
        expected_head=ids["fact"],
        authorization_id="auth:fact:successor",
        expires_at=clock.now() + timedelta(minutes=10),
    )

    replayed = service.resolver()(document_id, cutoff)
    records = replayed.store.canonical_records("FACT", document_id)
    eligible = [record for record in records if record["admission_time"] <= cutoff]
    assert [record["id"] for record in eligible] == [ids["fact"]]


# --- Coordination of authority and durable intake (sections 9 and 14) ---------


def test_intake_fails_closed_when_authority_history_is_in_another_database(tmp_path: Path) -> None:
    """The boundary's atomicity depends on one write lock covering both.

    A separate evidence database silently removes that coordination, so the
    requirement is checked rather than assumed.
    """

    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)

    separate = EvidenceIntelligenceRepository(tmp_path / "separate-evidence.sqlite")
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(separate),
        resolver=service.resolver(),
        clock=clock,
    )

    with pytest.raises(SourceHandlingBlockedError, match="same database"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_no_durable_intake(separate)


def test_intake_fails_closed_when_another_database_merely_looks_like_the_authority(
    tmp_path: Path,
) -> None:
    """A same-named table in a different file must not satisfy the coordination check.

    Coordination is proven by database identity, because a presence check alone
    would pass while the write lock still covered the wrong file.
    """

    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)

    decoy = tmp_path / "decoy-evidence.sqlite"
    separate = EvidenceIntelligenceRepository(decoy)
    connection = sqlite3.connect(decoy)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS source_handling_authority_records (admission_time TEXT)")
        connection.commit()
    finally:
        connection.close()

    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(separate),
        resolver=service.resolver(),
        clock=clock,
    )

    with pytest.raises(SourceHandlingBlockedError, match="same database"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_no_durable_intake(separate)


def test_issue_metadata_outside_the_operational_key_set_is_rejected(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = replace(
        _reference("issue body"),
        metadata={"issue_number": 407, "labels": ["runtime"], "body": "issue body"},
    )
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)
    repository, boundary = _boundary(service, clock)

    with pytest.raises(SourceHandlingBlockedError, match="non-operational fields"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_no_durable_intake(repository)


# --- Concurrency (design contract sections 5.1 and 9) -------------------------


def test_concurrent_publications_never_report_false_tamper(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    failures: list[BaseException] = []

    def publish(document_id: str) -> None:
        try:
            _publish(
                service,
                family="FACT",
                scope=document_id,
                payload=_fact_payload(document_id, clock.now()),
                rule_id=rule_id,
                expected_head=None,
                authorization_id=f"auth:fact:{document_id}",
                expires_at=clock.now() + timedelta(minutes=10),
            )
        except BaseException as error:  # noqa: BLE001 - the assertion inspects every failure
            failures.append(error)

    threads = [threading.Thread(target=publish, args=(f"doc-writer-{index}",)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [error for error in failures if "TAMPER" in str(error)] == []


def test_concurrent_reads_during_publication_never_report_false_tamper(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    _complete_authority(service, clock, rule_id, document_id="doc-reader")
    failures: list[BaseException] = []
    stop = threading.Event()

    def read() -> None:
        view = service.resolver()("doc-reader", clock.now()).store  # a read-only production view over live authority
        while not stop.is_set():
            try:
                view.canonical_records("FACT", "doc-reader")
            except BaseException as error:  # noqa: BLE001 - the assertion inspects every failure
                failures.append(error)
                return

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for index in range(6):
            clock.value = clock.value + timedelta(seconds=1)
            document_id = f"doc-concurrent-{index}"
            _publish(
                service,
                family="FACT",
                scope=document_id,
                payload=_fact_payload(document_id, clock.now()),
                rule_id=rule_id,
                expected_head=None,
                authorization_id=f"auth:fact:{document_id}",
                expires_at=clock.now() + timedelta(minutes=10),
            )
    finally:
        stop.set()
        reader.join()

    assert [error for error in failures if "TAMPER" in str(error)] == []


# --- Architecture (design contract section 14) --------------------------------


def test_source_handling_runtime_needs_no_import_time_monkeypatch_layer() -> None:
    """The hardened behavior lives in the canonical modules, not in a patch layer.

    Import-time patching made the canonical source read as if it were the
    implementation while different code actually ran, so the absence of that
    layer is pinned here.
    """

    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("hunter.evidence_intelligence.source_handling_runtime_hardening")
