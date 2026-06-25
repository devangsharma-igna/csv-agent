from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import context_store
from ..agents.context_builder import ContextBuilder
from ..auth import CurrentUser, require_super_admin, require_user
from ..db_client import mcp

log = logging.getLogger("igna.tables")
router = APIRouter()


def _bare(name: str) -> str:
    return name.split(".", 1)[-1] if "." in name else name


@router.get("")
async def list_tables(
    _user: CurrentUser = Depends(require_user),
) -> dict:
    tables = await mcp.list_tables(["public"])
    names = [_bare(t.get("name", "")) for t in tables if t.get("name")]
    names = [n for n in names if n]
    cached = set(context_store.list_cached())
    log.info("list_tables | found=%d with_context=%d", len(names), sum(1 for n in names if n in cached))
    return {"tables": [{"name": n, "has_context": n in cached} for n in names]}


@router.get("/{table}/context")
async def get_context_summary(
    table: str,
    _user: CurrentUser = Depends(require_user),
) -> dict:
    cached = context_store.load(table)
    exists_in_db = await mcp.table_exists(table)
    log.info("context_summary | table=%s cached=%s in_db=%s", table, bool(cached), exists_in_db)
    if not cached:
        return {"table": table, "has_context": False, "exists_in_db": exists_in_db}
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
async def refresh_context(
    table: str,
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict:
    log.info("refresh_context | table=%s", table)
    if not await mcp.table_exists(table):
        raise HTTPException(status_code=410, detail={"error": "table_deleted", "table": table})
    context = await ContextBuilder().build(table)
    context_store.save(table, context)
    log.info("refresh_context done | table=%s", table)
    return {"ok": True, "table": table}
