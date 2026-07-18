from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.market_validation import MarketValidationRenderer, MarketValidationRunner
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.persistence import (
    MARKET_VALIDATION_SCHEMA_VERSION,
    MarketValidationPersistenceAuthorizationService,
    MarketValidationPersistenceContext,
)
from hunter.persistence.market_validation import (
    CanonicalMarketValidationStore,
    bootstrap_canonical_market_validation_store,
    load_canonical_market_validation_persistence_config,
)
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.persistence.sql.repositories.records import SQLMarketValidationProjectResultRepository

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def test_complete_run_round_trips_atomically_and_preserves_outputs(tmp_path: Path) -> None:
    run, plan, store = _fixture(tmp_path)
    renderer = MarketValidationRenderer()
    before = (renderer.render_json(run), renderer.render_csv(run), renderer.render_markdown(run))

    persisted = store.persist(plan)

    with store.unit_of_work() as uow:
        repository = store.repository(uow)
        assert repository.run(persisted.id) == plan.run_record
        assert repository.projects_for_run(persisted.id) == plan.project_records
    after = (renderer.render_json(run), renderer.render_csv(run), renderer.render_markdown(run))
    assert after == before
    assert persisted.status == "complete"
    assert persisted.schema_version == MARKET_VALIDATION_SCHEMA_VERSION
    assert len(plan.project_records) == len(run.project_results)
    for native, record in zip(run.project_results, plan.project_records, strict=True):
        assert record.hunter_score == native.hunter_score
        assert record.rank == native.rank
        assert record.committee_decision == native.committee_decision
        assert record.committee_confidence == native.committee_confidence
        assert record.metadata["final_score"] == native.final_score


def test_identical_run_is_idempotent_and_conflicting_identity_is_rejected(tmp_path: Path) -> None:
    _, plan, store = _fixture(tmp_path)
    assert store.persist(plan) == plan.run_record
    assert store.persist(plan) == plan.run_record

    conflicting = replace(
        plan,
        run_record=replace(
            plan.run_record,
            authorized_payload={
                **plan.run_record.authorized_payload,
                "authority_classification": "production",
                "conflict": True,
            },
        ),
    )
    with pytest.raises(PersistenceIdentityConflictError):
        store.persist(conflicting)


def test_explicit_correction_preserves_run_and_project_history(tmp_path: Path) -> None:
    run, original, store = _fixture(tmp_path)
    store.persist(original)
    predecessors = {record.project_id: record for record in original.project_records}
    corrected_result = replace(run.project_results[0], hunter_score=0.01, final_score=0.01)
    corrected_run = replace(
        run,
        project_results=(corrected_result, *run.project_results[1:]),
        metadata=dict(run.metadata),
    )
    correction = MarketValidationPersistenceAuthorizationService().authorize(
        corrected_run,
        load_market_validation_config(),
        _context(NOW + timedelta(days=2)),
        predecessor_run=original.run_record,
        predecessor_projects=predecessors,
        correction_reason="authorized correction fixture",
    )
    store.persist(correction)

    with store.unit_of_work() as uow:
        repository = store.repository(uow)
        assert repository.run_history(run.run_id) == (original.run_record, correction.run_record)
        assert repository.project_history(run.run_id, corrected_result.project_id) == (
            original.project_records[0],
            correction.project_records[0],
        )
        assert repository.run(original.run_record.id) == original.run_record


def test_partial_project_failure_rolls_back_without_complete_run(tmp_path: Path, monkeypatch) -> None:
    _, plan, store = _fixture(tmp_path)
    original_save = SQLMarketValidationProjectResultRepository.save
    calls = 0

    def failing_save(repository, record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated project persistence failure")
        return original_save(repository, record)

    monkeypatch.setattr(SQLMarketValidationProjectResultRepository, "save", failing_save)
    with pytest.raises(RuntimeError, match="simulated"):
        store.persist(plan)

    with store.unit_of_work() as uow:
        repository = store.repository(uow)
        assert repository.run(plan.run_record.id) is None
        assert repository.project(plan.project_records[0].id) is None


def test_strict_known_replay_excludes_future_correction_and_unknown_known_time(tmp_path: Path) -> None:
    run, original, store = _fixture(tmp_path)
    store.persist(original)
    correction = MarketValidationPersistenceAuthorizationService().authorize(
        run,
        load_market_validation_config(),
        _context(NOW + timedelta(days=3), known_at=NOW + timedelta(days=2)),
        predecessor_run=original.run_record,
        predecessor_projects={record.project_id: record for record in original.project_records},
        correction_reason="late recorded correction",
    )
    store.persist(correction)

    with store.unit_of_work() as uow:
        repository = store.repository(uow)
        assert (
            repository.strict_known_run(
                run.run_id,
                effective_as_of=run.effective_at,
                known_by=NOW + timedelta(days=1),
            )
            == original.run_record
        )
        assert (
            repository.strict_known_run(
                run.run_id,
                effective_as_of=run.effective_at,
                known_by=NOW + timedelta(days=4),
            )
            == correction.run_record
        )

    unknown_run = replace(
        run,
        run_id="unknown-known-run",
        project_results=tuple(
            replace(result, run_id="unknown-known-run", result_id=f"unknown:{result.project_id}")
            for result in run.project_results
        ),
        metadata=dict(run.metadata),
    )
    unknown_plan = MarketValidationPersistenceAuthorizationService().authorize(
        unknown_run,
        replace(load_market_validation_config(), run_id="unknown-known-run"),
        _context(NOW, known_at=None, limitation="source known time unavailable"),
    )
    store.persist(unknown_plan)
    with store.unit_of_work() as uow:
        assert (
            store.repository(uow).strict_known_run(
                "unknown-known-run",
                effective_as_of=run.effective_at,
                known_by=NOW + timedelta(days=4),
            )
            is None
        )


def test_store_is_explicit_disabled_and_isolated_from_other_consumers(tmp_path: Path) -> None:
    missing = tmp_path / "canonical.sqlite"
    with pytest.raises(FileNotFoundError):
        CanonicalMarketValidationStore(missing)
    assert not missing.exists()

    config_path = tmp_path / "persistence.yaml"
    config_path.write_text(
        "market_validation_persistence:\n"
        "  enabled: false\n"
        "  database_path: data/market_validation/runtime/canonical.sqlite\n"
    )
    config = load_canonical_market_validation_persistence_config(config_path)
    with pytest.raises(RuntimeError, match="disabled"):
        CanonicalMarketValidationStore.from_config(config)

    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/data_ops.py",
        "src/hunter/dashboard_api.py",
        "src/hunter/operational_corpus.py",
        "src/hunter/opportunity",
        "src/hunter/pipeline.py",
        "src/hunter/timing",
        "src/hunter/automation",
        "src/hunter/persistence/experimental.py",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("persistence.market_validation" not in item.read_text() for item in files)


def test_canonical_committee_fields_are_native_market_validation_fields(tmp_path: Path) -> None:
    run, plan, _ = _fixture(tmp_path)
    for native, record in zip(run.project_results, plan.project_records, strict=True):
        assert record.committee_decision == native.committee_decision
        assert record.committee_confidence == native.committee_confidence
        assert record.authorized_payload["authority_classification"] == "production"
        assert record.record_type == "market-validation-project-result"


def _fixture(tmp_path: Path):
    config = load_market_validation_config()
    run = MarketValidationRunner(config).run()
    plan = MarketValidationPersistenceAuthorizationService().authorize(run, config, _context(NOW))
    path = bootstrap_canonical_market_validation_store(tmp_path / "market-validation" / "canonical.sqlite")
    return run, plan, CanonicalMarketValidationStore(path)


def _context(
    recorded_at: datetime,
    *,
    known_at: datetime | None = NOW - timedelta(hours=1),
    limitation: str | None = None,
) -> MarketValidationPersistenceContext:
    return MarketValidationPersistenceContext(
        recorded_at=recorded_at,
        known_at=known_at,
        known_time_limitation=limitation,
        model_version="market-validation-v1",
        methodology_fingerprint="market-validation-methodology-v1",
        source_versions={},
    )
