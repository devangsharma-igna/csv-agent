from __future__ import annotations

import asyncio
import io
from typing import Any

import pandas as pd

from .config import settings
from .csv_inference import sanitize_table

_ALLOWED_TYPES = {
    "text", "integer", "bigint", "double precision", "boolean",
    "timestamptz", "date", "numeric",
}
_TRUE_VALUES = {"true", "t", "1", "yes", "y"}
_FALSE_VALUES = {"false", "f", "0", "no", "n"}


class UploadValidationError(ValueError):
    pass


def rename_uploaded_columns(
    frame: pd.DataFrame,
    *,
    preview_names: list[str],
    requested_names: list[str],
) -> pd.DataFrame:
    if len(preview_names) != len(requested_names):
        raise UploadValidationError("Column configuration does not match the CSV")
    if len(set(requested_names)) != len(requested_names):
        raise UploadValidationError("Column names must be unique")
    return frame.rename(columns=dict(zip(preview_names, requested_names)))


def prepare_dataframe(
    frame: pd.DataFrame,
    columns: list[dict[str, Any]],
    primary_keys: list[str] | None = None,
) -> pd.DataFrame:
    expected = [col["name"] for col in columns]
    missing = [name for name in expected if name not in frame.columns]
    if missing:
        raise UploadValidationError(f"Missing configured column(s): {missing}")

    result = frame[expected].copy()
    for config in columns:
        name = config["name"]
        pg_type = str(config["type"]).lower()
        if pg_type not in _ALLOWED_TYPES:
            raise UploadValidationError(f"Unsupported type for {name}: {pg_type}")
        original = result[name]
        nonnull = original.notna()
        try:
            if pg_type in {"integer", "bigint"}:
                numeric = pd.to_numeric(original, errors="coerce")
                invalid = nonnull & (numeric.isna() | (numeric % 1 != 0))
                _raise_invalid(name, invalid)
                result[name] = numeric.astype("Int64")
            elif pg_type in {"double precision", "numeric"}:
                numeric = pd.to_numeric(original, errors="coerce")
                _raise_invalid(name, nonnull & numeric.isna())
                result[name] = numeric
            elif pg_type == "boolean":
                mapped = original.map(_coerce_bool)
                _raise_invalid(name, nonnull & mapped.isna())
                result[name] = mapped.astype("boolean")
            elif pg_type in {"date", "timestamptz"}:
                parsed = pd.to_datetime(original, errors="coerce", utc=pg_type == "timestamptz")
                _raise_invalid(name, nonnull & parsed.isna())
                result[name] = parsed.dt.date if pg_type == "date" else parsed
            else:
                result[name] = original.astype("string")
        except UploadValidationError:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise UploadValidationError(f"Could not coerce column {name} to {pg_type}: {exc}") from exc

        if not config.get("nullable", True):
            null_rows = result.index[result[name].isna()].tolist()
            if null_rows:
                shown = [index + 2 for index in null_rows[:5]]
                raise UploadValidationError(
                    f"Column {name} is NOT NULL but has NULL values at CSV row(s) {shown}"
                )
    keys = primary_keys or []
    if keys:
        missing_keys = [key for key in keys if key not in result.columns]
        if missing_keys:
            raise UploadValidationError(f"Missing primary key column(s): {missing_keys}")
        if result[keys].isna().any(axis=1).any():
            raise UploadValidationError("The primary key contains NULL values after type coercion")
        if result[keys].duplicated(keep=False).any():
            raise UploadValidationError("The primary key contains duplicate values after type coercion")
    return result


def _coerce_bool(value: Any) -> bool | None:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _raise_invalid(column: str, mask: pd.Series) -> None:
    invalid_rows = mask[mask].index.tolist()
    if invalid_rows:
        shown = [index + 2 for index in invalid_rows[:5]]
        raise UploadValidationError(
            f"Invalid value for column {column} at CSV row(s) {shown}"
        )


async def replace_table(
    *,
    table: str,
    frame: pd.DataFrame,
    columns: list[dict[str, Any]],
    primary_keys: list[str],
) -> None:
    if not settings.SUPABASE_DB_URL:
        raise UploadValidationError("SUPABASE_DB_URL is not configured")
    prepared = prepare_dataframe(frame, columns, primary_keys)
    await asyncio.to_thread(
        _replace_table_sync,
        sanitize_table(table),
        prepared,
        columns,
        primary_keys,
    )


def _replace_table_sync(
    table: str,
    frame: pd.DataFrame,
    columns: list[dict[str, Any]],
    primary_keys: list[str],
) -> None:
    import psycopg
    from psycopg import sql

    definitions = []
    for column in columns:
        definition = sql.SQL("{} {}").format(
            sql.Identifier(column["name"]),
            sql.SQL(str(column["type"]).lower()),
        )
        if not column.get("nullable", True):
            definition += sql.SQL(" NOT NULL")
        definitions.append(definition)
    if primary_keys:
        definitions.append(
            sql.SQL("PRIMARY KEY ({})").format(
                sql.SQL(", ").join(sql.Identifier(key) for key in primary_keys)
            )
        )

    create_sql = sql.SQL("CREATE TABLE {} ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(definitions),
    )
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column["name"]) for column in columns),
    )
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, na_rep="")

    with psycopg.connect(settings.SUPABASE_DB_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table)))
            cursor.execute(create_sql)
            with cursor.copy(copy_sql) as copy:
                copy.write(buffer.getvalue())
