from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from .base import (
    TableExistenceGate,
    load_prompt,
    react_loop,
    supabase_select_tool,
)

log = logging.getLogger("igna.agent.sql_agent")


class SQLAgent:
    """Agent 3 — writes + executes SQL, self-corrects on Postgres errors."""

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
        # Entry gate covered by orchestrator pre_sql. In-loop gate still runs
        # after every MCP observation inside react_loop.
        gate = TableExistenceGate(table, phase=self.name, in_loop=True)
        system = load_prompt("sql_agent")

        # Build a richer schema payload so the first SQL attempt succeeds more
        # often without needing a retry round-trip.
        # Include distinct cardinality for every column (helps choose between
        # COUNT(DISTINCT) vs GROUP BY, and flags low-cardinality text cols).
        # Include sample_rows from context so the agent sees actual value formats
        # (date strings, enum spellings, numeric precision).
        target_cols = set(parsed.get("target_columns") or [])
        schema_rich = {
            "table": context.get("table"),
            "pk": context.get("pk"),
            "columns": [
                {
                    "name": c["name"],
                    "type": c.get("type"),
                    "semantic": c.get("semantic"),
                    "distinct": c.get("distinct"),          # cardinality hint
                    "null_pct": c.get("null_pct"),          # avoid unnecessary IS NOT NULL
                    "is_target": c["name"] in target_cols,  # flag columns NL Parser chose
                }
                for c in context.get("columns", [])
            ],
        }
        # Attach up to 3 sample rows so the agent sees real value formats.
        sample_rows = context.get("sample_rows", [])[:3]

        user = (
            f"TABLE: {table}\n"
            f"SCHEMA:\n{json.dumps(schema_rich, default=str)}\n\n"
            f"SAMPLE ROWS (real data, use for format/spelling reference):\n"
            f"{json.dumps(sample_rows, default=str)}\n\n"
            f"REFINED QUERY: {parsed.get('refined_query') or parsed.get('intent')}\n"
            f"TARGET COLUMNS: {parsed.get('target_columns', [])}\n"
            f"FILTERS HINT: {parsed.get('filters_hint', '')}\n\n"
            f"Write and execute the SQL. Then reply ONLY with the JSON object."
        )
        # Capture each successful execute_sql observation so we can return the
        # ACTUAL rows without asking the LLM to echo them in its JSON output.
        # In the previous design the LLM spent ~40s/call regurgitating result
        # sets into a `rows: [...]` field — wasteful and a re-ask risk.
        observations: list[dict[str, Any]] = []
        result = await react_loop(
            system=system,
            user=user,
            tools=[supabase_select_tool()],
            gate=gate,
            max_iter=1 + settings.MAX_SQL_RETRIES * 2,
            observations=observations,
        )
        rows: list[dict[str, Any]] = []
        for obs in reversed(observations):
            if obs["tool"] == "execute_sql" and isinstance(obs["result"], list):
                rows = obs["result"]
                break
        # Merge captured rows with the LLM's final JSON (final_sql, notes).
        result["rows"] = rows
        result["row_count"] = result.get("row_count") or len(rows)
        log.info(
            "sql_agent ✓ | rows=%s sql=%s",
            result.get("row_count"), trunc(result.get("final_sql"), 300),
        )
        return result
