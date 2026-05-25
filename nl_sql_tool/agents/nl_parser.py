import json
import re

from utils.llm_client import chat

_SYSTEM_PROMPT = """\
You are a SQL expert working with a PostgreSQL database (Supabase).
You will receive a natural language query and a JSON context object
describing the target table.

Use the ReAct pattern strictly. At each step emit exactly one of:

  Thought: <your reasoning>
  Action: <virtual_action_name> | <argument>

Or, when done:

  Thought: FINAL: <your conclusion and confidence statement>
  Result: <valid JSON with keys: intent, sql>

Available virtual actions (no external calls — resolve from context):
  read_schema          | (no argument needed)
  read_sample_rows     | (no argument needed)
  read_semantic_summary| (no argument needed)
  draft_sql            | <your proposed SQL string>
  verify_columns       | <comma-separated list of column names used>

SQL rules:
- ALWAYS produce a valid SQL SELECT statement — never return null or an empty string for sql.
- If the user asks a general question ("describe the data", "what's in this table?",
  "show me the data"), return SELECT * FROM <table_name> LIMIT 10.
- Only reference columns that exist in the schema.
- Use ANSI SQL compatible with PostgreSQL.
- Always include LIMIT 100 unless the user explicitly asks for all rows.
- Use CTEs only when aggregating over a subquery.
- Use exact column names from the schema — no aliases unless necessary.
- Prefer simple WHERE clauses; avoid LIKE unless the user implies
  a partial text match.
- When the user asks for 'top N', use ORDER BY + LIMIT N.
- When filtering by date, use ISO 8601 format in the WHERE clause.\
"""


class NLParserError(Exception):
    pass


def parse_query(user_query: str, context: dict) -> dict:
    """
    Runs the ReAct loop (no live MCP calls) to produce:
      {"intent": "...", "sql": "SELECT ..."}
    """
    user_msg = f"Context:\n{json.dumps(context, indent=2)}\n\nUser question: {user_query}"
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for iteration in range(5):
        response_text = chat(messages, max_tokens=2000, temperature=0.2)
        print(f"[nl_parser] iter {iteration}: {response_text[:200]}")
        messages.append({"role": "assistant", "content": response_text})

        if "FINAL:" in response_text and "Result:" in response_text:
            return _extract_result_json(response_text)

        observation = _resolve_virtual_action(response_text, context)
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    raise NLParserError("NL parser did not converge in 5 iterations")


def _extract_result_json(text: str) -> dict:
    after_result = text.split("Result:", 1)[1].strip()
    match = re.search(r"\{.*\}", after_result, re.DOTALL)
    if not match:
        raise NLParserError(f"Could not find JSON in Result block: {after_result[:300]}")
    return json.loads(match.group())


def _resolve_virtual_action(response_text: str, context: dict) -> str:
    """Resolves virtual actions from the in-memory context dict."""
    match = re.search(r"Action:\s*(\w+)\s*\|?\s*(.*)", response_text)
    if not match:
        return "No valid Action found. Please emit a valid Action or FINAL Result."

    action = match.group(1).strip()
    argument = match.group(2).strip()

    if action == "read_schema":
        return json.dumps(context.get("schema", []))
    elif action == "read_sample_rows":
        return json.dumps(context.get("sample_rows", []))
    elif action == "read_semantic_summary":
        return context.get("semantic_summary", "No summary available.")
    elif action == "draft_sql":
        return "Draft noted. Proceed to verify_columns."
    elif action == "verify_columns":
        known = {col["column"].lower() for col in context.get("schema", [])}
        candidates = [c.strip().lower() for c in argument.split(",") if c.strip()]
        unknown = [c for c in candidates if c and c not in known]
        if unknown:
            return f"Unknown columns: {unknown}"
        return "OK"
    else:
        return f"Unknown virtual action '{action}'. Use one of: read_schema, read_sample_rows, read_semantic_summary, draft_sql, verify_columns."
