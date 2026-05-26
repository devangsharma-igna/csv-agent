"""
CSV → Supabase table uploader.

Uses a direct PostgreSQL connection (SQLAlchemy + psycopg2) so that
DDL (CREATE TABLE) and bulk INSERT work without touching the read-only
MCP connector used for queries.

Requires env var:
    SUPABASE_DATABASE_URL   — paste from Supabase Dashboard
                              Settings → Database → Connection string → URI
    Format: postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
"""

import io
import os
import re

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError


# ── Connection ────────────────────────────────────────────────────────────────

def get_db_engine():
    """
    Builds a SQLAlchemy engine from SUPABASE_DATABASE_URL.
    Raises ValueError with a helpful message if the env var is missing.
    """
    db_url = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
    if not db_url:
        raise ValueError(
            "SUPABASE_DATABASE_URL is not set.\n"
            "Find it in: Supabase Dashboard → Settings → Database → "
            "Connection string → URI\n"
            "Then add it to your .env file as SUPABASE_DATABASE_URL=..."
        )
    # SQLAlchemy requires 'postgresql+psycopg2://' — fix bare 'postgresql://' silently
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    print(f"[csv_uploader] Engine created (host redacted).")
    return create_engine(db_url, pool_pre_ping=True)


# ── Name helpers ──────────────────────────────────────────────────────────────

def suggest_table_name(filename: str) -> str:
    """
    Converts an uploaded filename into a valid PostgreSQL table name.
    e.g. 'My Restaurants (2024).csv' → 'my_restaurants_2024'
    """
    name = os.path.splitext(filename)[0]   # strip extension
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)  # non-alphanumeric → underscore
    name = re.sub(r"_+", "_", name).strip("_")  # collapse + trim underscores
    if name and name[0].isdigit():
        name = "t_" + name           # identifiers can't start with a digit
    return name or "uploaded_table"


def sanitize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replaces special characters in column names with underscores.
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


# ── Type inference ────────────────────────────────────────────────────────────

_DTYPE_LABELS = {
    "object":          "TEXT",
    "string":          "TEXT",
    "int64":           "BIGINT",
    "int32":           "INTEGER",
    "float64":         "DOUBLE PRECISION",
    "float32":         "REAL",
    "bool":            "BOOLEAN",
    "datetime64[ns]":  "TIMESTAMP",
}


def dtype_label(dtype) -> str:
    return _DTYPE_LABELS.get(str(dtype), "TEXT")


def build_column_preview(df: pd.DataFrame) -> list[dict]:
    """Returns a list of {column, sample_value, postgres_type} for display."""
    preview = []
    sample = df.head(1)
    for col in df.columns:
        preview.append({
            "Column": col,
            "Type": dtype_label(df[col].dtype),
            "Sample value": str(sample[col].iloc[0]) if len(sample) > 0 else "",
        })
    return preview


# ── Upload ────────────────────────────────────────────────────────────────────

def read_csv(file) -> pd.DataFrame:
    """
    Reads an uploaded file (Streamlit UploadedFile or file-like) into a DataFrame.
    Resets the cursor so the same object can be re-read if needed.
    """
    if hasattr(file, "seek"):
        file.seek(0)
    df = pd.read_csv(file)
    # Strip whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]
    print(f"[csv_uploader] Read CSV: {len(df)} rows, {len(df.columns)} columns.")
    return df


def upload_dataframe(
    df: pd.DataFrame,
    table_name: str,
    if_exists: str = "fail",
    chunksize: int = 500,
) -> dict:
    """
    Uploads a DataFrame as a Supabase table using SQLAlchemy.

    Parameters
    ----------
    df          : DataFrame to upload
    table_name  : Target table name in the public schema
    if_exists   : 'fail' | 'replace' | 'append'
    chunksize   : Rows per INSERT batch (default 500)

    Returns
    -------
    dict with keys: success (bool), message (str), rows (int), columns (int)
    """
    print(
        f"[csv_uploader] Uploading table='{table_name}', "
        f"rows={len(df)}, cols={len(df.columns)}, if_exists='{if_exists}'"
    )
    try:
        engine = get_db_engine()
        with engine.begin() as conn:          # auto-commits on success, rolls back on error
            df.to_sql(
                name=table_name,
                con=conn,
                schema="public",
                if_exists=if_exists,
                index=False,
                chunksize=chunksize,
                method="multi",               # faster bulk insert
            )
        msg = (
            f"Table '{table_name}' created successfully — "
            f"{len(df):,} rows · {len(df.columns)} columns."
        )
        print(f"[csv_uploader] {msg}")
        return {"success": True, "message": msg, "rows": len(df), "columns": len(df.columns)}

    except SQLAlchemyError as e:
        err = str(e.orig) if hasattr(e, "orig") and e.orig else str(e)
        print(f"[csv_uploader] SQLAlchemyError: {err}")
        # Surface the most useful part of Postgres errors
        if "already exists" in err:
            return {
                "success": False,
                "message": (
                    f"Table '{table_name}' already exists. "
                    "Choose 'Replace' or 'Append' to proceed."
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
