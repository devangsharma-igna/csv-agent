from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .logging_utils import trunc
from .db_client import mcp
from .routers import csv as csv_router
from .routers import query as query_router
from .routers import tables as tables_router

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore", "openai._base_client", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("igna.http")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("backend starting | log_level=%s frontend_origin=%s", settings.LOG_LEVEL, settings.FRONTEND_ORIGIN)
    await mcp.start()
    try:
        yield
    finally:
        log.info("backend stopping")
        await mcp.stop()


app = FastAPI(title="IGNA Query Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    log.info("-> %s %s | rid=%s client=%s", request.method, request.url.path, rid, request.client.host if request.client else "?")
    try:
        response = await call_next(request)
    except Exception as e:  # noqa: BLE001
        dt = (time.perf_counter() - t0) * 1000
        log.error("<- %s %s | rid=%s 500 (%.0fms) exc=%s", request.method, request.url.path, rid, dt, trunc(str(e), 300))
        raise
    dt = (time.perf_counter() - t0) * 1000
    log.info("<- %s %s | rid=%s %d (%.0fms)", request.method, request.url.path, rid, response.status_code, dt)
    return response


app.include_router(csv_router.router, prefix="/api/csv", tags=["csv"])
app.include_router(tables_router.router, prefix="/api/tables", tags=["tables"])
app.include_router(query_router.router, prefix="/api", tags=["query"])


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
