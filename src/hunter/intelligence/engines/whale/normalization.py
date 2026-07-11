from __future__ import annotations

from hunter.intelligence.engines.whale.models import WHALE_SIGNAL_TYPES, WhaleDataset, WhaleEvent


class WhaleNormalizer:
    def normalize(self, events: tuple[WhaleEvent, ...]) -> WhaleDataset:
        normalized: list[WhaleEvent] = []
        seen: set[str] = set()
        for event in sorted(events, key=lambda item: (item.timestamp.isoformat(), item.id)):
            if event.id in seen or event.event_type not in WHALE_SIGNAL_TYPES:
                continue
            normalized.append(
                WhaleEvent(
                    id=event.id,
                    asset=event.asset.strip().lower(),
                    event_type=event.event_type,
                    amount=_clamp_optional(event.amount),
                    direction=_normalize_direction(event.direction),
                    source=event.source,
                    timestamp=event.timestamp,
                    reliability=_clamp(event.reliability),
                    wallet_attribution_quality=_clamp(event.wallet_attribution_quality),
                    confirmation=_clamp(event.confirmation),
                    reference=event.reference,
                    raw_data=event.raw_data,
                    metadata={str(key): str(value) for key, value in event.metadata.items()},
                )
            )
            seen.add(event.id)
        return WhaleDataset(tuple(normalized))


def _normalize_direction(direction: str) -> str:
    value = direction.strip().lower().replace("-", "_").replace(" ", "_")
    return value or "unknown"


def _clamp_optional(value: float | None) -> float | None:
    return None if value is None else _clamp(value)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
