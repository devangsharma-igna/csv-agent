"""
CSV → Database table uploader.

Uses a direct PostgreSQL connection (SQLAlchemy + psycopg2) for DDL
(CREATE TABLE) and bulk INSERT — separate from the read-only MCP
connector used for queries.

Required env var:
    SUPABASE_DATABASE_URL — use the SESSION POOLER URL.
    Find it in: Dashboard → Settings → Database → Connection Pooling → Session mode (port 5432)
    Format: postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:5432/postgres

    NOTE: Do NOT use the Direct connection URL — it uses IPv6 and fails on most networks.
"""

import json
import os
import re

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

_POOLER_HINT = (
    "\n\nMake sure you are using the SESSION POOLER URL (port 5432), "
    "not the Direct connection URL.\n"
    "Find it in: Dashboard → Settings → Database → Connection Pooling → Session mode\n"
    "Format: postgresql://postgres.[PROJECT-REF]:[PASSWORD]"
    "@aws-0-[REGION].pooler.supabase.com:5432/postgres\n"
    "The Direct connection URL uses IPv6 and fails on most networks."
)


# ── Connection ────────────────────────────────────────────────────────────────

def get_db_engine():
    """Builds a SQLAlchemy engine from SUPABASE_DATABASE_URL."""
    db_url = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
    if not db_url:
        raise ValueError("Database connection URL is not configured." + _POOLER_HINT)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    connect_args = {"sslmode": "require"}
    print("[csv_uploader] Engine created (host redacted).")
    return create_engine(db_url, pool_pre_ping=True, connect_args=connect_args)


# ── Name helpers ──────────────────────────────────────────────────────────────

def suggest_table_name(filename: str) -> str:
    """
    Converts a filename into a valid PostgreSQL table name.
    e.g. 'My Sales (2024).csv' → 'my_sales_2024'
    """
    name = os.path.splitext(filename)[0]
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "t_" + name
    return name or "uploaded_table"


def sanitize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lowercases and replaces special characters in column names with underscores.
    e.g. 'area/location' → 'area_location', 'Market Segment' → 'market_segment'
    """
    new_cols = {}
    for col in df.columns:
        clean = col.lower().strip()
        clean = re.sub(r"[^a-z0-9]+", "_", clean)
        clean = re.sub(r"_+", "_", clean).strip("_")
        new_cols[col] = clean or f"col_{list(df.columns).index(col)}"
    df = df.rename(columns=new_cols)
    print(f"[csv_uploader] Column name mapping: {new_cols}")
    return df


def _safe_col(col: str) -> str:
    """Returns a double-quoted column identifier for use in raw SQL."""
    return f'"{col}"'


# ── Type inference ────────────────────────────────────────────────────────────

_DTYPE_LABELS = {
    "object":         "TEXT",
    "string":         "TEXT",
    "int64":          "BIGINT",
    "int32":          "INTEGER",
    "float64":        "DOUBLE PRECISION",
    "float32":        "REAL",
    "bool":           "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
}


def dtype_label(dtype) -> str:
    return _DTYPE_LABELS.get(str(dtype), "TEXT")


def build_column_preview(df: pd.DataFrame, primary_key: str | None = None) -> list[dict]:
    """Returns [{Column, Type, Primary Key, Sample value}] for display."""
    preview = []
    sample = df.head(1)
    for col in df.columns:
        preview.append({
            "Column": col,
            "Type": dtype_label(df[col].dtype),
            "Primary Key": "✓" if col == primary_key else "",
            "Sample value": str(sample[col].iloc[0]) if len(sample) > 0 else "",
        })
    return preview


# ── Data quality ──────────────────────────────────────────────────────────────

def remove_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Removes exact duplicate rows from a DataFrame.
    Returns (cleaned_df, num_rows_removed).
    """
    original_len = len(df)
    df_clean = df.drop_duplicates().reset_index(drop=True)
    removed = original_len - len(df_clean)
    print(f"[csv_uploader] Removed {removed} duplicate rows ({original_len} → {len(df_clean)}).")
    return df_clean, removed


# ── LLM Primary Key Analysis ──────────────────────────────────────────────────

def analyze_pk_candidates(df: pd.DataFrame) -> list[dict]:
    """
    Computes uniqueness and null statistics for every column.
    Used as input to the LLM primary key suggester.
    """
    total = len(df)
    stats = []
    for col in df.columns:
        unique_count = int(df[col].nunique())
        null_count = int(df[col].isnull().sum())
        unique_ratio = round(unique_count / total, 4) if total > 0 else 0.0
        stats.append({
            "column": col,
            "postgres_type": dtype_label(df[col].dtype),
            "total_rows": total,
            "unique_values": unique_count,
            "unique_ratio": unique_ratio,          # 1.0 means fully unique
            "null_count": null_count,
            "is_pk_eligible": unique_count == total and null_count == 0,
            "sample_values": [str(v) for v in df[col].dropna().head(3).tolist()],
        })
    return stats


def suggest_primary_key(df: pd.DataFrame) -> dict:
    """
    Calls the LLM to rank primary key candidates for the given DataFrame.

    Returns:
        {
          "suggestions": [
              {"column": str, "confidence": "high|medium|low", "reason": str},
              ...
          ],
          "composite": null | [col1, col2],
          "summary": str
        }
    """
    from utils.llm_client import chat  # local import to avoid circular dependency

    stats = analyze_pk_candidates(df)

    system_msg = (
        "You are a database schema expert. "
        "Always respond with valid JSON only — no markdown, no explanation outside the JSON."
    )

    user_msg = f"""Analyze these column statistics and suggest the best primary key for this table.

Column statistics:
{json.dumps(stats, indent=2)}

Rules:
- A valid primary key must be: fully unique (unique_ratio == 1.0) AND have no nulls (null_count == 0).
- Prefer integer/ID columns over long text strings.
- Prefer short, stable identifiers over descriptive names.
- Only suggest columns where is_pk_eligible is true, unless no such column exists.
- If no single column qualifies, suggest a composite key (list of 2 columns).
- If nothing is suitable at all, return an empty suggestions list.

Respond with ONLY this JSON structure:
{{
  "suggestions": [
    {{"column": "column_name", "confidence": "high", "reason": "brief reason"}},
    {{"column": "other_col",   "confidence": "medium", "reason": "brief reason"}}
  ],
  "composite": null,
  "summary": "one-sentence summary of the recommendation"
}}"""

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]

    print("[csv_uploader] Calling LLM for primary key analysis...")
    try:
        response = chat(messages, max_tokens=600, temperature=0.1)
        print(f"[csv_uploader] LLM PK response: {response}")
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in LLM response.")
        result = json.loads(match.group())
        return result
    except Exception as e:
        print(f"[csv_uploader] PK suggestion failed: {e}")
        return {
            "suggestions": [],
            "composite": None,
            "summary": f"Analysis failed: {e}",
        }


# ── DDL helpers ───────────────────────────────────────────────────────────────

def _build_create_table_ddl(
    table_name: str,
    df: pd.DataFrame,
    primary_key: str | None = None,
) -> str:
    """
    Builds a CREATE TABLE statement with correct PostgreSQL types.
    The primary key column gets a PRIMARY KEY constraint.
    All column names are double-quoted to handle special characters.
    """
    col_defs = []
    for col in df.columns:
        pg_type = dtype_label(df[col].dtype)
        quoted = _safe_col(col)
        if col == primary_key:
            col_defs.append(f"    {quoted} {pg_type} PRIMARY KEY")
        else:
            col_defs.append(f"    {quoted} {pg_type}")
    cols_sql = ",\n".join(col_defs)
    return f'CREATE TABLE public."{table_name}" (\n{cols_sql}\n)'


# ── Upload ────────────────────────────────────────────────────────────────────

def read_csv(file) -> pd.DataFrame:
    """Reads a CSV file or file-like object into a DataFrame."""
    if hasattr(file, "seek"):
        file.seek(0)
    df = pd.read_csv(file)
    df.columns = [str(c).strip() for c in df.columns]
    print(f"[csv_uploader] Read CSV: {len(df)} rows, {len(df.columns)} columns.")
    return df


def upload_dataframe(
    df: pd.DataFrame,
    table_name: str,
    if_exists: str = "fail",
    chunksize: int = 500,
    primary_key: str | None = None,
) -> dict:
    """
    Uploads a DataFrame as a database table using SQLAlchemy.

    Parameters
    ----------
    df          : DataFrame to upload
    table_name  : Target table name in the public schema
    if_exists   : 'fail' | 'replace' | 'append'
    chunksize   : Rows per INSERT batch
    primary_key : Column to set as PRIMARY KEY constraint (None = no PK)

    Returns
    -------
    dict: {success, message, rows, columns}
    """
    print(
        f"[csv_uploader] Uploading '{table_name}': "
        f"{len(df)} rows, {len(df.columns)} cols, "
        f"if_exists='{if_exists}', primary_key={primary_key!r}"
    )
    try:
        engine = get_db_engine()

        if primary_key:
            # Validate PK column is unique & non-null before hitting the DB
            if df[primary_key].isnull().any():
                return {
                    "success": False,
                    "message": f"Column '{primary_key}' contains null values and cannot be a primary key.",
                    "rows": 0, "columns": 0,
                }
            if df[primary_key].duplicated().any():
                dup_count = int(df[primary_key].duplicated().sum())
                return {
                    "success": False,
                    "message": (
                        f"Column '{primary_key}' has {dup_count:,} duplicate values "
                        "and cannot be a primary key. Remove duplicates first or choose a different column."
                    ),
                    "rows": 0, "columns": 0,
                }

            # Use raw DDL so we control the PRIMARY KEY constraint
            ddl = _build_create_table_ddl(table_name, df, primary_key=primary_key)
            with engine.begin() as conn:
                if if_exists == "replace":
                    conn.execute(text(f'DROP TABLE IF EXISTS public."{table_name}"'))
                    print(f"[csv_uploader] Dropped existing table '{table_name}'.")
                if if_exists in ("fail", "replace"):
                    conn.execute(text(ddl))
                    print(f"[csv_uploader] Created table '{table_name}' with PK='{primary_key}'.")
                # INSERT rows — if_exists='append' on an already-existing table
                df.to_sql(
                    name=table_name,
                    con=conn,
                    schema="public",
                    if_exists="append",   # table already created above
                    index=False,
                    chunksize=chunksize,
                    method="multi",
                )
        else:
            # No PK — let pandas handle the full CREATE + INSERT
            with engine.begin() as conn:
                df.to_sql(
                    name=table_name,
                    con=conn,
                    schema="public",
                    if_exists=if_exists,
                    index=False,
                    chunksize=chunksize,
                    method="multi",
                )

        pk_note = f" (primary key: {primary_key})" if primary_key else ""
        msg = (
            f"Table '{table_name}' created successfully{pk_note} — "
            f"{len(df):,} rows · {len(df.columns)} columns."
        )
        print(f"[csv_uploader] {msg}")
        return {"success": True, "message": msg, "rows": len(df), "columns": len(df.columns)}

    except SQLAlchemyError as e:
        err = str(e.orig) if hasattr(e, "orig") and e.orig else str(e)
        print(f"[csv_uploader] SQLAlchemyError: {err}")
        if "already exists" in err:
            return {
                "success": False,
                "message": (
                    f"Table '{table_name}' already exists. "
                    "Switch 'If table already exists' to Replace or Append."
                ),
                "rows": 0, "columns": 0,
            }
        if "could not translate host name" in err or "name or service not known" in err.lower():
            return {
                "success": False,
                "message": f"Cannot reach the database host.{_POOLER_HINT}",
                "rows": 0, "columns": 0,
            }
        if "unique" in err.lower() or "duplicate" in err.lower():
            return {
                "success": False,
                "message": (
                    f"Primary key violation: duplicate values exist in '{primary_key}'. "
                    "Remove duplicates or choose a different primary key."
                ),
                "rows": 0, "columns": 0,
            }
        return {"success": False, "message": f"Database error: {err}", "rows": 0, "columns": 0}

    except ValueError as e:
        print(f"[csv_uploader] Config error: {e}")
        return {"success": False, "message": str(e), "rows": 0, "columns": 0}

    except Exception as e:
        print(f"[csv_uploader] Unexpected error: {e}")
        return {"success": False, "message": f"Unexpected error: {e}", "rows": 0, "columns": 0}


# ── Drop table ────────────────────────────────────────────────────────────────

def drop_table(table_name: str) -> dict:
    """Permanently drops a table. Uses IF EXISTS so it never raises if already gone."""
    print(f"[csv_uploader] Dropping table '{table_name}'...")
    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS public."{table_name}"'))
        msg = f"Table '{table_name}' deleted."
        print(f"[csv_uploader] {msg}")
        return {"success": True, "message": msg}
    except SQLAlchemyError as e:
        err = str(e.orig) if hasattr(e, "orig") and e.orig else str(e)
        print(f"[csv_uploader] drop_table error: {err}")
        if "could not translate host name" in err or "name or service not known" in err.lower():
            return {"success": False, "message": f"Cannot reach database host.{_POOLER_HINT}"}
        return {"success": False, "message": f"Database error: {err}"}
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        print(f"[csv_uploader] drop_table unexpected error: {e}")
        return {"success": False, "message": f"Unexpected error: {e}"}


# ── List tables ───────────────────────────────────────────────────────────────

def list_existing_tables() -> list[str]:
    """Returns names of all tables currently in the public schema."""
    try:
        engine = get_db_engine()
        inspector = inspect(engine)
        tables = inspector.get_table_names(schema="public")
        print(f"[csv_uploader] Existing tables: {tables}")
        return tables
    except Exception as e:
        print(f"[csv_uploader] Could not list tables: {e}")
        return []
