import json
import re
from utils.llm_client import chat
from utils.mcp_client import call_tool, TableNotFoundError
from utils.context_io import load_context, save_context
from utils.row_parser import parse_rows

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

Step 2 — Fetch sample rows (read them, use them to write the summary — do NOT include them in your Result JSON):
  Action: execute_sql | {"query": "SELECT * FROM <table_name> LIMIT 5"}

Step 3 — Emit FINAL Result with the required JSON.

Rules:
- Use information_schema.columns for column types and nullability — never guess them.
- Do not call get_table_schema or list_tables — they are not available.
- Every column returned by information_schema.columns MUST appear in the output columns array.
- Use ONLY the exact column_name values returned by information_schema.columns — do not invent, shorten, alias, or paraphrase column names.
- Every column must be described in the semantic_summary.
- The semantic_summary must be written in plain English a business analyst could understand.
- Do not fabricate sample data — read it from execute_sql.
- Do NOT include sample_rows in the Result JSON — they are captured separately from the raw MCP response.

Required JSON output format (return ONLY this structure after Result:):
{
  "columns": [
    {"name": "<exact column_name from information_schema>", "type": "<data_type>", "nullable": <true|false>},
    ...
  ],
  "semantic_summary": "<plain English description covering all columns and table purpose>"
}\
"""


class ContextBuilderError(Exception):
    pass


async def build_context(table_name: str) -> dict:
    """
    Runs the ReAct loop to build columns, sample_rows, and semantic_summary.
    - columns and semantic_summary come from the LLM's final JSON.
    - sample_rows are captured directly from the MCP SELECT response (not via LLM serialization).
    Returns the full updated context dict (merged with existing context.json).
    Skips rebuild if context already has columns and semantic_summary for the current table.
    """
    ctx = load_context() or {}

    # Cache check — correct keys matching context.json structure
    if (
        ctx.get("table_name") == table_name
        and ctx.get("columns")
        and ctx.get("semantic_summary")
    ):
        print(f"[context_builder] Cache hit for table '{table_name}' — skipping rebuild.")
        return ctx

    print(f"[context_builder] Building context for table '{table_name}'...")

    # Capture sample rows directly from the MCP response as they come in
    _captured_sample_rows: list[dict] = []

    user_msg = f"Analyse the table named: {table_name}\nProduce the required context JSON."
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for iteration in range(6):
        print(f"[context_builder] --- Iteration {iteration} ---")
        response_text = chat(messages, max_tokens=4000, temperature=0.2)
        print(f"[context_builder] LLM response (iter {iteration}):\n{response_text}")
        messages.append({"role": "assistant", "content": response_text})

        if "FINAL:" in response_text and "Result:" in response_text:
            print(f"[context_builder] FINAL result detected at iteration {iteration}.")
            result_json = _extract_result_json(response_text)

            if not result_json.get("columns"):
                raise ContextBuilderError(
                    f"LLM result missing 'columns' key or returned empty columns array. "
                    f"Got keys: {list(result_json.keys())}"
                )

            print(
                f"[context_builder] Extracted {len(result_json['columns'])} columns."
            )
            print(
                f"[context_builder] Column names: "
                f"{[c['name'] for c in result_json['columns']]}"
            )
            print(
                f"[context_builder] Sample rows captured from MCP: "
                f"{len(_captured_sample_rows)}"
            )

            ctx.update(result_json)
            ctx["table_name"] = table_name
            # Store sample rows captured directly from MCP (not from LLM JSON)
            if _captured_sample_rows:
                ctx["sample_rows"] = _captured_sample_rows
            save_context(ctx)
            print(f"[context_builder] Context saved to context.json.")
            return ctx

        # Parse and dispatch action; capture sample rows if this is a SELECT * query
        observation, sample_rows = await _dispatch_action(response_text, table_name)
        if sample_rows:
            _captured_sample_rows = sample_rows
            print(f"[context_builder] Captured {len(sample_rows)} sample rows from MCP.")
        print(f"[context_builder] Observation (iter {iteration}): {observation}")
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    raise ContextBuilderError("Context builder did not converge in 6 iterations")


def _extract_result_json(text: str) -> dict:
    """
    Extracts the JSON object after 'Result:' using raw_decode so it stops
    at the first complete object and ignores any trailing text or code fences.
    """
    after_result = text.split("Result:", 1)[1].strip()

    # Strip optional markdown code fences
    after_result = re.sub(r"^```(?:json)?\s*\n?", "", after_result)
    after_result = re.sub(r"\n?```\s*$", "", after_result)

    start = after_result.find("{")
    if start == -1:
        raise ContextBuilderError(
            f"No JSON object found in Result block: {after_result[:300]}"
        )

    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(after_result, start)
        return obj
    except json.JSONDecodeError as e:
        raise ContextBuilderError(
            f"JSON parse error in Result block: {e}\n"
            f"Snippet around error: {after_result[max(0, start + e.pos - 100): start + e.pos + 100]!r}"
        )


async def _dispatch_action(response_text: str, table_name: str = "") -> tuple[str, list[dict]]:
    """
    Parses 'Action: execute_sql | {"query": "..."}' and calls the MCP tool.
    Returns (observation_text, sample_rows).
    sample_rows is non-empty only when the query looks like a SELECT * sample fetch.
    """
    match = re.search(r"Action:\s*(\w+)\s*\|\s*(\{.*?\})", response_text, re.DOTALL)
    if not match:
        return (
            "No valid Action found. The only available action is:\n"
            "  Action: execute_sql | {\"query\": \"<SQL>\"}\n"
            "Use information_schema.columns to get schema, then SELECT * LIMIT 5 for samples.",
            [],
        )

    tool_name = match.group(1).strip()
    raw_args = match.group(2).strip()

    if tool_name != "execute_sql":
        return (
            f"Tool '{tool_name}' is not available. "
            "The only available action is execute_sql. "
            "Use information_schema.columns to fetch schema instead of get_table_schema.",
            [],
        )

    try:
        arguments = json.loads(raw_args)
    except json.JSONDecodeError as e:
        return f"Could not parse action arguments as JSON: {e}", []

    print(f"[context_builder] Calling MCP execute_sql: {arguments}")
    try:
        raw_result = await call_tool("execute_sql", arguments)
        print(f"[context_builder] MCP execute_sql result: {raw_result}")


        # Detect sample-row queries (SELECT * FROM <table> LIMIT N) and capture rows
        query_lower = arguments.get("query", "").lower()
        sample_rows: list[dict] = []
        is_sample_query = (
            table_name
            and table_name.lower() in query_lower
            and "select *" in query_lower
            and "information_schema" not in query_lower
        )
        if is_sample_query:
            sample_rows = parse_rows(raw_result)
            print(f"[context_builder] Parsed {len(sample_rows)} rows from sample query.")

        return str(raw_result), sample_rows
    except TableNotFoundError:
        # Table was dropped — propagate immediately, do not return an observation string
        raise
    except Exception as e:
        print(f"[context_builder] MCP execute_sql error: {e}")
        return f"execute_sql error: {e}", []
