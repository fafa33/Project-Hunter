from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .shadow import ShadowObservation, ShadowRecorder


@dataclass(frozen=True)
class DomainAdapter:
    domain: str
    legacy: Callable[[dict[str, Any]], dict[str, Any]]
    successor: Callable[[dict[str, Any]], dict[str, Any]]

    def compare(
        self, recorder: ShadowRecorder, *, generation: int, head_sha: str, snapshot: dict[str, Any], observed_at: str
    ) -> ShadowObservation:
        return recorder.observe(
            generation=generation,
            head_sha=head_sha,
            domain=self.domain,
            inputs=snapshot,
            legacy=self.legacy,
            successor=self.successor,
            observed_at=observed_at,
        )


def decision_projection(state: str, description: str) -> dict[str, str]:
    if state not in {"success", "failure", "pending", "error"}:
        raise ValueError(f"invalid decision state: {state}")
    return {"state": state, "description": description}
