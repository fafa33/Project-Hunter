from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session

from hunter.persistence.sql.factory import RepositoryFactory
from hunter.persistence.sql.session import SessionFactory


class UnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.repositories: RepositoryFactory | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self._session_factory.create()
        self.repositories = RepositoryFactory(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        if self.session is None:
            msg = "UnitOfWork has no active session"
            raise RuntimeError(msg)
        self.session.commit()

    def rollback(self) -> None:
        if self.session is None:
            msg = "UnitOfWork has no active session"
            raise RuntimeError(msg)
        self.session.rollback()
