import json
import os
import re
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Module-level cached session state
_session: ClientSession | None = None
_session_context = None
_stdio_context = None


class MCPToolError(Exception):
    pass


class TableNotFoundError(Exception):
    """
    Raised immediately when PostgreSQL returns error code 42P01
    (undefined_table / relation does not exist).
    Propagates uncaught through every pipeline phase so the pipeline
    crashes instantly rather than continuing with a dead table reference.
    """
    def __init__(self, table_name: str, pg_message: str = ""):
        self.table_name = table_name
        self.pg_message = pg_message
        super().__init__(
            f"Table '{table_name}' no longer exists in the database. "
            f"It may have been dropped mid-query. ({pg_message})"
        )


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

    Raises:
      TableNotFoundError  — immediately when PostgreSQL returns 42P01
                        (relation does not exist). Never swallowed downstream.
      MCPToolError    — for all other MCP / DB errors.
    """
    try:
        session = await _get_session()
        result = await session.call_tool(tool_name, arguments)

        if result.isError:
            # Flatten all content pieces into one searchable string
            error_text = " ".join(
                item.text if hasattr(item, "text") else str(item)
                for item in result.content
            )
            print(f"[mcp_client] Tool error raw text: {error_text}")

            # ── Circuit-breaker: detect dropped table (PG code 42P01) ──
            if _is_table_gone(error_text):
                table_name = _extract_relation_name(error_text) or arguments.get("query", "")
                pg_msg = _extract_pg_message(error_text)
                print(
                    f"[mcp_client] 42P01 detected — raising TableNotFoundError "
                    f"for relation '{table_name}'"
                )
                raise TableNotFoundError(table_name, pg_msg)

            raise MCPToolError(f"MCP tool '{tool_name}' returned an error: {error_text}")

        # Success — return raw content
        contents = result.content
        if len(contents) == 1 and hasattr(contents[0], "text"):
            return contents[0].text
        return [c.text if hasattr(c, "text") else c for c in contents]

    except (TableNotFoundError, MCPToolError):
        raise
    except Exception as e:
        raise MCPToolError(f"MCP call to '{tool_name}' failed: {e}") from e


def _is_table_gone(error_text: str) -> bool:
    """Returns True if the error text indicates PostgreSQL error 42P01."""
    t = error_text.upper()
    return "42P01" in t or (
        "DOES NOT EXIST" in t and "RELATION" in t
    )


def _extract_relation_name(error_text: str) -> str:
    """Extracts the relation name from a 42P01 error message, if present."""
    # Matches: relation "some_table" does not exist
    m = re.search(r'relation\s+"([^"]+)"\s+does not exist', error_text, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_pg_message(error_text: str) -> str:
    """
    Tries to pull the PostgreSQL message string out of the JSON error envelope
    that the Supabase MCP server wraps errors in.
    """
    try:
        # error_text may be the raw content of a TextContent — try JSON parse
        data = json.loads(error_text)
        return data.get("error", {}).get("message", "")
    except (json.JSONDecodeError, AttributeError):
        pass
    # Fallback: return first 200 chars of raw text
    return error_text[:200]


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
