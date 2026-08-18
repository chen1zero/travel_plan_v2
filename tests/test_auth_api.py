"""HTTP tests for the minimal local-account flow and data isolation."""

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from agent_app.api.app import create_app
from agent_app.api.config import APISettings
from agent_app.api.repository import SQLitePlanRepository
from tests.test_api import _fake_harness, _fake_route_rebuilder


class AuthenticationAPITests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "auth.db"
        self._database_path = database_path
        repository = SQLitePlanRepository(database_path)
        self.app = create_app(
            api_settings=APISettings(
                database_path=database_path,
                max_workers=1,
                sse_poll_interval_seconds=0.01,
                sse_heartbeat_seconds=0.05,
            ),
            repository=repository,
            agent_factory=_fake_harness,
            route_rebuilder=_fake_route_rebuilder,
        )
        self._client_context = TestClient(self.app)
        self.client = self._client_context.__enter__()

    def tearDown(self):
        self._client_context.__exit__(None, None, None)
        self._temporary_directory.cleanup()

    @staticmethod
    def _travel_request():
        return {
            "destination_city": "北京",
            "start_date": "2099-08-01",
            "end_date": "2099-08-01",
            "preferences": ["历史文化"],
            "budget_cny": 1500,
            "accommodation_type": "经济型",
        }

    def _wait_for_completion(self, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.client.get(
                f"/api/travel-plans/{task_id}"
            ).json()
            if task["status"] in {"completed", "failed"}:
                return task
            time.sleep(0.01)
        self.fail("规划任务未在测试期限内完成")

    def test_register_login_logout_and_protected_route(self):
        protected = self.client.get("/api/sessions")
        self.assertEqual(401, protected.status_code)

        invalid_username = self.client.post(
            "/api/auth/register",
            json={"username": " a ", "password": "password-123"},
        )
        self.assertEqual(422, invalid_username.status_code)

        registered = self.client.post(
            "/api/auth/register",
            json={"username": "Alice_1", "password": "password-123"},
        )
        self.assertEqual(201, registered.status_code, registered.text)
        self.assertEqual("alice_1", registered.json()["username"])
        self.assertIn("HttpOnly", registered.headers["set-cookie"])
        raw_token = self.client.cookies.get("travel_session")
        with sqlite3.connect(self._database_path) as connection:
            stored_password, stored_token = connection.execute(
                """
                SELECT u.password_hash, auth.token_hash
                FROM users u
                JOIN auth_sessions auth ON auth.user_id = u.user_id
                WHERE u.username = 'alice_1'
                """
            ).fetchone()
        self.assertNotIn("password-123", stored_password)
        self.assertNotEqual(raw_token, stored_token)

        duplicate = self.client.post(
            "/api/auth/register",
            json={"username": "ALICE_1", "password": "password-456"},
        )
        self.assertEqual(409, duplicate.status_code)

        current = self.client.get("/api/auth/me")
        self.assertEqual(200, current.status_code)
        self.assertEqual("alice_1", current.json()["username"])

        logged_out = self.client.post("/api/auth/logout")
        self.assertEqual(204, logged_out.status_code)
        self.assertEqual(401, self.client.get("/api/auth/me").status_code)

        bad_login = self.client.post(
            "/api/auth/login",
            json={"username": "alice_1", "password": "wrong-pass"},
        )
        self.assertEqual(401, bad_login.status_code)
        logged_in = self.client.post(
            "/api/auth/login",
            json={"username": "alice_1", "password": "password-123"},
        )
        self.assertEqual(200, logged_in.status_code)

    def test_plans_are_isolated_between_users(self):
        self.client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
        created = self.client.post(
            "/api/travel-plans",
            json=self._travel_request(),
        )
        self.assertEqual(202, created.status_code, created.text)
        task_id = created.json()["task_id"]
        task = self._wait_for_completion(task_id)
        plan_id = task["plan_id"]

        self.client.post("/api/auth/logout")
        self.client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "password-456"},
        )

        self.assertEqual(
            404,
            self.client.get(f"/api/travel-plans/{task_id}").status_code,
        )
        self.assertEqual(
            404,
            self.client.get(f"/api/plans/{plan_id}").status_code,
        )
        self.assertEqual([], self.client.get("/api/sessions").json()["items"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
