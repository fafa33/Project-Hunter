from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine


def create_sqlite_engine(path: str | Path = ":memory:", *, echo: bool = False) -> Engine:
    if str(path) == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
    else:
        url = f"sqlite+pysqlite:///{Path(path)}"
    return create_engine(url, echo=echo, future=True)
