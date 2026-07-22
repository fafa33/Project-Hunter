from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.committee.models import CycleChampionSnapshot, InvestmentCommitteeAssessment


class CommitteePersistenceError(ValueError):
    pass


class InvestmentCommitteeRepository:
    """Read-only repository for authoritative committee outputs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS committee_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    champion_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS committee_assessments (
                    assessment_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    rank INTEGER NOT NULL CHECK (rank > 0),
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    eligibility_state TEXT NOT NULL,
                    committee_confidence REAL NOT NULL,
                    consensus_score REAL NOT NULL,
                    evidence_robustness REAL NOT NULL,
                    conflict_score REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(cycle_id) REFERENCES committee_cycles(cycle_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_committee_cycle_rank
                    ON committee_assessments(cycle_id, rank);
                CREATE INDEX IF NOT EXISTS ix_committee_project_created
                    ON committee_assessments(project_id, created_at DESC);
                """
            )

    def latest_cycle(self) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM committee_cycles ORDER BY created_at DESC, cycle_id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else json.loads(str(row[0]))

    def top_opportunities(self, *, limit: int = 10) -> tuple[dict[str, Any], ...]:
        if limit <= 0:
            return ()
        with sqlite3.connect(self.path) as conn:
            cycle = conn.execute(
                "SELECT cycle_id FROM committee_cycles ORDER BY created_at DESC, cycle_id DESC LIMIT 1"
            ).fetchone()
            if cycle is None:
                return ()
            rows = conn.execute(
                """
                SELECT payload_json
                FROM committee_assessments
                WHERE cycle_id = ?
                ORDER BY rank ASC
                LIMIT ?
                """,
                (str(cycle[0]), limit),
            ).fetchall()
        return tuple(json.loads(str(row[0])) for row in rows)

    def assessment(self, assessment_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM committee_assessments WHERE assessment_id = ?",
                (assessment_id,),
            ).fetchone()
        return None if row is None else json.loads(str(row[0]))


def persist_cycle(
    repository: InvestmentCommitteeRepository,
    champion: CycleChampionSnapshot,
    assessments: tuple[InvestmentCommitteeAssessment, ...],
) -> None:
    """Package-private authoritative insert used only by the service boundary."""

    if not assessments:
        raise CommitteePersistenceError("an authoritative cycle requires at least one assessment")
    ranks = tuple(item.rank for item in assessments)
    if ranks != tuple(range(1, len(assessments) + 1)):
        raise CommitteePersistenceError("assessment ranks must be contiguous and one-based")
    if champion.created_at != assessments[0].created_at:
        raise CommitteePersistenceError("champion chronology must match assessment cycle")

    cycle_payload = _json_safe(asdict(champion))
    canonical_cycle = _canonical(cycle_payload)
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_json FROM committee_cycles WHERE cycle_id = ?", (champion.id,)
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != canonical_cycle:
                    raise CommitteePersistenceError("cycle_id reused with divergent content")
                _verify_existing_assessments(conn, champion.id, assessments)
                conn.rollback()
                return
            conn.execute(
                "INSERT INTO committee_cycles(cycle_id, created_at, champion_id, payload_json) VALUES (?,?,?,?)",
                (champion.id, champion.created_at.isoformat(), champion.id, canonical_cycle),
            )
            for assessment in assessments:
                payload = _json_safe(asdict(assessment))
                conn.execute(
                    """
                    INSERT INTO committee_assessments(
                        assessment_id, cycle_id, project_id, rank, created_at, decision,
                        eligibility_state, committee_confidence, consensus_score,
                        evidence_robustness, conflict_score, payload_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        assessment.id,
                        champion.id,
                        assessment.project_id,
                        assessment.rank,
                        assessment.created_at.isoformat(),
                        assessment.decision,
                        assessment.eligibility_state,
                        assessment.committee_confidence,
                        assessment.consensus_score,
                        assessment.evidence_robustness,
                        assessment.conflict_score,
                        _canonical(payload),
                    ),
                )
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()


def _verify_existing_assessments(
    conn: sqlite3.Connection,
    cycle_id: str,
    assessments: tuple[InvestmentCommitteeAssessment, ...],
) -> None:
    rows = conn.execute(
        "SELECT assessment_id, payload_json FROM committee_assessments WHERE cycle_id = ? ORDER BY rank",
        (cycle_id,),
    ).fetchall()
    expected = tuple((item.id, _canonical(_json_safe(asdict(item)))) for item in assessments)
    actual = tuple((str(row["assessment_id"]), str(row["payload_json"])) for row in rows)
    if actual != expected:
        raise CommitteePersistenceError("cycle_id reused with divergent assessments")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
