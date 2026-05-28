"""Singleton Supabase MCP client.

Spawns `npx -y @supabase/mcp-server-supabase` over stdio at startup and exposes
the three calls the rest of the backend needs: list_tables, execute_sql,
apply_migration. All Supabase access in this codebase MUST go through here so
the agents stay 'detached from data' (per the project brief).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import settings
from .logging_utils import trunc

log = logging.getLogger("igna.mcp")

# Supabase MCP wraps execute_sql output in a prompt-injection-safe envelope.
# Two annoying realities of that envelope:
#   1. The opening UUID tag is mentioned TWICE — once inline in the prose
#      warning ("...within the below <untrusted-data-UUID> boundaries."), and
#      once as the real opening tag of the data block.
#   2. The closing `</untrusted-data-UUID>` is sometimes missing from the
#      response we receive (truncation? newer server? unclear), so we cannot
#      anchor on it.
# Robust strategy: find every opening tag, then for each (last to first) try
# JSONDecoder.raw_decode on the text immediately following it. The first one
# that successfully parses a JSON array/object is our payload.
_UNTRUSTED_OPEN_RE = re.compile(r"<untrusted-data-[A-Za-z0-9-]+>")


def _unwrap_untrusted_envelope(text: str) -> Any | None:
    matches = list(_UNTRUSTED_OPEN_RE.finditer(text))
    if not matches:
        return None
    decoder = json.JSONDecoder()
    for m in reversed(matches):
        rest = text[m.end():].lstrip()
        if not rest or rest[0] not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(rest)
            return obj
        except json.JSONDecodeError:
            continue
    return None


class SupabaseMCP:
    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._session is not None:
            return
        # NOTE: the Supabase MCP server defaults to read-only in recent versions.
        # We need DDL + INSERT, so we explicitly enable the 'database' feature group
        # WITHOUT --read-only. The 'docs' feature is enabled too for SQL hints.
        import os
        params = StdioServerParameters(
            command="npx",
            args=[
                "-y",
                "@supabase/mcp-server-supabase@latest",
                f"--project-ref={settings.SUPABASE_PROJECT_REF}",
                "--features=database,docs",
            ],
            env={
                **os.environ,  # inherit PATH so npx is findable on Windows
                "SUPABASE_ACCESS_TOKEN": settings.SUPABASE_PAT,
            },
        )
        log.info("spawning Supabase MCP server | project_ref=%s features=database,docs", settings.SUPABASE_PROJECT_REF)
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        # Enumerate available tools so we know what the server actually exposed.
        try:
            tools = await self._session.list_tools()
            tool_names = [t.name for t in tools.tools]
            log.info("MCP connected | tools=%s", trunc(tool_names, 600))
        except Exception:  # noqa: BLE001 - never fatal at startup
            log.exception("MCP list_tools failed at startup (continuing anyway)")

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None

    async def _call(self, name: str, args: dict[str, Any]) -> Any:
        assert self._session is not None, "MCP not started"
        log.info("MCP → %s | args=%s", name, trunc(args, 500))
        log.debug("MCP → %s | full_args=%s", name, trunc(args, 4000))
        t0 = time.perf_counter()
        async with self._lock:
            result = await self._session.call_tool(name, args)
        dt = (time.perf_counter() - t0) * 1000
        if result.isError:
            err = _flatten_text(result.content)
            log.warning("MCP ✗ %s (%.0fms) | error=%s", name, dt, trunc(err, 600))
            raise MCPToolError(name, err)
        payload = _parse_content(result.content)
        size = len(payload) if isinstance(payload, (list, dict, str)) else "?"
        log.info("MCP ✓ %s (%.0fms) | size=%s preview=%s", name, dt, size, trunc(payload, 300))
        log.debug("MCP ✓ %s | full_result=%s", name, trunc(payload, 4000))
        return payload

    async def list_tables(self, schemas: list[str] | None = None) -> list[dict[str, Any]]:
        payload = await self._call("list_tables", {"schemas": schemas or ["public"]})
        return payload if isinstance(payload, list) else payload.get("tables", [])

    async def execute_sql(self, query: str) -> list[dict[str, Any]]:
        payload = await self._call("execute_sql", {"query": query})
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("rows", payload.get("result", []))
        return []

    async def apply_migration(self, name: str, query: str) -> Any:
        return await self._call("apply_migration", {"name": name, "query": query})

    async def table_exists(self, table: str, schema: str = "public") -> bool:
        """Fast existence check used by the TableExistenceGate.

        Accepts either a bare table name (`data`) or a schema-qualified one
        (`public.data`) — splits on the first dot so the FE doesn't have to
        worry about it.
        """
        if "." in table:
            schema, table = table.split(".", 1)
        safe = table.replace("'", "''")
        safe_schema = schema.replace("'", "''")
        try:
            rows = await self.execute_sql(
                f"SELECT 1 AS ok FROM information_schema.tables "
                f"WHERE table_schema='{safe_schema}' AND table_name='{safe}' LIMIT 1"
            )
            return isinstance(rows, list) and len(rows) > 0
        except MCPToolError:
            tables = await self.list_tables([schema])
            return any(t.get("name") == table for t in tables)


class MCPToolError(RuntimeError):
    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"MCP tool '{tool}' failed: {message}")
        self.tool = tool
        self.message = message


def _flatten_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(_flatten_text(c) for c in content)
    if hasattr(content, "text"):
        return str(content.text)
    return str(content)


def _parse_content(content: Any) -> Any:
    text = _flatten_text(content)
    # Try the envelope unwrap first — works whether the outer text is plain
    # or wrapped in {"result": "..."}.
    inner = _unwrap_untrusted_envelope(text)
    if inner is not None:
        return inner
    # No envelope: maybe it's just a plain JSON value (list_tables etc).
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(outer, dict) and isinstance(outer.get("result"), str):
        inner = _unwrap_untrusted_envelope(outer["result"])
        if inner is not None:
            return inner
    return outer


mcp = SupabaseMCP()
