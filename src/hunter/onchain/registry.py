from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.models import OnChainSurface

EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class RegistryValidation:
    valid: bool
    surfaces: int
    projects_with_surface: int
    issues: tuple[str, ...]


class SurfaceRegistry:
    def __init__(self, config: OnChainConfig) -> None:
        self.config = config

    def surfaces_for_project(self, project: str) -> tuple[OnChainSurface, ...]:
        return tuple(surface for surface in self.config.surfaces if surface.project == project and surface.active)

    def validate(self) -> RegistryValidation:
        issues: list[str] = []
        chain_ids = {chain.chain_id for chain in self.config.chains if chain.enabled}
        pairs = Counter((surface.chain_id, surface.address.lower()) for surface in self.config.surfaces)
        for surface in self.config.surfaces:
            if surface.chain_id not in chain_ids:
                issues.append(f"{surface.project}:{surface.address}:unsupported_chain")
            if not EVM_ADDRESS.match(surface.address):
                issues.append(f"{surface.project}:{surface.address}:invalid_address")
            if not surface.source_url.startswith(("https://", "http://")):
                issues.append(f"{surface.project}:{surface.address}:invalid_source_url")
            if pairs[(surface.chain_id, surface.address.lower())] > 1:
                issues.append(f"{surface.project}:{surface.address}:duplicate_address")
        return RegistryValidation(
            valid=not issues,
            surfaces=len(self.config.surfaces),
            projects_with_surface=len({surface.project for surface in self.config.surfaces if surface.active}),
            issues=tuple(sorted(set(issues))),
        )
