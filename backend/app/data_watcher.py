"""Filesystem watcher for DATA_DIR.

on_deleted  → CSV removed
    • Drop DuckDB view  (CSV ↔ view are strictly bound)
    • Evict context     (stale immediately)
    • Tombstone table   (gate trips on next check)
    • Cancel in-flight asyncio tasks for this table (LLM call aborted instantly)

on_modified → CSV changed
    • Header diff against cached context:
        schema changed  → evict context (rebuild on next query)
        data only       → no-op (DuckDB reads fresh on every execute_sql)

on_created  → CSV appeared (re-upload / manual restore)
    • Clear tombstone
"""

from __future__ import annotations

import asyncio
import csv
import logging
import threading
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from . import context_store
from .config import settings

log = logging.getLogger("igna.watcher")

_lock = threading.Lock()
_deleted: set[str] = set()
_table_tasks: dict[str, set[Any]] = {}
_loop: asyncio.AbstractEventLoop | None = None


# ── Public API ────────────────────────────────────────────────────────────────

def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def is_tombstoned(table: str) -> bool:
    with _lock:
        return table in _deleted


def clear_tombstone(table: str) -> None:
    with _lock:
        _deleted.discard(table)
    log.debug("tombstone cleared | table=%s", table)


def register_task(table: str, task: Any) -> None:
    with _lock:
        _table_tasks.setdefault(table, set()).add(task)


def unregister_task(table: str, task: Any) -> None:
    with _lock:
        bucket = _table_tasks.get(table)
        if bucket:
            bucket.discard(task)
            if not bucket:
                _table_tasks.pop(table, None)


# ── Header comparison ─────────────────────────────────────────────────────────

def _csv_columns(path: Path) -> list[str] | None:
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return next(csv.reader(fh), None)
    except Exception:
        return None


def _schema_changed(table: str, csv_path: Path) -> bool:
    cached = context_store.load(table)
    if cached is None:
        return False
    cached_cols = [c["name"] for c in cached.get("columns", [])]
    new_cols = _csv_columns(csv_path)
    if new_cols is None:
        return False
    changed = cached_cols != new_cols
    if changed:
        log.info("schema diff | table=%s old=%s new=%s", table, cached_cols, new_cols)
    return changed


# ── Watchdog handler ──────────────────────────────────────────────────────────

class _DataDirHandler(FileSystemEventHandler):

    def on_deleted(self, event: FileDeletedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        table = path.stem

        from .db_client import mcp
        mcp.drop_view_sync(table)
        context_store.evict(table)

        with _lock:
            _deleted.add(table)
            tasks = list(_table_tasks.get(table, []))

        if tasks and _loop is not None:
            for task in tasks:
                _loop.call_soon_threadsafe(task.cancel)
            log.warning("DATA WATCHER: %d task(s) cancelled | table=%s", len(tasks), table)

        log.warning(
            "DATA WATCHER: CSV deleted → view dropped, context evicted, "
            "table tombstoned | table=%s", table,
        )

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        table = path.stem
        if _schema_changed(table, path):
            context_store.evict(table)
            log.warning("DATA WATCHER: schema changed → context evicted | table=%s", table)
        else:
            log.debug("DATA WATCHER: data modified, schema intact | table=%s", table)

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        table = path.stem
        with _lock:
            had = table in _deleted
            _deleted.discard(table)
        if had:
            log.info("DATA WATCHER: CSV restored → tombstone lifted | table=%s", table)


# ── Observer lifecycle ────────────────────────────────────────────────────────

_observer: Observer | None = None


def start() -> None:
    global _observer
    if _observer is not None:
        return
    data_dir = settings.data_path
    _observer = Observer()
    _observer.schedule(_DataDirHandler(), str(data_dir), recursive=False)
    _observer.start()
    log.info("data watcher started | watching=%s", data_dir)


def stop() -> None:
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join()
        _observer = None
    log.info("data watcher stopped")
