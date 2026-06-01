from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from ..mcp_client import MCPToolError, mcp
from .base import (
    TableExistenceGate,
    load_prompt,
    single_shot_json,
)

log = logging.getLogger("igna.agent.sql_agent")

_RETRY_SUFFIX = (
    "\n\nThe previous SQL failed with this Postgres error:\n"
    "ERROR: {error}\n\n"
    "Fix the SQL and return the corrected JSON object."
)


class SQLAgent:
    """Agent 3 — writes SQL in one shot, executes in Python, retries on error."""

    name = "sql_agent"

    async def run(
        self,
        *,
        table: str,
        parsed: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        log.info(
            "sql_agent ▶ | table=%s intent=%s targets=%s refined=%s",
            table, parsed.get("intent"), parsed.get("target_columns"),
            trunc(parsed.get("refined_query"), 250),
        )
        gate = TableExistenceGate(table, phase=self.name, in_loop=False)
        system = load_prompt("sql_agent")

        target_cols = set(parsed.get("target_columns") or [])
        schema_rich = {
            "table": context.get("table"),
            "pk": context.get("pk"),
            "columns": [
                {
                    "name": c["name"],
                    "type": c.get("type"),
                    "semantic": c.get("semantic"),
                    "distinct": c.get("distinct"),
                    "null_pct": c.get("null_pct"),
                    "is_target": c["name"] in target_cols,
                }
                for c in context.get("columns", [])
            ],
        }
        sample_rows = context.get("sample_rows", [])[:3]

        base_user = (
            f"TABLE: {table}\n"
            f"SCHEMA:\n{json.dumps(schema_rich, default=str)}\n\n"
            f"SAMPLE ROWS (real data, use for format/spelling reference):\n"
            f"{json.dumps(sample_rows, default=str)}\n\n"
            f"REFINED QUERY: {parsed.get('refined_query') or parsed.get('intent')}\n"
            f"TARGET COLUMNS: {parsed.get('target_columns', [])}\n"
            f"FILTERS HINT: {parsed.get('filters_hint', '')}\n\n"
            f"Write the SQL and reply ONLY with the JSON object."
        )

        user = base_user
        result: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        last_error: str = ""

        for attempt in range(1 + settings.MAX_SQL_RETRIES):
            result = await single_shot_json(system=system, user=user, phase=self.name)
            sql = (result.get("final_sql") or "").strip().rstrip(";")
            if not sql:
                log.warning("sql_agent attempt %d: no SQL in response", attempt + 1)
                break

            log.info("sql_agent attempt %d → %s", attempt + 1, trunc(sql, 400))
            try:
                rows = await mcp.execute_sql(sql)
                await gate.check()
                break
            except MCPToolError as exc:
                last_error = exc.message
                log.warning("sql_agent attempt %d failed | %s", attempt + 1, trunc(last_error, 300))
                if "does not exist" in last_error.lower() or "undefined_table" in last_error.lower():
                    await gate.check()  # raises TableDeletedError
                if attempt < settings.MAX_SQL_RETRIES:
                    user = base_user + _RETRY_SUFFIX.format(error=last_error)

        result["rows"] = rows
        result["row_count"] = result.get("row_count") or len(rows)
        if last_error and not rows:
            result.setdefault("notes", f"SQL failed after {1 + settings.MAX_SQL_RETRIES} attempts: {last_error}")
        log.info(
            "sql_agent ✓ | rows=%s sql=%s",
            result.get("row_count"), trunc(result.get("final_sql"), 300),
        )
        return result
