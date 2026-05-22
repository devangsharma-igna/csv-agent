import json
from utils.mcp_client import call_tool, MCPToolError


async def execute_query(sql: str) -> list[dict] | dict:
    """
    Calls MCP tool execute_sql with the validated SQL.
    Returns rows as a list of dicts on success.
    Returns {"error": str} on MCP failure — never raises.
    """
    try:
        raw = await call_tool("execute_sql", {"query": sql})
        return _parse_rows(raw)
    except MCPToolError as e:
        print(f"[executor] MCPToolError: {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"[executor] Unexpected error: {e}")
        return {"error": str(e)}


def _parse_rows(raw: any) -> list[dict]:
    """Coerces MCP result into a list of row dicts."""
    if isinstance(raw, list):
        # Already a list — check if items are dicts
        result = []
        for item in raw:
            if isinstance(item, dict):
                result.append(item)
            elif isinstance(item, str):
                # Try to parse each item as JSON
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed, list):
                        result.extend(parsed)
                    elif isinstance(parsed, dict):
                        result.append(parsed)
                except json.JSONDecodeError:
                    pass
        return result

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                # Supabase MCP may wrap rows under a key
                for key in ("rows", "data", "result", "results"):
                    if key in parsed and isinstance(parsed[key], list):
                        return parsed[key]
                return [parsed]
        except json.JSONDecodeError:
            pass

    return []
