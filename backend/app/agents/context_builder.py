from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from .base import (
    TableExistenceGate,
    list_tables_tool,
    load_prompt,
    react_loop,
    supabase_select_tool,
)

log = logging.getLogger("igna.agent.context_builder")


class ContextBuilder:
    """Agent 1 — persona: DB Architect (14 YOE). Profiles a table via MCP SQL."""

    name = "context_builder"

    async def build(self, table: str) -> dict[str, Any]:
        log.info("context_builder ▶ | table=%s", table)
        # Entry gate is enforced by the caller (orchestrator pre_context, or the
        # refresh endpoint). We only need the in-loop gate for MCP observations.
        gate = TableExistenceGate(table, phase=self.name, in_loop=True)
        system = load_prompt("context_builder")
        user = (
            f"Target table: `{table}` (schema: public).\n"
            f"Profile it as specified. Reply ONLY with the JSON object."
        )
        ctx = await react_loop(
            system=system,
            user=user,
            tools=[supabase_select_tool(), list_tables_tool()],
            gate=gate,
        )
        ctx["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        ctx.setdefault("table", table)
        log.info(
            "context_builder ✓ | table=%s row_count=%s columns=%d pk=%s quality_flags=%d",
            table, ctx.get("row_count"), len(ctx.get("columns", [])),
            ctx.get("pk"), len(ctx.get("data_quality_flags", [])),
        )
        return ctx
