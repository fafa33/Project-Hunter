from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from hunter.evidence_intelligence.repository import DEFAULT_EVIDENCE_INTELLIGENCE_DB
from hunter.persistence.experimental import (
    DERIVED_REASONING_SEMANTIC_TYPES,
    load_experimental_persistence_config,
)
from hunter.persistence.market_validation import load_canonical_market_validation_persistence_config
from hunter.persistence.prediction_evaluation import SEMANTIC_TYPES, load_prediction_evaluation_config
from hunter.store_readiness import inspect_store
from hunter.sufficiency.repository import DEFAULT_SUFFICIENCY_DB
from hunter.tokenomics.repository import DEFAULT_TOKENOMICS_DB

PROJECTION_VERSION = "analytical-store-readiness.v1"
StoreState = Literal[
    "unavailable",
    "absent",
    "schema_only",
    "populated",
    "stale",
    "migration_required",
    "unreachable",
    "failed",
]
FreshnessStatus = Literal["current", "stale", "unknown", "not_applicable"]

_OPPORTUNITY_TYPES = frozenset({"experimental.opportunity-metric-snapshot", "experimental.opportunity-assessment"})
_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "record_type",
        "schema_version",
        "created_at",
        "effective_at",
        "canonical_hash",
        "payload",
        "deleted_at",
    }
)


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    policy_id: str
    maximum_age: timedelta


@dataclass(frozen=True, slots=True)
class AnalyticalStoreHealth:
    store_id: str
    authority_classification: str
    state: StoreState
    reason: str
    enabled: bool
    configured: bool
    path: str | None
    schema_status: str
    migration_status: str
    data_record_count: int | None
    table_counts: dict[str, int] | None
    latest_reliable_recorded_at: str | None
    freshness_policy: str | None
    freshness_status: FreshnessStatus
    inspected_at: str
    projection_version: str = PROJECTION_VERSION

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_registered_analytical_stores(*, root: Path, observed_at: datetime) -> tuple[AnalyticalStoreHealth, ...]:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    observed = observed_at.astimezone(UTC)
    stores = [
        _legacy_store("tokenomics", "production", root / DEFAULT_TOKENOMICS_DB, observed),
        _legacy_store(
            "evidence_intelligence", "production-descriptive", root / DEFAULT_EVIDENCE_INTELLIGENCE_DB, observed
        ),
        _legacy_store("sufficiency", "operational-gating", root / DEFAULT_SUFFICIENCY_DB, observed),
    ]
    stores.extend(_configured_sql_stores(root, observed))
    return tuple(stores)


def apply_freshness(
    state: StoreState,
    latest_recorded_at: datetime | None,
    policy: FreshnessPolicy | None,
    observed_at: datetime,
) -> tuple[StoreState, FreshnessStatus, str]:
    if policy is None:
        return state, "not_applicable", "no documented store-specific freshness policy applies"
    if latest_recorded_at is None or latest_recorded_at.tzinfo is None:
        return state, "unknown", "reliable timezone-aware recorded-time evidence is unavailable"
    stale = observed_at.astimezone(UTC) - latest_recorded_at.astimezone(UTC) > policy.maximum_age
    if stale and state == "populated":
        return "stale", "stale", f"latest reliable record exceeds freshness policy {policy.policy_id}"
    return state, "current", f"freshness policy {policy.policy_id} is satisfied"


def _legacy_store(store_id: str, authority: str, path: Path, observed: datetime) -> AnalyticalStoreHealth:
    result = inspect_store(store_id, path)  # type: ignore[arg-type]
    return AnalyticalStoreHealth(
        store_id=store_id,
        authority_classification=authority,
        state=result.state,
        reason=result.reason,
        enabled=True,
        configured=True,
        path=result.path,
        schema_status=result.schema_status,
        migration_status=(
            "required"
            if result.state == "migration_required"
            else "current" if result.schema_status == "current" else "unknown"
        ),
        data_record_count=result.analytical_record_count,
        table_counts=result.table_counts if result.analytical_record_count is not None else None,
        latest_reliable_recorded_at=None,
        freshness_policy=None,
        freshness_status="not_applicable",
        inspected_at=observed.isoformat(),
    )


def _configured_sql_stores(root: Path, observed: datetime) -> tuple[AnalyticalStoreHealth, ...]:
    definitions: list[tuple[str, str, bool, Path | None, frozenset[str] | None, frozenset[str] | None, str | None]] = []
    definitions.append(
        _load_definition(
            "canonical_market_validation",
            "production",
            root / "configs/market_validation_persistence.yaml",
            root,
            load_canonical_market_validation_persistence_config,
            frozenset({"market-validation-run", "market-validation-project-result"}),
            None,
        )
    )
    experimental = _load_definition(
        "experimental_derived_reasoning",
        "experimental",
        root / "configs/experimental_persistence.yaml",
        root,
        load_experimental_persistence_config,
        frozenset({"analytical-record"}),
        DERIVED_REASONING_SEMANTIC_TYPES,
    )
    definitions.append(experimental)
    definitions.append(
        (
            "experimental_opportunity",
            "experimental",
            experimental[2],
            experimental[3],
            frozenset({"analytical-record"}),
            _OPPORTUNITY_TYPES,
            experimental[6],
        )
    )
    definitions.append(
        _load_definition(
            "canonical_prediction_evaluation",
            "canonical-evaluation",
            root / "configs/prediction_evaluation_persistence.yaml",
            root,
            load_prediction_evaluation_config,
            frozenset({"analytical-record"}),
            SEMANTIC_TYPES,
        )
    )
    return tuple(_inspect_sql_definition(item, observed) for item in definitions)


def _load_definition(store_id, authority, config_path, root, loader, record_types, semantic_types):
    try:
        config = loader(config_path)
    except FileNotFoundError:
        return (
            store_id,
            authority,
            False,
            None,
            record_types,
            semantic_types,
            "unavailable: configuration file is absent",
        )
    except (OSError, ValueError) as exc:
        return store_id, authority, False, None, record_types, semantic_types, f"configuration failed: {exc}"
    path = config.database_path if config.database_path.is_absolute() else root / config.database_path
    return store_id, authority, config.enabled, path, record_types, semantic_types, None


def _inspect_sql_definition(definition, observed: datetime) -> AnalyticalStoreHealth:
    store_id, authority, enabled, path, record_types, semantic_types, config_error = definition
    if config_error:
        if config_error.startswith("unavailable: "):
            return _sql_result(
                store_id,
                authority,
                "unavailable",
                config_error.removeprefix("unavailable: "),
                False,
                False,
                None,
                observed,
            )
        return _sql_result(store_id, authority, "failed", config_error, False, False, None, observed)
    if not enabled:
        return _sql_result(
            store_id, authority, "unavailable", "store is intentionally disabled", False, True, path, observed
        )
    if path is None:
        return _sql_result(
            store_id, authority, "unavailable", "no store path is configured", True, False, None, observed
        )
    if not path.exists():
        return _sql_result(
            store_id, authority, "absent", "configured store file does not exist", True, True, path, observed
        )
    if not path.is_file():
        return _sql_result(
            store_id, authority, "unreachable", "configured path is not a file", True, True, path, observed
        )
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                return _sql_result(
                    store_id,
                    authority,
                    "failed",
                    f"SQLite integrity check failed: {integrity}",
                    True,
                    True,
                    path,
                    observed,
                )
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "persistence_records" not in tables:
                return _sql_result(
                    store_id,
                    authority,
                    "migration_required",
                    "required schema object is missing: persistence_records",
                    True,
                    True,
                    path,
                    observed,
                    schema="mismatch",
                )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info('persistence_records')")}
            missing = sorted(_REQUIRED_COLUMNS - columns)
            if missing:
                return _sql_result(
                    store_id,
                    authority,
                    "migration_required",
                    f"required schema columns are missing: {', '.join(missing)}",
                    True,
                    True,
                    path,
                    observed,
                    schema="mismatch",
                )
            rows = connection.execute(
                "SELECT record_type, created_at, payload FROM persistence_records WHERE deleted_at IS NULL"
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        return _sql_result(
            store_id,
            authority,
            "failed",
            f"store is not a valid readable SQLite database: {exc}",
            True,
            True,
            path,
            observed,
            schema="unreadable",
        )
    except OSError as exc:
        return _sql_result(
            store_id,
            authority,
            "unreachable",
            f"store cannot be reached: {exc}",
            True,
            True,
            path,
            observed,
            schema="unreadable",
        )

    selected = []
    for record_type, created_at, payload_text in rows:
        if record_type not in record_types:
            continue
        if semantic_types is not None:
            try:
                if json.loads(payload_text).get("semantic_type") not in semantic_types:
                    continue
            except (json.JSONDecodeError, AttributeError):
                continue
        selected.append((_reliable_recorded_at(created_at, payload_text), payload_text))
    count = len(selected)
    latest = max((item[0] for item in selected if item[0] is not None), default=None)
    state: StoreState = "populated" if count else "schema_only"
    reason = (
        "current schema contains data-bearing records; readiness does not interpret them"
        if count
        else "current schema contains no data-bearing records for this store boundary"
    )
    return _sql_result(store_id, authority, state, reason, True, True, path, observed, count=count, latest=latest)


def _reliable_recorded_at(column_value: object, payload_text: str) -> str | None:
    candidates: list[object] = []
    try:
        payload = json.loads(payload_text)
        if isinstance(payload, dict):
            candidates.append(payload.get("created_at"))
    except json.JSONDecodeError:
        pass
    candidates.append(column_value)
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat()
    return None


def _sql_result(
    store_id: str,
    authority: str,
    state: StoreState,
    reason: str,
    enabled: bool,
    configured: bool,
    path: Path | None,
    observed: datetime,
    *,
    schema: str | None = None,
    count: int | None = None,
    latest: str | None = None,
) -> AnalyticalStoreHealth:
    schema_status = schema or ("current" if state in {"schema_only", "populated", "stale"} else state)
    return AnalyticalStoreHealth(
        store_id=store_id,
        authority_classification=authority,
        state=state,
        reason=reason,
        enabled=enabled,
        configured=configured,
        path=str(path) if path else None,
        schema_status=schema_status,
        migration_status=(
            "required" if state == "migration_required" else "current" if schema_status == "current" else "unknown"
        ),
        data_record_count=count,
        table_counts={"persistence_records": count} if count is not None else None,
        latest_reliable_recorded_at=latest,
        freshness_policy=None,
        freshness_status="not_applicable",
        inspected_at=observed.isoformat(),
    )
