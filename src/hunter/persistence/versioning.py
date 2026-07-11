from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PERSISTENCE_SCHEMA_VERSION = "persistence-record-v1"


@dataclass(frozen=True)
class SchemaVersion:
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Schema version is required")


@dataclass(frozen=True)
class MigrationDescriptor:
    source_version: SchemaVersion
    target_version: SchemaVersion
    description: str


class MigrationPlan(Protocol):
    @property
    def source_version(self) -> SchemaVersion:
        raise NotImplementedError

    @property
    def target_version(self) -> SchemaVersion:
        raise NotImplementedError

    def describe(self) -> MigrationDescriptor:
        raise NotImplementedError
