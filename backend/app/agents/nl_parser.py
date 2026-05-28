from __future__ import annotations

import json
import logging
from typing import Any

from ..logging_utils import trunc
from .base import load_prompt, single_shot_json

log = logging.getLogger("igna.agent.nl_parser")


class NLParser:
    """Agent 2 — intent + scope gate. No tools; pure classification."""

    name = "nl_parser"

    async def parse(self, *, question: str, context: dict[str, Any]) -> dict[str, Any]:
        log.info("nl_parser ▶ | table=%s question=%s", context.get("table"), trunc(question, 200))
        # Entry gate covered by orchestrator pre_parser. Single-shot, no MCP tools
        # in this agent, so no in-loop gate needed either.
        system = load_prompt("nl_parser")
        # Send only schema + semantic — never sample rows.
        schema_only = {
            "table": context.get("table"),
            "columns": [
                {"name": c["name"], "type": c.get("type"), "semantic": c.get("semantic"), "nullable": c.get("nullable")}
                for c in context.get("columns", [])
            ],
        }
        user = (
            f"TABLE CONTEXT:\n{json.dumps(schema_only, default=str)}\n\n"
            f"USER QUESTION: {question}\n\n"
            f"Reply ONLY with the JSON object."
        )
        result = await single_shot_json(system=system, user=user, phase=self.name)
        log.info(
            "nl_parser ✓ | allowed=%s intent=%s target=%s reason=%s",
            result.get("allowed"), result.get("intent"), result.get("target_columns"),
            trunc(result.get("reason"), 200),
        )
        return result
