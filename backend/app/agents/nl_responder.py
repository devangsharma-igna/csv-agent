from __future__ import annotations

import json
import logging
from typing import Any

from ..logging_utils import trunc
from .base import load_prompt, single_shot_json

log = logging.getLogger("igna.agent.nl_responder")


class NLResponder:
    """Agent — turns SQL result rows into a natural-language answer."""

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
        log.info("nl_responder | table=%s rows=%d", table, sql_result.get("row_count", 0))

        rows = sql_result.get("rows", [])
        total = sql_result.get("row_count", len(rows))
        sample = rows[:200]
        truncated = total > len(sample)

        schema_only = [
            {"name": c["name"], "type": c.get("type"), "semantic": c.get("semantic")}
            for c in context.get("columns", [])
        ]

        # Schema in system prompt → stable prefix → Azure input-token cache hit.
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
        log.info("nl_responder ok | wants_figure=%s answer=%s", result.get("wants_figure"), trunc(result.get("answer"), 300))
        return result
