import os
import re
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Module-level cached session state
_session: ClientSession | None = None
_session_context = None
_stdio_context = None


def _build_server_params() -> StdioServerParameters:
    """
    Constructs the StdioServerParameters for the Supabase MCP server.

    Newer versions of @supabase/mcp-server-supabase (≥ 0.4) require:
      --project-ref  <ref>           (extracted from SUPABASE_URL)
      SUPABASE_ACCESS_TOKEN          (PAT — we alias from SUPABASE_SERVICE_ROLE_KEY
                                      as a best-effort; works for self-hosted and
                                      some managed projects)

    Older versions only needed SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in the env.
    We pass everything so both old and new server versions are covered.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    # Extract "abcdefghij" from "https://abcdefghij.supabase.co"
    m = re.match(r"https://([a-z0-9]+)\.supabase\.co", supabase_url)
    project_ref = m.group(1) if m else None

    args = ["-y", "@supabase/mcp-server-supabase@latest", "--read-only"]
    if project_ref:
        args += ["--project-ref", project_ref]
        print(f"[mcp_client] using project-ref: {project_ref}")
    else:
        print("[mcp_client] WARNING: could not extract project-ref from SUPABASE_URL")

    # IMPORTANT: do NOT alias SUPABASE_SERVICE_ROLE_KEY → SUPABASE_ACCESS_TOKEN.
    # They are different credentials. The Management API (used by MCP ≥ 0.4)
    # requires a Personal Access Token (PAT), not the service role key.
    # The PAT must be set explicitly in .env as SUPABASE_ACCESS_TOKEN.
    env = {
        **os.environ,          # includes SUPABASE_ACCESS_TOKEN if set in .env
        "SUPABASE_URL": supabase_url,
        "SUPABASE_SERVICE_ROLE_KEY": service_role_key,
    }

    return StdioServerParameters(command="npx", args=args, env=env)


async def _get_session() -> ClientSession:
    global _session, _session_context, _stdio_context

    if _session is not None:
        return _session

    # Fail fast with a clear message if the PAT is missing
    if not os.environ.get("SUPABASE_ACCESS_TOKEN"):
        raise MCPToolError(
            "SUPABASE_ACCESS_TOKEN is not set. "
            "Generate a Personal Access Token at "
            "https://supabase.com/dashboard/account/tokens "
            "and add it to your .env file."
        )

    server_params = _build_server_params()

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
