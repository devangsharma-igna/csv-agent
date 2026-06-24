"""Supabase MCP database adapter.

All agent-generated SQL is executed through Supabase's project-scoped MCP
server. The rest of the application continues to depend on the small `mcp`
interface that previously wrapped DuckDB.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .config import settings
from .logging_utils import trunc

log = logging.getLogger("igna.db")


class MCPToolError(RuntimeError):
    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"DB tool '{tool}' failed: {message}")
        self.tool = tool
        self.message = message


def normalize_tool_result(*, structured: Any, text: str | None) -> list[dict[str, Any]]:
    """Normalize Supabase MCP tool output into rows used by existing callers."""
    value = structured
    if value is None and text:
        value = _parse_mcp_string(text)

    while isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        if key not in {"result", "rows", "data"}:
            break
        value = value[key]
    if isinstance(value, str):
        value = _parse_mcp_string(value)

    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [row if isinstance(row, dict) else {"result": row} for row in value]
    if isinstance(value, dict):
        for key in ("rows", "data", "result", "tables"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row if isinstance(row, dict) else {"result": row} for row in rows]
        return [value]
    return [{"result": value}]


def _parse_mcp_string(value: str) -> Any:
    wrapped = re.search(
        r"<(untrusted-data-[^>]+)>\s*((?:\[|\{).*?)\s*</\1>",
        value,
        flags=re.DOTALL,
    )
    candidate = wrapped.group(2) if wrapped else value
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return value


class SupabaseMCP:
    async def start(self) -> None:
        log.info("Supabase MCP configured | url=%s", settings.supabase_mcp_url)

    async def stop(self) -> None:
        return None

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        if not settings.SUPABASE_ACCESS_TOKEN:
            raise MCPToolError(name, "SUPABASE_ACCESS_TOKEN is not configured")

        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx

        headers = {"Authorization": f"Bearer {settings.SUPABASE_ACCESS_TOKEN}"}
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60.0) as client:
                async with streamable_http_client(
                    settings.supabase_mcp_url,
                    http_client=client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments=arguments)
        except Exception as exc:
            raise MCPToolError(name, str(exc)) from exc

        if result.isError:
            message = " ".join(
                getattr(item, "text", str(item))
                for item in result.content
            )
            raise MCPToolError(name, message or "Supabase MCP tool failed")

        text_parts = [
            item.text
            for item in result.content
            if getattr(item, "type", None) == "text" and hasattr(item, "text")
        ]
        rows = normalize_tool_result(
            structured=result.structuredContent,
            text="\n".join(text_parts) or None,
        )
        dt = (time.perf_counter() - t0) * 1000
        log.info("MCP %s ✓ (%.0fms) | rows=%d", name, dt, len(rows))
        return rows

    async def execute_sql(self, query: str) -> list[dict[str, Any]]:
        log.info("MCP SQL → %s", trunc(query, 400))
        return await self._call_tool("execute_sql", {"query": query})

    async def list_tables(self, schemas: list[str] | None = None) -> list[dict[str, Any]]:
        return await self._call_tool("list_tables", {"schemas": schemas or ["public"]})

    async def apply_migration(self, name: str, query: str) -> list[dict[str, Any]]:
        return await self._call_tool("apply_migration", {"name": name, "query": query})

    async def table_exists(self, table: str, schema: str = "public") -> bool:
        if "." in table:
            schema, table = table.split(".", 1)
        safe_table = table.replace("'", "''")
        safe_schema = schema.replace("'", "''")
        rows = await self.execute_sql(
            "SELECT EXISTS ("
            "SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = '{safe_schema}' AND table_name = '{safe_table}'"
            ") AS exists"
        )
        return bool(rows and rows[0].get("exists"))


mcp = SupabaseMCP()
