import unittest
from unittest.mock import AsyncMock, patch

from app.agents.query_planner import QueryPlanner


class QueryPlannerSemanticLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_denied_lookup_when_text_columns_are_plausible(self) -> None:
        context = {
            "table": "tickets",
            "pk": ["ticket_id"],
            "columns": [
                {
                    "name": "ticket_id",
                    "type": "INTEGER",
                    "semantic": "Unique ticket identifier.",
                    "nullable": False,
                    "distinct": 42,
                    "null_pct": 0.0,
                },
                {
                    "name": "subject",
                    "type": "VARCHAR",
                    "semantic": "Free-text ticket subject or title entered by the requester.",
                    "nullable": False,
                    "distinct": 42,
                    "null_pct": 0.0,
                },
                {
                    "name": "status",
                    "type": "VARCHAR",
                    "semantic": "Current ticket status such as Open or Closed.",
                    "nullable": False,
                    "distinct": 5,
                    "null_pct": 0.0,
                },
            ],
            "sample_rows": [
                {"ticket_id": 1, "subject": "Benchmark-Office", "status": "Closed"},
                {"ticket_id": 2, "subject": "Trademark to Registered", "status": "Closed"},
                {"ticket_id": 3, "subject": "RE: TIMESHEETS PRO 365", "status": "Open"},
            ],
        }
        first_plan = {
            "allowed": False,
            "reason": "no column or value related to 'Bergen County surrogate' in this table",
            "intent": "lookup",
            "target_columns": [],
            "filters_hint": "",
            "refined_query": "",
            "final_sql": "",
            "notes": "",
        }
        rescued_plan = {
            "allowed": True,
            "reason": "answerable",
            "intent": "lookup",
            "target_columns": ["subject"],
            "filters_hint": "subject contains Bergen County surrogate",
            "refined_query": "Return tickets whose subject contains Bergen County surrogate.",
            "final_sql": "SELECT * FROM \"tickets\" WHERE \"subject\" ILIKE '%Bergen County surrogate%' LIMIT 1000",
            "notes": "",
        }

        with (
            patch("app.agents.query_planner.single_shot_json", new=AsyncMock(side_effect=[first_plan, rescued_plan])) as llm_mock,
            patch("app.agents.query_planner.mcp.execute_sql", new=AsyncMock(return_value=[{"ticket_id": 9140, "subject": "Bergen County surrogate"}])) as sql_mock,
            patch("app.agents.query_planner.TableExistenceGate.check", new=AsyncMock()),
        ):
            plan, rows = await QueryPlanner().plan(
                question="Bergen County surrogate ticket details all",
                context=context,
            )

        self.assertTrue(plan["allowed"])
        self.assertEqual(["subject"], plan["target_columns"])
        self.assertEqual(rows, [{"ticket_id": 9140, "subject": "Bergen County surrogate"}])
        self.assertEqual(llm_mock.await_count, 2)
        self.assertEqual(sql_mock.await_count, 1)
        rescue_system = llm_mock.await_args_list[1].kwargs["system"]
        self.assertIn("SEARCHABLE TEXT COLUMNS", rescue_system)
        self.assertIn("absence of an exact value in sample rows", rescue_system)

    async def test_does_not_retry_obvious_world_knowledge_denial(self) -> None:
        context = {
            "table": "tickets",
            "pk": ["ticket_id"],
            "columns": [
                {
                    "name": "subject",
                    "type": "VARCHAR",
                    "semantic": "Free-text ticket subject or title entered by the requester.",
                    "nullable": False,
                    "distinct": 42,
                    "null_pct": 0.0,
                },
                {
                    "name": "status",
                    "type": "VARCHAR",
                    "semantic": "Current ticket status such as Open or Closed.",
                    "nullable": False,
                    "distinct": 5,
                    "null_pct": 0.0,
                },
            ],
            "sample_rows": [],
        }
        denied_plan = {
            "allowed": False,
            "reason": "no GDP-related column in this table",
            "intent": "lookup",
            "target_columns": [],
            "filters_hint": "",
            "refined_query": "",
            "final_sql": "",
            "notes": "",
        }

        with (
            patch("app.agents.query_planner.single_shot_json", new=AsyncMock(return_value=denied_plan)) as llm_mock,
            patch("app.agents.query_planner.mcp.execute_sql", new=AsyncMock()) as sql_mock,
            patch("app.agents.query_planner.TableExistenceGate.check", new=AsyncMock()),
        ):
            plan, rows = await QueryPlanner().plan(
                question="What is the GDP of Brazil?",
                context=context,
            )

        self.assertFalse(plan["allowed"])
        self.assertEqual([], rows)
        self.assertEqual(llm_mock.await_count, 1)
        self.assertEqual(sql_mock.await_count, 0)


if __name__ == "__main__":
    unittest.main()
