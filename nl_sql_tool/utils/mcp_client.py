import os
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Module-level cached session state
_session: ClientSession | None = None
_session_context = None
_stdio_context = None


async def _get_session() -> ClientSession:
    global _session, _session_context, _stdio_context

    if _session is not None:
        return _session

    env = {**os.environ, "SUPABASE_URL": os.environ["SUPABASE_URL"],
           "SUPABASE_SERVICE_ROLE_KEY": os.environ["SUPABASE_SERVICE_ROLE_KEY"]}

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@supabase/mcp-server-supabase@latest", "--read-only"],
        env=env,
    )

    _stdio_context = stdio_client(server_params)
    read, write = await _stdio_context.__aenter__()

    _session_context = ClientSession(read, write)
    _session = await _session_context.__aenter__()
    await _session.initialize()

    return _session


async def call_tool(tool_name: str, arguments: dict) -> any:
    """
    Starts the MCP server (or reuses a cached session),
    calls the named tool, and returns the result content.
    Raises MCPToolError on failure.
    """
    try:
        session = await _get_session()
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            raise MCPToolError(f"MCP tool '{tool_name}' returned an error: {result.content}")
        # Return raw content list or first text item
        contents = result.content
        if len(contents) == 1 and hasattr(contents[0], "text"):
            return contents[0].text
        return [c.text if hasattr(c, "text") else c for c in contents]
    except MCPToolError:
        raise
    except Exception as e:
        raise MCPToolError(f"MCP call to '{tool_name}' failed: {e}") from e


async def close_mcp_session() -> None:
    global _session, _session_context, _stdio_context

    if _session_context is not None:
        try:
            await _session_context.__aexit__(None, None, None)
        except Exception:
            pass
        _session_context = None

    if _stdio_context is not None:
        try:
            await _stdio_context.__aexit__(None, None, None)
        except Exception:
            pass
        _stdio_context = None

    _session = None


class MCPToolError(Exception):
    pass
