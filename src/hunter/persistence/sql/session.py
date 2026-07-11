from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker


class SessionFactory:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def create(self) -> Session:
        return self._maker()

    def scoped(self) -> scoped_session[Session]:
        return scoped_session(self._maker)


class SessionManager:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory.create()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
