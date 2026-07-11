from __future__ import annotations

from hunter.committee.models import CycleChampionSnapshot, InvestmentCommitteeAssessment


class InvestmentCommitteeReportRenderer:
    def render_project_sections(self, assessment: InvestmentCommitteeAssessment) -> tuple[tuple[str, str], ...]:
        return (
            ("Committee Decision", f"{assessment.decision}"),
            ("Eligibility", assessment.eligibility_state),
            ("Committee Confidence", f"{assessment.committee_confidence:.4f}"),
            (
                "Approval and Opposition",
                f"approval={assessment.approval_score:.4f}; opposition={assessment.opposition_score:.4f}",
            ),
            (
                "Consensus and Conflict",
                f"consensus={assessment.consensus_score:.4f}; conflict={assessment.conflict_score:.4f}",
            ),
            ("Engine Votes", _votes(assessment)),
            ("Positive Drivers", _lines(assessment.positive_drivers)),
            ("Negative Drivers", _lines(assessment.negative_drivers)),
            ("Conflicting Engines", _lines(assessment.conflicts)),
            ("Abstaining Engines", _lines(assessment.abstentions)),
            ("Critical Risks", _lines(assessment.risks)),
            ("Evidence Robustness", f"{assessment.evidence_robustness:.4f}"),
            ("Thesis Fragility", f"{assessment.thesis_fragility:.4f}"),
            ("What Could Change the Decision", _lines(assessment.invalidation_conditions)),
            ("Decision History", _lines(assessment.explanation)),
            ("Supporting Persisted Evidence", _lines(assessment.source_record_ids)),
        )

    def render_project_markdown(self, assessment: InvestmentCommitteeAssessment) -> str:
        return "\n\n".join(f"## {title}\n\n{body}" for title, body in self.render_project_sections(assessment))

    def render_champion_sections(
        self,
        snapshot: CycleChampionSnapshot,
        ranking: tuple[InvestmentCommitteeAssessment, ...],
    ) -> tuple[tuple[str, str], ...]:
        leader = snapshot.selected_project_id or "No Qualified Candidate"
        return (
            ("Highest Conviction Candidate or No Qualified Candidate", leader),
            ("Committee Confidence", f"{snapshot.committee_confidence:.4f}"),
            ("Consensus", f"{snapshot.consensus_score:.4f}"),
            ("Lead Over Runner-up", f"{snapshot.lead_margin:.4f}"),
            ("Why This Project Ranked First", snapshot.selection_reason),
            ("Why the Runner-up Ranked Lower", snapshot.no_selection_reason or "winner conditions satisfied"),
            ("Main Supporting Engines", _lines(ranking[0].positive_drivers if ranking else ())),
            ("Main Opposing Engines", _lines(ranking[0].negative_drivers if ranking else ())),
            ("Critical Risks", _lines(ranking[0].risks if ranking else ())),
            ("Missing Evidence", _lines(ranking[0].abstentions if ranking else ())),
            ("Thesis Invalidation Conditions", _lines(ranking[0].invalidation_conditions if ranking else ())),
            ("Candidate Ranking Table", _ranking(ranking)),
        )


def _votes(assessment: InvestmentCommitteeAssessment) -> str:
    return "\n".join(
        f"- {vote.engine_name}: {vote.vote} score={vote.source_score:.4f} confidence={vote.source_confidence:.4f}"
        for vote in assessment.votes
    )


def _ranking(ranking: tuple[InvestmentCommitteeAssessment, ...]) -> str:
    return (
        "\n".join(
            f"- {idx + 1}. {item.project_id}: {item.decision} confidence={item.committee_confidence:.4f}"
            for idx, item in enumerate(ranking)
        )
        or "- none"
    )


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- none"
