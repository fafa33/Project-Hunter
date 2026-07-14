from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.competitive.conflicts import CompetitiveReplayContext, CompetitiveReplayQuery
from hunter.competitive.repository import CompetitiveRepository


@dataclass(frozen=True)
class CompetitiveReportContext:
    cutoff: datetime | None = None
    strict_known_by_hunter: bool = False

    def replay_context(self) -> CompetitiveReplayContext:
        return CompetitiveReplayContext(cutoff=self.cutoff, strict_known_by_hunter=self.strict_known_by_hunter)

    @property
    def mode(self) -> str:
        return self.replay_context().mode

    @property
    def known_at_cutoff(self) -> bool:
        return self.replay_context().known_at_cutoff


class CompetitiveReporter:
    def __init__(self, repository: CompetitiveRepository) -> None:
        self.repository = repository
        self.replay = CompetitiveReplayQuery(repository)

    def coverage(self) -> dict[str, str]:
        peer_sets = self.repository.count("competitive_peer_sets")
        evidence_backed = self.repository.count("competitive_relationships")
        algorithmic = self.repository.count("algorithmic_peer_relationships")
        conflicts = self.repository.count("competitive_conflict_links")
        dimensions = self.repository.count("competitive_comparison_dimensions")
        return {
            "mode": "current",
            "peer_sets": str(peer_sets),
            "evidence_backed_relationships": str(evidence_backed),
            "algorithmic_peer_relationships": str(algorithmic),
            "conflict_links": str(conflicts),
            "comparison_dimensions": str(dimensions),
        }

    def report(self, context: CompetitiveReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        peer_sets = (
            self.repository.peer_sets()
            if context.cutoff is None
            else self.repository.peer_sets_at(
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        )
        for peer_set in peer_sets:
            rows.append(
                {
                    "mode": context.mode,
                    "peer_set_id": str(peer_set["peer_set_id"]),
                    "candidate_id": str(peer_set["subject_candidate_id"]),
                    "status": str(peer_set["status"]),
                    "confidence": str(peer_set["confidence"]),
                    "coverage": str(peer_set["coverage"]),
                    "freshness": str(peer_set["freshness"]),
                    "conflicts": str(peer_set["conflict_status"]),
                    "evidence_backed": str(peer_set["evidence_backed_count"]),
                    "algorithmic": str(peer_set["algorithmic_peer_count"]),
                    "known_at_cutoff": str(context.known_at_cutoff).lower(),
                }
            )
        return tuple(rows)

    def peers(self, candidate_id: str, context: CompetitiveReportContext) -> tuple[dict[str, str], ...]:
        view = self.replay.view_for_subject(candidate_id, context.replay_context())
        rows: list[dict[str, str]] = []
        for relationship in view.relationships:
            rows.append(
                {
                    "mode": view.mode,
                    "candidate_id": candidate_id,
                    "relationship_id": relationship.relationship_id,
                    "peer_candidate_id": relationship.peer_candidate_id,
                    "kind": "evidence_backed",
                    "relationship_type": relationship.relationship_type,
                    "confidence": str(relationship.confidence),
                    "freshness": str(relationship.freshness),
                    "conflicts": relationship.conflict_status,
                    "lineage": "claim_and_span",
                    "known_at_cutoff": str(view.known_at_cutoff).lower(),
                }
            )
        if context.cutoff is None:
            algorithmic_rows = self.repository.algorithmic_relationships_for_subject(candidate_id)
        else:
            algorithmic_rows = self.repository.algorithmic_relationships_for_subject_at(
                candidate_id,
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        for algorithmic_row in algorithmic_rows:
            rows.append(
                {
                    "mode": context.mode,
                    "candidate_id": candidate_id,
                    "relationship_id": str(algorithmic_row["relationship_id"]),
                    "peer_candidate_id": str(algorithmic_row["peer_candidate_id"]),
                    "kind": "algorithmic_similarity",
                    "relationship_type": str(algorithmic_row["relationship_type"]),
                    "confidence": str(algorithmic_row["confidence"]),
                    "freshness": str(algorithmic_row["freshness"]),
                    "conflicts": "none",
                    "lineage": "policy_dimensions",
                    "known_at_cutoff": str(context.known_at_cutoff).lower(),
                }
            )
        return tuple(rows)

    def competitors(self, candidate_id: str, context: CompetitiveReportContext) -> tuple[dict[str, str], ...]:
        return tuple(row for row in self.peers(candidate_id, context) if row["kind"] == "evidence_backed")

    def explain(self, candidate_id: str, context: CompetitiveReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for row in self.peers(candidate_id, context):
            missing = "none"
            if row["kind"] == "algorithmic_similarity":
                dimensions = (
                    self.repository.comparison_dimensions_for_relationship(row["relationship_id"])
                    if context.cutoff is None
                    else self.repository.comparison_dimensions_for_relationship_at(
                        row["relationship_id"],
                        context.cutoff,
                        strict_known_by_hunter=context.strict_known_by_hunter,
                    )
                )
                missing = str(sum(1 for dimension in dimensions if str(dimension["match_status"]) == "missing"))
            rows.append({**row, "missing_evidence": missing, "replay_mode": context.mode})
        return tuple(rows)

    def conflicts(self, context: CompetitiveReportContext) -> tuple[dict[str, str], ...]:
        rows = []
        for link in self.repository.conflict_links(
            cutoff=context.cutoff, strict_known_by_hunter=context.strict_known_by_hunter
        ):
            rows.append(
                {
                    "mode": context.mode,
                    "conflict_id": str(link["conflict_id"]),
                    "relationship_id": str(link["relationship_id"]),
                    "role": str(link["role"]),
                    "created_at": str(link["created_at"]),
                    "known_at_cutoff": str(context.known_at_cutoff).lower(),
                }
            )
        return tuple(rows)
