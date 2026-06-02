"""Local DuckDB backend — same async API surface as the old SupabaseMCP.

CSV files live in DATA_DIR/{table}.csv and are registered as views in the
DuckDB 'public' schema so all existing information_schema queries, bare
table-name references, and agent tool calls work without change.

No MCP spawn, no network — every call is in-process (~microseconds).
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

# PostgreSQL type names used in CommitRequest → DuckDB column types for read_csv().
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
    """In-process DuckDB store with the same async interface as SupabaseMCP.

    * CSV files are the source of truth — stored in DATA_DIR/{table}.csv.
    * Each CSV is exposed as a DuckDB VIEW in the 'public' schema.
    * All information_schema queries work unchanged (DuckDB is SQL-92 compliant).
    * table_exists() is an O(1) file-system check; the catalog fallback is for
      edge cases only.
    * A single connection with an asyncio.Lock serialises writes; reads are fast
      in-process and never block on network I/O.
    """

    def __init__(self) -> None:
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._con is not None:
            return
        db_path = settings.data_path / "igna.duckdb"
        log.info("opening DuckDB | path=%s", db_path)
        self._con = duckdb.connect(str(db_path))
        # Create the 'public' schema so information_schema queries that filter on
        # table_schema = 'public' work without any changes to callers.
        self._con.execute("CREATE SCHEMA IF NOT EXISTS public")
        # Make bare table references (e.g. FROM "zomato") resolve to public.
        self._con.execute("SET schema = 'public'")
        # Re-register all CSV views — idempotent, repairs catalog if .duckdb was
        # recreated while the data/ directory was preserved.
        await self._restore_views()

    async def stop(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # ------------------------------------------------------------------ public API (mirrors SupabaseMCP)

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
        rows = await self.execute_sql(
            "SELECT table_name AS name, table_schema AS schema "
            "FROM information_schema.tables "
            f"WHERE table_schema IN ({placeholders}) "
            "AND table_type IN ('VIEW', 'BASE TABLE') "
            "ORDER BY table_name"
        )
        return rows

    async def apply_migration(self, name: str, query: str) -> Any:
        """No-op: DDL is now handled by register_csv. Kept for API compatibility."""
        log.debug("apply_migration ignored (local mode) | name=%s", name)
        return {"ok": True}

    async def table_exists(self, table: str, schema: str = "public") -> bool:
        if "." in table:
            schema, table = table.split(".", 1)
        # O(1) file-system check covers the common path.
        if (settings.data_path / f"{table}.csv").exists():
            return True
        # Catalog fallback for edge cases (manual DDL, etc.).
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
        """Register a CSV file as a DuckDB VIEW in the public schema.

        ``columns`` is a list of {name, type} dicts with PostgreSQL type names.
        When provided, types are mapped to DuckDB equivalents via read_csv().
        When omitted, read_csv_auto() sniffs types (used during restore on startup).
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
        log.info("register_csv | table=%s path=%s typed=%s", table, csv_path.name, bool(columns))
        try:
            async with self._lock:
                await asyncio.to_thread(self._con.execute, sql)
        except duckdb.Error as exc:
            raise MCPToolError("register_csv", str(exc)) from exc
        log.info("register_csv ✓ | table=%s", table)

    # ------------------------------------------------------------------ internal

    def _run_query(self, query: str) -> list[dict[str, Any]]:
        assert self._con is not None, "DB not started"
        # Use the connection directly (not cursor()) so the SET schema = 'public'
        # applied in start() is honoured — cursor() creates an independent
        # sub-connection in DuckDB and does not inherit session-level settings.
        result = self._con.execute(query)
        if result.description is None:
            return []
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    async def _restore_views(self) -> None:
        """Re-register all CSV views from DATA_DIR at startup. Fast and idempotent."""
        csvs = list(settings.data_path.glob("*.csv"))
        log.info("restore_views | %d CSV(s) in %s", len(csvs), settings.data_path)
        for csv_path in csvs:
            table = csv_path.stem
            try:
                await self.register_csv(table, csv_path)
            except Exception:
                log.exception("restore_views ✗ | table=%s (skipping)", table)


mcp = LocalDB()
