from __future__ import annotations

import json
import logging
from typing import Any

from ..logging_utils import trunc
from .base import load_prompt, single_shot_json

log = logging.getLogger("igna.agent.nl_responder")


class NLResponder:
    """Agent 4 — final NL answer; decides whether a figure is warranted."""

    name = "nl_responder"

    async def respond(
        self,
        *,
        table: str,
        question: str,
        parsed: dict[str, Any],
        sql_result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        log.info("nl_responder ▶ | table=%s rows_seen=%d", table, sql_result.get("row_count", 0))
        # Entry gate covered by orchestrator pre_responder. No MCP tools here.
        rows = sql_result.get("rows", [])
        total = sql_result.get("row_count", len(rows))
        # Cap rows fed to the LLM to keep token cost bounded.
        sample = rows[:200]
        truncated = total > len(sample)
        schema_only = [
            {"name": c["name"], "type": c.get("type"), "semantic": c.get("semantic")}
            for c in context.get("columns", [])
        ]
        # Schema is stable per table → pin to system prompt for prefix caching.
        # Rows + SQL are query-specific → stay in the user turn.
        system = (
            load_prompt("nl_responder")
            + f"\n\n## TABLE SCHEMA\n{json.dumps(schema_only)}"
        )
        user = (
            f"USER QUESTION: {question}\n"
            f"INTENT: {parsed.get('intent')}\n"
            f"TARGET COLUMNS: {parsed.get('target_columns')}\n"
            f"SQL: {sql_result.get('final_sql')}\n"
            f"ROW COUNT (full): {total}  (showing {len(sample)}{' — truncated' if truncated else ''})\n"
            f"ROWS:\n{json.dumps(sample, default=str)}\n\n"
            f"Reply ONLY with the JSON object."
        )
        result = await single_shot_json(system=system, user=user, phase=self.name)
        log.info(
            "nl_responder ✓ | wants_figure=%s answer=%s",
            result.get("wants_figure"), trunc(result.get("answer"), 300),
        )
        return result
