from __future__ import annotations

from hunter.historical.models import (
    HistoricalCalibrationMetric,
    HistoricalEngineMetric,
    HistoricalEngineOutput,
    HistoricalOutcome,
)


def calibration_metric(
    outputs: tuple[HistoricalEngineOutput, ...],
    outcomes: tuple[HistoricalOutcome, ...],
    *,
    minimum_sample_size: int,
) -> HistoricalCalibrationMetric:
    samples = tuple(
        (output, success)
        for output in outputs
        if (success := _success(outcomes, output.case_id)) is not None
        and output.status == "AVAILABLE"
        and output.evidence_ids
    )
    distribution = _confidence_distribution(tuple(output for output, _ in samples))
    if len(samples) < minimum_sample_size:
        return HistoricalCalibrationMetric(
            metric_id="historical-calibration",
            brier_score="INSUFFICIENT_SAMPLE_SIZE",
            calibration_error="INSUFFICIENT_SAMPLE_SIZE",
            reliability_buckets=(("INSUFFICIENT_SAMPLE_SIZE", len(samples), "INSUFFICIENT_SAMPLE_SIZE"),),
            sample_size_status="INSUFFICIENT_SAMPLE_SIZE",
            confidence_distribution=distribution,
        )
    brier = sum((sample[0].confidence - float(sample[1])) ** 2 for sample in samples) / len(samples)
    expected = sum(sample[0].confidence for sample in samples) / len(samples)
    observed = sum(float(sample[1]) for sample in samples) / len(samples)
    buckets = _reliability_curve(samples)
    return HistoricalCalibrationMetric(
        metric_id="historical-calibration",
        brier_score=round(brier, 6),
        calibration_error=round(abs(expected - observed), 6),
        reliability_buckets=tuple(
            (label, count, observed_probability) for label, _, observed_probability, count in buckets
        ),
        sample_size_status="OK",
        expected_probability=round(expected, 6),
        observed_probability=round(observed, 6),
        reliability_curve=buckets,
        confidence_distribution=distribution,
    )


def engine_metrics(
    outputs: tuple[HistoricalEngineOutput, ...],
    outcomes: tuple[HistoricalOutcome, ...],
) -> tuple[HistoricalEngineMetric, ...]:
    by_engine: dict[str, list[HistoricalEngineOutput]] = {}
    for output in outputs:
        by_engine.setdefault(output.engine, []).append(output)
    rows = []
    for engine, engine_outputs in sorted(by_engine.items()):
        samples = tuple((output, _success(outcomes, output.case_id)) for output in engine_outputs)
        available = tuple(output for output in engine_outputs if output.status == "AVAILABLE" and output.evidence_ids)
        successes = tuple(item for item in samples if item[1] is True)
        positives = tuple(item for item in samples if item[0].score >= 0.5)
        negative = tuple(item for item in samples if item[0].score < 0.5)
        rows.append(
            HistoricalEngineMetric(
                engine=engine,
                historical_availability=round((len(available) / max(len(engine_outputs), 1)) * 100.0, 2),
                predictive_association="INSUFFICIENT_SAMPLE_SIZE",
                hit_rate_when_positive=_rate(tuple(item for item in positives if item[1] is True), positives),
                hit_rate_when_negative=_rate(tuple(item for item in negative if item[1] is False), negative),
                false_positive_contribution=sum(1 for item in positives if item[1] is False),
                false_negative_contribution=sum(1 for item in negative if item[1] is True),
                agreement_with_success=_rate(successes, samples),
                disagreement_with_success=_rate(tuple(item for item in samples if item[1] is False), samples),
                marginal_contribution="INSUFFICIENT_SAMPLE_SIZE",
                evidence_quality=round(sum(output.confidence for output in engine_outputs) / len(engine_outputs), 4),
                freshness_quality=100.0,
                sample_count=len(engine_outputs),
            )
        )
    return tuple(rows)


def _success(outcomes: tuple[HistoricalOutcome, ...], case_id: str) -> bool | None:
    outcome = next((item for item in outcomes if item.case_id == case_id), None)
    if outcome is None or outcome.final_success_label == "INSUFFICIENT_OUTCOME_DATA":
        return None
    return outcome.final_success_label in {"MAJOR_WINNER", "MODERATE_WINNER", "OUTPERFORMED_BENCHMARK", "SURVIVED"}


def _rate(numerator: tuple[object, ...], denominator: tuple[object, ...]) -> float | str:
    if not denominator:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round((len(numerator) / len(denominator)) * 100.0, 2)


def _confidence_distribution(outputs: tuple[HistoricalEngineOutput, ...]) -> tuple[tuple[str, int], ...]:
    buckets = {"0.00-0.25": 0, "0.25-0.50": 0, "0.50-0.75": 0, "0.75-1.00": 0}
    for output in outputs:
        confidence = max(0.0, min(1.0, output.confidence))
        if confidence < 0.25:
            buckets["0.00-0.25"] += 1
        elif confidence < 0.5:
            buckets["0.25-0.50"] += 1
        elif confidence < 0.75:
            buckets["0.50-0.75"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return tuple(buckets.items())


def _reliability_curve(
    samples: tuple[tuple[HistoricalEngineOutput, bool], ...],
) -> tuple[tuple[str, float | str, float | str, int], ...]:
    rows = []
    ranges = (
        ("0.00-0.25", 0.0, 0.25),
        ("0.25-0.50", 0.25, 0.5),
        ("0.50-0.75", 0.5, 0.75),
        ("0.75-1.00", 0.75, 1.000001),
    )
    for label, lower, upper in ranges:
        bucket = tuple(sample for sample in samples if lower <= sample[0].confidence < upper)
        if not bucket:
            rows.append((label, "INSUFFICIENT_SAMPLE_SIZE", "INSUFFICIENT_SAMPLE_SIZE", 0))
            continue
        expected = sum(sample[0].confidence for sample in bucket) / len(bucket)
        observed = sum(float(sample[1]) for sample in bucket) / len(bucket)
        rows.append((label, round(expected, 6), round(observed, 6), len(bucket)))
    return tuple(rows)
