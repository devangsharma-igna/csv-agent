from __future__ import annotations

import io
import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import context_store
from ..agents.context_builder import ContextBuilder
from ..auth import CurrentUser, require_super_admin
from ..csv_inference import CsvPreview, parse_csv, sanitize_table
from ..db_client import mcp
from ..supabase_upload import (
    UploadValidationError,
    rename_uploaded_columns,
    replace_table,
)

log = logging.getLogger("igna.csv")
router = APIRouter()
_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}


class ColumnConfig(BaseModel):
    name: str
    type: str
    nullable: bool = True
    null_fill: Any | None = None


class CommitRequest(BaseModel):
    preview_id: str
    table_name: str
    columns: list[ColumnConfig]
    primary_keys: list[str] = []


@router.post("/preview")
async def csv_preview(
    file: UploadFile = File(...),
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, Any]:
    raw = await file.read()
    try:
        preview = parse_csv(raw, file.filename or "table")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}") from exc

    preview_id = f"pv_{abs(hash((file.filename, len(raw))))}"
    _PREVIEW_CACHE[preview_id] = {"preview": preview, "raw": raw}
    return {"preview_id": preview_id, **_preview_to_json(preview)}


@router.post("/commit")
async def csv_commit(
    req: CommitRequest,
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, Any]:
    cached = _PREVIEW_CACHE.get(req.preview_id)
    if cached is None:
        raise HTTPException(status_code=400, detail="preview expired; re-upload the CSV")

    preview: CsvPreview = cached["preview"]
    table = sanitize_table(req.table_name)
    sanitized_by_orig = {c.original_name: c.sanitized_name for c in preview.columns}
    frame = pd.read_csv(io.BytesIO(cached["raw"])).rename(columns=sanitized_by_orig)
    try:
        frame = rename_uploaded_columns(
            frame,
            preview_names=[column.sanitized_name for column in preview.columns],
            requested_names=[column.name for column in req.columns],
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for config in req.columns:
        if config.name in frame.columns and config.null_fill is not None:
            frame[config.name] = frame[config.name].where(
                frame[config.name].notna(), config.null_fill
            )

    if req.primary_keys:
        missing = [key for key in req.primary_keys if key not in frame.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"PK column(s) not in CSV: {missing}")
        keys = frame[req.primary_keys]
        null_count = int(keys.isna().any(axis=1).sum())
        if null_count:
            raise HTTPException(
                status_code=400,
                detail=f"{null_count} row(s) have NULL in PK column(s) {req.primary_keys}.",
            )
        duplicate_count = int(keys.duplicated(keep=False).sum())
        if duplicate_count:
            sample = keys[keys.duplicated(keep=False)].head(5).astype(str).to_dict("records")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{duplicate_count} row(s) violate PK uniqueness on "
                    f"{req.primary_keys}. Examples: {sample}"
                ),
            )

    replaced = await mcp.table_exists(table)
    column_names = [column.name for column in req.columns]
    try:
        await replace_table(
            table=table,
            frame=frame[column_names],
            columns=[column.model_dump(exclude={"null_fill"}) for column in req.columns],
            primary_keys=req.primary_keys,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Supabase upload failed")
        raise HTTPException(status_code=500, detail=f"Failed to upload table: {exc}") from exc

    context_store.evict(table)
    context = await ContextBuilder().build(table)
    context_path = context_store.save(table, context)
    _PREVIEW_CACHE.pop(req.preview_id, None)
    return {
        "table": table,
        "row_count": len(frame),
        "replaced": replaced,
        "context_path": str(context_path),
    }


def _preview_to_json(preview: CsvPreview) -> dict[str, Any]:
    return {
        "suggested_table_name": preview.suggested_table_name,
        "row_count": preview.row_count,
        "suggested_pks": preview.suggested_pks,
        "columns": [
            {
                "original_name": column.original_name,
                "sanitized_name": column.sanitized_name,
                "inferred_type": column.inferred_type,
                "nullable": column.nullable,
                "null_count": column.null_count,
                "sample_values": column.sample_values,
                "unique": column.unique,
                "pk_candidate_score": column.pk_candidate_score,
            }
            for column in preview.columns
        ],
        "preview_rows": preview.preview_rows,
    }
