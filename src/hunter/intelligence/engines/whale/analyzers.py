from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.whale.models import WhaleAnalysis, WhaleDataset, WhaleEvent, WhaleSignal


class WhaleAnalyzer:
    def analyze(self, dataset: WhaleDataset) -> WhaleAnalysis:
        grouped = dataset.by_type()
        signals = tuple(self._signal(event_type, events) for event_type, events in grouped.items())
        accumulating_assets = tuple(
            sorted({event.asset for event in dataset.events if event.event_type == "accumulation"})
        )
        distributing_assets = tuple(
            sorted({event.asset for event in dataset.events if event.event_type == "distribution"})
        )
        exchange_flow = self._exchange_flow(grouped.get("exchange_flow", ()))
        smart_money_activity = self._activity(grouped.get("smart_money", ()))
        notable_events = tuple(
            f"{signal.event_type}:{signal.direction}"
            for signal in signals
            if signal.direction not in {"unknown", "neutral"}
        )
        return WhaleAnalysis(
            signals=signals,
            accumulating_assets=accumulating_assets,
            distributing_assets=distributing_assets,
            exchange_flow=exchange_flow,
            smart_money_activity=smart_money_activity,
            notable_events=notable_events,
            metadata={"engine": "whale"},
        )

    def _signal(self, event_type: str, events: tuple[WhaleEvent, ...]) -> WhaleSignal:
        amount_values = [event.amount for event in events if event.amount is not None]
        amount_strength = mean(amount_values) if amount_values else 0.0
        reliability = mean(
            mean([event.reliability, event.wallet_attribution_quality, event.confirmation]) for event in events
        )
        direction = self._dominant_direction(events)
        return WhaleSignal(
            name=f"whale_{event_type}",
            event_type=event_type,
            strength=round(amount_strength, 4),
            direction=direction,
            confidence=round(reliability, 4),
            event_count=len(events),
        )

    def _dominant_direction(self, events: tuple[WhaleEvent, ...]) -> str:
        counts: dict[str, int] = {}
        for event in events:
            counts[event.direction] = counts.get(event.direction, 0) + 1
        if not counts:
            return "unknown"
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _exchange_flow(self, events: tuple[WhaleEvent, ...]) -> str:
        if not events:
            return "unknown"
        directions = {event.direction for event in events}
        if "outflow" in directions and "inflow" not in directions:
            return "net_outflow"
        if "inflow" in directions and "outflow" not in directions:
            return "net_inflow"
        return "mixed"

    def _activity(self, events: tuple[WhaleEvent, ...]) -> str:
        if not events:
            return "unknown"
        average = mean(event.amount for event in events if event.amount is not None)
        if average >= 0.65:
            return "elevated"
        if average <= 0.35:
            return "muted"
        return "moderate"
