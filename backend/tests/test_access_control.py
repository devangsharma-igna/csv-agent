import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.params import Depends
from fastapi.testclient import TestClient

from app.auth import (
    SESSION_COOKIE,
    CurrentUser,
    Role,
    require_super_admin,
    require_user,
    sessions,
)
from app.main import app
from app.pending_writes import PendingWriteStore
from app.routers.csv import csv_commit, csv_preview
from app.routers.query import (
    ConfirmRequest,
    QueryRequest,
    cancel_query,
    confirm_query,
    query,
)
from app.routers.tables import get_context_summary, list_tables, refresh_context


USER = CurrentUser("igna.user@gmail.com", Role.USER, "user-session")
ADMIN = CurrentUser("igna.admin@gmail.com", Role.SUPER_ADMIN, "admin-session")
OTHER_ADMIN = CurrentUser(
    "igna.admin@gmail.com",
    Role.SUPER_ADMIN,
    "other-admin-session",
)


class RouteDependencyTests(unittest.TestCase):
    def assert_dependency(self, endpoint, parameter: str, dependency) -> None:
        default = inspect.signature(endpoint).parameters[parameter].default
        self.assertIsInstance(default, Depends)
        self.assertIs(default.dependency, dependency)

    def test_authenticated_route_dependencies_are_wired(self) -> None:
        self.assert_dependency(query, "user", require_user)
        self.assert_dependency(list_tables, "_user", require_user)
        self.assert_dependency(get_context_summary, "_user", require_user)

    def test_super_admin_route_dependencies_are_wired(self) -> None:
        self.assert_dependency(csv_preview, "_admin", require_super_admin)
        self.assert_dependency(csv_commit, "_admin", require_super_admin)
        self.assert_dependency(refresh_context, "_admin", require_super_admin)
        self.assert_dependency(confirm_query, "admin", require_super_admin)
        self.assert_dependency(cancel_query, "admin", require_super_admin)


class AuthenticationEntryPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)

    def test_table_and_query_routes_require_auth_before_business_logic(self) -> None:
        with (
            patch("app.routers.tables.mcp.list_tables", new=AsyncMock()) as list_mock,
            patch("app.routers.tables.mcp.table_exists", new=AsyncMock()) as exists_mock,
            patch("app.routers.query.QueryPlanner.plan", new=AsyncMock()) as planner,
        ):
            responses = [
                self.client.get("/api/tables"),
                self.client.get("/api/tables/tickets/context"),
                self.client.post(
                    "/api/query",
                    json={"table": "tickets", "question": "Show open tickets"},
                ),
            ]

        self.assertEqual([response.status_code for response in responses], [401, 401, 401])
        list_mock.assert_not_awaited()
        exists_mock.assert_not_awaited()
        planner.assert_not_awaited()

    def test_user_is_denied_admin_routes_before_business_logic(self) -> None:
        session_id = sessions.create(CurrentUser(USER.username, USER.role))
        self.addCleanup(sessions.revoke, session_id)
        self.client.cookies.set(SESSION_COOKIE, session_id)

        with (
            patch("app.routers.csv.parse_csv") as parse_csv,
            patch("app.routers.csv.replace_table", new=AsyncMock()) as replace_table,
            patch("app.routers.tables.mcp.table_exists", new=AsyncMock()) as exists_mock,
            patch("app.routers.query.mcp.execute_sql", new=AsyncMock()) as execute_sql,
        ):
            responses = [
                self.client.post(
                    "/api/csv/preview",
                    files={"file": ("tickets.csv", b"id\n1\n", "text/csv")},
                ),
                self.client.post(
                    "/api/csv/commit",
                    json={
                        "preview_id": "missing",
                        "table_name": "tickets",
                        "columns": [],
                        "primary_keys": [],
                    },
                ),
                self.client.post("/api/tables/tickets/refresh"),
                self.client.post(
                    "/api/query/confirm",
                    json={"confirmation_id": "missing"},
                ),
                self.client.delete("/api/query/pending/missing"),
            ]

        self.assertEqual([response.status_code for response in responses], [403] * 5)
        parse_csv.assert_not_called()
        replace_table.assert_not_awaited()
        exists_mock.assert_not_awaited()
        execute_sql.assert_not_awaited()


class PendingWriteOwnershipTests(unittest.TestCase):
    def test_unauthorized_attempt_does_not_consume_pending_write(self) -> None:
        store = PendingWriteStore()
        pending = store.create(
            table="tickets",
            sql="DROP TABLE tickets",
            summary="Drop tickets.",
            affected_tables=["tickets"],
            owner_session_id=ADMIN.session_id,
        )

        self.assertIsNone(
            store.consume(pending.confirmation_id, OTHER_ADMIN.session_id)
        )
        self.assertFalse(
            store.cancel(pending.confirmation_id, OTHER_ADMIN.session_id)
        )
        self.assertEqual(
            store.consume(pending.confirmation_id, ADMIN.session_id),
            pending,
        )


class QueryRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_read_returns_at_most_200_rows(self) -> None:
        rows = [{"ticket_id": index} for index in range(250)]
        plan = {
            "allowed": True,
            "operation": "read",
            "final_sql": "SELECT ticket_id FROM tickets",
            "row_count": len(rows),
        }
        with (
            patch("app.routers.query.TableExistenceGate.check", new=AsyncMock()),
            patch(
                "app.routers.query.context_store.load",
                return_value={"table": "tickets", "columns": []},
            ),
            patch(
                "app.routers.query.QueryPlanner.plan",
                new=AsyncMock(return_value=(plan, rows)),
            ),
            patch(
                "app.routers.query.NLResponder.respond",
                new=AsyncMock(
                    return_value={"answer": "Tickets.", "wants_figure": False}
                ),
            ),
        ):
            result = await query(
                QueryRequest(table="tickets", question="Show all tickets"),
                USER,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows"], rows[:200])

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
            patch(
                "app.routers.query.context_store.load",
                return_value={"table": "tickets", "columns": []},
            ),
            patch(
                "app.routers.query.QueryPlanner.plan",
                new=AsyncMock(return_value=(plan, [])),
            ),
            patch("app.routers.query.pending_writes.create") as create,
            patch(
                "app.routers.query.mcp.execute_sql",
                new=AsyncMock(),
            ) as execute_sql,
        ):
            with self.assertRaises(HTTPException) as raised:
                await query(
                    QueryRequest(
                        table="tickets",
                        question="Remove the tickets table",
                    ),
                    USER,
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail,
            {"error": "read_only_role"},
        )
        create.assert_not_called()
        execute_sql.assert_not_awaited()

    async def test_admin_write_is_bound_to_session_and_requires_confirmation(self) -> None:
        plan = {
            "allowed": True,
            "operation": "write",
            "final_sql": "DROP TABLE tickets",
            "summary": "Drop tickets.",
            "affected_tables": ["tickets"],
        }
        pending = SimpleNamespace(
            confirmation_id="confirmation-id",
            summary=plan["summary"],
            sql=plan["final_sql"],
            expires_at=1_900_000_000,
        )
        with (
            patch("app.routers.query.TableExistenceGate.check", new=AsyncMock()),
            patch(
                "app.routers.query.context_store.load",
                return_value={"table": "tickets", "columns": []},
            ),
            patch(
                "app.routers.query.QueryPlanner.plan",
                new=AsyncMock(return_value=(plan, [])),
            ),
            patch(
                "app.routers.query.pending_writes.create",
                return_value=pending,
            ) as create,
        ):
            result = await query(
                QueryRequest(
                    table="tickets",
                    question="Remove the tickets table",
                ),
                ADMIN,
            )

        self.assertEqual(result["status"], "confirmation_required")
        create.assert_called_once_with(
            table="tickets",
            sql=plan["final_sql"],
            summary=plan["summary"],
            affected_tables=["tickets"],
            owner_session_id=ADMIN.session_id,
        )


class PendingWriteRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_admin_cannot_consume_then_owner_can_confirm(self) -> None:
        store = PendingWriteStore()
        pending = store.create(
            table="tickets",
            sql="DELETE FROM tickets WHERE ticket_id = 7",
            summary="Delete ticket 7.",
            affected_tables=["tickets"],
            owner_session_id=ADMIN.session_id,
        )
        with (
            patch("app.routers.query.pending_writes", store),
            patch(
                "app.routers.query.mcp.execute_sql",
                new=AsyncMock(return_value=[{"ticket_id": 7}]),
            ) as execute_sql,
            patch(
                "app.routers.query.mcp.table_exists",
                new=AsyncMock(return_value=False),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await confirm_query(
                    ConfirmRequest(confirmation_id=pending.confirmation_id),
                    OTHER_ADMIN,
                )

            result = await confirm_query(
                ConfirmRequest(confirmation_id=pending.confirmation_id),
                ADMIN,
            )

        self.assertEqual(raised.exception.status_code, 410)
        self.assertEqual(result["status"], "write_ok")
        execute_sql.assert_awaited_once_with(pending.sql)

    async def test_wrong_admin_cannot_cancel_then_owner_can_cancel(self) -> None:
        store = PendingWriteStore()
        pending = store.create(
            table="tickets",
            sql="DROP TABLE tickets",
            summary="Drop tickets.",
            affected_tables=["tickets"],
            owner_session_id=ADMIN.session_id,
        )
        with patch("app.routers.query.pending_writes", store):
            unauthorized = await cancel_query(
                pending.confirmation_id,
                OTHER_ADMIN,
            )
            owner = await cancel_query(
                pending.confirmation_id,
                ADMIN,
            )

        self.assertEqual(unauthorized, {"cancelled": False})
        self.assertEqual(owner, {"cancelled": True})


if __name__ == "__main__":
    unittest.main()
