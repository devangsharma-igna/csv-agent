import re

# SQL reserved words and common function names to exclude from column candidates
_RESERVED = frozenset({
    "select", "from", "where", "order", "by", "group", "having", "on", "set",
    "and", "or", "not", "in", "is", "null", "true", "false", "as", "asc",
    "desc", "limit", "offset", "join", "inner", "outer", "left", "right",
    "full", "cross", "union", "all", "distinct", "case", "when", "then",
    "else", "end", "with", "insert", "update", "delete", "into", "values",
    "between", "like", "ilike", "exists", "any", "some", "table", "into",
    "count", "sum", "avg", "max", "min", "coalesce", "cast", "extract",
    "date_trunc", "now", "current_date", "current_timestamp", "interval",
    "to_char", "row_number", "rank", "dense_rank", "over", "partition",
    "filter", "within", "preceding", "following", "unbounded", "current",
    "row", "rows", "range", "lag", "lead", "first_value", "last_value",
    "ntile", "percent_rank", "cume_dist", "length", "lower", "upper",
    "trim", "replace", "substring", "position", "strpos", "concat",
    "round", "floor", "ceil", "ceiling", "abs", "mod", "power", "sqrt",
    "greatest", "least", "nullif",
})


def validate_sql(sql: str, context: dict) -> tuple[bool, str]:
    """
    Returns (True, "") if every column reference in sql exists in
    context["schema"].
    Returns (False, reason_str) listing any unrecognised column names.
    """
    known_columns = {col["column"].lower() for col in context.get("schema", [])}
    sql_lower = sql.lower()

    # Strip string literals to avoid false matches on data values
    sql_stripped = re.sub(r"'[^']*'", " ", sql_lower)

    # Extract candidate identifiers after SQL clause keywords
    # Look for tokens after SELECT, WHERE, ORDER BY, GROUP BY, HAVING, ON, SET
    clause_pattern = re.compile(
        r"(?:select|where|order\s+by|group\s+by|having|on|set)\s+(.*?)(?=\bfrom\b|\bwhere\b|"
        r"\border\b|\bgroup\b|\bhaving\b|\blimit\b|\boffset\b|\bjoin\b|\bunion\b|$)",
        re.DOTALL,
    )

    # Simpler approach: tokenise the whole SQL and filter
    tokens = re.findall(r"[a-z_][a-z0-9_]*", sql_stripped)

    # Collect identifiers that appear to be column references (after aliases stripped)
    candidates = set()
    for token in tokens:
        if token in _RESERVED:
            continue
        # Strip numeric-looking tokens
        if re.fullmatch(r"\d+", token):
            continue
        candidates.add(token)

    # Handle qualified references like "alias.column_name"
    qualified = re.findall(r"[a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)", sql_stripped)
    qualified_set = set(qualified)

    # Replace qualified references: only check the column part
    for q in qualified_set:
        candidates.discard(q)  # will be added clean below
        candidates.add(q)

    # Remove the table name itself from candidates
    table_name = context.get("table_name", "").lower()
    candidates.discard(table_name)

    # Only flag tokens that look like they could be column names
    # (i.e. not reserved, not the table name, not a number)
    offenders = [c for c in candidates if c not in known_columns and c not in _RESERVED]

    if offenders:
        return False, f"Unknown columns: {sorted(offenders)}"
    return True, ""
