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
        r"CASE\b.+\bWHEN\b.+\bTHEN\b.+\bEND\b|"
        r"(?:NULL|TRUE|FALSE)\s*;?\s*$|"
        r"(?:CURRENT_(?:USER|ROLE|DATE|TIME|TIMESTAMP|SCHEMA|CATALOG)|"
        r"SESSION_USER|USER)\s*;?\s*$|"
        r"[A-Za-z_][\w$]*\s*\("
        r")",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^\s*INSERT\s+INTO\b", re.IGNORECASE),
    re.compile(r"^\s*UPDATE\s+(?:ONLY\s+)?[\w.\"-]+\s+SET\b", re.IGNORECASE),
    re.compile(r"^\s*DELETE\s+FROM\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:DROP|ALTER)\s+"
        r"(?:TABLE|VIEW|SCHEMA|DATABASE|INDEX|FUNCTION|PROCEDURE|ROLE|TYPE|TRIGGER)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?"
        r"(?:TABLE|VIEW|SCHEMA|DATABASE|INDEX|FUNCTION|PROCEDURE|ROLE|TYPE|TRIGGER)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*DROP\s+OWNED\s+BY\b", re.IGNORECASE),
    re.compile(r"^\s*ALTER\s+SYSTEM\s+(?:SET|RESET)\b", re.IGNORECASE),
    re.compile(r"^\s*CREATE\s+USER\s+[\w\"-]+\b", re.IGNORECASE),
    re.compile(r"^\s*TRUNCATE\s+(?:TABLE\s+)?[\w.\"-]+\b", re.IGNORECASE),
    re.compile(r"^\s*GRANT\s+.+\s+(?:ON|TO)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*REVOKE\s+.+\s+(?:ON|FROM)\b", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"^\s*WITH\s+(?:RECURSIVE\s+)?[\w\"-]+"
        r"(?:\s*\([^)]*\))?\s+AS\s*\(",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*CALL\s+[\w.\"-]+\s*\(", re.IGNORECASE),
    re.compile(r"^\s*EXECUTE\s+[\w\"-]+\b", re.IGNORECASE),
    re.compile(r"^\s*MERGE\s+INTO\b", re.IGNORECASE),
)
_CODE_FENCE = re.compile(
    r"```(?:[A-Za-z0-9_+-]+[ \t]*)?\r?\n(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_LEADING_SQL_COMMENT = re.compile(
    r"^\s*(?:--[^\r\n]*(?:\r?\n|$)|/\*.*?\*/)",
    re.DOTALL,
)
_SQL_INJECTION_SHAPE = re.compile(
    r"(?:\bUNION\s+SELECT\b)|"
    r"(?:['\"]\s*OR\s+\d+\s*=\s*\d+\s*(?:--|#|/\*))",
    re.IGNORECASE,
)


def _strip_leading_sql_comments(text: str) -> str:
    candidate = text
    while match := _LEADING_SQL_COMMENT.match(candidate):
        candidate = candidate[match.end():]
    return candidate.strip()


def _split_sql_segments(text: str) -> list[str]:
    segments: list[str] = []
    start = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == ";":
            segments.append(text[start:index])
            start = index + 1
        index += 1
    segments.append(text[start:])
    return segments


def _executable_segments(text: str) -> list[str]:
    sources = [text]
    sources.extend(match.group("body") for match in _CODE_FENCE.finditer(text))
    return [
        normalized
        for source in sources
        for segment in _split_sql_segments(source)
        if (normalized := _strip_leading_sql_comments(segment))
    ]


def looks_like_raw_sql(text: str) -> bool:
    """Return True when chat input structurally resembles executable SQL."""
    candidate = text.strip()
    if not candidate:
        return False
    if _SQL_INJECTION_SHAPE.search(candidate):
        return True
    return any(
        pattern.search(segment)
        for segment in _executable_segments(candidate)
        for pattern in _RAW_SQL_STARTS
    )


def is_mutating_sql(sql: str) -> bool:
    """Return False only when SQL is confidently read-only."""
    cleaned = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL).strip()
    words = re.findall(r"[A-Za-z_]+", cleaned.upper())
    if not words:
        return True
    if any(word in _MUTATING_WORDS for word in words):
        return True
    return words[0] not in _READ_PREFIXES and words[0] != "WITH"
