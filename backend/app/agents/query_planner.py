from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from ..db_client import MCPToolError, mcp
from .base import TableExistenceGate, load_prompt, single_shot_json

log = logging.getLogger("igna.agent.query_planner")

_RETRY_SUFFIX = (
    "\n\nThe generated SQL failed with this Postgres error:\n"
    "ERROR: {error}\n\n"
    "Fix ONLY the `final_sql` field and return the corrected JSON object."
)


class QueryPlanner:
    """Merged scope-gate + SQL writer — replaces NLParser + SQLAgent.

    One LLM call decides if the question is answerable AND writes the SQL.
    Python then executes it (with one retry on error).  Saves one full LLM
    round-trip (~400-500 ms) versus the old two-agent sequential flow.
    """

    name = "query_planner"

    async def plan(
        self,
        *,
        question: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return (plan_dict, rows).

        plan_dict keys:
          allowed, reason, intent, target_columns, filters_hint,
          refined_query, final_sql, row_count, notes
        rows: the raw execute_sql result (empty list when allowed=False or SQL fails).
        """
        table = context.get("table", "")
        log.info("query_planner ▶ | table=%s question=%s", table, trunc(question, 200))

        gate = TableExistenceGate(table, phase=self.name, in_loop=False)
        system = load_prompt("query_planner")

        all_columns = context.get("columns", [])
        schema_full = {
            "table": table,
            "pk": context.get("pk"),
            "columns": [
                {
                    "name": c["name"],
                    "type": c.get("type"),
                    "semantic": c.get("semantic"),
                    "nullable": c.get("nullable"),
                    "distinct": c.get("distinct"),
                    "null_pct": c.get("null_pct"),
                }
                for c in all_columns
            ],
        }
        sample_rows = context.get("sample_rows", [])[:3]

        # Pin table context to the system prompt so Azure OpenAI's prefix cache
        # can reuse it across consecutive queries to the same table.
        # The user turn carries only the question (changes every call).
        system = (
            load_prompt("query_planner")
            + f"\n\n## TABLE CONTEXT\n{json.dumps(schema_full, default=str)}"
            + f"\n\n## SAMPLE ROWS (use for enum spellings, date formats, numeric precision)\n"
            + json.dumps(sample_rows, default=str)
        )

        base_user = (
            f"USER QUESTION: {question}\n\n"
            f"Reply ONLY with the JSON object."
        )

        user = base_user
        plan: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        last_error = ""

        for attempt in range(1 + settings.MAX_SQL_RETRIES):
            plan = await single_shot_json(system=system, user=user, phase=self.name)

            if not plan.get("allowed", False):
                log.info(
                    "query_planner DENIED | reason=%s", trunc(plan.get("reason"), 200)
                )
                break

            sql = (plan.get("final_sql") or "").strip().rstrip(";")
            if not sql:
                log.warning("query_planner attempt %d: allowed but no SQL", attempt + 1)
                break

            log.info("query_planner attempt %d → %s", attempt + 1, trunc(sql, 400))
            try:
                rows = await mcp.execute_sql(sql)
                await gate.check()
                log.info("query_planner ✓ | rows=%d", len(rows))
                break
            except MCPToolError as exc:
                last_error = exc.message
                log.warning(
                    "query_planner attempt %d failed | %s", attempt + 1, trunc(last_error, 300)
                )
                if "does not exist" in last_error.lower() or "undefined_table" in last_error.lower():
                    await gate.check()  # raises TableDeletedError
                if attempt < settings.MAX_SQL_RETRIES:
                    user = base_user + _RETRY_SUFFIX.format(error=last_error)

        plan["rows"] = rows
        plan["row_count"] = plan.get("row_count") or len(rows)
        if last_error and not rows:
            plan.setdefault(
                "notes",
                f"SQL failed after {1 + settings.MAX_SQL_RETRIES} attempts: {last_error}",
            )
        return plan, rows
