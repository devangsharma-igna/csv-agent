from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from .base import load_prompt, single_shot_json

log = logging.getLogger("igna.agent.nl_parser")

# Common English stop-words that add no semantic signal for column matching.
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "not", "no", "nor",
    "so", "yet", "both", "either", "neither", "each", "every", "all", "any",
    "few", "more", "most", "other", "some", "such", "than", "too", "very",
    "just", "how", "what", "which", "who", "whom", "whose", "when", "where",
    "why", "me", "my", "we", "our", "you", "your", "it", "its", "they",
    "them", "their", "this", "that", "these", "those", "i", "s", "t",
    "many", "much", "give", "show", "tell", "find", "get", "list", "count",
    "top", "bottom", "first", "last", "total", "sum", "average", "avg",
    "min", "max", "number", "per", "between", "across", "over", "under",
    "above", "below", "about", "there", "here", "into", "out", "up", "down",
    "if", "then", "else", "after", "before", "during", "while",
})


def _question_keywords(question: str) -> set[str]:
    """Extract meaningful lowercase words from the user's question."""
    tokens = re.findall(r"[a-zA-Z0-9]+", question.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) >= 2}


def _column_score(col: dict[str, Any], keywords: set[str]) -> int:
    """Return keyword-hit count for a column (name + semantic)."""
    haystack = (
        re.sub(r"[^a-z0-9 ]+", " ", (col.get("name") or "").lower()) + " "
        + (col.get("semantic") or "").lower()
    )
    return sum(1 for kw in keywords if kw in haystack)


def _filter_columns(
    columns: list[dict[str, Any]],
    keywords: set[str],
    max_cols: int,
) -> list[dict[str, Any]]:
    """Keep the most-relevant columns for wide tables.

    For tables with ≤ max_cols columns this is a no-op.
    For wider tables, score each column by keyword overlap and return the
    top max_cols. We always keep at least 5 columns even if nothing scores,
    so narrow-miss questions still get a fair scope check.
    """
    if len(columns) <= max_cols:
        return columns
    scored = sorted(columns, key=lambda c: -_column_score(c, keywords))
    kept = scored[:max_cols]
    # Preserve original column order so the prompt reads naturally.
    kept_names = {c["name"] for c in kept}
    filtered = [c for c in columns if c["name"] in kept_names]
    log.info(
        "nl_parser column pre-filter | total=%d kept=%d max=%d keywords=%s",
        len(columns), len(filtered), max_cols, trunc(sorted(keywords), 200),
    )
    return filtered


class NLParser:
    """Agent 2 — intent + scope gate. No tools; pure classification."""

    name = "nl_parser"

    async def parse(self, *, question: str, context: dict[str, Any]) -> dict[str, Any]:
        log.info("nl_parser ▶ | table=%s question=%s", context.get("table"), trunc(question, 200))
        # Entry gate covered by orchestrator pre_parser. Single-shot, no MCP tools
        # in this agent, so no in-loop gate needed either.
        system = load_prompt("nl_parser")

        # For wide tables, filter to columns most relevant to the question so
        # the prompt stays under ~1 900 tokens even for 50-column tables.
        all_columns = context.get("columns", [])
        keywords = _question_keywords(question)
        relevant_columns = _filter_columns(all_columns, keywords, settings.NL_PARSER_MAX_COLUMNS)

        schema_only = {
            "table": context.get("table"),
            "columns": [
                {
                    "name": c["name"],
                    "type": c.get("type"),
                    "semantic": c.get("semantic"),
                    "nullable": c.get("nullable"),
                }
                for c in relevant_columns
            ],
        }

        # Mention if we trimmed so the LLM knows it may not see every column.
        trimmed_note = ""
        if len(relevant_columns) < len(all_columns):
            trimmed_note = (
                f"\n(Note: showing {len(relevant_columns)} of {len(all_columns)} columns "
                f"most relevant to this question. If you cannot determine relevance from "
                f"these columns, answer allowed=false with reason 'insufficient column context'.)\n"
            )

        user = (
            f"TABLE CONTEXT:\n{json.dumps(schema_only, default=str)}\n"
            f"{trimmed_note}\n"
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
