"""Read-only post-commit observation of Source Handling persistence (ADR 0038 section 1).

ADR 0038 replicates every ADR 0036 transaction into exactly one commit of the anchored Source Handling
ledger. The repositories serialize writers with ``BEGIN IMMEDIATE``. An observer notified right after
each successful commit can therefore capture exactly that transaction's effect.

The observer receives only the database path, after the commit has completed. It cannot alter,
delay or roll back the transaction. Observation is scoped to the calling context (``ContextVar``), never
process-global, and with no observer installed the repositories behave exactly as before.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from pathlib import Path

CommitObserver = Callable[[Path], None]

_OBSERVER: ContextVar[CommitObserver | None] = ContextVar("source_handling_commit_observer", default=None)


@contextlib.contextmanager
def observe_commits(observer: CommitObserver) -> Iterator[None]:
    """Install ``observer`` for Source Handling commits made in this context."""

    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def notify_commit(path: str | Path) -> None:
    """Called by the Source Handling repositories immediately after a successful commit."""

    observer = _OBSERVER.get()
    if observer is not None:
        observer(Path(path))
