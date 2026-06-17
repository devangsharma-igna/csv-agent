"""CSV parsing + schema inference. Pandas is used ONLY here, at the upload boundary.

Agents never see a DataFrame — they query via DuckDB execute_sql.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


# Postgres reserved words we don't want as column names (subset of the ~700 list).
_RESERVED = {
    "all", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "both", "case",
    "cast", "check", "collate", "column", "constraint", "create", "current_date",
    "current_time", "current_timestamp", "current_user", "default", "deferrable",
    "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for",
    "foreign", "from", "grant", "group", "having", "in", "initially", "intersect",
    "into", "lateral", "leading", "limit", "localtime", "localtimestamp", "not",
    "null", "offset", "on", "only", "or", "order", "placing", "primary", "references",
    "returning", "select", "session_user", "some", "symmetric", "table", "then",
    "to", "trailing", "true", "union", "unique", "user", "using", "variadic", "when",
    "where", "window", "with",
}


@dataclass
class ColumnProfile:
    original_name: str
    sanitized_name: str
    inferred_type: str  # postgres type
    nullable: bool
    null_count: int
    sample_values: list[Any]
    unique: bool
    pk_candidate_score: float  # 0..1; 1.0 means perfect (unique, non-null, narrow)


@dataclass
class CsvPreview:
    suggested_table_name: str
    row_count: int
    columns: list[ColumnProfile]
    suggested_pks: list[str]  # column names (sanitized); empty if none
    preview_rows: list[dict[str, Any]]


def sanitize_column(name: str, existing: set[str] | None = None) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "col"
    if s[0].isdigit():
        s = "c_" + s
    if s in _RESERVED:
        s = s + "_"
    if existing is not None:
        base, i = s, 2
        while s in existing:
            s = f"{base}_{i}"
            i += 1
        existing.add(s)
    return s


def sanitize_table(name: str) -> str:
    return sanitize_column(name)  # same rules


def _infer_pg_type(s: pd.Series) -> str:
    s_nonnull = s.dropna()
    if s_nonnull.empty:
        return "text"
    if pd.api.types.is_bool_dtype(s_nonnull):
        return "boolean"
    if pd.api.types.is_integer_dtype(s_nonnull):
        # Detect overflow into bigint
        mn, mx = s_nonnull.min(), s_nonnull.max()
        if mn >= -2_147_483_648 and mx <= 2_147_483_647:
            return "integer"
        return "bigint"
    if pd.api.types.is_float_dtype(s_nonnull):
        return "double precision"
    if pd.api.types.is_datetime64_any_dtype(s_nonnull):
        return "timestamptz"
    # Try parsing as datetime — but only if values *look* date-ish, to avoid
    # pandas spending time + emitting warnings on plainly textual columns.
    if s_nonnull.dtype == object:
        sample = s_nonnull.astype(str).head(20)
        looks_dateish = sample.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}").mean() > 0.8
        if looks_dateish:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(s_nonnull, errors="raise", utc=False)
                if parsed.notna().all():
                    return "timestamptz"
            except (ValueError, TypeError):
                pass
    # Try numeric
    try:
        coerced = pd.to_numeric(s_nonnull, errors="raise")
        if (coerced.astype("int64") == coerced).all():
            return "bigint"
        return "double precision"
    except (ValueError, TypeError):
        pass
    return "text"


def parse_csv(raw: bytes, filename: str = "table") -> CsvPreview:
    df = pd.read_csv(io.BytesIO(raw))
    used: set[str] = set()
    cols: list[ColumnProfile] = []
    for orig in df.columns:
        s = df[orig]
        sanitized = sanitize_column(str(orig), used)
        pg_type = _infer_pg_type(s)
        null_count = int(s.isna().sum())
        unique = bool(s.dropna().is_unique and null_count == 0 and len(s) > 0)
        score = 0.0
        if unique:
            score += 0.6
        if null_count == 0:
            score += 0.2
        if pg_type in ("integer", "bigint"):
            score += 0.15
        if "id" in sanitized.lower():
            score += 0.05
        sample = [_jsonable(v) for v in s.dropna().head(5).tolist()]
        cols.append(ColumnProfile(
            original_name=str(orig),
            sanitized_name=sanitized,
            inferred_type=pg_type,
            nullable=null_count > 0,
            null_count=null_count,
            sample_values=sample,
            unique=unique,
            pk_candidate_score=round(score, 3),
        ))

    suggested_pks: list[str] = []
    single_pk = [c for c in cols if c.unique and not c.nullable]
    if single_pk:
        single_pk.sort(key=lambda c: -c.pk_candidate_score)
        suggested_pks = [single_pk[0].sanitized_name]
    else:
        # Try 2-column composite from the best candidates
        candidates = sorted(cols, key=lambda c: -c.pk_candidate_score)[:4]
        if len(candidates) >= 2 and df.duplicated(
            subset=[c.original_name for c in candidates[:2]]
        ).sum() == 0:
            suggested_pks = [candidates[0].sanitized_name, candidates[1].sanitized_name]

    name_stem = filename.rsplit(".", 1)[0]
    return CsvPreview(
        suggested_table_name=sanitize_table(name_stem),
        row_count=int(len(df)),
        columns=cols,
        suggested_pks=suggested_pks,
        preview_rows=[
            {c.sanitized_name: _jsonable(df[c.original_name].iloc[i]) for c in cols}
            for i in range(min(10, len(df)))
        ],
    )


def _jsonable(v: Any) -> Any:
    # pd.isna chokes on arrays/lists; guard with a scalar check first.
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    # numpy scalars (numpy.bool_, numpy.int64, numpy.float64, ...) → Python scalars
    if hasattr(v, "item") and v.__class__.__module__ == "numpy":
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    return v


def build_create_table(table: str, columns: list[dict[str, Any]], pks: list[str]) -> str:
    """columns: [{name, type, nullable}], pks: [name,...]"""
    safe_table = sanitize_table(table)
    col_defs = []
    for c in columns:
        line = f'  "{c["name"]}" {c["type"]}'
        if not c.get("nullable", True):
            line += " NOT NULL"
        col_defs.append(line)
    if pks:
        pk_cols = ", ".join(f'"{p}"' for p in pks)
        col_defs.append(f"  PRIMARY KEY ({pk_cols})")
    body = ",\n".join(col_defs)
    return (
        f'DROP TABLE IF EXISTS "{safe_table}" CASCADE;\n'
        f'CREATE TABLE "{safe_table}" (\n{body}\n);'
    )


def build_insert_batches(
    table: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    batch_size: int = 500,
) -> list[str]:
    """Build batched INSERT statements with values inlined and properly escaped.

    We can't bind params over the MCP execute_sql interface, so we escape literals.
    """
    safe_table = sanitize_table(table)
    col_list = ", ".join(f'"{c}"' for c in columns)
    statements: list[str] = []
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        values_sql = ",\n".join("(" + ", ".join(_pg_literal(r.get(c)) for c in columns) + ")" for r in chunk)
        statements.append(f'INSERT INTO "{safe_table}" ({col_list}) VALUES\n{values_sql};')
    return statements


def _pg_literal(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"
