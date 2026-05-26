from utils.mcp_client import call_tool, MCPToolError, TableNotFoundError
from utils.row_parser import parse_rows


async def execute_query(sql: str) -> list[dict] | dict:
    """
    Calls MCP tool execute_sql with the validated SQL.
    Returns rows as a list of dicts on success.
    Returns {"error": str} on generic MCP failure.

    IMPORTANT: TableNotFoundError is intentionally NOT caught here.
    It propagates straight up to run_pipeline which is the single
    point that handles it (clears context, crashes pipeline instantly).
    """
    try:
        raw = await call_tool("execute_sql", {"query": sql})
        rows = parse_rows(raw)
        print(f"[executor] Parsed {len(rows)} rows from MCP response.")
        return rows
    except TableNotFoundError:
        # Let it propagate — do not swallow
        raise
    except MCPToolError as e:
        print(f"[executor] MCPToolError: {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"[executor] Unexpected error: {e}")
        return {"error": str(e)}
