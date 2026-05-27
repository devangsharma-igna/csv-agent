"""
FastAPI backend for CSV Agent.
Wraps existing Python agents and utilities as REST endpoints.
Run with: uvicorn backend.api:app --reload --port 8000
"""

from dotenv import load_dotenv
load_dotenv()

import asyncio
import io
import json
import re
import sys
import os

# Ensure project root is on the path so sibling packages resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.schemas import (
    TableConfirmRequest,
    QueryRequest,
    QueryResponse,
    TableDeleteRequest,
)

from utils.context_io import load_context, save_context
from utils.mcp_client import call_tool, close_mcp_session, MCPToolError, TableNotFoundError
from utils.llm_client import chat
from utils.figure_builder import build_figures
from utils.csv_uploader import (
    suggest_table_name,
    sanitize_column_names,
    build_column_preview,
    read_csv,
    upload_dataframe,
    list_existing_tables,
    drop_table,
    remove_duplicates,
    suggest_primary_key,
)
from agents.context_builder import build_context, ContextBuilderError
from agents.nl_parser import parse_query, NLParserError, _SYSTEM_PROMPT, _extract_result_json
from agents.guardrail import check_query_scope
from agents.nl_responder import generate_nl_response
from agents.sql_validator import validate_sql
from agents.executor import execute_query

import pandas as pd

app = FastAPI(title="IGNA CSV Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Helpers (mirrored from main.py, no Streamlit deps)
# ──────────────────────────────────────────────

def _clear_table_context() -> None:
    try:
        save_context({})
        print("[api] context.json cleared.")
    except Exception as ex:
        print(f"[api] Failed to clear context.json: {ex}")


async def _check_table_exists(table_name: str) -> tuple[bool, str]:
    query = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        f"AND table_name = '{table_name}' LIMIT 1"
    )
    try:
        raw = await call_tool("execute_sql", {"query": query})
        exists = table_name.lower() in str(raw).lower()
        return exists, ""
    except MCPToolError as e:
        err = str(e)
        if any(w in err.lower() for w in ("authoriz", "unauthorized", "401", "forbidden", "403")):
            return False, (
                "Database authorization failed. "
                "Your access token must be a Personal Access Token (PAT) — "
                "not the service role key."
            )
        return False, f"MCP error during table check: {err}"
    except Exception as e:
        return False, f"Unexpected error during table check: {e}"


async def run_pipeline(user_query: str) -> dict:
    result = {"intent": "", "sql": "", "rows": None, "error": None, "out_of_scope": False, "table_gone": False}
    try:
        ctx = load_context() or {}
        table_name = ctx.get("table_name", "")

        if not table_name:
            result["error"] = "No table configured. Please set a table first."
            return result

        found, err_msg = await _check_table_exists(table_name)
        if not found:
            result["error"] = err_msg or f"CSV '{table_name}' no longer exists."
            return result

        context_ready = bool(
            ctx.get("table_name") == table_name
            and ctx.get("columns")
            and ctx.get("semantic_summary")
        )
        if not context_ready:
            ctx = await build_context(table_name)

        column_names = [col["name"] for col in ctx.get("columns", [])]
        in_scope, oos_reason = check_query_scope(
            user_query, ctx.get("semantic_summary", ""), column_names=column_names
        )
        if not in_scope:
            result["out_of_scope"] = True
            result["error"] = oos_reason
            return result

        parsed = parse_query(user_query, ctx)
        result["intent"] = parsed.get("intent", "")
        result["sql"] = parsed.get("sql") or ""

        if not result["sql"]:
            result["error"] = (
                "The parser could not produce a SQL query for this question. "
                "Try rephrasing as a specific data request."
            )
            return result

        known_columns = [col["name"] for col in ctx.get("columns", [])]
        needs_quoting = [n for n in known_columns if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', n)]
        quoting_note = ""
        if needs_quoting:
            examples = ", ".join(f'"{n}"' for n in needs_quoting)
            quoting_note = f"\nCRITICAL — These columns MUST be double-quoted in SQL: {examples}"

        retries = 0
        while retries <= 2:
            valid, reason = validate_sql(result["sql"], ctx)
            if valid:
                break
            if retries == 2:
                result["error"] = "Could not generate valid SQL for this query. Please rephrase."
                return result
            correction_messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Context:\n{json.dumps(ctx, indent=2)}\n\n"
                    f"IMPORTANT — Valid column names (use ONLY these):\n{json.dumps(known_columns)}"
                    f"{quoting_note}\n\nUser question: {user_query}"
                )},
                {"role": "assistant", "content": f"Thought: FINAL: Generating SQL.\nResult: {json.dumps(parsed)}"},
                {"role": "user", "content": (
                    f"Observation: SQL validation failed — {reason}. "
                    f"Revise the SQL to use ONLY these valid columns: {known_columns}."
                    f"{quoting_note} Re-emit your FINAL Result."
                )},
            ]
            correction_response = chat(correction_messages, max_tokens=2000, temperature=0.2)
            if "Result:" in correction_response:
                try:
                    parsed = _extract_result_json(correction_response)
                    result["intent"] = parsed.get("intent", result["intent"])
                    result["sql"] = parsed.get("sql", result["sql"])
                except Exception:
                    pass
            retries += 1

        rows = await execute_query(result["sql"])
        if isinstance(rows, dict) and "error" in rows:
            result["error"] = rows["error"]
        else:
            result["rows"] = rows

    except TableNotFoundError as e:
        _clear_table_context()
        result["table_gone"] = True
        result["error"] = str(e)
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
# Routes
# ──────────────────────────────────────────────

@app.get("/api/context")
def get_context():
    return load_context() or {}


@app.post("/api/table/confirm")
async def confirm_table(req: TableConfirmRequest):
    found, err_msg = await _check_table_exists(req.table_name)
    await close_mcp_session()
    if found:
        save_context({"table_name": req.table_name})
        return {"success": True}
    return {"success": False, "error": err_msg or f"Table '{req.table_name}' not found."}


@app.post("/api/table/change")
def change_table():
    ctx = load_context() or {}
    ctx.pop("table_name", None)
    save_context(ctx)
    return {"success": True}


@app.post("/api/table/rebuild-context")
def rebuild_context():
    ctx = load_context() or {}
    for key in ("columns", "sample_rows", "semantic_summary"):
        ctx.pop(key, None)
    save_context(ctx)
    return {"success": True}


@app.delete("/api/table")
def delete_table(req: TableDeleteRequest):
    result = drop_table(req.table_name)
    if result["success"]:
        _clear_table_context()
    return result


@app.post("/api/query")
async def query_data(req: QueryRequest):
    pipeline_result = await run_pipeline(req.user_query.strip())

    response = QueryResponse(
        intent=pipeline_result.get("intent", ""),
        sql=pipeline_result.get("sql", ""),
        rows=pipeline_result.get("rows"),
        error=pipeline_result.get("error"),
        out_of_scope=pipeline_result.get("out_of_scope", False),
        table_gone=pipeline_result.get("table_gone", False),
    )

    rows = pipeline_result.get("rows") or []
    if rows and not pipeline_result.get("error"):
        ctx_now = load_context() or {}
        semantic_summary = ctx_now.get("semantic_summary", "")

        show_nl = req.response_format in ("NL", "NL + Figures")
        show_fig = req.response_format in ("Figures", "NL + Figures")

        if show_nl:
            try:
                response.nl_answer = generate_nl_response(
                    user_query=req.user_query.strip(),
                    sql=pipeline_result.get("sql", ""),
                    rows=rows,
                    semantic_summary=semantic_summary,
                )
            except Exception as e:
                response.nl_answer = f"(Could not generate answer: {e})"

        if show_fig:
            try:
                figures = build_figures(rows, user_query=req.user_query.strip())
                response.figures = [fig.to_json() for _, fig in figures] if figures else []
            except Exception as e:
                response.figures = []

    return response


@app.post("/api/upload/analyze-pk")
async def analyze_pk(file: UploadFile = File(...)):
    contents = await file.read()
    df = read_csv(io.BytesIO(contents))
    suggestions = suggest_primary_key(df)
    return suggestions


@app.get("/api/upload/tables")
def get_tables():
    return {"tables": list_existing_tables()}


@app.post("/api/upload/preview")
async def upload_preview(
    file: UploadFile = File(...),
    sanitize: str = Form("false"),
    primary_key: str = Form(""),
    remove_dups: str = Form("false"),
):
    contents = await file.read()
    df = read_csv(io.BytesIO(contents))

    remove_dups_bool = remove_dups.lower() == "true"
    sanitize_bool = sanitize.lower() == "true"
    pk = primary_key.strip() or None

    n_dups = int(df.duplicated().sum())
    if remove_dups_bool and n_dups > 0:
        df, _ = remove_duplicates(df)

    df_preview = sanitize_column_names(df.copy()) if sanitize_bool else df.copy()

    # Remap PK if sanitized
    preview_pk = pk
    if sanitize_bool and pk:
        sanitized_name = re.sub(r"[^a-z0-9]+", "_", pk.lower().strip()).strip("_")
        preview_pk = sanitized_name if sanitized_name else pk

    col_info = build_column_preview(df_preview, primary_key=preview_pk)
    sample_rows = df_preview.head(5).where(pd.notnull(df_preview.head(5)), None).to_dict(orient="records")

    return {
        "n_rows": len(df),
        "n_dups": n_dups,
        "columns": list(df_preview.columns),
        "col_info": col_info,
        "sample_rows": sample_rows,
        "preview_pk": preview_pk,
    }


@app.post("/api/upload")
async def upload_csv(
    file: UploadFile = File(...),
    table_name: str = Form(...),
    if_exists: str = Form("fail"),
    sanitize: str = Form("false"),
    primary_key: str = Form(""),
    remove_dups: str = Form("false"),
):
    contents = await file.read()
    df = read_csv(io.BytesIO(contents))

    remove_dups_bool = remove_dups.lower() == "true"
    sanitize_bool = sanitize.lower() == "true"
    pk = primary_key.strip() or None

    if remove_dups_bool:
        df, _ = remove_duplicates(df)

    df_upload = sanitize_column_names(df.copy()) if sanitize_bool else df.copy()

    # Remap PK name if sanitized
    upload_pk = pk
    if sanitize_bool and pk:
        sanitized_name = re.sub(r"[^a-z0-9]+", "_", pk.lower().strip()).strip("_")
        upload_pk = sanitized_name if sanitized_name else pk

    result = upload_dataframe(
        df=df_upload,
        table_name=table_name.strip(),
        if_exists=if_exists,
        primary_key=upload_pk,
    )
    return result
