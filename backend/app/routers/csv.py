from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import context_store
from ..agents.context_builder import ContextBuilder
from ..config import settings
from ..csv_inference import (
    CsvPreview,
    parse_csv,
    sanitize_table,
)
from ..db_client import MCPToolError, mcp

log = logging.getLogger("igna.csv")
router = APIRouter()


# In-memory cache of parsed CSVs keyed by an opaque preview_id so commit doesn't
# require a second upload. Cleared on commit. Fine for single-user desktop use.
_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}


class ColumnConfig(BaseModel):
    name: str
    type: str
    nullable: bool = True
    null_fill: Any | None = None  # value to substitute for nulls during insert


class CommitRequest(BaseModel):
    preview_id: str
    table_name: str
    columns: list[ColumnConfig]
    primary_keys: list[str] = []


@router.post("/preview")
async def csv_preview(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    log.info("csv preview ▶ | filename=%s bytes=%d", file.filename, len(raw))
    try:
        preview = parse_csv(raw, file.filename or "table")
    except Exception as e:  # noqa: BLE001 - surface parse failures to the FE
        log.exception("csv preview ✗ | parse failed")
        raise HTTPException(status_code=400, detail=f"CSV parse error: {e}") from e

    preview_id = f"pv_{abs(hash((file.filename, len(raw))))}"
    _PREVIEW_CACHE[preview_id] = {"preview": preview, "raw": raw, "filename": file.filename}
    log.info(
        "csv preview ✓ | preview_id=%s rows=%d cols=%d suggested_pks=%s",
        preview_id, preview.row_count, len(preview.columns), preview.suggested_pks,
    )
    return {"preview_id": preview_id, **_preview_to_json(preview)}


@router.post("/commit")
async def csv_commit(req: CommitRequest) -> dict[str, Any]:
    log.info("csv commit ▶ | preview_id=%s table=%s pks=%s cols=%d",
             req.preview_id, req.table_name, req.primary_keys, len(req.columns))
    cached = _PREVIEW_CACHE.get(req.preview_id)
    if cached is None:
        log.warning("csv commit ✗ | preview expired preview_id=%s", req.preview_id)
        raise HTTPException(status_code=400, detail="preview expired; re-upload the CSV")
    preview: CsvPreview = cached["preview"]

    table = sanitize_table(req.table_name)
    if not table:
        raise HTTPException(status_code=400, detail="invalid table name")

    # Materialize the dataframe up front so we can validate PK BEFORE writing
    # anything — a failed validation on disk would require cleanup.
    sanitized_by_orig = {c.original_name: c.sanitized_name for c in preview.columns}
    null_fills = {c.name: c.null_fill for c in req.columns}
    import io
    import pandas as pd

    df = pd.read_csv(io.BytesIO(cached["raw"]))
    df = df.rename(columns=sanitized_by_orig)
    for col, fill in null_fills.items():
        if col in df.columns and fill is not None:
            df[col] = df[col].where(df[col].notna(), fill)

    # --- PK uniqueness pre-flight ---
    if req.primary_keys:
        missing = [pk for pk in req.primary_keys if pk not in df.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"primary key column(s) not found in CSV: {missing}")
        pk_subset = df[req.primary_keys]
        null_in_pk = pk_subset.isna().any(axis=1).sum()
        if null_in_pk:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{int(null_in_pk)} row(s) have NULL in the chosen primary key column(s) "
                    f"{req.primary_keys}. Either pick a different PK, set a null-fill, or import without a PK."
                ),
            )
        dup_mask = pk_subset.duplicated(keep=False)
        dup_count = int(dup_mask.sum())
        if dup_count:
            sample = (
                pk_subset[dup_mask]
                .head(5)
                .astype(str)
                .apply(lambda r: ", ".join(f"{k}={v}" for k, v in r.items()), axis=1)
                .tolist()
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{dup_count} row(s) violate uniqueness on PK {req.primary_keys}. "
                    f"Examples: {sample}. Pick a different PK, deduplicate the CSV, or import without a PK."
                ),
            )

    # Evict stale context before writing so a mid-flight failure doesn't leave
    # old context pointing at the new (potentially different) schema.
    replaced = await mcp.table_exists(table)
    context_store.evict(table)

    # Write cleaned DataFrame to data directory. DuckDB reads it directly as a
    # view — no DDL, no INSERT batches needed.
    col_names = [c.name for c in req.columns]
    dest = settings.data_path / f"{table}.csv"
    df[col_names].to_csv(dest, index=False)
    log.info("csv commit | wrote %d rows → %s (%s)", len(df), dest,
             "replacing" if replaced else "new")

    # Register as a DuckDB view with the user-confirmed column types.
    columns_spec = [{"name": c.name, "type": c.type} for c in req.columns]
    try:
        await mcp.register_csv(table, dest, columns_spec)
    except MCPToolError as e:
        log.exception("register_csv failed: %s", e.message)
        raise HTTPException(status_code=500, detail=f"Failed to register table: {e.message}") from e

    # Build and persist fresh context for the new table.
    log.info("csv commit | triggering Context Builder for fresh schema")
    builder = ContextBuilder()
    context = await builder.build(table)
    context_path = context_store.save(table, context)

    _PREVIEW_CACHE.pop(req.preview_id, None)
    log.info("csv commit ✓ | table=%s rows=%d replaced=%s context=%s",
             table, len(df), replaced, context_path)
    return {
        "table": table,
        "row_count": len(df),
        "replaced": replaced,
        "context_path": str(context_path),
    }


def _preview_to_json(p: CsvPreview) -> dict[str, Any]:
    return {
        "suggested_table_name": p.suggested_table_name,
        "row_count": p.row_count,
        "suggested_pks": p.suggested_pks,
        "columns": [
            {
                "original_name": c.original_name,
                "sanitized_name": c.sanitized_name,
                "inferred_type": c.inferred_type,
                "nullable": c.nullable,
                "null_count": c.null_count,
                "sample_values": c.sample_values,
                "unique": c.unique,
                "pk_candidate_score": c.pk_candidate_score,
            }
            for c in p.columns
        ],
        "preview_rows": p.preview_rows,
    }
