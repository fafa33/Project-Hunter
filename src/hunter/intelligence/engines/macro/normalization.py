from __future__ import annotations

from hunter.intelligence.engines.macro.models import MACRO_DOMAINS, MacroDataPoint, MacroDataset


class MacroNormalizer:
    def normalize(self, points: tuple[MacroDataPoint, ...]) -> MacroDataset:
        normalized: list[MacroDataPoint] = []
        seen: set[str] = set()
        for point in sorted(points, key=lambda item: (item.domain, item.timestamp.isoformat(), item.source)):
            if point.domain not in MACRO_DOMAINS or point.domain in seen:
                continue
            normalized.append(
                MacroDataPoint(
                    domain=point.domain,
                    value=_clamp_optional(point.value),
                    previous_value=_clamp_optional(point.previous_value),
                    source=point.source,
                    timestamp=point.timestamp,
                    reliability=_clamp(point.reliability),
                    reference=point.reference,
                    raw_data=point.raw_data,
                )
            )
            seen.add(point.domain)
        return MacroDataset(tuple(normalized))


def _clamp_optional(value: float | None) -> float | None:
    return None if value is None else _clamp(value)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
