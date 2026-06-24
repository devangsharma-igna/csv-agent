"""Common scaffolding for all agents.

- Azure OpenAI GPT-4.1 client (shared singleton).
- Persona/few-shot prompt loader (markdown files in app/prompts/).
- ReAct loop with a strict iteration cap.
- TableExistenceGate: re-checks table existence at every agent boundary AND
  after every tool observation. Trips TableDeletedError on first miss.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

from openai import AsyncAzureOpenAI

from ..config import settings
from ..logging_utils import trunc
from ..db_client import MCPToolError, mcp

log = logging.getLogger("igna.agent")
gate_log = logging.getLogger("igna.gate")
llm_log = logging.getLogger("igna.llm")

_request_gate_cache: ContextVar[dict[str, bool] | None] = ContextVar(
    "_request_gate_cache", default=None
)


def init_gate_cache() -> None:
    _request_gate_cache.set({})


_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_client: AsyncAzureOpenAI | None = None


def get_llm() -> AsyncAzureOpenAI:
    global _client
    if _client is None:
        _client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return _client


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


class TableDeletedError(Exception):
    def __init__(self, table: str, phase: str) -> None:
        super().__init__(f"Table '{table}' no longer exists (phase: {phase})")
        self.table = table
        self.phase = phase


class TableExistenceGate:
    """Hard circuit breaker checked at every agent boundary and after every tool call.

    in_loop=False (boundary): checks per-request cache first, hits DB on miss.
    in_loop=True  (in ReAct):  always issues a fresh check to catch mid-loop deletion.
    Tombstone is always checked first — fires instantly without any I/O.
    """

    def __init__(self, table: str, phase: str, *, in_loop: bool = False) -> None:
        self.table = table
        self.phase = phase
        self.in_loop = in_loop

    async def check(self) -> None:
        cache = _request_gate_cache.get()

        if not self.in_loop and cache is not None and self.table in cache:
            exists = cache[self.table]
            if not exists:
                gate_log.warning("gate TRIPPED (cached) | phase=%s table=%s", self.phase, self.table)
                raise TableDeletedError(self.table, self.phase)
            gate_log.debug("gate ok (cached) | phase=%s table=%s", self.phase, self.table)
            return

        exists = await mcp.table_exists(self.table)
        if cache is not None:
            cache[self.table] = exists

        if not exists:
            gate_log.warning("gate TRIPPED | phase=%s table=%s", self.phase, self.table)
            raise TableDeletedError(self.table, self.phase)
        gate_log.debug("gate ok | phase=%s table=%s", self.phase, self.table)


# ── Tool helpers ──────────────────────────────────────────────────────────────

Tool = dict[str, Any]
ToolImpl = Callable[[dict[str, Any]], Awaitable[Any]]


def db_select_tool() -> tuple[Tool, ToolImpl]:
    spec = {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Execute a single read-only SQL SELECT against the local database. "
                "No DDL, no INSERT/UPDATE/DELETE. Returns the rows as JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    async def impl(args: dict[str, Any]) -> Any:
        q = args["query"].strip().rstrip(";")
        upper = q.upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE "):
            if forbidden in upper + " ":
                log.warning("agent tried forbidden statement: %s", forbidden.strip())
                raise MCPToolError("execute_sql", f"forbidden statement: {forbidden.strip()}")
        log.info("agent SQL -> %s", trunc(q, 400))
        return await mcp.execute_sql(q)

    return spec, impl


def list_tables_tool() -> tuple[Tool, ToolImpl]:
    spec = {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List tables in the public schema of the local database.",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    async def impl(_: dict[str, Any]) -> Any:
        return await mcp.list_tables(["public"])

    return spec, impl


# ── ReAct loop ────────────────────────────────────────────────────────────────

async def react_loop(
    *,
    system: str,
    user: str,
    tools: list[tuple[Tool, ToolImpl]],
    gate: TableExistenceGate | None,
    response_format: dict | None = None,
    max_iter: int | None = None,
    observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    llm = get_llm()
    tool_specs = [t[0] for t in tools]
    tool_impls = {t[0]["function"]["name"]: t[1] for t in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    limit = max_iter or settings.MAX_REACT_ITERATIONS
    phase = gate.phase if gate else "?"
    llm_log.info("ReAct start | phase=%s tools=%s max_iter=%d", phase, [s["function"]["name"] for s in tool_specs], limit)

    for step in range(limit):
        kwargs: dict[str, Any] = {
            "model": settings.AZURE_OPENAI_DEPLOYMENT,
            "messages": messages,
            "temperature": 0.1,
        }
        if tool_specs:
            kwargs["tools"] = tool_specs
            kwargs["tool_choice"] = "auto"
        if response_format and not tool_specs:
            kwargs["response_format"] = response_format

        t0 = time.perf_counter()
        resp = await llm.chat.completions.create(**kwargs)
        dt = (time.perf_counter() - t0) * 1000
        msg = resp.choices[0].message
        usage = getattr(resp, "usage", None)
        llm_log.info(
            "LLM step=%d (%.0fms) | tokens=p%s/c%s tool_calls=%d",
            step + 1, dt,
            getattr(usage, "prompt_tokens", "?") if usage else "?",
            getattr(usage, "completion_tokens", "?") if usage else "?",
            len(msg.tool_calls or []),
        )

        if msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                impl = tool_impls.get(name)
                if impl is None:
                    observation = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        observation = await impl(args)
                        if observations is not None:
                            observations.append({"tool": name, "args": args, "result": observation})
                    except MCPToolError as e:
                        observation = {"error": e.message}
                        if gate and ("does not exist" in e.message.lower() or "undefined_table" in e.message.lower()):
                            await gate.check()
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(observation, default=str)[:8000],
                })
                if gate is not None:
                    await gate.check()
            continue

        content = msg.content or "{}"
        cleaned = _strip_code_fence(content)
        try:
            result = json.loads(cleaned)
            llm_log.info("ReAct done | phase=%s steps=%d", phase, step + 1)
            return result
        except json.JSONDecodeError:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "Reply ONLY with the final JSON object as specified. No prose."})
            resp2 = await llm.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(resp2.choices[0].message.content or "{}")

    raise RuntimeError(f"ReAct iteration cap ({limit}) exceeded")


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        else:
            s = s.lstrip("`").lstrip("json").lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s


async def single_shot_json(*, system: str, user: str, phase: str = "?") -> dict[str, Any]:
    llm = get_llm()
    llm_log.info("LLM single-shot | phase=%s user=%s", phase, trunc(user, 250))
    t0 = time.perf_counter()
    resp = await llm.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    dt = (time.perf_counter() - t0) * 1000
    usage = getattr(resp, "usage", None)
    result = json.loads(resp.choices[0].message.content or "{}")
    llm_log.info(
        "LLM single-shot done | phase=%s (%.0fms) tokens=p%s/c%s",
        phase, dt,
        getattr(usage, "prompt_tokens", "?") if usage else "?",
        getattr(usage, "completion_tokens", "?") if usage else "?",
    )
    return result
