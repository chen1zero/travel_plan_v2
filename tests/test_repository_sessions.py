"""SQLite migration tests for session-aware planning history."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_app.api.repository import SQLitePlanRepository
from tests.test_api import _plan_payload


class RepositorySessionMigrationTests(unittest.TestCase):
    def test_persists_structured_failure_context(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(
                Path(directory) / "failures.db"
            )
            task, _created = repository.create_task(
                {
                    "destination_city": "北京",
                    "start_date": "2099-08-18",
                    "end_date": "2099-08-18",
                    "preferences": ["历史文化"],
                    "budget_cny": 1500,
                    "accommodation_type": "经济型",
                },
                None,
                "user_test",
            )
            repository.fail_task(
                task["task_id"],
                "外部服务暂时不可用",
                error_code="NETWORK_ERROR",
                retryable=True,
                error_id="err_test",
            )
            repository.append_event(
                task["task_id"],
                "tool.failed",
                "地图服务调用失败",
                stage="weather",
                metadata={
                    "tool": "maps_weather",
                    "tool_call_id": "call_test",
                    "attempt": 2,
                    "error_code": "NETWORK_ERROR",
                    "retryable": True,
                },
            )

            failed = repository.get_task(task["task_id"])
            event = repository.list_events(task["task_id"])[0]

            self.assertEqual("NETWORK_ERROR", failed["error_code"])
            self.assertTrue(failed["retryable"])
            self.assertEqual("err_test", failed["error_id"])
            self.assertEqual("maps_weather", event["tool"])
            self.assertEqual(2, event["attempt"])

    def test_migrates_a_legacy_plan_into_a_reusable_session(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    current_stage TEXT,
                    plan_id TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    plan_json TEXT NOT NULL
                );
                CREATE TABLE events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    stage TEXT,
                    message TEXT NOT NULL,
                    plan_id TEXT
                );
                """
            )
            request = {
                "destination_city": "北京",
                "start_date": "2099-08-01",
                "end_date": "2099-08-01",
                "preferences": ["历史文化"],
                "budget_cny": 1500,
                "accommodation_type": "经济型",
            }
            connection.execute(
                """
                INSERT INTO tasks VALUES (
                    'task_legacy', NULL, 'completed', ?, NULL,
                    'plan_legacy', NULL, '2026-01-01', '2026-01-01'
                )
                """,
                (json.dumps(request, ensure_ascii=False),),
            )
            connection.execute(
                """
                INSERT INTO plans VALUES (
                    'plan_legacy', 'task_legacy', 1, 'completed',
                    '2026-01-01', ?
                )
                """,
                (json.dumps(_plan_payload(), ensure_ascii=False),),
            )
            connection.commit()
            connection.close()

            repository = SQLitePlanRepository(database_path)
            plan = repository.get_plan("plan_legacy")

            self.assertIsNotNone(plan)
            self.assertTrue(plan["session_id"].startswith("session_"))
            self.assertEqual(
                "plan_legacy",
                repository.get_session(plan["session_id"])[
                    "current_plan_id"
                ],
            )
            self.assertEqual(request, plan["context"]["request"])
            self.assertIn("故宫博物院", plan["context"]["attractions"])
            self.assertIn("测试酒店", plan["context"]["hotels"])
            self.assertEqual("agent", plan["source_type"])
            history = repository.list_sessions()
            self.assertEqual(1, len(history["items"]))
            self.assertEqual(
                plan["session_id"], history["items"][0]["session_id"]
            )
            self.assertEqual(1, history["items"][0]["revision_count"])
            turns = repository.get_session_turns(plan["session_id"])
            self.assertEqual(1, len(turns))
            self.assertEqual("task_legacy", turns[0]["task_id"])
            messages = repository.list_session_messages(plan["session_id"])
            self.assertEqual(
                ["user", "assistant"],
                [message["role"] for message in messages],
            )
            self.assertTrue(
                all(message["state"] == "committed" for message in messages)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
