from __future__ import annotations

import re

_READ_PREFIXES = {"SELECT", "SHOW", "EXPLAIN", "VALUES"}
_MUTATING_WORDS = {
    "ALTER", "CALL", "COMMENT", "CREATE", "DELETE", "DO", "DROP", "GRANT",
    "INSERT", "MERGE", "REINDEX", "REVOKE", "TRUNCATE", "UPDATE", "VACUUM",
}


def is_mutating_sql(sql: str) -> bool:
    """Return False only when SQL is confidently read-only."""
    cleaned = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL).strip()
    words = re.findall(r"[A-Za-z_]+", cleaned.upper())
    if not words:
        return True
    if any(word in _MUTATING_WORDS for word in words):
        return True
    return words[0] not in _READ_PREFIXES and words[0] != "WITH"
