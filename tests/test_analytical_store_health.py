from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hunter.analytical_store_health import (
    FreshnessPolicy,
    apply_freshness,
    inspect_registered_analytical_stores,
)
from hunter.persistence.sql import create_schema, create_sqlite_engine

NOW = datetime(2026, 1, 5, tzinfo=UTC)


def test_all_registered_stores_are_discovered_once(tmp_path: Path) -> None:
    stores = inspect_registered_analytical_stores(root=tmp_path, observed_at=NOW)
    assert [item.store_id for item in stores] == [
        "tokenomics",
        "evidence_intelligence",
        "sufficiency",
        "canonical_market_validation",
        "experimental_derived_reasoning",
        "experimental_opportunity",
        "canonical_prediction_evaluation",
    ]
    assert len({item.store_id for item in stores}) == len(stores)
    assert all(item.state in {"unavailable", "absent"} for item in stores)


def test_disabled_configured_stores_are_unavailable_not_absent(tmp_path: Path) -> None:
    _configs(tmp_path, enabled=False)
    stores = _by_id(tmp_path)
    assert stores["canonical_market_validation"].state == "unavailable"
    assert stores["canonical_market_validation"].configured is True
    assert stores["canonical_market_validation"].enabled is False
    assert stores["canonical_market_validation"].data_record_count is None


def test_enabled_missing_store_is_absent_without_creation(tmp_path: Path) -> None:
    _configs(tmp_path)
    stores = _by_id(tmp_path)
    assert stores["canonical_prediction_evaluation"].state == "absent"
    assert not (tmp_path / "canonical.sqlite").exists()


def test_current_empty_schema_is_schema_only(tmp_path: Path) -> None:
    _configs(tmp_path)
    _schema(tmp_path / "canonical.sqlite")
    result = _by_id(tmp_path)["canonical_prediction_evaluation"]
    assert result.state == "schema_only"
    assert result.data_record_count == 0
    assert result.table_counts == {"persistence_records": 0}


def test_populated_store_reports_only_matching_records_and_recorded_time(tmp_path: Path) -> None:
    _configs(tmp_path)
    path = tmp_path / "experimental.sqlite"
    _schema(path)
    _insert(path, "derived", "analytical-record", "experimental.probability-assessment")
    _insert(path, "opportunity", "analytical-record", "experimental.opportunity-assessment")
    stores = _by_id(tmp_path)
    derived = stores["experimental_derived_reasoning"]
    opportunity = stores["experimental_opportunity"]
    assert derived.state == opportunity.state == "populated"
    assert derived.data_record_count == opportunity.data_record_count == 1
    assert derived.latest_reliable_recorded_at == NOW.isoformat()
    assert derived.freshness_status == "not_applicable"


def test_missing_schema_is_migration_required(tmp_path: Path) -> None:
    _configs(tmp_path)
    with sqlite3.connect(tmp_path / "canonical.sqlite"):
        pass
    result = _by_id(tmp_path)["canonical_prediction_evaluation"]
    assert result.state == "migration_required"
    assert result.migration_status == "required"
    assert result.data_record_count is None


def test_unreachable_and_failed_are_distinct(tmp_path: Path) -> None:
    _configs(tmp_path)
    (tmp_path / "canonical.sqlite").mkdir()
    assert _by_id(tmp_path)["canonical_prediction_evaluation"].state == "unreachable"
    (tmp_path / "canonical.sqlite").rmdir()
    (tmp_path / "canonical.sqlite").write_bytes(b"not sqlite")
    failed = _by_id(tmp_path)["canonical_prediction_evaluation"]
    assert failed.state == "failed"
    assert failed.data_record_count is None


def test_stale_requires_policy_and_reliable_recorded_time() -> None:
    policy = FreshnessPolicy("documented-test-policy", timedelta(days=1))
    old = NOW - timedelta(days=2)
    no_policy = apply_freshness("populated", old, None, NOW)
    unknown = apply_freshness("populated", None, policy, NOW)
    stale = apply_freshness("populated", old, policy, NOW)
    assert no_policy == ("populated", "not_applicable", "no documented store-specific freshness policy applies")
    assert unknown == (
        "populated",
        "unknown",
        "reliable timezone-aware recorded-time evidence is unavailable",
    )
    assert stale == (
        "stale",
        "stale",
        "latest reliable record exceeds freshness policy documented-test-policy",
    )


def test_inspection_is_deterministic_and_does_not_modify_store(tmp_path: Path) -> None:
    _configs(tmp_path)
    path = tmp_path / "canonical.sqlite"
    _schema(path)
    before = path.read_bytes()
    first = inspect_registered_analytical_stores(root=tmp_path, observed_at=NOW)
    second = inspect_registered_analytical_stores(root=tmp_path, observed_at=NOW)
    assert first == second
    assert path.read_bytes() == before


def _by_id(root: Path):
    return {item.store_id: item for item in inspect_registered_analytical_stores(root=root, observed_at=NOW)}


def _configs(root: Path, *, enabled: bool = True) -> None:
    configs = root / "configs"
    configs.mkdir()
    value = str(enabled).lower()
    (configs / "market_validation_persistence.yaml").write_text(
        f"market_validation_persistence:\n  enabled: {value}\n  database_path: market.sqlite\n",
        encoding="utf-8",
    )
    (configs / "experimental_persistence.yaml").write_text(
        f"experimental_persistence:\n  enabled: {value}\n  database_path: experimental.sqlite\n",
        encoding="utf-8",
    )
    (configs / "prediction_evaluation_persistence.yaml").write_text(
        f"prediction_evaluation_persistence:\n  enabled: {value}\n  database_path: canonical.sqlite\n",
        encoding="utf-8",
    )


def _schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(path)
    create_schema(engine)
    engine.dispose()


def _insert(path: Path, identity: str, record_type: str, semantic_type: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO persistence_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                record_type,
                "v1",
                NOW.isoformat(),
                NOW.isoformat(),
                "hash",
                json.dumps({"semantic_type": semantic_type}),
                None,
            ),
        )
