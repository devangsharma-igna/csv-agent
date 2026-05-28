from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from .. import context_store
from ..agents.context_builder import ContextBuilder
from ..mcp_client import mcp

log = logging.getLogger("igna.tables")
router = APIRouter()


@router.get("")
async def list_tables() -> dict:
    tables = await mcp.list_tables(["public"])
    names = [t.get("name") for t in tables if t.get("name")]
    cached = set(context_store.list_cached())
    log.info("list_tables | found=%d with_context=%d", len(names), sum(1 for n in names if n in cached))
    return {
        "tables": [
            {"name": n, "has_context": n in cached} for n in names
        ]
    }


@router.get("/{table}/context")
async def get_context_summary(table: str) -> dict:
    """Lightweight summary of the cached context. Used by the Query page to
    show context-presence + freshness at all times."""
    cached = context_store.load(table)
    exists_in_db = await mcp.table_exists(table)
    log.info("context_summary | table=%s cached=%s in_db=%s", table, bool(cached), exists_in_db)
    if not cached:
        return {
            "table": table,
            "has_context": False,
            "exists_in_db": exists_in_db,
        }
    return {
        "table": table,
        "has_context": True,
        "exists_in_db": exists_in_db,
        "generated_at": cached.get("generated_at"),
        "row_count": cached.get("row_count"),
        "column_count": len(cached.get("columns", [])),
        "pk": cached.get("pk", []),
        "data_quality_flags": cached.get("data_quality_flags", []),
        "columns": [
            {"name": c.get("name"), "type": c.get("type"), "semantic": c.get("semantic"), "null_pct": c.get("null_pct")}
            for c in cached.get("columns", [])
        ],
    }


@router.post("/{table}/refresh")
async def refresh_context(table: str) -> dict:
    log.info("refresh_context ▶ | table=%s", table)
    if not await mcp.table_exists(table):
        log.warning("refresh_context ✗ | table_deleted table=%s", table)
        raise HTTPException(status_code=410, detail={"error": "table_deleted", "table": table})
    context = await ContextBuilder().build(table)
    context_store.save(table, context)
    log.info("refresh_context ✓ | table=%s", table)
    return {"ok": True, "table": table}
