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
- ONLY reference column names that appear in the context "columns" array — never invent,
  guess, or alias column names. If unsure, call verify_columns first.
- You MAY (and should) select multiple columns when needed to fully answer the question.
- Use ANSI SQL compatible with PostgreSQL.
- Always include LIMIT 100 unless the user explicitly asks for all rows.
- Use CTEs only when aggregating over a subquery.
- Use exact column names from the schema — no aliases unless necessary.
- IMPORTANT: If a column name contains any special character (/, -, space, or anything
  other than letters, digits, and underscores), you MUST wrap it in double quotes in the SQL.
  Example: SELECT "area/location", "features/category" FROM restraunts
  Failure to quote such names will cause a SQL syntax error.
- Prefer simple WHERE clauses; avoid LIKE unless the user implies a partial text match.
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
    # Build an explicit column list to reinforce the no-hallucination constraint
    columns = context.get("columns", [])
    column_names = [col["name"] for col in columns]

    # Identify columns that need double-quoting in SQL (contain non-word characters)
    needs_quoting = [
        name for name in column_names
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name)
    ]
    quoting_note = ""
    if needs_quoting:
        examples = ", ".join(f'"{n}"' for n in needs_quoting)
        quoting_note = (
            f"\nCRITICAL — These columns contain special characters and MUST be "
            f"double-quoted every time they appear in SQL:\n"
            f"  {examples}\n"
            f"Wrong:  SELECT area/location  ← syntax error\n"
            f'Correct: SELECT "area/location"  ← required\n'
        )

    user_msg = (
        f"Context:\n{json.dumps(context, indent=2)}\n\n"
        f"IMPORTANT — Valid column names for this table (use ONLY these, no others):\n"
        f"{json.dumps(column_names)}"
        f"{quoting_note}\n\n"
        f"User question: {user_query}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    print(f"[nl_parser] Starting parse for query: {user_query!r}")
    print(f"[nl_parser] Table: {context.get('table_name')}, columns available: {len(column_names)}")

    for iteration in range(5):
        print(f"[nl_parser] --- Iteration {iteration} ---")
        response_text = chat(messages, max_tokens=2000, temperature=0.2)
        print(f"[nl_parser] LLM response (iter {iteration}):\n{response_text}")
        messages.append({"role": "assistant", "content": response_text})

        if "FINAL:" in response_text and "Result:" in response_text:
            result = _extract_result_json(response_text)
            print(f"[nl_parser] FINAL result: intent={result.get('intent')!r}, sql={result.get('sql')!r}")
            return result

        observation = _resolve_virtual_action(response_text, context)
        print(f"[nl_parser] Observation (iter {iteration}): {observation}")
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
        # Return columns array — same key as stored in context.json
        return json.dumps(context.get("columns", []))
    elif action == "read_sample_rows":
        return json.dumps(context.get("sample_rows", []))
    elif action == "read_semantic_summary":
        return context.get("semantic_summary", "No summary available.")
    elif action == "draft_sql":
        return "Draft noted. Proceed to verify_columns."
    elif action == "verify_columns":
        # Column names stored under 'name' key in the columns array
        known = {col["name"].lower() for col in context.get("columns", [])}
        raw_candidates = [c.strip() for c in argument.split(",") if c.strip()]
        # Strip surrounding double-quotes that the LLM adds for special-char columns
        # e.g. '"area/location"' → 'area/location'
        candidates = [c.strip('"').lower() for c in raw_candidates]
        unknown = [c for c in candidates if c and c not in known]
        if unknown:
            valid_list = sorted(known)
            return (
                f"Unknown columns: {unknown}. "
                f"Valid column names are: {valid_list}"
            )
        return "OK — all columns verified."
    else:
        return (
            f"Unknown virtual action '{action}'. "
            "Use one of: read_schema, read_sample_rows, read_semantic_summary, "
            "draft_sql, verify_columns."
        )
