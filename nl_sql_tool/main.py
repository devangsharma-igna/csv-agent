from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import sys
import os

# Ensure project root is on the path so agents/utils are importable
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

from utils.context_io import load_context, save_context
from utils.mcp_client import call_tool, close_mcp_session, MCPToolError
from utils.llm_client import chat
from agents.context_builder import build_context, ContextBuilderError
from agents.nl_parser import parse_query, NLParserError, _SYSTEM_PROMPT, _extract_result_json
from agents.sql_validator import validate_sql
from agents.executor import execute_query


# ──────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────

async def run_pipeline(user_query: str) -> dict:
    """
    Runs all four phases in order.
    Returns a result dict with keys: intent, sql, rows (or error).
    """
    result = {"intent": "", "sql": "", "rows": None, "error": None}
    try:
        ctx = load_context() or {}
        table_name = ctx.get("table_name", "")

        # Phase 1 — Context Builder
        ctx = await build_context(table_name)

        # Phase 2 — NL Parser
        parsed = parse_query(user_query, ctx)
        result["intent"] = parsed.get("intent", "")
        # parsed["sql"] can be None (JSON null) when the LLM returns it explicitly;
        # `or ""` converts both None and "" to a falsy empty string for the guard below.
        result["sql"] = parsed.get("sql") or ""

        # Guard: parser returned no SQL
        if not result["sql"]:
            result["error"] = (
                "The parser could not produce a SQL query for this question. "
                "Try rephrasing as a specific data request "
                "(e.g. 'Show me the top 10 rows' or 'Count rows by department')."
            )
            return result

        # Phase 3 — SQL Validator with up to 2 correction retries
        known_columns = [col["column"] for col in ctx.get("schema", [])]
        retries = 0
        while retries <= 2:
            valid, reason = validate_sql(result["sql"], ctx)
            if valid:
                break
            if retries == 2:
                result["error"] = (
                    "Could not generate valid SQL for this query. Please rephrase."
                )
                return result

            print(f"[main] SQL validation failed (attempt {retries + 1}): {reason}")
            correction_messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Context:\n{json.dumps(ctx, indent=2)}\n\nUser question: {user_query}",
                },
                {
                    "role": "assistant",
                    "content": f"Thought: FINAL: Generating SQL.\nResult: {json.dumps(parsed)}",
                },
                {
                    "role": "user",
                    "content": (
                        f"Observation: SQL validation failed — {reason}. "
                        f"Revise the SQL to use only these valid columns: {known_columns}. "
                        "Re-emit your FINAL Result."
                    ),
                },
            ]
            correction_response = chat(correction_messages, max_tokens=2000, temperature=0.2)
            print(f"[main] Correction response: {correction_response[:200]}")
            if "Result:" in correction_response:
                try:
                    parsed = _extract_result_json(correction_response)
                    result["intent"] = parsed.get("intent", result["intent"])
                    result["sql"] = parsed.get("sql", result["sql"])
                except Exception:
                    pass
            retries += 1

        # Phase 4 — Executor
        rows = await execute_query(result["sql"])
        if isinstance(rows, dict) and "error" in rows:
            result["error"] = rows["error"]
        else:
            result["rows"] = rows

    except ContextBuilderError as e:
        result["error"] = f"Context builder failed: {e}"
    except NLParserError as e:
        result["error"] = f"Query parser failed: {e}"
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"
    finally:
        await close_mcp_session()

    return result


# ──────────────────────────────────────────────
# Phase 0 — MCP-based table verification
# ──────────────────────────────────────────────

async def _verify_table_exists_mcp(table_name: str) -> tuple[bool, str]:
    """
    Calls execute_sql via MCP to confirm the table exists in the public schema.
    Returns (True, "") on success, (False, error_message) on failure.
    All DB communication goes through MCP by design.
    """
    query = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        f"AND table_name = '{table_name}' LIMIT 1"
    )
    try:
        raw = await call_tool("execute_sql", {"query": query})
        text = str(raw)
        print(f"[phase0] MCP verify response: {text}")
        exists = table_name.lower() in text.lower()
        return exists, ""
    except MCPToolError as e:
        err = str(e)
        print(f"[phase0] MCP error: {err}")
        if any(w in err.lower() for w in ("authoriz", "unauthorized", "401", "forbidden", "403")):
            return False, (
                "MCP authorization failed. "
                "SUPABASE_ACCESS_TOKEN must be a Personal Access Token (PAT) — "
                "not the service role key. "
                "Generate one at: https://supabase.com/dashboard/account/tokens"
            )
        return False, f"MCP error during table verification: {err}"
    except Exception as e:
        return False, f"Unexpected error during table verification: {e}"
    finally:
        await close_mcp_session()


# ──────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────

st.set_page_config(page_title="NL → SQL Query Tool", layout="wide")

# Sidebar
with st.sidebar:
    st.header("Supabase Table")
    ctx = load_context()
    current_table = ctx.get("table_name", "") if ctx else ""

    if current_table:
        st.success(f"Active table: **{current_table}**")
    else:
        st.info("No table configured.")

    if current_table and st.button("Change table"):
        ctx = load_context() or {}
        ctx.pop("table_name", None)
        save_context(ctx)
        st.rerun()

    if current_table and st.button("Rebuild context"):
        ctx = load_context() or {}
        for key in ("schema", "sample_rows", "semantic_summary"):
            ctx.pop(key, None)
        save_context(ctx)
        st.success("Context cleared. It will rebuild on your next query.")

# Main area
st.title("NL → SQL Query Tool")

# Phase 0 — Table check
ctx = load_context()
table_name = ctx.get("table_name", "") if ctx else ""

if not table_name:
    st.warning(
        "No table found. Please import your CSV into Supabase first, "
        "then enter the table name below."
    )
    with st.form("table_form"):
        table_input = st.text_input("Supabase table name")
        submitted = st.form_submit_button("Confirm table")

    if submitted and table_input.strip():
        with st.spinner("Verifying table via MCP..."):
            found, err_msg = asyncio.run(_verify_table_exists_mcp(table_input.strip()))
        if found:
            save_context({"table_name": table_input.strip()})
            st.success(f"Table '{table_input.strip()}' confirmed.")
            st.rerun()
        elif err_msg:
            st.error(err_msg)
        else:
            st.error(
                f"Table '{table_input.strip()}' not found in Supabase. "
                "Check the name and try again."
            )
    st.stop()

# Query input
user_query = st.text_input(
    "Ask a question about your data",
    placeholder='e.g. "Show me the top 10 customers by revenue"',
)

run_btn = st.button("Run query", type="primary")

if run_btn and user_query.strip():
    pipeline_result = None

    status_placeholder = st.empty()
    with status_placeholder.container():
        st.write(f"✓ Table verified: **{table_name}**")

        ctx = load_context() or {}
        context_cached = bool(
            ctx.get("schema") and ctx.get("sample_rows") and ctx.get("semantic_summary")
        )
        if context_cached:
            st.write("✓ Context loaded (cached)")
        else:
            st.write("⟳ Building context (first run or rebuild)...")

        st.write("⟳ Parsing query...")
        st.write("⟳ Validating SQL...")
        st.write("⟳ Executing...")

    with st.spinner("Running pipeline..."):
        pipeline_result = asyncio.run(run_pipeline(user_query.strip()))

    status_placeholder.empty()
    with status_placeholder.container():
        st.write(f"✓ Table verified: **{table_name}**")
        st.write("✓ Context ready")
        st.write("✓ Query parsed")
        if not pipeline_result.get("error"):
            st.write("✓ SQL validated")
            st.write("✓ Executed")
        else:
            st.write("✗ Pipeline error")

    # Results
    if pipeline_result.get("error"):
        st.error(pipeline_result["error"])
    else:
        rows = pipeline_result.get("rows", [])
        if rows:
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
            st.caption(f"Rows returned: {len(rows)}")
        else:
            st.info("Query executed successfully but returned no rows.")

    # Debug expander
    with st.expander("Debug — pipeline details"):
        st.subheader("SQL executed")
        st.code(pipeline_result.get("sql", ""), language="sql")
        st.subheader("Detected intent")
        st.write(pipeline_result.get("intent", ""))
        st.subheader("context.json")
        st.json(load_context())

elif run_btn:
    st.warning("Please enter a question before running.")
