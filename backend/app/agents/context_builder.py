from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from ..mcp_client import mcp
from .base import (
    TableExistenceGate,
    load_prompt,
    react_loop,
    supabase_select_tool,
)

log = logging.getLogger("igna.agent.context_builder")

_SCHEMA_SQL = """\
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = '{table}'
ORDER BY ordinal_position"""


class ContextBuilder:
    """Agent 1 — persona: DB Architect (14 YOE). Profiles a table via MCP SQL."""

    name = "context_builder"

    async def build(self, table: str) -> dict[str, Any]:
        log.info("context_builder ▶ | table=%s", table)
        # Pre-hoist Call 1 (schema introspection) into Python — it's deterministic
        # SQL with no LLM judgment, so running it here saves one ReAct iteration.
        schema_rows = await mcp.execute_sql(_SCHEMA_SQL.format(table=table))
        log.debug("context_builder schema pre-fetch | columns=%d", len(schema_rows))

        # Entry gate is enforced by the caller (orchestrator pre_context, or the
        # refresh endpoint). We only need the in-loop gate for MCP observations.
        gate = TableExistenceGate(table, phase=self.name, in_loop=True)
        system = load_prompt("context_builder")
        user = (
            f"Target table: `{table}` (schema: public).\n"
            f"Schema (pre-fetched — do NOT call execute_sql for this again):\n"
            f"{json.dumps(schema_rows, default=str)}\n\n"
            f"Profile it as specified. Reply ONLY with the JSON object."
        )
        ctx = await react_loop(
            system=system,
            user=user,
            tools=[supabase_select_tool()],
            gate=gate,
            max_iter=4,
        )
        ctx["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        ctx.setdefault("table", table)
        log.info(
            "context_builder ✓ | table=%s row_count=%s columns=%d pk=%s quality_flags=%d",
            table, ctx.get("row_count"), len(ctx.get("columns", [])),
            ctx.get("pk"), len(ctx.get("data_quality_flags", [])),
        )
        return ctx
