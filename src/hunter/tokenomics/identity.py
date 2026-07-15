from __future__ import annotations

from typing import Any

from hunter.execution.identity import identity


def tokenomics_id(kind: str, payload: Any) -> str:
    return identity(f"tokenomics-{kind}", payload)
