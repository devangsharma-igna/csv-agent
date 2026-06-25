from __future__ import annotations

import re

_READ_PREFIXES = {"SELECT", "SHOW", "EXPLAIN", "VALUES"}
_MUTATING_WORDS = {
    "ALTER", "CALL", "COMMENT", "CREATE", "DELETE", "DO", "DROP", "GRANT",
    "INSERT", "MERGE", "REINDEX", "REVOKE", "TRUNCATE", "UPDATE", "VACUUM",
}
_RAW_SQL_STARTS = (
    re.compile(
        r"^\s*SELECT\s+(?:"
        r".+\s+FROM\b|"
        r"[*\d'\"(]|"
        r"(?:CURRENT_(?:USER|DATE|TIME|TIMESTAMP|SCHEMA|CATALOG)|SESSION_USER|USER)\b|"
        r"[A-Za-z_][\w$]*\s*\("
        r")",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^\s*INSERT\s+INTO\b", re.IGNORECASE),
    re.compile(r"^\s*UPDATE\s+[\w.\"-]+\s+SET\b", re.IGNORECASE),
    re.compile(r"^\s*DELETE\s+FROM\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:DROP|ALTER|CREATE)\s+"
        r"(?:TABLE|VIEW|SCHEMA|DATABASE|INDEX|FUNCTION|PROCEDURE|ROLE|TYPE|TRIGGER)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*TRUNCATE\s+(?:TABLE\s+)?[\w.\"-]+\b", re.IGNORECASE),
    re.compile(r"^\s*GRANT\s+.+\s+(?:ON|TO)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*REVOKE\s+.+\s+(?:ON|FROM)\b", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"^\s*WITH\s+(?:RECURSIVE\s+)?[\w\"-]+\s+AS\s*\(",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*CALL\s+[\w.\"-]+\s*\(", re.IGNORECASE),
    re.compile(r"^\s*EXECUTE\s+[\w\"-]+\b", re.IGNORECASE),
    re.compile(r"^\s*MERGE\s+INTO\b", re.IGNORECASE),
)
_SQL_CODE_FENCE = re.compile(r"```sql\b", re.IGNORECASE)
_OTHER_SQL_CODE_FENCE = re.compile(
    r"```(?:postgresql[ \t]*)?\r?\n(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_SQL_INJECTION_SHAPE = re.compile(
    r"(?:\bUNION\s+SELECT\b)|"
    r"(?:['\"]\s*OR\s+\d+\s*=\s*\d+\s*(?:--|#|/\*))",
    re.IGNORECASE,
)


def looks_like_raw_sql(text: str) -> bool:
    """Return True when chat input structurally resembles executable SQL."""
    candidate = text.strip()
    if not candidate:
        return False
    if _SQL_CODE_FENCE.search(candidate):
        return True
    if any(
        any(pattern.search(match.group("body")) for pattern in _RAW_SQL_STARTS)
        for match in _OTHER_SQL_CODE_FENCE.finditer(candidate)
    ):
        return True
    if _SQL_INJECTION_SHAPE.search(candidate):
        return True
    return any(pattern.search(candidate) for pattern in _RAW_SQL_STARTS)


def is_mutating_sql(sql: str) -> bool:
    """Return False only when SQL is confidently read-only."""
    cleaned = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL).strip()
    words = re.findall(r"[A-Za-z_]+", cleaned.upper())
    if not words:
        return True
    if any(word in _MUTATING_WORDS for word in words):
        return True
    return words[0] not in _READ_PREFIXES and words[0] != "WITH"
