"""Read/write context dumps to ./context/{table}.json."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger("igna.context")


_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")


def _path_for(table: str) -> Path:
    safe = _SAFE_NAME.sub("_", table).strip("_") or "_unnamed"
    return settings.context_path / f"{safe}.json"


def save(table: str, payload: dict[str, Any]) -> Path:
    p = _path_for(table)
    body = json.dumps(payload, indent=2, default=str)
    p.write_text(body, encoding="utf-8")
    log.info("context saved | table=%s path=%s bytes=%d cols=%d",
             table, p, len(body), len(payload.get("columns", [])))
    return p


def load(table: str) -> dict[str, Any] | None:
    p = _path_for(table)
    if not p.exists():
        log.debug("context miss | table=%s", table)
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        log.info("context hit | table=%s path=%s", table, p)
        return data
    except json.JSONDecodeError:
        log.warning("context unreadable | table=%s path=%s (will rebuild)", table, p)
        return None


def evict(table: str) -> bool:
    p = _path_for(table)
    if p.exists():
        p.unlink()
        log.info("context evicted | table=%s path=%s", table, p)
        return True
    return False


def list_cached() -> list[str]:
    return [p.stem for p in settings.context_path.glob("*.json") if not p.name.startswith("_")]
