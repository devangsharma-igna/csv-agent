"""Filesystem watcher for DATA_DIR.

Runs a watchdog Observer in a background thread. Handles three events:

on_deleted  → CSV removed externally
    • Tombstone the table  (gate trips instantly on next check)
    • Drop the DuckDB view (CSV ↔ view are strictly bound; no orphan views)
    • Evict the context    (stale context gone immediately)

on_modified → CSV file changed externally
    • Read only the header line of the new file (cheap, no full parse)
    • Compare column names against the cached context
    • Schema changed  (columns added / removed / renamed) → evict context
      so it is rebuilt on the next query
    • Data only changed (same columns, values differ) → do nothing;
      DuckDB reads the CSV fresh on every query, so the change is already
      visible to the next execute_sql call without any cache invalidation

on_created  → fresh CSV appeared (re-upload or manual restore)
    • Clear tombstone so the gate stops blocking the table

No application-level query result caching exists in this codebase.
The only caching in play is Azure OpenAI's transparent input-token prefix
caching, enabled by pinning the table context to the system prompt.
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

# Shared lock covers both _deleted and _table_tasks.
_lock = threading.Lock()

# Tombstone set — gate reads this before touching the filesystem.
_deleted: set[str] = set()

# Active query tasks per table.  When a CSV is deleted, every in-flight task
# for that table is cancelled so the LLM call is aborted immediately rather
# than allowed to run to completion before the gate has a chance to fire.
_table_tasks: dict[str, set[Any]] = {}   # Any = asyncio.Task

# Event loop reference set at startup so the watcher thread can schedule
# task cancellations on the correct loop.
_loop: asyncio.AbstractEventLoop | None = None


# ── Public API ────────────────────────────────────────────────────────────────

def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once from the async lifespan so the watcher thread has the loop."""
    global _loop
    _loop = loop


def is_tombstoned(table: str) -> bool:
    """Return True if the CSV was deleted and has not been re-created yet."""
    with _lock:
        return table in _deleted


def clear_tombstone(table: str) -> None:
    """Remove the tombstone (called when a fresh CSV is committed / re-uploaded)."""
    with _lock:
        _deleted.discard(table)
    log.debug("tombstone cleared | table=%s", table)


def register_task(table: str, task: Any) -> None:
    """Track an in-flight query task for table so it can be cancelled on deletion."""
    with _lock:
        _table_tasks.setdefault(table, set()).add(task)


def unregister_task(table: str, task: Any) -> None:
    """Remove a completed task from the registry."""
    with _lock:
        bucket = _table_tasks.get(table)
        if bucket:
            bucket.discard(task)
            if not bucket:
                _table_tasks.pop(table, None)


# ── Header comparison helper ──────────────────────────────────────────────────

def _csv_columns(path: Path) -> list[str] | None:
    """Read only the first (header) line of a CSV. Returns None on any error."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            return next(reader, None)
    except Exception:
        return None


def _schema_changed(table: str, csv_path: Path) -> bool:
    """Return True if the CSV header differs from the column names in cached context."""
    cached = context_store.load(table)
    if cached is None:
        # No context to compare against — nothing to invalidate.
        return False
    cached_cols = [c["name"] for c in cached.get("columns", [])]
    new_cols = _csv_columns(csv_path)
    if new_cols is None:
        # Couldn't read the file (may still be mid-write); treat as no change.
        return False
    changed = cached_cols != new_cols
    if changed:
        log.info(
            "schema diff detected | table=%s | old=%s | new=%s",
            table, cached_cols, new_cols,
        )
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

        # Import here to avoid a circular import at module load time.
        from .db_client import mcp
        mcp.drop_view_sync(table)   # view and CSV are bound — drop together

        context_store.evict(table)  # context references the old schema

        with _lock:
            _deleted.add(table)
            # Grab all in-flight tasks for this table while holding the lock.
            tasks = list(_table_tasks.get(table, []))

        # Cancel every in-flight query task for this table.
        # call_soon_threadsafe is required because this callback runs in the
        # watchdog thread, not the asyncio event loop thread.
        if tasks and _loop is not None:
            for task in tasks:
                _loop.call_soon_threadsafe(task.cancel)
            log.warning(
                "DATA WATCHER ✗ | %d in-flight task(s) cancelled | table=%s",
                len(tasks), table,
            )

        log.warning(
            "DATA WATCHER ✗ | CSV deleted → view dropped, context evicted, "
            "table tombstoned | table=%s",
            table,
        )

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        table = path.stem

        # Only invalidate context when the column layout changed.
        # Pure data edits (same columns, different values) leave the context
        # intact — DuckDB already reads the file fresh on every query.
        if _schema_changed(table, path):
            context_store.evict(table)
            log.warning(
                "DATA WATCHER ⚠ | CSV schema changed → context evicted "
                "(will rebuild on next query) | table=%s",
                table,
            )
        else:
            log.debug(
                "DATA WATCHER ✓ | CSV data modified, schema unchanged → "
                "no cache action needed | table=%s",
                table,
            )

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".csv":
            return
        table = path.stem
        with _lock:
            had_tombstone = table in _deleted
            _deleted.discard(table)
        if had_tombstone:
            log.info(
                "DATA WATCHER ✓ | CSV restored → tombstone lifted | table=%s", table
            )


# ── Observer lifecycle ────────────────────────────────────────────────────────

_observer: Observer | None = None


def start() -> None:
    """Start the background filesystem observer. Call once at app startup."""
    global _observer
    if _observer is not None:
        return
    data_dir = settings.data_path
    _observer = Observer()
    _observer.schedule(_DataDirHandler(), str(data_dir), recursive=False)
    _observer.start()
    log.info("data watcher started | watching=%s", data_dir)


def stop() -> None:
    """Stop the observer. Call at app shutdown."""
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join()
        _observer = None
    log.info("data watcher stopped")
