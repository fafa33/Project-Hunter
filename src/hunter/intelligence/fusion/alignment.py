from __future__ import annotations

from hunter.intelligence.fusion.models import FusionInput, FusionTarget


def align_to_target(inputs: tuple[FusionInput, ...], target: FusionTarget) -> tuple[FusionInput, ...]:
    if target.target_type != "project":
        return inputs
    return tuple(item for item in inputs if item.project == target.target_id)


def effective_window(inputs: tuple[FusionInput, ...]) -> tuple[str, str] | tuple[()]:
    if not inputs:
        return ()
    values = sorted(item.effective_at.isoformat() for item in inputs)
    return values[0], values[-1]
