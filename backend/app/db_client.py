"""Local DuckDB backend.

CSV files live in DATA_DIR/{table}.csv and are registered as views in the
DuckDB 'public' schema so all information_schema queries, bare table-name
references, and agent tool calls work without change.

No network, no spawned process — every call is in-process (~microseconds).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import duckdb

from .config import settings
from .logging_utils import trunc

log = logging.getLogger("igna.db")

_PG_TO_DUCK: dict[str, str] = {
    "integer": "INTEGER",
    "bigint": "BIGINT",
    "double precision": "DOUBLE",
    "boolean": "BOOLEAN",
    "text": "VARCHAR",
    "character varying": "VARCHAR",
    "timestamptz": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
}


class MCPToolError(RuntimeError):
    """Name preserved so every caller's `except MCPToolError` continues to work."""

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"DB tool '{tool}' failed: {message}")
        self.tool = tool
        self.message = message


class LocalDB:
    """In-process DuckDB store.

    * CSV files are the source of truth — stored in DATA_DIR/{table}.csv.
    * Each CSV is exposed as a DuckDB VIEW in the 'public' schema.
    * All information_schema queries work unchanged (DuckDB is SQL-92 compliant).
    * table_exists() is an O(1) file-system check; catalog fallback for edge cases.
    * A single connection with an asyncio.Lock serialises writes.
    """

    def __init__(self) -> None:
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._con is not None:
            return
        log.info("opening DuckDB (in-memory)")
        self._con = duckdb.connect()
        self._con.execute("CREATE SCHEMA IF NOT EXISTS public")
        self._con.execute("SET schema = 'public'")
        await self._restore_views()

    async def stop(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # ------------------------------------------------------------------ public API

    async def execute_sql(self, query: str) -> list[dict[str, Any]]:
        log.info("DB SQL → %s", trunc(query, 400))
        t0 = time.perf_counter()
        try:
            async with self._lock:
                rows = await asyncio.to_thread(self._run_query, query)
            dt = (time.perf_counter() - t0) * 1000
            log.info("DB SQL ✓ (%.0fms) | rows=%d preview=%s", dt, len(rows), trunc(rows, 300))
            return rows
        except duckdb.Error as exc:
            dt = (time.perf_counter() - t0) * 1000
            msg = str(exc)
            log.warning("DB SQL ✗ (%.0fms) | error=%s", dt, trunc(msg, 600))
            raise MCPToolError("execute_sql", msg) from exc

    async def list_tables(self, schemas: list[str] | None = None) -> list[dict[str, Any]]:
        schema_list = schemas or ["public"]
        placeholders = ", ".join(f"'{s}'" for s in schema_list)
        return await self.execute_sql(
            "SELECT table_name AS name, table_schema AS schema "
            "FROM information_schema.tables "
            f"WHERE table_schema IN ({placeholders}) "
            "AND table_type IN ('VIEW', 'BASE TABLE') "
            "ORDER BY table_name"
        )

    async def apply_migration(self, name: str, query: str) -> Any:
        """No-op — DDL is handled by register_csv. Kept for interface compatibility."""
        log.debug("apply_migration ignored (local mode) | name=%s", name)
        return {"ok": True}

    async def table_exists(self, table: str, schema: str = "public") -> bool:
        if "." in table:
            schema, table = table.split(".", 1)
        if (settings.data_path / f"{table}.csv").exists():
            return True
        safe_t = table.replace("'", "''")
        safe_s = schema.replace("'", "''")
        try:
            rows = await self.execute_sql(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema='{safe_s}' AND table_name='{safe_t}' LIMIT 1"
            )
            return len(rows) > 0
        except MCPToolError:
            return False

    # ------------------------------------------------------------------ CSV registration

    async def register_csv(
        self,
        table: str,
        csv_path: Path,
        columns: list[dict[str, Any]] | None = None,
    ) -> None:
        """Register a CSV as a DuckDB VIEW in the public schema.

        columns: list of {name, type} with PostgreSQL type names.
        When provided, uses explicit typing. When omitted, auto-sniffs (startup restore).
        """
        safe_path = str(csv_path).replace("\\", "/").replace("'", "''")
        if columns:
            duck_cols = {c["name"]: _PG_TO_DUCK.get(c["type"], "VARCHAR") for c in columns}
            cols_arg = ", ".join(f"'{n}': '{t}'" for n, t in duck_cols.items())
            sql = (
                f'CREATE OR REPLACE VIEW public."{table}" AS '
                f"SELECT * FROM read_csv('{safe_path}', header=True, columns={{{cols_arg}}})"
            )
        else:
            sql = (
                f'CREATE OR REPLACE VIEW public."{table}" AS '
                f"SELECT * FROM read_csv_auto('{safe_path}', header=True)"
            )
        log.info("register_csv | table=%s typed=%s", table, bool(columns))
        try:
            async with self._lock:
                await asyncio.to_thread(self._con.execute, sql)
        except duckdb.Error as exc:
            raise MCPToolError("register_csv", str(exc)) from exc
        log.info("register_csv ✓ | table=%s", table)

    def drop_view_sync(self, table: str) -> None:
        """Drop the DuckDB view synchronously (called from the watchdog thread)."""
        if self._con is None:
            return
        try:
            self._con.execute(f'DROP VIEW IF EXISTS public."{table}"')
            log.info("drop_view_sync ✓ | table=%s", table)
        except duckdb.Error as exc:
            log.warning("drop_view_sync failed | table=%s error=%s", table, exc)

    # ------------------------------------------------------------------ internal

    def _run_query(self, query: str) -> list[dict[str, Any]]:
        assert self._con is not None, "DB not started"
        result = self._con.execute(query)
        if result.description is None:
            return []
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    async def _restore_views(self) -> None:
        """Re-register CSV views at startup; evict context that drifted offline."""
        from . import context_store
        from .data_watcher import _schema_changed

        csvs = list(settings.data_path.glob("*.csv"))
        log.info("restore_views | %d CSV(s) in %s", len(csvs), settings.data_path)
        for csv_path in csvs:
            table = csv_path.stem
            try:
                await self.register_csv(table, csv_path)
                if _schema_changed(table, csv_path):
                    context_store.evict(table)
                    log.warning("restore_views: schema drift → context evicted | table=%s", table)
            except Exception:
                log.exception("restore_views ✗ | table=%s (skipping)", table)


mcp = LocalDB()
