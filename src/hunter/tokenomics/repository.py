from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.tokenomics.models import (
    REPORT_MODES,
    AddressClassification,
    AllocationDefinition,
    ClaimArtifactLink,
    ClassificationEvidenceLink,
    ConflictResolutionEvent,
    EvidenceLifecycleEvent,
    ExchangeFlowWindow,
    HolderBalanceSnapshot,
    HolderEntry,
    ObservationConflict,
    ObservationConflictMember,
    SupplyDefinitionReconciliation,
    SupplyObservation,
    TokenAsset,
    TokenomicsEvidenceArtifact,
    TokenomicsEvidenceClaim,
    TokenomicsReportObservationLink,
    TokenomicsReportRun,
    TokenomicsReportSufficiencyLink,
    TokenomicsSufficiencyAssessmentRecord,
    TokenRepresentation,
    TransferObservation,
    UnlockEvent,
    VenueMarketObservation,
    VestingSchedule,
    VestingScheduleSegment,
)

DEFAULT_TOKENOMICS_DB = Path("data/tokenomics/runtime/tokenomics.sqlite")

IMMUTABLE_TABLES: frozenset[str] = frozenset(
    {
        "token_assets",
        "token_representations",
        "tokenomics_evidence_artifacts",
        "tokenomics_evidence_claims",
        "tokenomics_claim_artifact_links",
        "tokenomics_evidence_lifecycle_events",
        "tokenomics_observation_conflicts",
        "tokenomics_conflict_members",
        "tokenomics_conflict_resolution_events",
        "tokenomics_report_runs",
        "tokenomics_report_observation_links",
        "tokenomics_report_sufficiency_links",
    }
)

TABLES: frozenset[str] = frozenset(
    {
        "token_asset_identities",
        "token_assets",
        "token_representation_identities",
        "token_representations",
        "tokenomics_evidence_artifacts",
        "tokenomics_evidence_claims",
        "tokenomics_claim_artifact_links",
        "tokenomics_evidence_lifecycle_events",
        "tokenomics_supply_observations",
        "tokenomics_supply_definition_reconciliations",
        "tokenomics_supply_reconciliation_claim_links",
        "tokenomics_allocation_definitions",
        "tokenomics_allocation_evidence_links",
        "tokenomics_vesting_schedules",
        "tokenomics_vesting_schedule_segments",
        "tokenomics_unlock_events",
        "tokenomics_vesting_schedule_evidence_links",
        "tokenomics_holder_balance_snapshots",
        "tokenomics_holder_entries",
        "tokenomics_address_classifications",
        "tokenomics_address_classification_evidence_links",
        "tokenomics_venue_market_observations",
        "tokenomics_transfer_observations",
        "tokenomics_exchange_flow_windows",
        "tokenomics_observation_conflicts",
        "tokenomics_conflict_members",
        "tokenomics_conflict_resolution_events",
        "tokenomics_report_runs",
        "tokenomics_report_observation_links",
        "tokenomics_sufficiency_assessments",
        "tokenomics_report_sufficiency_links",
    }
)


class TokenomicsIntegrityError(ValueError):
    """Raised when an immutable tokenomics identity is reused with divergent data."""


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS token_asset_identities (
    asset_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS token_assets (
    asset_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    PRIMARY KEY (asset_id, schema_version, effective_at, recorded_at),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE INDEX IF NOT EXISTS token_assets_identity_replay_idx
ON token_assets(asset_id, effective_at, recorded_at, schema_version);

CREATE TABLE IF NOT EXISTS token_representation_identities (
    representation_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS token_representations (
    representation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    decimals INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    PRIMARY KEY (representation_id, schema_version, effective_at, recorded_at),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS token_representations_asset_chain_address_idx
ON token_representations(asset_id, chain, contract_address, effective_at, recorded_at, schema_version);

CREATE INDEX IF NOT EXISTS token_representations_identity_replay_idx
ON token_representations(representation_id, effective_at, recorded_at, schema_version);

CREATE TABLE IF NOT EXISTS tokenomics_evidence_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (source_uri, content_hash)
);

CREATE TABLE IF NOT EXISTS tokenomics_evidence_claims (
    claim_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    unit TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    confidence_state TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS tokenomics_evidence_claims_logical_idx
ON tokenomics_evidence_claims(asset_id, subject, predicate, value, unit, effective_at, recorded_at, schema_version);

CREATE TABLE IF NOT EXISTS tokenomics_claim_artifact_links (
    link_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (claim_id, artifact_id, role),
    FOREIGN KEY (claim_id) REFERENCES tokenomics_evidence_claims(claim_id),
    FOREIGN KEY (artifact_id) REFERENCES tokenomics_evidence_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_claim_artifact_links_claim_idx
ON tokenomics_claim_artifact_links(claim_id, position, artifact_id);

CREATE TABLE IF NOT EXISTS tokenomics_evidence_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    predecessor_event_id TEXT,
    predecessor_claim_id TEXT,
    reason TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES tokenomics_evidence_claims(claim_id),
    FOREIGN KEY (predecessor_event_id) REFERENCES tokenomics_evidence_lifecycle_events(event_id),
    FOREIGN KEY (predecessor_claim_id) REFERENCES tokenomics_evidence_claims(claim_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_evidence_lifecycle_predecessor_idx
ON tokenomics_evidence_lifecycle_events(predecessor_event_id, predecessor_claim_id, claim_id, effective_at, recorded_at);

CREATE TABLE IF NOT EXISTS tokenomics_supply_observations (
    observation_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    supply_metric TEXT NOT NULL,
    amount TEXT NOT NULL,
    unit TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    availability_state TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, supply_metric, effective_at, observed_at, recorded_at, schema_version),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_supply_observations_representation_metric_time_idx
ON tokenomics_supply_observations(representation_id, supply_metric, effective_at, observed_at, recorded_at);

CREATE TABLE IF NOT EXISTS tokenomics_supply_definition_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    supply_metric TEXT NOT NULL,
    definition_state TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (asset_id, supply_metric, effective_at, recorded_at, schema_version),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_supply_reconciliation_claim_links (
    link_id TEXT PRIMARY KEY,
    reconciliation_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (reconciliation_id, claim_id, role),
    FOREIGN KEY (reconciliation_id) REFERENCES tokenomics_supply_definition_reconciliations(reconciliation_id),
    FOREIGN KEY (claim_id) REFERENCES tokenomics_evidence_claims(claim_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_allocation_definitions (
    allocation_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    category TEXT NOT NULL,
    percentage REAL,
    amount TEXT,
    unit TEXT NOT NULL,
    effective_start_at TEXT NOT NULL,
    effective_end_at TEXT,
    recorded_at TEXT NOT NULL,
    availability_state TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (asset_id, category, effective_start_at, effective_end_at, recorded_at, schema_version),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_allocations_asset_category_effective_idx
ON tokenomics_allocation_definitions(asset_id, category, effective_start_at, effective_end_at);

CREATE TABLE IF NOT EXISTS tokenomics_allocation_evidence_links (
    link_id TEXT PRIMARY KEY,
    allocation_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (allocation_id, claim_id, role),
    FOREIGN KEY (allocation_id) REFERENCES tokenomics_allocation_definitions(allocation_id),
    FOREIGN KEY (claim_id) REFERENCES tokenomics_evidence_claims(claim_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_vesting_schedules (
    schedule_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    allocation_id TEXT NOT NULL,
    schedule_state TEXT NOT NULL,
    effective_start_at TEXT NOT NULL,
    effective_end_at TEXT,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (asset_id, allocation_id, effective_start_at, effective_end_at, recorded_at, schema_version),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id),
    FOREIGN KEY (allocation_id) REFERENCES tokenomics_allocation_definitions(allocation_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_vesting_schedules_asset_allocation_effective_idx
ON tokenomics_vesting_schedules(asset_id, allocation_id, effective_start_at, effective_end_at);

CREATE TABLE IF NOT EXISTS tokenomics_vesting_schedule_segments (
    segment_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    segment_state TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    amount TEXT,
    percentage REAL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (schedule_id, start_at, end_at, recorded_at, schema_version),
    FOREIGN KEY (schedule_id) REFERENCES tokenomics_vesting_schedules(schedule_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_unlock_events (
    unlock_event_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    unlock_state TEXT NOT NULL,
    unlock_at TEXT NOT NULL,
    amount TEXT,
    percentage REAL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (schedule_id, unlock_at, amount, percentage, recorded_at, schema_version),
    FOREIGN KEY (schedule_id) REFERENCES tokenomics_vesting_schedules(schedule_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_unlock_events_schedule_time_idx
ON tokenomics_unlock_events(schedule_id, unlock_at, recorded_at);

CREATE TABLE IF NOT EXISTS tokenomics_vesting_schedule_evidence_links (
    link_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (schedule_id, claim_id, role),
    FOREIGN KEY (schedule_id) REFERENCES tokenomics_vesting_schedules(schedule_id),
    FOREIGN KEY (claim_id) REFERENCES tokenomics_evidence_claims(claim_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_holder_balance_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, observed_at, recorded_at, schema_version),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_holder_entries (
    entry_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    address TEXT NOT NULL,
    balance TEXT NOT NULL,
    unit TEXT NOT NULL,
    attribution_basis TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (snapshot_id, address),
    FOREIGN KEY (snapshot_id) REFERENCES tokenomics_holder_balance_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_holder_entries_snapshot_address_idx
ON tokenomics_holder_entries(snapshot_id, address);

CREATE TABLE IF NOT EXISTS tokenomics_address_classifications (
    classification_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    address TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category <> 'whale'),
    attribution_basis TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    confidence_state TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, address, category, valid_from, valid_to, recorded_at, schema_version),
    CHECK (attribution_basis <> 'balance_only' OR category = 'unknown'),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_address_classifications_address_category_validity_idx
ON tokenomics_address_classifications(representation_id, address, category, valid_from, valid_to, recorded_at);

CREATE TABLE IF NOT EXISTS tokenomics_address_classification_evidence_links (
    link_id TEXT PRIMARY KEY,
    classification_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (classification_id, artifact_id, role),
    FOREIGN KEY (classification_id) REFERENCES tokenomics_address_classifications(classification_id),
    FOREIGN KEY (artifact_id) REFERENCES tokenomics_evidence_artifacts(artifact_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_venue_market_observations (
    observation_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    pair TEXT NOT NULL,
    price TEXT NOT NULL,
    volume_24h TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, venue, pair, window_start, window_end, recorded_at, schema_version),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_venue_market_observations_token_venue_window_idx
ON tokenomics_venue_market_observations(representation_id, venue, window_start, window_end);

CREATE TABLE IF NOT EXISTS tokenomics_transfer_observations (
    observation_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    amount TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, tx_hash, observed_at, recorded_at, schema_version),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_exchange_flow_windows (
    window_id TEXT PRIMARY KEY,
    representation_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    inflow TEXT,
    outflow TEXT,
    coverage_state TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (representation_id, venue, window_start, window_end, recorded_at, schema_version),
    FOREIGN KEY (representation_id) REFERENCES token_representation_identities(representation_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_exchange_flow_windows_token_venue_window_idx
ON tokenomics_exchange_flow_windows(representation_id, venue, window_start, window_end);

CREATE TABLE IF NOT EXISTS tokenomics_observation_conflicts (
    conflict_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_conflict_members (
    member_id TEXT PRIMARY KEY,
    conflict_id TEXT NOT NULL,
    observation_table TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (conflict_id, observation_table, observation_id, role),
    FOREIGN KEY (conflict_id) REFERENCES tokenomics_observation_conflicts(conflict_id)
);

CREATE INDEX IF NOT EXISTS tokenomics_conflict_members_conflict_idx
ON tokenomics_conflict_members(conflict_id, observation_table, observation_id);

CREATE TABLE IF NOT EXISTS tokenomics_conflict_resolution_events (
    event_id TEXT PRIMARY KEY,
    conflict_id TEXT NOT NULL,
    resolution_state TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    predecessor_event_id TEXT,
    reason TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (conflict_id) REFERENCES tokenomics_observation_conflicts(conflict_id),
    FOREIGN KEY (predecessor_event_id) REFERENCES tokenomics_conflict_resolution_events(event_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_report_runs (
    run_id TEXT PRIMARY KEY,
    execution_identity TEXT NOT NULL,
    report_mode TEXT NOT NULL,
    cutoff_at TEXT,
    started_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (execution_identity, report_mode, cutoff_at, recorded_at, schema_version)
);

CREATE INDEX IF NOT EXISTS tokenomics_report_runs_execution_cutoff_idx
ON tokenomics_report_runs(execution_identity, report_mode, cutoff_at, recorded_at);

CREATE TABLE IF NOT EXISTS tokenomics_report_observation_links (
    link_id TEXT PRIMARY KEY,
    report_run_id TEXT NOT NULL,
    observation_table TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (report_run_id, observation_table, observation_id, role),
    FOREIGN KEY (report_run_id) REFERENCES tokenomics_report_runs(run_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_sufficiency_assessments (
    assessment_id TEXT PRIMARY KEY,
    report_run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    assessment_scope TEXT NOT NULL,
    availability_state TEXT NOT NULL,
    confidence_state TEXT NOT NULL,
    limitation TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (report_run_id) REFERENCES tokenomics_report_runs(run_id),
    FOREIGN KEY (asset_id) REFERENCES token_asset_identities(asset_id)
);

CREATE TABLE IF NOT EXISTS tokenomics_report_sufficiency_links (
    link_id TEXT PRIMARY KEY,
    report_run_id TEXT NOT NULL,
    assessment_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE (report_run_id, assessment_id, role),
    FOREIGN KEY (report_run_id) REFERENCES tokenomics_report_runs(run_id),
    FOREIGN KEY (assessment_id) REFERENCES tokenomics_sufficiency_assessments(assessment_id)
);
"""


class TokenomicsRepository:
    def __init__(self, path: str | Path = DEFAULT_TOKENOMICS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_token_asset(self, asset: TokenAsset) -> None:
        payload = _payload(asset)
        with self._connect() as conn:
            conn.execute("BEGIN")
            _insert_identity(conn, "token_asset_identities", "asset_id", asset.asset_id)
            _insert_immutable_payload(
                conn,
                "token_assets",
                payload,
                key=("asset_id", "schema_version", "effective_at", "recorded_at"),
            )

    def save_token_representation(self, representation: TokenRepresentation) -> None:
        payload = _payload(representation)
        with self._connect() as conn:
            conn.execute("BEGIN")
            _insert_identity(
                conn, "token_representation_identities", "representation_id", representation.representation_id
            )
            _insert_immutable_payload(
                conn,
                "token_representations",
                payload,
                key=("representation_id", "schema_version", "effective_at", "recorded_at"),
            )

    def save_evidence_artifact(self, artifact: TokenomicsEvidenceArtifact) -> None:
        self._upsert("tokenomics_evidence_artifacts", _payload(artifact), key=("artifact_id",))

    def save_evidence_claim(self, claim: TokenomicsEvidenceClaim) -> None:
        self._upsert("tokenomics_evidence_claims", _payload(claim), key=("claim_id",))

    def save_claim_with_lineage(
        self,
        claim: TokenomicsEvidenceClaim,
        *,
        artifacts: Iterable[TokenomicsEvidenceArtifact] = (),
        artifact_links: Iterable[ClaimArtifactLink] = (),
        lifecycle_events: Iterable[EvidenceLifecycleEvent] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "tokenomics_evidence_claims", _payload(claim), key=("claim_id",))
            for artifact in artifacts:
                _upsert_payload(conn, "tokenomics_evidence_artifacts", _payload(artifact), key=("artifact_id",))
            for link in artifact_links:
                _upsert_payload(conn, "tokenomics_claim_artifact_links", _payload(link), key=("link_id",))
            for event in lifecycle_events:
                _upsert_payload(conn, "tokenomics_evidence_lifecycle_events", _payload(event), key=("event_id",))

    def save_claim_artifact_link(self, link: ClaimArtifactLink) -> None:
        self._upsert("tokenomics_claim_artifact_links", _payload(link), key=("link_id",))

    def save_evidence_lifecycle_event(self, event: EvidenceLifecycleEvent) -> None:
        self._upsert("tokenomics_evidence_lifecycle_events", _payload(event), key=("event_id",))

    def save_supply_observation(self, observation: SupplyObservation) -> None:
        self._upsert("tokenomics_supply_observations", _payload(observation), key=("observation_id",))

    def save_supply_definition_reconciliation(self, reconciliation: SupplyDefinitionReconciliation) -> None:
        self._upsert(
            "tokenomics_supply_definition_reconciliations",
            _payload(reconciliation),
            key=("reconciliation_id",),
        )

    def save_allocation_definition(self, allocation: AllocationDefinition) -> None:
        self._upsert("tokenomics_allocation_definitions", _payload(allocation), key=("allocation_id",))

    def save_vesting_schedule(self, schedule: VestingSchedule) -> None:
        self._upsert("tokenomics_vesting_schedules", _payload(schedule), key=("schedule_id",))

    def save_vesting_schedule_segment(self, segment: VestingScheduleSegment) -> None:
        self._upsert("tokenomics_vesting_schedule_segments", _payload(segment), key=("segment_id",))

    def save_unlock_event(self, event: UnlockEvent) -> None:
        self._upsert("tokenomics_unlock_events", _payload(event), key=("unlock_event_id",))

    def save_holder_balance_snapshot(self, snapshot: HolderBalanceSnapshot) -> None:
        self._upsert("tokenomics_holder_balance_snapshots", _payload(snapshot), key=("snapshot_id",))

    def save_holder_entry(self, entry: HolderEntry) -> None:
        self._upsert("tokenomics_holder_entries", _payload(entry), key=("entry_id",))

    def save_address_classification(self, classification: AddressClassification) -> None:
        self._upsert("tokenomics_address_classifications", _payload(classification), key=("classification_id",))

    def save_classification_evidence_link(self, link: ClassificationEvidenceLink) -> None:
        self._upsert("tokenomics_address_classification_evidence_links", _payload(link), key=("link_id",))

    def save_venue_market_observation(self, observation: VenueMarketObservation) -> None:
        self._upsert("tokenomics_venue_market_observations", _payload(observation), key=("observation_id",))

    def save_transfer_observation(self, observation: TransferObservation) -> None:
        self._upsert("tokenomics_transfer_observations", _payload(observation), key=("observation_id",))

    def save_exchange_flow_window(self, window: ExchangeFlowWindow) -> None:
        self._upsert("tokenomics_exchange_flow_windows", _payload(window), key=("window_id",))

    def save_observation_conflict(self, conflict: ObservationConflict) -> None:
        self._upsert("tokenomics_observation_conflicts", _payload(conflict), key=("conflict_id",))

    def save_observation_conflict_member(self, member: ObservationConflictMember) -> None:
        self._upsert("tokenomics_conflict_members", _payload(member), key=("member_id",))

    def save_conflict_resolution_event(self, event: ConflictResolutionEvent) -> None:
        self._upsert("tokenomics_conflict_resolution_events", _payload(event), key=("event_id",))

    def save_report_run(self, run: TokenomicsReportRun) -> None:
        self._upsert("tokenomics_report_runs", _payload(run), key=("run_id",))

    def save_report_observation_link(self, link: TokenomicsReportObservationLink) -> None:
        self._upsert("tokenomics_report_observation_links", _payload(link), key=("link_id",))

    def save_sufficiency_assessment(self, assessment: TokenomicsSufficiencyAssessmentRecord) -> None:
        self._upsert("tokenomics_sufficiency_assessments", _payload(assessment), key=("assessment_id",))

    def save_report_sufficiency_link(self, link: TokenomicsReportSufficiencyLink) -> None:
        self._upsert("tokenomics_report_sufficiency_links", _payload(link), key=("link_id",))

    def claim_lifecycle(self, claim_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return _rows(
                conn,
                """
                SELECT * FROM tokenomics_evidence_lifecycle_events
                WHERE claim_id = ?
                ORDER BY effective_at, recorded_at, event_id
                """,
                (claim_id,),
            )

    def claim_lineage(self, claim_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return _rows(
                conn,
                """
                SELECT * FROM tokenomics_claim_artifact_links
                WHERE claim_id = ?
                ORDER BY position, artifact_id
                """,
                (claim_id,),
            )

    def evidence_claims(
        self, *, asset_id: str | None = None, predicate: str | None = None
    ) -> tuple[dict[str, Any], ...]:
        filters: list[str] = []
        params: list[object] = []
        if asset_id is not None:
            filters.append("asset_id = ?")
            params.append(asset_id)
        if predicate is not None:
            filters.append("predicate = ?")
            params.append(predicate)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._connect() as conn:
            return _rows(
                conn,
                f"SELECT * FROM tokenomics_evidence_claims {where} ORDER BY subject, predicate, value, claim_id",
                tuple(params),
            )

    def address_classifications_at(
        self,
        representation_id: str,
        address: str,
        at: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        _aware("at", at)
        filters = [
            "representation_id = ?",
            "address = ?",
            "valid_from <= ?",
            "(valid_to IS NULL OR valid_to > ?)",
        ]
        params: list[object] = [representation_id, address, _serialize(at), _serialize(at)]
        if strict_known_by_hunter:
            filters.append("recorded_at <= ?")
            params.append(_serialize(at))
        with self._connect() as conn:
            return _rows(
                conn,
                f"""
                SELECT * FROM tokenomics_address_classifications
                WHERE {" AND ".join(filters)}
                ORDER BY valid_from DESC, recorded_at DESC, classification_id
                """,
                tuple(params),
            )

    def conflict_members(self, conflict_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return _rows(
                conn,
                """
                SELECT * FROM tokenomics_conflict_members
                WHERE conflict_id = ?
                ORDER BY observation_table, observation_id, role
                """,
                (conflict_id,),
            )

    def token_asset_at(
        self, asset_id: str, cutoff: datetime, *, report_mode: str = "known_by_hunter"
    ) -> dict[str, Any] | None:
        return self._version_at("token_assets", "asset_id", asset_id, cutoff, report_mode=report_mode)

    def token_representation_at(
        self,
        representation_id: str,
        cutoff: datetime,
        *,
        report_mode: str = "known_by_hunter",
    ) -> dict[str, Any] | None:
        return self._version_at(
            "token_representations",
            "representation_id",
            representation_id,
            cutoff,
            report_mode=report_mode,
        )

    def count(self, table: str) -> int:
        _ensure_table(table)
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def table_names(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
            return tuple(str(row["name"]) for row in rows)

    def index_names(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name").fetchall()
            return tuple(str(row["name"]) for row in rows if not str(row["name"]).startswith("sqlite_autoindex"))

    def columns(self, table: str) -> tuple[str, ...]:
        _ensure_table(table)
        with self._connect() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return tuple(str(row["name"]) for row in rows)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _upsert(self, table: str, payload: dict[str, Any], *, key: tuple[str, ...]) -> None:
        with self._connect() as conn:
            _upsert_payload(conn, table, payload, key=key)

    def _version_at(
        self,
        table: str,
        identity_column: str,
        identity_value: str,
        cutoff: datetime,
        *,
        report_mode: str,
    ) -> dict[str, Any] | None:
        _ensure_table(table)
        _aware("cutoff", cutoff)
        if report_mode not in REPORT_MODES:
            allowed = ", ".join(sorted(REPORT_MODES))
            raise ValueError(f"report_mode must be one of: {allowed}")
        filters = [f"{identity_column} = ?", "effective_at <= ?"]
        params: list[object] = [identity_value, _serialize(cutoff)]
        if report_mode == "known_by_hunter":
            filters.append("recorded_at <= ?")
            params.append(_serialize(cutoff))
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM {table}
                WHERE {" AND ".join(filters)}
                ORDER BY effective_at DESC, recorded_at DESC, schema_version DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["report_mode"] = report_mode
        result["reconstructed_after_cutoff"] = report_mode == "reconstructed" and str(result["recorded_at"]) > str(
            _serialize(cutoff)
        )
        return result


def _upsert_payload(conn: sqlite3.Connection, table: str, payload: dict[str, Any], *, key: tuple[str, ...]) -> None:
    _ensure_table(table)
    if table in IMMUTABLE_TABLES:
        _insert_immutable_payload(conn, table, payload, key=key)
        return
    columns = tuple(payload)
    placeholders = ", ".join("?" for _ in columns)
    updates = tuple(column for column in columns if column not in key)
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in updates)
    conflict = ", ".join(key)
    values = tuple(payload[column] for column in columns)
    if update_sql:
        sql = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT ({conflict}) DO UPDATE SET {update_sql}
        """
    else:
        sql = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT ({conflict}) DO NOTHING
        """
    conn.execute(sql, values)


def _insert_immutable_payload(
    conn: sqlite3.Connection,
    table: str,
    payload: dict[str, Any],
    *,
    key: tuple[str, ...],
) -> None:
    _ensure_table(table)
    existing = _existing_by_key(conn, table, key, payload)
    if existing is not None:
        _ensure_identical_payload(table, key, payload, existing)
        return
    columns = tuple(payload)
    placeholders = ", ".join("?" for _ in columns)
    try:
        conn.execute(
            f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            """,
            tuple(payload[column] for column in columns),
        )
    except sqlite3.IntegrityError as exc:
        existing = _existing_by_key(conn, table, key, payload)
        if existing is not None:
            _ensure_identical_payload(table, key, payload, existing)
            return
        raise TokenomicsIntegrityError(f"immutable tokenomics record conflicts in {table}") from exc


def _insert_identity(conn: sqlite3.Connection, table: str, identity_column: str, identity_value: str) -> None:
    _ensure_table(table)
    conn.execute(
        f"""
        INSERT INTO {table} ({identity_column})
        VALUES (?)
        ON CONFLICT ({identity_column}) DO NOTHING
        """,
        (identity_value,),
    )


def _existing_by_key(
    conn: sqlite3.Connection,
    table: str,
    key: tuple[str, ...],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    where = " AND ".join(f"{column} = ?" for column in key)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {where}",
        tuple(payload[column] for column in key),
    ).fetchone()
    return None if row is None else dict(row)


def _ensure_identical_payload(
    table: str,
    key: tuple[str, ...],
    payload: dict[str, Any],
    existing: dict[str, Any],
) -> None:
    existing_payload = {column: existing[column] for column in payload}
    if existing_payload != payload:
        key_material = ", ".join(f"{column}={payload[column]!r}" for column in key)
        raise TokenomicsIntegrityError(f"immutable tokenomics record conflict in {table}: {key_material}")


def _payload(item: object) -> dict[str, Any]:
    if not is_dataclass(item):
        raise TypeError("repository payloads must be dataclasses")
    return {field.name: _serialize(getattr(item, field.name)) for field in fields(item)}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in conn.execute(sql, params).fetchall())


def _ensure_table(table: str) -> None:
    if table not in TABLES:
        raise ValueError(f"unknown tokenomics table: {table}")


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
