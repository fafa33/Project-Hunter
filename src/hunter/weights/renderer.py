from __future__ import annotations

from hunter.weights.configuration import WeightConfig
from hunter.weights.models import WeightedScore, WeightRecommendation


class WeightReportRenderer:
    def render_status(self, config: WeightConfig) -> str:
        return (
            f"version={config.version} active={config.active} total_weight={sum(config.weights.values()):.6f} "
            f"calibration_policy={config.calibration_policy}"
        )

    def render_validate(self, config: WeightConfig) -> str:
        return f"valid=true version={config.version} weights={len(config.weights)} total={sum(config.weights.values()):.6f}"

    def render_report(self, config: WeightConfig, score: WeightedScore | None = None) -> str:
        lines = [
            f"Scoring Version: {config.version}",
            f"Active: {config.active}",
            "Weights:",
        ]
        for engine, weight in config.weights.items():
            lines.append(f"{engine}\tbase_weight={weight:.6f}")
        if score is not None:
            lines.extend(
                [
                    f"Hunter Score: {score.hunter_score:.4f}",
                    f"Final Score: {score.final_score:.4f}",
                    "Contributions:",
                ]
            )
            for item in score.contributions:
                lines.append(
                    f"{item.engine}\traw={item.raw_score:.4f}\tbase={item.base_weight:.6f}"
                    f"\tadjusted={item.adjusted_weight:.4f}\tcontribution={item.weighted_contribution:.4f}"
                    f"\tconfidence={item.confidence:.4f}\tfreshness={item.freshness:.4f}"
                    f"\tcoverage={item.evidence_coverage:.4f}\tversion={item.scoring_version}"
                )
        return "\n".join(lines)

    def render_recommendation(self, recommendation: WeightRecommendation) -> str:
        lines = [
            f"status={recommendation.status}",
            f"scoring_version={recommendation.scoring_version}",
            f"sample_size={recommendation.sample_size}",
            f"minimum_sample_size={recommendation.minimum_sample_size}",
        ]
        for engine, adjustment in recommendation.recommended_adjustments.items():
            lines.append(f"{engine}\trecommended_adjustment={adjustment:.4f}")
        return "\n".join(lines)
