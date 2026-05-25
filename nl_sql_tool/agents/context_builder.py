import json
import re
from utils.llm_client import chat
from utils.mcp_client import call_tool
from utils.context_io import load_context, save_context

_SYSTEM_PROMPT = """\
You are a senior data analyst. Your job is to deeply understand a
database table by inspecting its schema and sample data, then produce
a rich semantic summary that will later help an LLM translate natural
language questions into accurate SQL.

Use the ReAct pattern strictly. At each step emit exactly one of:

  Thought: <your reasoning about what to do next>
  Action: execute_sql | {"query": "<SQL>"}

Or, when done:

  Thought: FINAL: <your complete conclusion>
  Result: <valid JSON matching the required output schema>

The only available action is execute_sql. Use it for all DB access.

Step 1 — Fetch the exact column schema (always do this first):
  Action: execute_sql | {"query": "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '<table_name>' ORDER BY ordinal_position"}

Step 2 — Fetch sample rows:
  Action: execute_sql | {"query": "SELECT * FROM <table_name> LIMIT 5"}

Step 3 — Emit FINAL Result with the required JSON.

Rules:
- Use information_schema.columns for column types and nullability — never guess them.
- Do not call get_table_schema or list_tables — they are not available.
- Every column returned by information_schema.columns must appear in the output schema array.
- Every column must appear in the semantic_summary.
- The semantic_summary must be written in plain English a business analyst could understand.
- Do not fabricate sample data — read it from execute_sql.\
"""


class ContextBuilderError(Exception):
    pass


async def build_context(table_name: str) -> dict:
    """
    Runs the ReAct loop to build schema, sample_rows, and semantic_summary.
    Returns the full updated context dict (merged with existing context.json).
    Skips if context.json already has all three keys for the current table.
    """
    ctx = load_context() or {}

    # Cache check
    if (
        ctx.get("table_name") == table_name
        and ctx.get("schema")
        and ctx.get("sample_rows")
        and ctx.get("semantic_summary")
    ):
        print("[context_builder] Cache hit — skipping rebuild.")
        return ctx

    user_msg = f"Analyse the table named: {table_name}\nProduce the required context JSON."
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for iteration in range(6):
        response_text = chat(messages, max_tokens=4000, temperature=0.2)
        print(f"[context_builder] iter {iteration}: {response_text[:200]}")
        messages.append({"role": "assistant", "content": response_text})

        if "FINAL:" in response_text and "Result:" in response_text:
            result_json = _extract_result_json(response_text)
            ctx.update(result_json)
            ctx["table_name"] = table_name
            save_context(ctx)
            return ctx

        # Parse and dispatch action
        observation = await _dispatch_action(response_text)
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    raise ContextBuilderError("Context builder did not converge in 6 iterations")


def _extract_result_json(text: str) -> dict:
    """Extracts the JSON block after 'Result:' in the LLM response."""
    after_result = text.split("Result:", 1)[1].strip()
    # Find the outermost JSON object
    match = re.search(r"\{.*\}", after_result, re.DOTALL)
    if not match:
        raise ContextBuilderError(f"Could not find JSON in Result block: {after_result[:300]}")
    return json.loads(match.group())


async def _dispatch_action(response_text: str) -> str:
    """Parses 'Action: execute_sql | {"query": "..."}' and calls the MCP tool."""
    match = re.search(r"Action:\s*(\w+)\s*\|\s*(\{.*?\})", response_text, re.DOTALL)
    if not match:
        return (
            "No valid Action found. The only available action is:\n"
            "  Action: execute_sql | {\"query\": \"<SQL>\"}\n"
            "Use information_schema.columns to get schema, then SELECT * LIMIT 5 for samples."
        )

    tool_name = match.group(1).strip()
    raw_args = match.group(2).strip()

    if tool_name != "execute_sql":
        return (
            f"Tool '{tool_name}' is not available. "
            "The only available action is execute_sql. "
            "Use information_schema.columns to fetch schema instead of get_table_schema."
        )

    try:
        arguments = json.loads(raw_args)
    except json.JSONDecodeError as e:
        return f"Could not parse action arguments as JSON: {e}"

    try:
        result = await call_tool("execute_sql", arguments)
        return str(result)
    except Exception as e:
        return f"execute_sql error: {e}"
