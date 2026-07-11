from __future__ import annotations

from sqlalchemy import Engine

from hunter.persistence.sql.base import SQLBase


def create_schema(engine: Engine) -> None:
    SQLBase.metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    SQLBase.metadata.drop_all(engine)
