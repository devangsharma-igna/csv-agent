# Two-Level Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two hardcoded login roles, enforce read-only access for User, retain confirmed unrestricted database operations for Super Admin, reject raw SQL chat input, and expose read rows in a contained Raw data disclosure.

**Architecture:** Add a small in-memory cookie-session module and inject its FastAPI dependencies only at existing HTTP entry points. Preserve the planner/database workflow; authorization wraps it, while a focused raw-SQL detector runs before the planner. The React app gains a small auth context, protected routes, and a reusable raw-data table without changing query orchestration.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, unittest, React 18, TypeScript, React Router, Tailwind CSS, Vitest, Testing Library.

---

## File Structure

**Create**

- `backend/app/auth.py` - fixed users, roles, in-memory opaque sessions, cookie helpers, and FastAPI authorization dependencies.
- `backend/app/routers/auth.py` - login, session restoration, and logout endpoints.
- `backend/tests/test_auth.py` - authentication/session behavior and cookie contract.
- `backend/tests/test_access_control.py` - role enforcement across query, CSV, context, confirmation, and cancellation paths.
- `frontend/src/auth.tsx` - auth state, startup session restoration, login/logout actions, and 401 handling.
- `frontend/src/pages/LoginPage.tsx` - fixed-account login form with no signup flow.
- `frontend/src/components/RawDataTable.tsx` - bounded, vertically and horizontally scrollable result table.
- `frontend/src/components/RawDataTable.test.tsx` - empty, tall, and wide raw-data rendering tests.
- `frontend/src/App.test.tsx` - role-based routing/navigation tests.
- `frontend/src/test/setup.ts` - Testing Library DOM cleanup/matchers.

**Modify**

- `backend/app/config.py` - localhost-aware secure-cookie setting only.
- `backend/app/main.py` - register auth router and permit credentials in CORS.
- `backend/app/sql_safety.py` - add raw-SQL chat detection without changing mutating-SQL classification.
- `backend/app/pending_writes.py` - bind pending writes to the creating session.
- `backend/app/routers/query.py` - inject role/session, reject raw SQL before planner, gate writes, and return read rows.
- `backend/app/routers/csv.py` - require Super Admin for preview and commit.
- `backend/app/routers/tables.py` - require authentication for reads and Super Admin for refresh.
- `backend/tests/test_supabase_write_flow.py` - update direct route calls and pending-write tests for session ownership.
- `frontend/src/api.ts` - auth API types/calls, `credentials: include`, centralized 401 notification, and read rows.
- `frontend/src/App.tsx` - protected shell, role-based nav, login route, and logout.
- `frontend/src/main.tsx` - mount the auth provider.
- `frontend/src/pages/QueryPage.tsx` - role-aware refresh/confirmation controls and Raw data disclosure.
- `frontend/package.json` and `frontend/package-lock.json` - frontend test runner dependencies/scripts.
- `README.md` - document fixed local credentials, role matrix, raw-SQL policy, and session behavior.

## Task 1: Backend Authentication and Browser Sessions

**Files:**

- Create: `backend/tests/test_auth.py`
- Create: `backend/app/auth.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Write failing authentication/session tests**

Create `backend/tests/test_auth.py` with tests using the wished-for API:

```python
import unittest

from app.auth import Role, SessionStore, authenticate


class AuthenticationTests(unittest.TestCase):
    def test_authenticates_super_admin(self) -> None:
        user = authenticate("igna.admin@gmail.com", "admin@123")
        self.assertEqual(user.username, "igna.admin@gmail.com")
        self.assertEqual(user.role, Role.SUPER_ADMIN)

    def test_authenticates_read_only_user(self) -> None:
        user = authenticate("igna.user@gmail.com", "user@123")
        self.assertEqual(user.role, Role.USER)

    def test_rejects_invalid_credentials_without_revealing_account_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid credentials"):
            authenticate("igna.admin@gmail.com", "wrong")

    def test_session_is_opaque_and_can_be_revoked(self) -> None:
        store = SessionStore()
        user = authenticate("igna.user@gmail.com", "user@123")
        session_id = store.create(user)

        self.assertNotIn(user.username, session_id)
        self.assertEqual(store.get(session_id), user)
        self.assertTrue(store.revoke(session_id))
        self.assertIsNone(store.get(session_id))
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_auth -v
```

Expected: import failure because `app.auth` does not exist.

- [ ] **Step 3: Implement the minimal authentication domain**

Create `backend/app/auth.py` with:

```python
from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Cookie, Depends, HTTPException


SESSION_COOKIE = "igna_session"


class Role(StrEnum):
    USER = "user"
    SUPER_ADMIN = "super_admin"


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: Role
    session_id: str = ""


_CREDENTIALS = {
    "igna.admin@gmail.com": ("admin@123", Role.SUPER_ADMIN),
    "igna.user@gmail.com": ("user@123", Role.USER),
}


def authenticate(username: str, password: str) -> CurrentUser:
    stored = _CREDENTIALS.get(username.strip().lower())
    if stored is None or not hmac.compare_digest(stored[0], password):
        raise ValueError("invalid credentials")
    return CurrentUser(username=username.strip().lower(), role=stored[1])


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CurrentUser] = {}

    def create(self, user: CurrentUser) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = CurrentUser(
            username=user.username,
            role=user.role,
            session_id=session_id,
        )
        return session_id

    def get(self, session_id: str | None) -> CurrentUser | None:
        return self._sessions.get(session_id or "")

    def revoke(self, session_id: str | None) -> bool:
        return self._sessions.pop(session_id or "", None) is not None


sessions = SessionStore()


def require_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> CurrentUser:
    user = sessions.get(session_id)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    return user


def require_super_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if user.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail={"error": "super_admin_required"})
    return user
```

Add only this setting to `backend/app/config.py`:

```python
    AUTH_COOKIE_SECURE: bool = False
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_auth -v
```

Expected: all four tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/auth.py backend/app/config.py backend/tests/test_auth.py
git commit -m "feat: add fixed-account browser sessions"
```

## Task 2: Authentication HTTP Endpoints

**Files:**

- Modify: `backend/tests/test_auth.py`
- Create: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add failing endpoint-function tests**

Extend `backend/tests/test_auth.py`:

```python
from fastapi import HTTPException, Response

from app.auth import SESSION_COOKIE, sessions
from app.routers.auth import LoginRequest, login, logout, me


class AuthenticationRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        sessions._sessions.clear()

    def test_login_sets_browser_session_cookie(self) -> None:
        response = Response()
        result = login(
            LoginRequest(username="igna.admin@gmail.com", password="admin@123"),
            response,
        )
        cookie = response.headers["set-cookie"]
        self.assertEqual(result["role"], "super_admin")
        self.assertIn(f"{SESSION_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("expires=", cookie.lower())

    def test_login_rejects_bad_password(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            login(
                LoginRequest(username="igna.user@gmail.com", password="wrong"),
                Response(),
            )
        self.assertEqual(raised.exception.status_code, 401)

    def test_me_returns_authenticated_identity(self) -> None:
        user = authenticate("igna.user@gmail.com", "user@123")
        session_id = sessions.create(user)
        current = sessions.get(session_id)
        self.assertEqual(me(current), {
            "username": "igna.user@gmail.com",
            "role": "user",
        })

    def test_logout_revokes_session_and_clears_cookie(self) -> None:
        user = authenticate("igna.user@gmail.com", "user@123")
        session_id = sessions.create(user)
        response = Response()
        result = logout(response, sessions.get(session_id))
        self.assertEqual(result, {"logged_out": True})
        self.assertIsNone(sessions.get(session_id))
        self.assertIn(f"{SESSION_COOKIE}=\"\"", response.headers["set-cookie"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_auth.AuthenticationRouteTests -v
```

Expected: import failure because `app.routers.auth` does not exist.

- [ ] **Step 3: Add the auth router**

Create `backend/app/routers/auth.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import (
    SESSION_COOKIE,
    CurrentUser,
    authenticate,
    require_user,
    sessions,
)
from ..config import settings

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


def _identity(user: CurrentUser) -> dict[str, str]:
    return {"username": user.username, "role": user.role.value}


@router.post("/login")
def login(req: LoginRequest, response: Response) -> dict[str, str]:
    try:
        user = authenticate(req.username, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials"}) from exc
    session_id = sessions.create(user)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return _identity(sessions.get(session_id))


@router.get("/me")
def me(user: CurrentUser = Depends(require_user)) -> dict[str, str]:
    return _identity(user)


@router.post("/logout")
def logout(
    response: Response,
    user: CurrentUser = Depends(require_user),
) -> dict[str, bool]:
    sessions.revoke(user.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return {"logged_out": True}
```

Register it in `backend/app/main.py`:

```python
from .routers import auth as auth_router

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
```

Do not add a global auth middleware; endpoint dependencies keep `/api/health` and `/api/auth/login` public and make authorization explicit.

- [ ] **Step 4: Run the auth suite and verify GREEN**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_auth -v
```

Expected: all authentication tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/main.py backend/app/routers/auth.py backend/tests/test_auth.py
git commit -m "feat: expose login session endpoints"
```

## Task 3: Raw SQL Chat Guardrail

**Files:**

- Modify: `backend/tests/test_supabase_write_flow.py`
- Modify: `backend/app/sql_safety.py`
- Modify: `backend/app/routers/query.py`

- [ ] **Step 1: Write failing raw-SQL detector tests**

Add to `SqlClassificationTests`:

```python
from app.sql_safety import looks_like_raw_sql

def test_rejects_executable_sql_chat(self) -> None:
    samples = [
        "SELECT * FROM tickets",
        "```sql\nDROP TABLE tickets;\n```",
        "WITH x AS (DELETE FROM tickets RETURNING *) SELECT * FROM x",
        "' OR 1=1 --",
        "show rows UNION SELECT password FROM users",
    ]
    for sample in samples:
        with self.subTest(sample=sample):
            self.assertTrue(looks_like_raw_sql(sample))

def test_allows_natural_language_with_sql_adjacent_words(self) -> None:
    samples = [
        "Show the selected tickets",
        "Update me on how many tickets are open",
        "Which table has the highest count?",
        "Delete-related requests by category",
    ]
    for sample in samples:
        with self.subTest(sample=sample):
            self.assertFalse(looks_like_raw_sql(sample))
```

Add an async route test proving short-circuiting:

```python
from app.auth import CurrentUser, Role
from fastapi import HTTPException

async def test_query_rejects_raw_sql_before_planner(self) -> None:
    user = CurrentUser("igna.admin@gmail.com", Role.SUPER_ADMIN, "session-a")
    with patch("app.routers.query.QueryPlanner.plan", new=AsyncMock()) as planner:
        with self.assertRaises(HTTPException) as raised:
            await query(
                QueryRequest(table="tickets", question="DROP TABLE tickets"),
                user,
            )
    self.assertEqual(raised.exception.status_code, 400)
    self.assertEqual(raised.exception.detail["error"], "raw_sql_denied")
    planner.assert_not_awaited()
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_supabase_write_flow.SqlClassificationTests -v
```

Expected: import failure for `looks_like_raw_sql`.

- [ ] **Step 3: Implement a conservative structural detector**

Add to `backend/app/sql_safety.py`:

```python
_RAW_SQL_START = re.compile(
    r"^\s*(?:```sql\s*)?(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|"
    r"TRUNCATE|GRANT|REVOKE|WITH|CALL|EXECUTE|MERGE)\b",
    re.IGNORECASE,
)
_SQL_INJECTION_SHAPE = re.compile(
    r"(?:\bUNION\s+SELECT\b)|(?:['\"]?\s+OR\s+\d+\s*=\s*\d+\s*(?:--|#|/\*))",
    re.IGNORECASE,
)


def looks_like_raw_sql(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if _RAW_SQL_START.search(candidate):
        return True
    if re.search(r"```sql\b", candidate, re.IGNORECASE):
        return True
    return bool(_SQL_INJECTION_SHAPE.search(candidate))
```

At the start of `query()` in `backend/app/routers/query.py`, before table gates or context loading:

```python
from fastapi import APIRouter, Depends, HTTPException
from ..auth import CurrentUser, require_user
from ..sql_safety import is_mutating_sql, looks_like_raw_sql

async def query(
    req: QueryRequest,
    user: CurrentUser = Depends(require_user),
) -> dict[str, Any]:
    if looks_like_raw_sql(req.question):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "raw_sql_denied",
                "message": "Raw SQL is not accepted. Describe the operation in natural language.",
            },
        )
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_supabase_write_flow.SqlClassificationTests tests.test_supabase_write_flow.QueryPlannerWriteTests -v
```

Expected: detector and planner-short-circuit tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/sql_safety.py backend/app/routers/query.py backend/tests/test_supabase_write_flow.py
git commit -m "feat: reject raw SQL chat input"
```

## Task 4: Enforce Roles at Every Existing API Entry Point

**Files:**

- Create: `backend/tests/test_access_control.py`
- Modify: `backend/app/pending_writes.py`
- Modify: `backend/app/routers/query.py`
- Modify: `backend/app/routers/csv.py`
- Modify: `backend/app/routers/tables.py`
- Modify: `backend/tests/test_supabase_write_flow.py`

- [ ] **Step 1: Write failing authorization tests**

Create `backend/tests/test_access_control.py` with direct route tests that avoid starting MCP:

```python
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.auth import CurrentUser, Role, require_super_admin
from app.pending_writes import PendingWriteStore
from app.routers.query import QueryRequest, query


USER = CurrentUser("igna.user@gmail.com", Role.USER, "user-session")
ADMIN = CurrentUser("igna.admin@gmail.com", Role.SUPER_ADMIN, "admin-session")


class RoleDependencyTests(unittest.TestCase):
    def test_user_is_not_super_admin(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            require_super_admin(USER)
        self.assertEqual(raised.exception.status_code, 403)

    def test_super_admin_is_accepted(self) -> None:
        self.assertEqual(require_super_admin(ADMIN), ADMIN)


class PendingWriteOwnershipTests(unittest.TestCase):
    def test_only_owner_session_can_consume_or_cancel(self) -> None:
        store = PendingWriteStore()
        pending = store.create(
            table="tickets",
            sql="DROP TABLE tickets",
            summary="Drop tickets.",
            affected_tables=["tickets"],
            owner_session_id="admin-session",
        )
        self.assertIsNone(store.consume(pending.confirmation_id, "other-session"))
        self.assertFalse(store.cancel(pending.confirmation_id, "other-session"))
        self.assertEqual(
            store.consume(pending.confirmation_id, "admin-session"),
            pending,
        )


class QueryRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_execute_read_flow(self) -> None:
        plan = {
            "allowed": True,
            "operation": "read",
            "final_sql": "SELECT ticket_id FROM tickets",
        }
        with (
            patch("app.routers.query.TableExistenceGate.check", new=AsyncMock()),
            patch("app.routers.query.context_store.load", return_value={"table": "tickets", "columns": []}),
            patch("app.routers.query.QueryPlanner.plan", new=AsyncMock(return_value=(plan, [{"ticket_id": 7}]))),
            patch("app.routers.query.NLResponder.respond", new=AsyncMock(return_value={"answer": "Ticket 7.", "wants_figure": False})),
        ):
            result = await query(QueryRequest(table="tickets", question="Show ticket 7"), USER)
        self.assertEqual(result["status"], "ok")

    async def test_user_cannot_create_pending_write(self) -> None:
        plan = {
            "allowed": True,
            "operation": "write",
            "final_sql": "DROP TABLE tickets",
            "summary": "Drop tickets.",
            "affected_tables": ["tickets"],
        }
        with (
            patch("app.routers.query.TableExistenceGate.check", new=AsyncMock()),
            patch("app.routers.query.context_store.load", return_value={"table": "tickets", "columns": []}),
            patch("app.routers.query.QueryPlanner.plan", new=AsyncMock(return_value=(plan, []))),
            patch("app.routers.query.pending_writes.create") as create,
        ):
            with self.assertRaises(HTTPException) as raised:
                await query(QueryRequest(table="tickets", question="Remove the tickets table"), USER)
        self.assertEqual(raised.exception.status_code, 403)
        create.assert_not_called()

    async def test_admin_write_still_requires_confirmation(self) -> None:
        plan = {
            "allowed": True,
            "operation": "write",
            "final_sql": "DROP TABLE tickets",
            "summary": "Drop tickets.",
            "affected_tables": ["tickets"],
        }
        with (
            patch("app.routers.query.TableExistenceGate.check", new=AsyncMock()),
            patch("app.routers.query.context_store.load", return_value={"table": "tickets", "columns": []}),
            patch("app.routers.query.QueryPlanner.plan", new=AsyncMock(return_value=(plan, []))),
        ):
            result = await query(QueryRequest(table="tickets", question="Remove the tickets table"), ADMIN)
        self.assertEqual(result["status"], "confirmation_required")
```

Also add direct tests for `csv_preview`, `csv_commit`, and `refresh_context` asserting that their `require_super_admin` dependency rejects `USER` before route business logic. Keep those tests dependency-focused; existing upload tests already cover upload business logic.

- [ ] **Step 2: Run authorization tests and verify RED**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_access_control -v
```

Expected: failures because pending writes have no owner and routes do not enforce roles.

- [ ] **Step 3: Bind pending writes to their creator session**

Update `backend/app/pending_writes.py`:

```python
@dataclass(frozen=True)
class PendingWrite:
    confirmation_id: str
    table: str
    sql: str
    summary: str
    affected_tables: list[str]
    owner_session_id: str
    expires_at: float

def create(
    self,
    *,
    table: str,
    sql: str,
    summary: str,
    affected_tables: list[str],
    owner_session_id: str,
) -> PendingWrite:
    self._purge()
    confirmation_id = secrets.token_urlsafe(24)
    pending = PendingWrite(
        confirmation_id=confirmation_id,
        table=table,
        sql=sql,
        summary=summary,
        affected_tables=affected_tables,
        owner_session_id=owner_session_id,
        expires_at=time.time() + self.ttl_seconds,
    )
    self._items[confirmation_id] = pending
    return pending

def consume(self, confirmation_id: str, owner_session_id: str) -> PendingWrite | None:
    self._purge()
    pending = self._items.get(confirmation_id)
    if pending is None or pending.owner_session_id != owner_session_id:
        return None
    return self._items.pop(confirmation_id)

def cancel(self, confirmation_id: str, owner_session_id: str) -> bool:
    return self.consume(confirmation_id, owner_session_id) is not None
```

Update existing pending-write tests to pass explicit owner session IDs.

- [ ] **Step 4: Add minimal route dependencies and write gates**

In `backend/app/routers/csv.py`:

```python
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from ..auth import CurrentUser, require_super_admin

async def csv_preview(
    file: UploadFile = File(...),
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, Any]:
    raw = await file.read()
    try:
        preview = parse_csv(raw, file.filename or "table")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse error: {exc}") from exc
    preview_id = f"pv_{abs(hash((file.filename, len(raw))))}"
    _PREVIEW_CACHE[preview_id] = {"preview": preview, "raw": raw}
    return {"preview_id": preview_id, **_preview_to_json(preview)}

async def csv_commit(
    req: CommitRequest,
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, Any]:
```

The snippet shows the replacement signature. Keep the current `csv_commit` body byte-for-byte after that signature.

In `backend/app/routers/tables.py`, inject `require_user` into `list_tables` and `get_context_summary`, and `require_super_admin` into `refresh_context`. Do not otherwise modify their bodies:

```python
async def list_tables(
    _user: CurrentUser = Depends(require_user),
) -> dict:

async def get_context_summary(
    table: str,
    _user: CurrentUser = Depends(require_user),
) -> dict:

async def refresh_context(
    table: str,
    _admin: CurrentUser = Depends(require_super_admin),
) -> dict:
```

In `backend/app/routers/query.py`:

```python
if plan.get("operation") == "write" or is_mutating_sql(sql):
    if user.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail={"error": "read_only_role"})
    pending = pending_writes.create(
        table=table,
        sql=sql,
        summary=plan.get("summary") or plan.get("refined_query") or "Execute the proposed database operation.",
        affected_tables=plan.get("affected_tables") or [table],
        owner_session_id=user.session_id,
    )

async def confirm_query(
    req: ConfirmRequest,
    admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, Any]:
    pending = pending_writes.consume(req.confirmation_id, admin.session_id)
    if pending is None:
        raise HTTPException(status_code=410, detail={"error": "confirmation_expired"})

async def cancel_query(
    confirmation_id: str,
    admin: CurrentUser = Depends(require_super_admin),
) -> dict[str, bool]:
    return {
        "cancelled": pending_writes.cancel(confirmation_id, admin.session_id)
    }
```

The planner remains unchanged. The role check happens after independent SQL classification and before the pending store/database.

- [ ] **Step 5: Run backend authorization and regression suites**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_access_control tests.test_supabase_write_flow tests.test_supabase_upload tests.test_query_planner -v
```

Expected: all tests pass; no database or LLM calls occur in denied-path tests.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/pending_writes.py backend/app/routers/query.py backend/app/routers/csv.py backend/app/routers/tables.py backend/tests/test_access_control.py backend/tests/test_supabase_write_flow.py
git commit -m "feat: enforce read-only and super-admin roles"
```

## Task 5: Return Bounded Raw Rows from Read Queries

**Files:**

- Modify: `backend/tests/test_access_control.py`
- Modify: `backend/app/routers/query.py`

- [ ] **Step 1: Write a failing response-contract test**

Extend the successful read test:

```python
self.assertEqual(result["rows"], [{"ticket_id": 7}])
```

Add a bounded-result assertion using 250 mocked rows:

```python
self.assertEqual(len(result["rows"]), 200)
```

Use the existing responder convention of at most 200 rows as the UI payload boundary.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest tests.test_access_control.QueryRoleTests -v
```

Expected: failure because the query response has no `rows` key.

- [ ] **Step 3: Add rows to the existing response**

Modify only the final success dictionary in `backend/app/routers/query.py`:

```python
return {
    "status": "ok",
    "answer": answer.get("answer", ""),
    "figure_b64": figure_b64,
    "sql": sql_result.get("final_sql"),
    "row_count": sql_result.get("row_count"),
    "rows": sql_result.get("rows", [])[:200],
    "parsed": parsed,
}
```

- [ ] **Step 4: Run focused and full backend tests**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all backend tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/routers/query.py backend/tests/test_access_control.py
git commit -m "feat: expose bounded raw query rows"
```

## Task 6: Frontend Auth API and Session State

**Files:**

- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/auth.tsx`
- Create: `frontend/src/test/setup.ts`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Install the minimal frontend test dependencies**

Run:

```powershell
Set-Location frontend
npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Add scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Add Vite test configuration:

```typescript
test: {
  environment: 'jsdom',
  setupFiles: './src/test/setup.ts',
}
```

Create `frontend/src/test/setup.ts`:

```typescript
import '@testing-library/jest-dom/vitest';
```

- [ ] **Step 2: Write a failing auth-provider contract test in `frontend/src/App.test.tsx`**

Start with a minimal test:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { AuthProvider } from './auth';

afterEach(() => vi.restoreAllMocks());

describe('authentication shell', () => {
  it('shows login when session restoration returns 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { error: 'authentication_required' } }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    ));
    render(
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider><App /></AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Run the test and verify RED**

Run:

```powershell
Set-Location frontend
npm test -- src/App.test.tsx
```

Expected: import failures because `AuthProvider` and login UI do not exist.

- [ ] **Step 4: Add credentialed API helpers and auth state**

In `frontend/src/api.ts`:

```typescript
export type Role = 'user' | 'super_admin';
export interface AuthUser { username: string; role: Role; }

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function request(input: RequestInfo | URL, init: RequestInit = {}) {
  const response = await fetch(input, { ...init, credentials: 'include' });
  if (response.status === 401) onUnauthorized?.();
  return response;
}

export async function login(username: string, password: string): Promise<AuthUser> {
  return unwrap(await request('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }));
}

export async function getCurrentUser(): Promise<AuthUser> {
  return unwrap(await request('/api/auth/me'));
}

export async function logout(): Promise<void> {
  await unwrap(await request('/api/auth/logout', { method: 'POST' }));
}
```

Replace every existing direct Fetch API call with the `request` wrapper; do not change URLs, payloads, or response handling.

Create `frontend/src/auth.tsx`:

```tsx
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  setUnauthorizedHandler,
  type AuthUser,
} from './api';

interface AuthValue {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    getCurrentUser().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  const value = useMemo<AuthValue>(() => ({
    user,
    loading,
    login: async (username, password) => setUser(await loginRequest(username, password)),
    logout: async () => {
      try { await logoutRequest(); } finally { setUser(null); }
    },
  }), [user, loading]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
```

Wrap `<App />` with `<AuthProvider>` in `frontend/src/main.tsx`.

- [ ] **Step 5: Re-run the focused test**

Run:

```powershell
Set-Location frontend
npm test -- src/App.test.tsx
```

Expected: it still fails only because `App` does not yet render the login page; auth state compiles.

- [ ] **Step 6: Commit**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/api.ts frontend/src/auth.tsx frontend/src/test/setup.ts frontend/src/main.tsx frontend/src/App.test.tsx
git commit -m "feat: add frontend session state"
```

## Task 7: Login Page and Role-Protected Navigation

**Files:**

- Create: `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/pages/QueryPage.tsx`

- [ ] **Step 1: Expand failing role/navigation tests**

Add tests to `frontend/src/App.test.tsx` for:

```tsx
it('hides upload navigation from a read-only user', async () => {
  mockAuthenticatedUser({ username: 'igna.user@gmail.com', role: 'user' });
  renderApp('/');
  expect(await screen.findByText('igna.user@gmail.com')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /upload csv/i })).not.toBeInTheDocument();
});

it('shows upload navigation to super admin', async () => {
  mockAuthenticatedUser({ username: 'igna.admin@gmail.com', role: 'super_admin' });
  renderApp('/');
  expect(await screen.findByRole('link', { name: /upload csv/i })).toBeInTheDocument();
});

it('redirects a read-only user away from upload', async () => {
  mockAuthenticatedUser({ username: 'igna.user@gmail.com', role: 'user' });
  renderApp('/upload');
  expect(await screen.findByText(/ask any question/i)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /upload csv/i })).not.toBeInTheDocument();
});
```

Use a shared fetch mock that returns the authenticated user for `/api/auth/me`, an empty table list for `/api/tables`, and appropriate JSON for other startup calls.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
Set-Location frontend
npm test -- src/App.test.tsx
```

Expected: login/protected navigation assertions fail.

- [ ] **Step 3: Implement the login page**

Create `frontend/src/pages/LoginPage.tsx`:

```tsx
import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../auth';

export default function LoginPage() {
  const { user, login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await login(username, password);
    } catch {
      setError('Invalid username or password.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen grid place-items-center bg-slate-100 px-4">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <div>
          <h1 className="text-xl font-semibold">Sign in</h1>
          <p className="mt-1 text-sm text-slate-500">IGNA Query Agent</p>
        </div>
        <label className="block text-sm font-medium">
          Email
          <input className="mt-1 w-full rounded border px-3 py-2" type="email" value={username} onChange={e => setUsername(e.target.value)} required />
        </label>
        <label className="block text-sm font-medium">
          Password
          <input className="mt-1 w-full rounded border px-3 py-2" type="password" value={password} onChange={e => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        <button disabled={busy} className="w-full rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: Make the application shell role-aware**

Refactor `frontend/src/App.tsx` minimally:

```tsx
function ProtectedApp() {
  const { user, loading, logout } = useAuth();
  if (loading) return <div className="min-h-screen grid place-items-center text-slate-500">Loading...</div>;
  if (!user) return <LoginPage />;

  const isAdmin = user.role === 'super_admin';
  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b bg-white">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center gap-6">
          <Link to="/" className="font-semibold text-lg">IGNA Query Agent</Link>
          <nav className="flex gap-4 text-sm">
            <NavLink to="/" end>Query</NavLink>
            {isAdmin && <NavLink to="/upload">Upload CSV</NavLink>}
          </nav>
          <div className="ml-auto text-right">
            <div className="text-sm">{user.username}</div>
            <div className="text-xs text-slate-500">{isAdmin ? 'Super Admin' : 'User'}</div>
          </div>
          <button onClick={logout} className="text-sm text-slate-600 hover:text-slate-900">Logout</button>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-6">
        <Routes>
          <Route path="/" element={<QueryPage />} />
          <Route path="/upload" element={isAdmin ? <UploadPage /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return <ProtectedApp />;
}
```

Use the existing header/main Tailwind classes exactly where possible.

In `QueryPage.tsx`, read `user` from `useAuth()` and render `rebuild context` plus pending confirmation controls only when `user.role === 'super_admin'`.

- [ ] **Step 5: Run role/navigation tests and production build**

Run:

```powershell
Set-Location frontend
npm test -- src/App.test.tsx
npm run build
```

Expected: tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/pages/LoginPage.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/pages/QueryPage.tsx
git commit -m "feat: add role-protected application shell"
```

## Task 8: Scrollable Raw Data Disclosure

**Files:**

- Create: `frontend/src/components/RawDataTable.test.tsx`
- Create: `frontend/src/components/RawDataTable.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/QueryPage.tsx`

- [ ] **Step 1: Write failing raw-data component tests**

Create `frontend/src/components/RawDataTable.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import RawDataTable from './RawDataTable';

describe('RawDataTable', () => {
  it('shows a clear empty state', () => {
    render(<RawDataTable rows={[]} />);
    expect(screen.getByText('No rows returned.')).toBeInTheDocument();
  });

  it('renders all discovered columns and values', () => {
    render(<RawDataTable rows={[
      { ticket_id: 7, subject: 'Wide subject' },
      { ticket_id: 8, status: 'Open' },
    ]} />);
    expect(screen.getByRole('columnheader', { name: 'ticket_id' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'subject' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'status' })).toBeInTheDocument();
    expect(screen.getByText('Wide subject')).toBeInTheDocument();
  });

  it('uses a bounded two-axis scroll container', () => {
    render(<RawDataTable rows={[{ a: 1, b: 2 }]} />);
    expect(screen.getByTestId('raw-data-scroll')).toHaveClass('max-h-72', 'overflow-auto');
  });
});
```

- [ ] **Step 2: Run component tests and verify RED**

Run:

```powershell
Set-Location frontend
npm test -- src/components/RawDataTable.test.tsx
```

Expected: import failure because `RawDataTable` does not exist.

- [ ] **Step 3: Implement the bounded raw-data table**

Create `frontend/src/components/RawDataTable.tsx`:

```tsx
function display(value: unknown): string {
  if (value == null) return 'null';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function RawDataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <p className="p-2 text-xs text-slate-500">No rows returned.</p>;
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));

  return (
    <div data-testid="raw-data-scroll" className="mt-1 max-h-72 max-w-full overflow-auto rounded border bg-white">
      <table className="min-w-max border-collapse text-left text-xs">
        <thead className="sticky top-0 z-10 bg-slate-100 text-slate-700">
          <tr>{columns.map(column => <th key={column} className="whitespace-nowrap border-b border-r px-2 py-1.5 font-medium">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="even:bg-slate-50">
              {columns.map(column => (
                <td key={column} className="max-w-sm whitespace-nowrap border-b border-r px-2 py-1.5 font-mono text-slate-700">
                  {display(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Add rows to the query message contract and disclosure**

In `frontend/src/api.ts`:

```typescript
export interface QueryResponse {
  status: 'ok' | 'out_of_scope' | 'confirmation_required';
  answer?: string;
  figure_b64?: string | null;
  sql?: string;
  row_count?: number;
  parsed?: Record<string, unknown>;
  reason?: string;
  confirmation_id?: string;
  summary?: string;
  expires_at?: string;
  rows?: Record<string, unknown>[];
}
```

In `QueryPage.tsx`, extend `Msg`:

```typescript
rows?: Record<string, unknown>[];
```

Store `rows: r.rows ?? []` for successful read responses. Beside the existing SQL disclosure, render:

```tsx
{m.rows && (
  <details className="mt-2 text-xs text-slate-500">
    <summary className="cursor-pointer select-none">
      Raw data ({m.rows.length} row{m.rows.length === 1 ? '' : 's'})
    </summary>
    <RawDataTable rows={m.rows} />
  </details>
)}
```

Do not issue a fetch when the disclosure opens.

- [ ] **Step 5: Run frontend tests and build**

Run:

```powershell
Set-Location frontend
npm test
npm run build
```

Expected: all tests pass; build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/components/RawDataTable.tsx frontend/src/components/RawDataTable.test.tsx frontend/src/api.ts frontend/src/pages/QueryPage.tsx
git commit -m "feat: add scrollable raw data disclosure"
```

## Task 9: Documentation and Full Verification

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Update operational documentation**

Add a concise section covering:

```markdown
## Local access control

| Role | Username | Password | Access |
|---|---|---|---|
| Super Admin | `igna.admin@gmail.com` | `admin@123` | Reads, uploads, context rebuilds, confirmed DML/DDL |
| User | `igna.user@gmail.com` | `user@123` | Reads only |

- No signup or JWT is used.
- Authentication uses an HTTP-only browser-session cookie; closing the browser or logging out ends the session.
- Backend restart invalidates active sessions.
- Raw SQL pasted into chat is rejected for both roles. Super Admin must describe writes in natural language and confirm generated SQL.
```

Update the limitations section from “No auth / login” to state that credentials are hardcoded and intended for controlled/local deployment.

- [ ] **Step 2: Run complete backend verification**

Run:

```powershell
Set-Location backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all backend tests pass.

- [ ] **Step 3: Run complete frontend verification**

Run:

```powershell
Set-Location frontend
npm test
npm run build
```

Expected: all frontend tests pass and production build succeeds without TypeScript errors.

- [ ] **Step 4: Perform browser smoke verification**

Start the existing backend/frontend development servers and verify:

1. Unauthenticated `/` shows login.
2. User login shows Query only; direct `/upload` redirects.
3. User read question succeeds and Raw data expands within a bounded two-axis scroll area.
4. User natural-language write request returns a read-only denial and creates no confirmation.
5. Both roles receive `raw_sql_denied` for `SELECT * FROM tickets` and `DROP TABLE tickets`.
6. Super Admin sees Upload CSV and context rebuild.
7. Super Admin natural-language destructive request returns confirmation; cancel makes no DB change.
8. A separately generated Super Admin confirmation succeeds only in its creating session.
9. Logout returns to login; browser close does not leave a persistent cookie.

- [ ] **Step 5: Check diff scope**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only planned authentication, authorization, guardrail, raw-data, tests, lockfile, and documentation files changed.

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md
git commit -m "docs: document application access roles"
```

## Plan Self-Review

- Every design requirement maps to a task.
- Authentication is server-authoritative and does not use JWT or signup.
- Browser-session cookie has no persistence attributes.
- Every write-capable route receives an explicit Super Admin dependency or an in-function role gate.
- Raw SQL is rejected before context, LLM, pending-write, or database work.
- Existing planner, database adapter, upload implementation, and confirmation business logic remain intact.
- Pending confirmation ownership is session-bound.
- Raw rows reuse existing query results and are bounded to 200.
- Frontend role hiding is backed by backend enforcement.
- Backend behavior is developed with failing tests first; frontend state and UI components have focused tests before implementation.
