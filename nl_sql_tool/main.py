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
from utils.figure_builder import build_figures
from agents.context_builder import build_context, ContextBuilderError
from agents.nl_parser import parse_query, NLParserError, _SYSTEM_PROMPT, _extract_result_json
from agents.nl_responder import generate_nl_response
from agents.sql_validator import validate_sql
from agents.executor import execute_query


# ──────────────────────────────────────────────
# Shared table existence check (circuit-breaker)
# ──────────────────────────────────────────────

async def _check_table_exists(table_name: str) -> tuple[bool, str]:
    """
    Verifies the table exists in the public schema via MCP.
    Used as a circuit-breaker at the start of every pipeline run
    and during initial table registration.
    Returns (True, "") if found, (False, error_message) otherwise.
    """
    query = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        f"AND table_name = '{table_name}' LIMIT 1"
    )
    print(f"[circuit_breaker] Checking table existence: '{table_name}'")
    try:
        raw = await call_tool("execute_sql", {"query": query})
        text = str(raw)
        print(f"[circuit_breaker] MCP response: {text}")
        exists = table_name.lower() in text.lower()
        if exists:
            print(f"[circuit_breaker] Table '{table_name}' confirmed.")
        else:
            print(f"[circuit_breaker] Table '{table_name}' NOT found in response.")
        return exists, ""
    except MCPToolError as e:
        err = str(e)
        print(f"[circuit_breaker] MCPToolError: {err}")
        if any(w in err.lower() for w in ("authoriz", "unauthorized", "401", "forbidden", "403")):
            return False, (
                "MCP authorization failed. "
                "SUPABASE_ACCESS_TOKEN must be a Personal Access Token (PAT) — "
                "not the service role key. "
                "Generate one at: https://supabase.com/dashboard/account/tokens"
            )
        return False, f"MCP error during table check: {err}"
    except Exception as e:
        print(f"[circuit_breaker] Unexpected error: {e}")
        return False, f"Unexpected error during table check: {e}"


# ──────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────

async def run_pipeline(user_query: str) -> dict:
    """
    Runs all phases in order. Table existence is checked first as a circuit-breaker.
    Context build is skipped entirely if already cached — it only runs when there is
    no cached context (first run) or after the user clicks 'Rebuild context'.
    Returns a result dict with keys: intent, sql, rows (or error).
    """
    result = {"intent": "", "sql": "", "rows": None, "error": None}
    try:
        ctx = load_context() or {}
        table_name = ctx.get("table_name", "")

        if not table_name:
            result["error"] = "No table configured. Please set a table first."
            return result

        # ── Circuit-breaker: verify table exists before doing any work ──
        print(f"[pipeline] Phase 0 — table existence check for '{table_name}'")
        found, err_msg = await _check_table_exists(table_name)
        if not found:
            result["error"] = err_msg or (
                f"CSV '{table_name}' no longer exists. "
                "Please re-import your CSV."
            )
            return result
        print(f"[pipeline] Phase 0 passed — table '{table_name}' exists.")

        # ── Phase 1 — Context Builder (only if not already cached) ──
        context_ready = bool(
            ctx.get("table_name") == table_name
            and ctx.get("columns")
            and ctx.get("semantic_summary")
        )
        if context_ready:
            print(f"[pipeline] Phase 1 — context already cached, skipping build.")
        else:
            print(f"[pipeline] Phase 1 — no cached context, building now...")
            ctx = await build_context(table_name)
            print(f"[pipeline] Phase 1 complete — "
                  f"{len(ctx.get('columns', []))} columns loaded.")

        # ── Phase 2 — NL Parser ──
        print(f"[pipeline] Phase 2 — parsing query: {user_query!r}")
        parsed = parse_query(user_query, ctx)
        result["intent"] = parsed.get("intent", "")
        # parsed["sql"] can be None (JSON null); `or ""` normalises to falsy empty string
        result["sql"] = parsed.get("sql") or ""
        print(f"[pipeline] Phase 2 complete — intent={result['intent']!r}, sql={result['sql']!r}")

        if not result["sql"]:
            result["error"] = (
                "The parser could not produce a SQL query for this question. "
                "Try rephrasing as a specific data request "
                "(e.g. 'Show me the top 10 rows' or 'Count rows by department')."
            )
            return result

        # ── Phase 3 — SQL Validator with up to 2 correction retries ──
        print(f"[pipeline] Phase 3 — validating SQL...")
        known_columns = [col["name"] for col in ctx.get("columns", [])]
        retries = 0
        while retries <= 2:
            valid, reason = validate_sql(result["sql"], ctx)
            if valid:
                print(f"[pipeline] Phase 3 passed on attempt {retries + 1}.")
                break
            if retries == 2:
                print(f"[pipeline] Phase 3 FAILED after 2 correction attempts: {reason}")
                result["error"] = (
                    "Could not generate valid SQL for this query. Please rephrase."
                )
                return result

            print(f"[pipeline] Phase 3 validation failed (attempt {retries + 1}): {reason}")
            correction_messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Context:\n{json.dumps(ctx, indent=2)}\n\n"
                        f"IMPORTANT — Valid column names (use ONLY these):\n"
                        f"{json.dumps(known_columns)}\n\n"
                        f"User question: {user_query}"
                    ),
                },
                {
                    "role": "assistant",
                    "content": f"Thought: FINAL: Generating SQL.\nResult: {json.dumps(parsed)}",
                },
                {
                    "role": "user",
                    "content": (
                        f"Observation: SQL validation failed — {reason}. "
                        f"Revise the SQL to use ONLY these valid columns: {known_columns}. "
                        "Re-emit your FINAL Result."
                    ),
                },
            ]
            correction_response = chat(correction_messages, max_tokens=2000, temperature=0.2)
            print(f"[pipeline] Phase 3 correction response:\n{correction_response}")
            if "Result:" in correction_response:
                try:
                    parsed = _extract_result_json(correction_response)
                    result["intent"] = parsed.get("intent", result["intent"])
                    result["sql"] = parsed.get("sql", result["sql"])
                    print(f"[pipeline] Phase 3 corrected SQL: {result['sql']!r}")
                except Exception as ex:
                    print(f"[pipeline] Phase 3 could not extract corrected JSON: {ex}")
            retries += 1

        # ── Phase 4 — Executor ──
        print(f"[pipeline] Phase 4 — executing SQL: {result['sql']!r}")
        rows = await execute_query(result["sql"])
        if isinstance(rows, dict) and "error" in rows:
            print(f"[pipeline] Phase 4 error: {rows['error']}")
            result["error"] = rows["error"]
        else:
            print(f"[pipeline] Phase 4 complete — {len(rows)} rows returned.")
            result["rows"] = rows

    except ContextBuilderError as e:
        print(f"[pipeline] ContextBuilderError: {e}")
        result["error"] = f"Context builder failed: {e}"
    except NLParserError as e:
        print(f"[pipeline] NLParserError: {e}")
        result["error"] = f"Query parser failed: {e}"
    except Exception as e:
        print(f"[pipeline] Unexpected error: {e}")
        result["error"] = f"Unexpected error: {e}"
    finally:
        await close_mcp_session()

    return result


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

    # Show context build status
    context_built = bool(
        ctx and ctx.get("columns") and ctx.get("semantic_summary")
    ) if ctx else False
    if current_table:
        if context_built:
            col_count = len(ctx.get("columns", []))
            st.success(f"Context ready ({col_count} columns)")
        else:
            st.warning("Context not built yet — will build on first query.")

    if current_table and st.button("Change table"):
        ctx = load_context() or {}
        ctx.pop("table_name", None)
        save_context(ctx)
        st.rerun()

    if current_table and st.button("Rebuild context"):
        ctx = load_context() or {}
        # Clear the correct keys that hold context data
        for key in ("columns", "sample_rows", "semantic_summary"):
            ctx.pop(key, None)
        save_context(ctx)
        st.success("Context cleared. It will rebuild on your next query.")
        st.rerun()

# Main area
st.title("NL → SQL Query Tool")

# Phase 0 — Table setup check
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
            found, err_msg = asyncio.run(_check_table_exists(table_input.strip()))
            asyncio.run(close_mcp_session())
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

# Response format selector
response_format = st.radio(
    "Response format",
    options=["NL", "Figures", "NL + Figures"],
    horizontal=True,
    help=(
        "NL — plain-English answer  |  "
        "Figures — auto-detected charts  |  "
        "NL + Figures — both"
    ),
)

run_btn = st.button("Run query", type="primary")

if run_btn and user_query.strip():
    pipeline_result = None

    # Reload context to get latest state
    ctx = load_context() or {}
    context_built = bool(ctx.get("columns") and ctx.get("semantic_summary"))

    status_placeholder = st.empty()
    with status_placeholder.container():
        st.write(f"⟳ Checking table '{table_name}'...")
        if context_built:
            st.write("✓ Context already built (cached)")
        else:
            st.write("⟳ Building context (first run or after rebuild)...")
        st.write("⟳ Parsing query...")
        st.write("⟳ Validating SQL...")
        st.write("⟳ Executing...")

    with st.spinner("Running pipeline..."):
        pipeline_result = asyncio.run(run_pipeline(user_query.strip()))

    status_placeholder.empty()
    with status_placeholder.container():
        if pipeline_result.get("error"):
            st.write(f"✓ Table: **{table_name}**")
            st.write("✗ Pipeline error (see below)")
        else:
            st.write(f"✓ Table '{table_name}' verified")
            st.write("✓ Context ready")
            st.write("✓ Query parsed")
            st.write("✓ SQL validated")
            st.write("✓ Executed")

    # ── Results ──────────────────────────────────────────────────────────
    if pipeline_result.get("error"):
        st.error(pipeline_result["error"])
    else:
        rows = pipeline_result.get("rows", [])
        sql = pipeline_result.get("sql", "")
        ctx_now = load_context() or {}
        semantic_summary = ctx_now.get("semantic_summary", "")

        if not rows:
            st.info("Query executed successfully but returned no rows.")
        else:
            import pandas as pd

            show_nl = response_format in ("NL", "NL + Figures")
            show_fig = response_format in ("Figures", "NL + Figures")

            # ── Natural Language response ─────────────────────────────
            if show_nl:
                with st.spinner("Generating answer..."):
                    nl_answer = generate_nl_response(
                        user_query=user_query.strip(),
                        sql=sql,
                        rows=rows,
                        semantic_summary=semantic_summary,
                    )
                st.markdown(f"### Answer\n{nl_answer}")

            # ── Figures ───────────────────────────────────────────────
            if show_fig:
                with st.spinner("Building charts..."):
                    figures = build_figures(rows, user_query=user_query.strip())

                if figures:
                    st.markdown("### Charts")
                    for title, fig in figures:
                        st.subheader(title)
                        st.plotly_chart(fig, use_container_width=True)
                elif show_nl:
                    # NL + Figures but no chartable data — note it quietly
                    st.caption("No chart could be auto-detected for this result set.")
                else:
                    # Figures-only mode, nothing to show
                    st.info(
                        "No chart could be auto-detected for this result. "
                        "Try 'NL' or 'NL + Figures' to see a text answer instead."
                    )

            # ── Raw data table (always shown below response) ──────────
            with st.expander(f"Raw data ({len(rows)} rows)", expanded=False):
                st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # ── Debug expander ────────────────────────────────────────────────────
    with st.expander("Debug — pipeline details"):
        st.subheader("SQL executed")
        st.code(pipeline_result.get("sql", ""), language="sql")
        st.subheader("Detected intent")
        st.write(pipeline_result.get("intent", ""))
        st.subheader("context.json")
        st.json(load_context())

elif run_btn:
    st.warning("Please enter a question before running.")
