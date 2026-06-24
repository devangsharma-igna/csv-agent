from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingWrite:
    confirmation_id: str
    table: str
    sql: str
    summary: str
    affected_tables: list[str]
    expires_at: float


class PendingWriteStore:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingWrite] = {}

    def create(
        self,
        *,
        table: str,
        sql: str,
        summary: str,
        affected_tables: list[str],
    ) -> PendingWrite:
        self._purge()
        confirmation_id = secrets.token_urlsafe(24)
        pending = PendingWrite(
            confirmation_id=confirmation_id,
            table=table,
            sql=sql,
            summary=summary,
            affected_tables=affected_tables,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._items[confirmation_id] = pending
        return pending

    def consume(self, confirmation_id: str) -> PendingWrite | None:
        self._purge()
        return self._items.pop(confirmation_id, None)

    def cancel(self, confirmation_id: str) -> bool:
        self._purge()
        return self._items.pop(confirmation_id, None) is not None

    def _purge(self) -> None:
        now = time.time()
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


pending_writes = PendingWriteStore()
