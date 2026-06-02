from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import context_store, data_watcher
from ..agents.base import TableDeletedError, TableExistenceGate, init_gate_cache
from ..agents.context_builder import ContextBuilder
from ..agents.figure_builder import FigureBuilder
from ..agents.nl_responder import NLResponder
from ..agents.query_planner import QueryPlanner
from ..logging_utils import trunc
from ..db_client import MCPToolError

log = logging.getLogger("igna.query")
router = APIRouter()


class QueryRequest(BaseModel):
    table: str
    question: str


@router.post("/query")
async def query(req: QueryRequest) -> dict[str, Any]:
    # Normalise schema-qualified names (e.g. 'public.foo' → 'foo').
    # Supabase MCP list_tables may return qualified names; strip the prefix so
    # information_schema queries and context-store keys stay consistent.
    table = req.table.strip()
    if "." in table:
        table = table.split(".", 1)[1]
    if not table:
        raise HTTPException(status_code=400, detail="missing table")
    log.info("════ query START | table=%s question=%s", table, trunc(req.question, 250))
    init_gate_cache()

    # Register this asyncio task so the watcher can cancel it instantly if
    # the table's CSV is deleted while an LLM call is in flight.
    task = asyncio.current_task()
    data_watcher.register_task(table, task)

    try:
        # Boundary gate 1: cache miss → fresh MCP call; populates cache.
        log.info("phase=pre_pipeline | gate check")
        await TableExistenceGate(table, "pre_pipeline").check()

        # 1) Context — load cached or build.
        context = context_store.load(table)
        if context is None:
            log.info("phase=pre_context | no cached context, building")
            await TableExistenceGate(table, "pre_context").check()
            context = await ContextBuilder().build(table)
            context_store.save(table, context)
        else:
            log.info("phase=pre_context | reusing cached context (%d columns)", len(context.get("columns", [])))

        # 2) Plan + SQL — one LLM call for scope gate + SQL generation.
        log.info("phase=pre_planner | gate check")
        await TableExistenceGate(table, "pre_planner").check()
        plan, rows = await QueryPlanner().plan(question=req.question, context=context)

        if not plan.get("allowed", False):
            log.warning("════ query DENIED (out_of_scope) | reason=%s", trunc(plan.get("reason"), 250))
            return {
                "status": "out_of_scope",
                "reason": plan.get("reason") or "question is out of scope for this table",
                "parsed": plan,
            }

        # Normalise into the same shape downstream agents + response expect.
        parsed = plan
        sql_result = {
            "final_sql": plan.get("final_sql"),
            "row_count": plan.get("row_count"),
            "rows": rows,
            "notes": plan.get("notes", ""),
        }

        # Boundary gate: before NL Responder.
        log.info("phase=pre_responder | gate check")
        await TableExistenceGate(table, "pre_responder").check()
        answer = await NLResponder().respond(
            table=table,
            question=req.question,
            parsed=parsed,
            sql_result=sql_result,
            context=context,
        )

        # Optional figure (no LLM, no MCP — but still gate for symmetry).
        figure_b64 = None
        if answer.get("wants_figure"):
            log.info("phase=pre_figure | gate check, building figure")
            await TableExistenceGate(table, "pre_figure").check()
            figure_b64 = FigureBuilder().render(answer.get("figure_spec", {}), sql_result.get("rows", []))
        else:
            log.info("phase=pre_figure | skipped (responder didn't request figure)")

        log.info("════ query OK | rows=%s has_figure=%s", sql_result.get("row_count"), bool(figure_b64))
        return {
            "status": "ok",
            "answer": answer.get("answer", ""),
            "figure_b64": figure_b64,
            "sql": sql_result.get("final_sql"),
            "row_count": sql_result.get("row_count"),
            "parsed": parsed,
        }

    except asyncio.CancelledError:
        # Watcher cancelled this task because the CSV was deleted mid-LLM-call.
        # Suppressing CancelledError here is intentional — we convert it to an
        # HTTP 410 so the frontend gets a clean error instead of a hung request.
        log.warning("════ query ABORT (cancelled — table deleted mid-flight) | table=%s", table)
        context_store.evict(table)
        raise HTTPException(
            status_code=410,
            detail={
                "error": "table_deleted",
                "table": table,
                "phase": "mid_llm_call",
                "message": f"Table '{table}' was deleted while the query was running.",
            },
        )
    except TableDeletedError as e:
        log.warning("════ query ABORT (table_deleted) | table=%s phase=%s", e.table, e.phase)
        context_store.evict(e.table)
        raise HTTPException(
            status_code=410,
            detail={
                "error": "table_deleted",
                "table": e.table,
                "phase": e.phase,
                "message": str(e),
            },
        ) from e
    except MCPToolError as e:
        log.error("════ query ABORT (mcp_error) | %s", trunc(e.message, 400))
        raise HTTPException(status_code=502, detail={"error": "mcp_error", "message": e.message}) from e
    except RuntimeError as e:
        log.error("════ query ABORT (agent_failure) | %s", trunc(str(e), 400))
        raise HTTPException(status_code=500, detail={"error": "agent_failure", "message": str(e)}) from e
    finally:
        data_watcher.unregister_task(table, task)
