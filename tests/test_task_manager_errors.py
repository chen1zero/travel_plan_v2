"""Task-level timeout and controlled failure behavior."""

from pathlib import Path
from threading import Event
import tempfile
import time
import unittest

from agent_app.api.repository import (
    SQLitePlanRepository,
    TaskStateConflictError,
)
from agent_app.api.schemas import TravelRequest
from agent_app.api.services.task_manager import TaskManager


class _BlockingHarness:
    def __init__(self) -> None:
        self.cancelled = Event()
        self.released = Event()

    def run(self, _request, **_arguments):
        self.released.wait(timeout=2)
        return "{}"

    def cancel(self) -> None:
        self.cancelled.set()
        self.released.set()

    def close(self) -> None:
        self.released.set()


class TaskManagerErrorTests(unittest.TestCase):
    @staticmethod
    def _request_data():
        return {
            "destination_city": "北京",
            "start_date": "2099-08-18",
            "end_date": "2099-08-18",
            "preferences": ["历史文化"],
            "budget_cny": 1500,
            "accommodation_type": "经济型",
        }

    def test_timeout_marks_failure_and_cancels_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(
                Path(directory) / "timeout.db"
            )
            harness = _BlockingHarness()
            manager = TaskManager(
                repository,
                lambda _callback: harness,
                max_workers=1,
                task_timeout_seconds=0.05,
            )
            try:
                task = manager.create_task(
                    TravelRequest(
                        destination_city="北京",
                        start_date="2099-08-18",
                        end_date="2099-08-18",
                        preferences=["历史文化"],
                        budget_cny=1500,
                        accommodation_type="经济型",
                    ),
                    None,
                    "user_timeout",
                )
                deadline = time.monotonic() + 2
                current = repository.get_task(task["task_id"])
                while (
                    current is not None
                    and current["status"] not in {"completed", "failed"}
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                    current = repository.get_task(task["task_id"])

                self.assertIsNotNone(current)
                self.assertEqual("failed", current["status"])
                self.assertEqual("TASK_TIMEOUT", current["error_code"])
                self.assertTrue(current["retryable"])
                self.assertTrue(harness.cancelled.wait(timeout=1))
                failed_events = [
                    event
                    for event in repository.list_events(task["task_id"])
                    if event["type"] == "task.failed"
                ]
                self.assertEqual(1, len(failed_events))
                self.assertEqual(
                    "TASK_TIMEOUT",
                    failed_events[0]["error_code"],
                )
            finally:
                manager.shutdown()

    def test_terminal_task_state_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(
                Path(directory) / "state-race.db"
            )
            failed_task, _ = repository.create_task(
                self._request_data(), None, "user_failure"
            )
            self.assertTrue(
                repository.set_task_running(failed_task["task_id"])
            )
            self.assertTrue(
                repository.fail_task(failed_task["task_id"], "超时")
            )
            with self.assertRaises(TaskStateConflictError):
                repository.complete_task(failed_task["task_id"], {})

            completed_task, _ = repository.create_task(
                self._request_data(), None, "user_completed"
            )
            self.assertTrue(
                repository.set_task_running(completed_task["task_id"])
            )
            repository.complete_task(completed_task["task_id"], {})
            self.assertFalse(
                repository.fail_task(completed_task["task_id"], "超时")
            )
            current = repository.get_task(completed_task["task_id"])
            self.assertEqual("completed", current["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
