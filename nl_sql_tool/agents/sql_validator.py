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
    Returns (True, "") if every column reference in sql exists in context["columns"].
    Returns (False, reason_str) listing any unrecognised column names.

    Handles:
    - Normal unquoted identifiers:  dining_rating
    - Double-quoted identifiers:    "area/location", "features/category"
    - Qualified references:         t.dining_rating
    """
    if not sql:
        return False, "No SQL was generated."

    # Build known-column set (lowercase) from context
    known_columns = {col["name"].lower() for col in context.get("columns", [])}
    table_name = context.get("table_name", "").lower()

    print(f"[sql_validator] Validating SQL against {len(known_columns)} known columns.")
    print(f"[sql_validator] SQL: {sql}")
    print(f"[sql_validator] Known columns: {sorted(known_columns)}")

    offenders: list[str] = []

    # ── 1. Check double-quoted identifiers first ───────────────────────────
    # These cover column names with special chars like "area/location"
    quoted_idents = re.findall(r'"([^"]+)"', sql)
    for ident in quoted_idents:
        ident_lower = ident.lower()
        # Skip the table name itself quoted (e.g. "restraunts")
        if ident_lower == table_name:
            continue
        if ident_lower not in known_columns and ident_lower not in _RESERVED:
            offenders.append(f'"{ident}"')
            print(f"[sql_validator] Unknown quoted identifier: \"{ident}\"")

    # ── 2. Strip quoted identifiers and string literals from SQL, then
    #       tokenise for unquoted identifiers ──────────────────────────────
    sql_lower = sql.lower()
    # Remove string literals ('...')
    sql_stripped = re.sub(r"'[^']*'", " ", sql_lower)
    # Remove double-quoted identifiers (already checked above)
    sql_stripped = re.sub(r'"[^"]*"', " ", sql_stripped)

    tokens = re.findall(r"[a-z_][a-z0-9_]*", sql_stripped)

    candidates: set[str] = set()
    for token in tokens:
        if token in _RESERVED:
            continue
        if re.fullmatch(r"\d+", token):
            continue
        candidates.add(token)

    # Handle qualified references like alias.column_name
    qualified = re.findall(r"[a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)", sql_stripped)
    for q in qualified:
        candidates.discard(q)
        candidates.add(q)

    candidates.discard(table_name)

    for c in candidates:
        if c not in known_columns and c not in _RESERVED:
            offenders.append(c)
            print(f"[sql_validator] Unknown unquoted token: {c}")

    if offenders:
        print(f"[sql_validator] Validation FAILED — unknown identifiers: {sorted(offenders)}")
        return False, f"Unknown columns: {sorted(offenders)}"

    print(f"[sql_validator] Validation PASSED.")
    return True, ""
