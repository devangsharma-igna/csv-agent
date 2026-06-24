from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from ..db_client import MCPToolError, mcp
from ..sql_safety import is_mutating_sql
from .base import TableExistenceGate, load_prompt, single_shot_json

log = logging.getLogger("igna.agent.query_planner")

_RETRY_SUFFIX = (
    "\n\nThe generated SQL failed with this error:\n"
    "ERROR: {error}\n\n"
    "Fix ONLY the `final_sql` field and return the corrected JSON object."
)

_SEMANTIC_LOOKUP_RESCUE = """

## SEARCH RESCUE
- Sample rows are ONLY for enum spellings, date formats, and numeric precision.
- The absence of an exact value in sample rows is NOT grounds for denial.
- If the user mentions a literal person, project, subject, title, email, identifier, or other free-text value, map it to the most plausible existing text column(s) using column semantics.
- When a literal value plausibly belongs in one or more text columns, mark the query as allowed and generate a read-only SQL lookup using ILIKE.
- If multiple text columns are plausible, combine them with OR.
- Deny ONLY when no existing column could plausibly hold the requested concept or value.
"""

_TEXT_TYPES = {"VARCHAR", "TEXT", "CHAR", "CHARACTER VARYING", "STRING"}
_LOOKUP_INTENTS = {"", "lookup", "describe", "filter"}
_NON_LOOKUP_CUES = (
    "count",
    "how many",
    "average",
    "avg",
    "sum",
    "total",
    "trend",
    "over time",
    "group by",
    "compare",
    "comparison",
    "highest",
    "lowest",
    "top ",
    "bottom ",
    "rank",
    "ranking",
)
_WRITE_ACTION_CUES = (
    "delete ",
    "remove ",
    "update ",
    "insert ",
    "add ",
    "create ",
    "send ",
    "email ",
    "close ticket",
    "reassign ",
)
_WORLD_KNOWLEDGE_CUES = (
    "gdp",
    "weather",
    "stock price",
    "stock market",
    "exchange rate",
    "population",
    "capital of",
    "president",
    "prime minister",
    "ceo",
)


def _searchable_text_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    searchable: list[dict[str, Any]] = []
    for col in columns:
        col_type = str(col.get("type") or "").upper()
        if col_type not in _TEXT_TYPES:
            continue
        distinct = col.get("distinct")
        null_pct = float(col.get("null_pct") or 0.0)
        if distinct == 0 or null_pct >= 100.0:
            continue
        searchable.append(
            {
                "name": col.get("name"),
                "semantic": col.get("semantic"),
                "distinct": distinct,
                "null_pct": null_pct,
            }
        )
    return searchable


def _should_retry_semantic_lookup(
    *,
    question: str,
    plan: dict[str, Any],
    searchable_text_columns: list[dict[str, Any]],
) -> bool:
    if not searchable_text_columns:
        return False
    if str(plan.get("intent") or "").lower() not in _LOOKUP_INTENTS:
        return False

    q = question.lower()
    if any(_has_cue(q, cue) for cue in _NON_LOOKUP_CUES):
        return False
    if any(_has_cue(q, cue) for cue in _WRITE_ACTION_CUES):
        return False
    if any(_has_cue(q, cue) for cue in _WORLD_KNOWLEDGE_CUES):
        return False
    return True


def _has_cue(question: str, cue: str) -> bool:
    cue = cue.strip().lower()
    if " " in cue:
        return cue in question
    return re.search(rf"\b{re.escape(cue)}\b", question) is not None


async def _retry_semantic_lookup(
    *,
    question: str,
    schema_full: dict[str, Any],
    sample_rows: list[dict[str, Any]],
    searchable_text_columns: list[dict[str, Any]],
    denied_plan: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    system = (
        load_prompt("query_planner")
        + f"\n\n## TABLE CONTEXT\n{json.dumps(schema_full, default=str)}"
        + f"\n\n## SAMPLE ROWS (use for enum spellings, date formats, numeric precision)\n"
        + json.dumps(sample_rows, default=str)
        + _SEMANTIC_LOOKUP_RESCUE
        + f"\n\n## SEARCHABLE TEXT COLUMNS\n{json.dumps(searchable_text_columns, default=str)}"
    )
    user = (
        f"USER QUESTION: {question}\n\n"
        f"Earlier denial: {json.dumps(denied_plan, default=str)}\n\n"
        "Re-evaluate the question under the search rescue rules. "
        "Reply ONLY with the JSON object."
    )
    return await single_shot_json(system=system, user=user, phase=f"{phase}_semantic_lookup_rescue")


class QueryPlanner:
    """Merged scope-gate + SQL writer — one LLM call per query.

    Table context is pinned to the system prompt so Azure's prefix cache
    can reuse it across consecutive queries to the same table.
    """

    name = "query_planner"

    async def plan(
        self,
        *,
        question: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        table = context.get("table", "")
        log.info("query_planner | table=%s question=%s", table, trunc(question, 200))

        gate = TableExistenceGate(table, phase=self.name, in_loop=False)

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

        # Context in system prompt → stable prefix → Azure input-token cache hit
        # on consecutive queries to the same table.
        system = (
            load_prompt("query_planner")
            + f"\n\n## TABLE CONTEXT\n{json.dumps(schema_full, default=str)}"
            + f"\n\n## SAMPLE ROWS (use for enum spellings, date formats, numeric precision)\n"
            + json.dumps(sample_rows, default=str)
        )
        base_user = f"USER QUESTION: {question}\n\nReply ONLY with the JSON object."
        searchable_text_columns = _searchable_text_columns(all_columns)

        user = base_user
        plan: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        last_error = ""

        for attempt in range(1 + settings.MAX_SQL_RETRIES):
            plan = await single_shot_json(system=system, user=user, phase=self.name)

            if not plan.get("allowed", False):
                if _should_retry_semantic_lookup(
                    question=question,
                    plan=plan,
                    searchable_text_columns=searchable_text_columns,
                ):
                    plan = await _retry_semantic_lookup(
                        question=question,
                        schema_full=schema_full,
                        sample_rows=sample_rows,
                        searchable_text_columns=searchable_text_columns,
                        denied_plan=plan,
                        phase=self.name,
                    )
                log.info("query_planner DENIED | reason=%s", trunc(plan.get("reason"), 200))
                if not plan.get("allowed", False):
                    break

            sql = (plan.get("final_sql") or "").strip().rstrip(";")
            if not sql:
                log.warning("query_planner attempt %d: allowed but no SQL", attempt + 1)
                break
            if plan.get("operation") == "write" or is_mutating_sql(sql):
                plan["operation"] = "write"
                log.info("query_planner write preview | %s", trunc(sql, 400))
                break

            log.info("query_planner attempt %d | %s", attempt + 1, trunc(sql, 400))
            try:
                rows = await mcp.execute_sql(sql)
                await gate.check()
                log.info("query_planner ok | rows=%d", len(rows))
                break
            except MCPToolError as exc:
                last_error = exc.message
                log.warning("query_planner attempt %d failed | %s", attempt + 1, trunc(last_error, 300))
                if "does not exist" in last_error.lower() or "undefined_table" in last_error.lower():
                    await gate.check()
                if attempt < settings.MAX_SQL_RETRIES:
                    user = base_user + _RETRY_SUFFIX.format(error=last_error)

        plan["rows"] = rows
        plan.setdefault("operation", "read")
        plan["row_count"] = plan.get("row_count") or len(rows)
        if last_error and not rows:
            plan.setdefault("notes", f"SQL failed after {1 + settings.MAX_SQL_RETRIES} attempts: {last_error}")
        return plan, rows
