"""
Shared utility for coercing MCP execute_sql responses into a plain list of
row-dicts. Handles all known Supabase MCP response shapes:

  Shape 1 — direct JSON array (older MCP versions):
      [{"col": "val"}, ...]

  Shape 2 — wrapped list under a key:
      {"rows": [...]} / {"data": [...]} / {"result": [...]}

  Shape 3 — Supabase MCP security-wrapper string (current versions):
      {"result": "...preamble...<untrusted-data-UUID>\n[{...rows...}]\n</untrusted-data-UUID>..."}
"""

import json
import re


def parse_rows(raw: object) -> list[dict]:
    """Coerces any MCP execute_sql result into a list of row dicts."""

    if isinstance(raw, list):
        return _flatten_list(raw)

    if isinstance(raw, str):
        # Try direct JSON parse first
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Not JSON at all — try extracting an array from the raw string
            return _extract_array_from_text(raw)

        if isinstance(parsed, list):
            return _flatten_list(parsed)

        if isinstance(parsed, dict):
            # Shape 2 — list under a key
            for key in ("rows", "data", "results"):
                if isinstance(parsed.get(key), list):
                    return _flatten_list(parsed[key])

            # Shape 3 — Supabase MCP security-wrapper: "result" is a string
            result_val = parsed.get("result")
            if isinstance(result_val, list):
                return _flatten_list(result_val)
            if isinstance(result_val, str):
                return _extract_array_from_text(result_val)

            # Fallback: treat the dict itself as a single row
            return [parsed]

    return []


def _flatten_list(items: list) -> list[dict]:
    """Ensures every element is a dict; parses JSON strings if needed."""
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            try:
                parsed = json.loads(item)
                if isinstance(parsed, list):
                    result.extend(i for i in parsed if isinstance(i, dict))
                elif isinstance(parsed, dict):
                    result.append(parsed)
            except json.JSONDecodeError:
                pass
    return result


def _extract_array_from_text(text: str) -> list[dict]:
    """
    Extracts a JSON array from Supabase MCP's security-wrapper string:

        ...preamble...
        <untrusted-data-UUID>
        [{...actual rows...}]
        </untrusted-data-UUID>
        ...postamble...

    Falls back to finding the first [...] block anywhere in the string.
    """
    # Primary: extract content between <untrusted-data-...> tags
    tag_match = re.search(
        r"<untrusted-data-[^>]+>\s*(.*?)\s*</untrusted-data-[^>]+>",
        text,
        re.DOTALL,
    )
    if tag_match:
        inner = tag_match.group(1).strip()
        try:
            parsed = json.loads(inner)
            if isinstance(parsed, list):
                return _flatten_list(parsed)
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass

    # Fallback: find the first complete [...] block in the string
    bracket_match = re.search(r"\[.*?\]", text, re.DOTALL)
    if bracket_match:
        try:
            parsed = json.loads(bracket_match.group())
            if isinstance(parsed, list):
                return _flatten_list(parsed)
        except json.JSONDecodeError:
            pass

    return []
