from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from sqlalchemy import Column, DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class CursorStore(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str) -> None: ...


class InMemoryCursorStore:
    """Test helper — deterministic cursor store."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._d.get(key)

    def set(self, key: str, value: str) -> None:
        self._d[key] = value


class NoopCursorStore:
    """Dev helper: always returns None, never persists."""

    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: str) -> None:
        return None


class _Base(DeclarativeBase):
    pass


class _CursorRow(_Base):
    __tablename__ = "beema_cursors"

    stream_name = Column(String(128), primary_key=True)
    cursor_value = Column(String(512), nullable=False, default="")
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SQLiteCursor:
    stream: str
    cursor_value: str
    updated_at: datetime


class SQLiteCursorStore:
    """
    SQLite-backed CursorStore.

    Contract matches the BEEMA ingest consumer's needs:
      - get(key) -> Optional[str]
      - set(key, value) -> None
    """

    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(db_url, future=True)
        _Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(self._engine, expire_on_commit=False, future=True)

    def get(self, key: str) -> Optional[str]:
        with self._Session() as s:
            row: _CursorRow | None = s.get(_CursorRow, key)
            return row.cursor_value if row else None

    def set(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc)
        with self._Session() as s:
            row: _CursorRow | None = s.get(_CursorRow, key)
            if row is None:
                row = _CursorRow(stream_name=key, cursor_value=value, updated_at=now)
                s.add(row)
            else:
                row.cursor_value = value
                row.updated_at = now
            s.commit()

    def reset(self, key: str) -> None:
        with self._Session() as s:
            row: _CursorRow | None = s.get(_CursorRow, key)
            if row is not None:
                s.delete(row)
                s.commit()

