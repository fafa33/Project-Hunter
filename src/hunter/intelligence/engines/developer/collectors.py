from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.developer.models import DeveloperRecord, DeveloperSnapshot
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class DeveloperCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[DeveloperRecord, ...]:
        raise NotImplementedError


class ContextDeveloperCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[DeveloperRecord, ...]:
        value = context.get("developer_records", ())
        if isinstance(value, DeveloperSnapshot):
            return (value,)
        if isinstance(value, tuple | list):
            return tuple(item for item in value if _is_developer_record(item))
        return ()


def _is_developer_record(value: object) -> bool:
    from hunter.intelligence.engines.developer.models import (
        ContributorSnapshot,
        DeveloperEvent,
        IssueSnapshot,
        PullRequestSnapshot,
        ReleaseSnapshot,
        RepositorySnapshot,
    )

    return isinstance(
        value,
        (
            DeveloperSnapshot,
            RepositorySnapshot,
            ContributorSnapshot,
            ReleaseSnapshot,
            PullRequestSnapshot,
            IssueSnapshot,
            DeveloperEvent,
        ),
    )
