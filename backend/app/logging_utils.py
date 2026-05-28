"""Logging helpers — concise, structured, single place for redaction/truncation.

Goal: every meaningful action in the backend produces ONE readable log line.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("igna")


_SENSITIVE_KEYS = re.compile(r"(token|key|secret|password|pat)", re.IGNORECASE)


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if _SENSITIVE_KEYS.search(k) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def trunc(value: Any, limit: int = 400) -> str:
    """Render any value to a single-line string, capped at `limit` chars."""
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        try:
            s = json.dumps(_redact(value), default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(value)
    else:
        s = str(value)
    s = s.replace("\n", " ").replace("\r", " ")
    if len(s) > limit:
        s = s[:limit] + f"…(+{len(s) - limit} more)"
    return s


@contextmanager
def timed(action: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log start/end of an action with elapsed ms. Yields a dict for extra fields."""
    extra: dict[str, Any] = {}
    fields_str = " ".join(f"{k}={trunc(v, 200)}" for k, v in fields.items())
    log.info("▶ %s %s", action, fields_str)
    t0 = time.perf_counter()
    try:
        yield extra
        dt = (time.perf_counter() - t0) * 1000
        extra_str = " ".join(f"{k}={trunc(v, 200)}" for k, v in extra.items())
        log.info("✓ %s (%.0fms) %s", action, dt, extra_str)
    except Exception as e:  # noqa: BLE001 - re-raised
        dt = (time.perf_counter() - t0) * 1000
        log.error("✗ %s (%.0fms) error=%s", action, dt, trunc(str(e), 500))
        raise
