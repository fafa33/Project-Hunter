"""Final runtime hardening for ADR 0036 Source Handling production seams.

This module installs fail-closed runtime guards identified by the final hostile
review of Issue #407. It is imported by the package after the canonical
persistence module has loaded so the guards apply to every normal import path,
including direct submodule imports.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import hunter.evidence_intelligence.repository as evidence_repository
import hunter.evidence_intelligence.source_handling_persistence as persistence
from hunter.evidence_intelligence.intake import (
    EvidenceIntakeReference,
    EvidenceIntakeResult,
    EvidenceIntelligenceIntakeService,
    PreparedEvidenceIntake,
    evidence_document_id,
)
from hunter.evidence_intelligence.pre_model import resolve_pre_model_source_handling
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError, validate_durable_payload
from hunter.execution import Clock, SystemClock

_INSTALLED = False
_ISSUE_METADATA_KEYS = frozenset({"issue_number", "labels"})


class _StrictRepositoryClock:
    """Admission clock that is strictly monotonic across the authority repository."""

    def __init__(self, delegate: Clock, path: Path) -> None:
        self._delegate = delegate
        self._path = path

    def now(self) -> datetime:
        candidate = persistence._aware_utc("repository admission clock", self._delegate.now())
        last: datetime | None = None
        if self._path.exists():
            connection = sqlite3.connect(self._path)
            try:
                row = connection.execute(
                    "SELECT admission_time FROM source_handling_authority_records ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            finally:
                connection.close()
            if row is not None and row[0] is not None:
                last = persistence._parse_time(str(row[0]))
        if last is not None and candidate <= last:
            candidate = last + timedelta(microseconds=1)
        return candidate


def _install_repository_clock() -> None:
    original_init = persistence.SourceHandlingAuthorityRepository.__init__

    def hardened_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._clock = _StrictRepositoryClock(self._clock, self.path)
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS source_handling_registry_logical_identity_unique
                ON source_handling_authority_records(
                    json_extract(payload_json, '$.field_category_registry_id')
                )
                WHERE family = 'FIELD_CATEGORY_REGISTRY'
                """)
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS source_handling_rule_logical_identity_unique
                ON source_handling_authority_records(
                    json_extract(payload_json, '$.authorization_rule_id')
                )
                WHERE family = 'AUTHORIZATION_RULE'
                """)
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise SourceHandlingBlockedError("logical authority identity is duplicated") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    persistence.SourceHandlingAuthorityRepository.__init__ = hardened_init  # type: ignore[method-assign]


def _install_writer_transaction_guard() -> None:
    def writer_transaction(self: Any) -> Iterator[sqlite3.Connection]:
        with self._connect(verify_operator_root=False, verify_history=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
                ).fetchone()
                if row is None:
                    raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
                persistence._verify_operator_root_row(row, self._operator_root)
                persistence._verify_authenticated_history(
                    connection,
                    verification_public_key_bytes=self._verification_public_key_bytes,
                    operator_root=self._operator_root,
                )
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    persistence.SourceHandlingAuthorityRepository._transaction = contextlib.contextmanager(writer_transaction)


def _install_fact_completeness_guard() -> None:
    original_validate = persistence._validate_payload_shape

    def hardened_validate(family: str, scope: str, payload: Mapping[str, Any]) -> None:
        original_validate(family, scope, payload)
        if family != "FACT":
            return
        fact = payload.get("fact")
        if not isinstance(fact, Mapping):
            raise SourceHandlingBlockedError("fact publication payload is incomplete")
        if fact.get("availability_known") is not True:
            raise SourceHandlingBlockedError("FACT dimension is unknown: availability_known")
        for field in ("withdrawn", "deleted_at_source", "historically_unavailable"):
            if type(fact.get(field)) is not bool:
                raise SourceHandlingBlockedError(f"FACT availability state is unknown or malformed: {field}")

    persistence._validate_payload_shape = hardened_validate


def _install_read_snapshot_guard() -> None:
    def snapshot_connect(self: Any) -> Iterator[sqlite3.Connection]:
        if not self._path.exists():
            raise SourceHandlingBlockedError("Source Handling authority database is unavailable")
        connection = sqlite3.connect(f"file:{self._path.resolve()}?mode=ro", uri=True, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        try:
            row = connection.execute(
                "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
            ).fetchone()
            if row is None:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
            persistence._verify_operator_root_row(row, self._operator_root)
            persistence._verify_authenticated_history(
                connection,
                verification_public_key_bytes=self._verification_public_key_bytes,
                operator_root=self._operator_root,
            )
            yield connection
        finally:
            connection.rollback()
            connection.close()

    persistence.SqliteSourceHandlingAuthorityReadView._connect = contextlib.contextmanager(snapshot_connect)


def _enforce_fact_persistence_restriction(
    *,
    fact: Mapping[str, Any],
    durable_payload: Mapping[str, Any],
) -> None:
    restriction = fact.get("persistence_restriction")
    nonempty = {name for name, value in durable_payload.items() if isinstance(value, Mapping) and value}
    if restriction == "FULL_CONTENT_ALLOWED":
        return
    if restriction == "DERIVED_ONLY":
        forbidden = {"source_derived_text"}
    elif restriction == "METADATA_ONLY":
        forbidden = {"content_derived_ids", "locator_urls", "source_derived_text"}
    elif restriction == "NO_PERSISTENCE":
        forbidden = set(nonempty)
    else:
        raise SourceHandlingBlockedError("Issue Source persistence restriction is unknown")
    if nonempty & forbidden:
        raise SourceHandlingBlockedError("Issue Source FACT persistence restriction forbids prepared durable artifacts")


def _validate_issue_metadata(metadata: Mapping[str, Any]) -> None:
    if set(metadata) - _ISSUE_METADATA_KEYS:
        raise SourceHandlingBlockedError("Issue Source metadata contains unsupported non-operational fields")
    issue_number = metadata.get("issue_number")
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number <= 0:
        raise SourceHandlingBlockedError("Issue Source issue_number metadata is malformed")
    labels = metadata.get("labels")
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise SourceHandlingBlockedError("Issue Source labels metadata is malformed")


def _authority_current_cutoff(path: Path, clock: Clock) -> datetime:
    cutoff = persistence._aware_utc("Issue Source authority cutoff", clock.now())
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT admission_time FROM source_handling_authority_records ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is not None and row[0] is not None:
        cutoff = max(cutoff, persistence._parse_time(str(row[0])))
    return cutoff


def _persist_prepared_in_connection(prepared: PreparedEvidenceIntake, connection: sqlite3.Connection) -> None:
    result = prepared.result
    evidence_repository._upsert_payload(
        connection,
        "evidence_documents",
        evidence_repository._payload(result.document),
        key=("document_id",),
    )
    evidence_repository._upsert_payload(
        connection,
        "evidence_document_versions",
        evidence_repository._payload(result.document_version),
        key=("version_id",),
    )
    for span in result.spans:
        evidence_repository._upsert_payload(
            connection,
            "evidence_spans",
            evidence_repository._payload(span),
            key=("span_id",),
        )
    evidence_repository._upsert_payload(
        connection,
        "document_lifecycle_events",
        evidence_repository._payload(result.document_event),
        key=("event_id",),
    )
    evidence_repository._upsert_payload(
        connection,
        "source_authority_verification_events",
        evidence_repository._payload(result.authority_event),
        key=("verification_id",),
    )
    for link in prepared.span_links:
        payload = evidence_repository._payload(link)
        payload["event_id"] = payload.pop("owner_id")
        evidence_repository._upsert_payload(
            connection,
            "document_lifecycle_event_span_links",
            payload,
            key=("link_id",),
        )


def _install_issue_intake_guard() -> None:
    def hardened_init(
        self: Any,
        *,
        intake: EvidenceIntelligenceIntakeService,
        resolver: persistence.ProductionSourceHandlingAuthorityResolver,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(intake, EvidenceIntelligenceIntakeService):
            raise TypeError("Issue Source boundary requires EvidenceIntelligenceIntakeService")
        if not isinstance(resolver, persistence.ProductionSourceHandlingAuthorityResolver):
            raise TypeError("Issue Source boundary requires the production read-only resolver")
        self._intake = intake
        self._resolver = resolver
        self._clock = clock or SystemClock()

    def hardened_ingest(
        self: Any,
        reference: EvidenceIntakeReference,
        *,
        processing_run_id: str,
        processed_at: datetime,
    ) -> EvidenceIntakeResult:
        artifact_time = persistence._aware_utc("Issue Source processed_at", processed_at)
        _validate_issue_metadata(reference.metadata)
        document_id = evidence_document_id(reference)
        prepared = self._intake.prepare(
            reference,
            processing_run_id=processing_run_id,
            processed_at=artifact_time,
        )
        durable_payload = persistence._issue_intake_durable_payload(prepared)

        connection = self._intake.repository._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            authority_cutoff = _authority_current_cutoff(self._intake.repository.path, self._clock)
            resolved = resolve_pre_model_source_handling(self._resolver(document_id, authority_cutoff))
            decision = resolved.decision
            if decision.get("retention_decision") != "ALLOW":
                raise SourceHandlingBlockedError("Issue Source retention is not allowed")
            if decision.get("deletion_lifecycle_decision") in {"DELETE", "BLOCKED"}:
                raise SourceHandlingBlockedError("Issue Source deletion lifecycle blocks durable intake")
            dispositions = decision.get("durable_dispositions")
            if not isinstance(dispositions, Mapping) or not dispositions:
                raise SourceHandlingBlockedError("Issue Source durable dispositions are unavailable")
            fact = resolved.fact_record.get("fact")
            if not isinstance(fact, Mapping):
                raise SourceHandlingBlockedError("Issue Source fact authority is unavailable")
            secret_presence = set(persistence._string_values(fact.get("secret_presence")))
            _enforce_fact_persistence_restriction(fact=fact, durable_payload=durable_payload)
            validate_durable_payload(
                decision=decision,
                registry=resolved.registry_record,
                payload=durable_payload,
                secret_presence=secret_presence,
            )
            _persist_prepared_in_connection(prepared, connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return prepared.result

    persistence.IssueSourceTransientIntakeBoundary.__init__ = hardened_init  # type: ignore[method-assign]
    persistence.IssueSourceTransientIntakeBoundary.ingest = hardened_ingest  # type: ignore[method-assign]


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_repository_clock()
    _install_writer_transaction_guard()
    _install_fact_completeness_guard()
    _install_read_snapshot_guard()
    _install_issue_intake_guard()
    _INSTALLED = True
