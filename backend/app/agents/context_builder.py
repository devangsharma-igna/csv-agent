from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
from typing import Any

from ..mcp_client import mcp
from .base import load_prompt, single_shot_json

log = logging.getLogger("igna.agent.context_builder")

_SCHEMA_SQL = """\
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = '{schema}' AND table_name = '{table}'
ORDER BY ordinal_position"""


# ---------------------------------------------------------------------------
# Pure-Python helpers — no LLM involved
# ---------------------------------------------------------------------------

def _build_stats_sql(bare_table: str, schema_rows: list[dict]) -> str:
    """Build one-pass aggregation CTE covering every column."""
    parts = ["COUNT(*) AS _total_rows"]
    for row in schema_rows:
        col = row["column_name"]
        q = f'"{col}"'
        parts.append(f'COUNT(DISTINCT {q}) AS "{col}_distinct"')
        parts.append(
            f'ROUND(100.0 * SUM(CASE WHEN {q} IS NULL THEN 1 ELSE 0 END)'
            f' / NULLIF(COUNT(*), 0), 2) AS "{col}_null_pct"'
        )
    cols_sql = ",\n  ".join(parts)
    return f'SELECT\n  {cols_sql}\nFROM "{bare_table}"'


def _parse_stats(
    schema_rows: list[dict],
    stats_row: dict,
) -> tuple[int, dict[str, dict]]:
    """Parse the flat stats row into {col: {distinct, null_pct}} and total rows."""
    total = int(stats_row.get("_total_rows") or 0)
    per_col: dict[str, dict] = {}
    for row in schema_rows:
        col = row["column_name"]
        raw_distinct = stats_row.get(f"{col}_distinct")
        raw_null_pct = stats_row.get(f"{col}_null_pct")
        per_col[col] = {
            "distinct": int(raw_distinct) if raw_distinct is not None else None,
            "null_pct": float(raw_null_pct) if raw_null_pct is not None else 0.0,
        }
    return total, per_col


def _col_samples(col_name: str, sample_rows: list[dict], max_vals: int = 5) -> list:
    """Extract up to max_vals unique non-null sample values for a column."""
    seen: list = []
    for row in sample_rows:
        v = row.get(col_name)
        if v is not None and v not in seen:
            seen.append(v)
            if len(seen) >= max_vals:
                break
    return seen


def _quality_flags(columns: list[dict]) -> list[dict]:
    """Derive data-quality flags from column stats — no LLM needed."""
    flags = []
    for col in columns:
        null_pct = col.get("null_pct") or 0.0
        distinct = col.get("distinct")
        col_type = col.get("type", "")
        if null_pct > 50:
            flags.append({
                "column": col["name"],
                "issue": "high_nulls",
                "detail": f"{null_pct:.1f}% null values.",
            })
        if (
            distinct is not None
            and distinct <= 2
            and col_type in ("text", "character varying", "varchar")
        ):
            flags.append({
                "column": col["name"],
                "issue": "low_distinct",
                "detail": f"Only {distinct} distinct value(s) — likely a flag or boolean-style column.",
            })
    return flags


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ContextBuilder:
    """Profiles a table with zero LLM calls for mechanical work.

    Python handles: stats SQL generation, execution, parsing, quality flags.
    LLM handles: per-column semantic descriptions + PK inference only.

    Old flow  : schema fetch → LLM writes stats SQL + executes → LLM writes full JSON  (~136s for 46 cols)
    New flow  : parallel(schema, sample) → stats execute → LLM semantics only          (~10-12s for 46 cols)
    """

    name = "context_builder"

    async def build(self, table: str) -> dict[str, Any]:
        log.info("context_builder ▶ | table=%s", table)

        if "." in table:
            schema, bare_table = table.split(".", 1)
        else:
            schema, bare_table = "public", table

        # Step 1 — parallel: schema introspection + sample rows (independent queries)
        schema_rows, sample_rows = await asyncio.gather(
            mcp.execute_sql(_SCHEMA_SQL.format(schema=schema, table=bare_table)),
            mcp.execute_sql(f'SELECT * FROM "{bare_table}" LIMIT 8'),
        )
        log.debug("context_builder fetched | columns=%d sample_rows=%d", len(schema_rows), len(sample_rows))

        if not schema_rows:
            log.warning("context_builder: no columns found for table=%s — returning empty context", table)
            return _empty_context(table)

        # Step 2 — Python builds and executes the stats CTE
        stats_sql = _build_stats_sql(bare_table, schema_rows)
        stats_results = await mcp.execute_sql(stats_sql)
        stats_row = stats_results[0] if stats_results else {}
        total_rows, per_col = _parse_stats(schema_rows, stats_row)
        log.debug("context_builder stats parsed | total_rows=%d", total_rows)

        # Step 3 — LLM: semantic descriptions + PK only (compact input, small output)
        col_specs = [
            {
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "distinct": per_col[row["column_name"]]["distinct"],
                "null_pct": per_col[row["column_name"]]["null_pct"],
                "samples": _col_samples(row["column_name"], sample_rows),
            }
            for row in schema_rows
        ]
        system = load_prompt("context_builder")
        user = (
            f"TABLE: {bare_table}\n"
            f"ROW COUNT: {total_rows}\n"
            f"COLUMN SPECS:\n{json.dumps(col_specs, default=str)}\n\n"
            f"Reply ONLY with the JSON object."
        )
        llm_result = await single_shot_json(system=system, user=user, phase=self.name)

        semantics: dict[str, str] = llm_result.get("semantics") or {}
        pk: list[str] = llm_result.get("pk") or []

        # Step 4 — Python assembles the full context document
        columns = [
            {
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "null_pct": per_col[row["column_name"]]["null_pct"],
                "distinct": per_col[row["column_name"]]["distinct"],
                "semantic": semantics.get(row["column_name"], ""),
            }
            for row in schema_rows
        ]

        ctx: dict[str, Any] = {
            "table": table,
            "row_count": total_rows,
            "columns": columns,
            "sample_rows": sample_rows[:5],
            "pk": pk,
            "relationships": [],
            "data_quality_flags": _quality_flags(columns),
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        log.info(
            "context_builder ✓ | table=%s row_count=%d columns=%d pk=%s quality_flags=%d",
            table, total_rows, len(columns), pk, len(ctx["data_quality_flags"]),
        )
        return ctx


def _empty_context(table: str) -> dict[str, Any]:
    return {
        "table": table,
        "row_count": 0,
        "columns": [],
        "sample_rows": [],
        "pk": [],
        "relationships": [],
        "data_quality_flags": [{
            "column": "*",
            "issue": "table_missing",
            "detail": f"Table '{table}' does not exist or is inaccessible.",
        }],
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
