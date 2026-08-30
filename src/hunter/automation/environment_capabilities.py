"""Deterministic execution-environment capability assessment for agent dispatch."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED_CAPABILITIES_ENV = "HUNTER_AGENT_REQUIRED_CAPABILITIES"
AVAILABLE_CAPABILITIES_ENV = "HUNTER_AGENT_AVAILABLE_CAPABILITIES"
SUPPORTED_CAPABILITIES = frozenset({"egress", "github_push", "representative_preflight"})
_CAP_SYS_PTRACE = 19


@dataclass(frozen=True, slots=True)
class EnvironmentCapabilityAssessment:
    """Result of comparing task requirements with known runtime capabilities."""

    suitable: bool
    required: tuple[str, ...]
    available: tuple[str, ...]
    reasons: tuple[str, ...]


def _capabilities(value: object, *, name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, str):
        raise ValueError(f"{name} must be configured as a JSON capability array")
    try:
        decoded = json.loads(value)
    except ValueError:
        raise ValueError(f"{name} must be valid JSON") from None
    if not isinstance(decoded, list) or any(not isinstance(item, str) or not item for item in decoded):
        raise ValueError(f"{name} must be a JSON array of non-empty strings")
    if len(set(decoded)) != len(decoded):
        raise ValueError(f"{name} must not contain duplicate capabilities")
    unknown = sorted(set(decoded) - SUPPORTED_CAPABILITIES)
    if unknown:
        raise ValueError(f"{name} contains unsupported capabilities: {','.join(unknown)}")
    return tuple(sorted(decoded))


def _has_effective_cap_sys_ptrace() -> bool:
    """Return true only when Linux explicitly reports CAP_SYS_PTRACE effective."""
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in status.splitlines():
        if not line.startswith("CapEff:"):
            continue
        try:
            effective = int(line.split(":", 1)[1].strip(), 16)
        except ValueError:
            return False
        return bool(effective & (1 << _CAP_SYS_PTRACE))
    return False


def assess_environment_capabilities(
    environ: Mapping[str, str] | None = None,
) -> EnvironmentCapabilityAssessment:
    """Assess only explicitly required capabilities and known unsafe conditions."""
    values = os.environ if environ is None else environ
    required = _capabilities(values.get(REQUIRED_CAPABILITIES_ENV), name=REQUIRED_CAPABILITIES_ENV)
    available = _capabilities(values.get(AVAILABLE_CAPABILITIES_ENV), name=AVAILABLE_CAPABILITIES_ENV)

    missing = sorted(set(required) - set(available))
    reasons = [f"missing_capability:{capability}" for capability in missing]

    if "representative_preflight" in required and "representative_preflight" in available:
        get_euid = getattr(os, "geteuid", None)
        if callable(get_euid) and get_euid() == 0 and _has_effective_cap_sys_ptrace():
            reasons.append("representative_preflight:root_with_cap_sys_ptrace")

    return EnvironmentCapabilityAssessment(
        suitable=not reasons,
        required=required,
        available=available,
        reasons=tuple(sorted(reasons)),
    )


__all__ = [
    "AVAILABLE_CAPABILITIES_ENV",
    "EnvironmentCapabilityAssessment",
    "REQUIRED_CAPABILITIES_ENV",
    "SUPPORTED_CAPABILITIES",
    "assess_environment_capabilities",
]
