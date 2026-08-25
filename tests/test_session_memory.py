"""SQLite transcript and deterministic L2 memory regression tests."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from agent_app.api.repository import SQLitePlanRepository
from agent_app.api.services.memory import MemoryPolicy, SessionMemoryManager
from tests.test_api import _plan_payload


class SessionMemoryTests(unittest.TestCase):
    @staticmethod
    def _request(**updates):
        request = {
            "destination_city": "北京",
            "start_date": "2099-08-01",
            "end_date": "2099-08-01",
            "preferences": ["历史文化"],
            "budget_cny": 1500,
            "accommodation_type": "经济型",
        }
        request.update(updates)
        return request

    @staticmethod
    def _complete(repository, request, plan, context):
        task, _created = repository.create_task(request, None, "user_test")
        if not repository.set_task_running(task["task_id"]):
            raise AssertionError("测试任务未进入 running")
        return repository.complete_task(task["task_id"], plan, context)

    def test_message_lifecycle_excludes_failed_turns_from_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(Path(directory) / "memory.db")
            request = self._request()
            context = {
                "request": request,
                "attractions": "景点研究",
                "weather": "天气研究",
                "hotels": "酒店研究",
            }
            plan = self._complete(
                repository, request, _plan_payload(), context
            )

            follow_up = self._request(
                session_id=plan["session_id"],
                previous_plan_id=plan["plan_id"],
                additional_requirements="这个请求会失败",
            )
            failed, _created = repository.create_task(
                follow_up, None, "user_test"
            )
            self.assertTrue(repository.set_task_running(failed["task_id"]))
            self.assertTrue(repository.fail_task(failed["task_id"], "失败"))

            messages = repository.list_session_messages(plan["session_id"])
            self.assertEqual(
                ["committed", "committed", "failed"],
                [message["state"] for message in messages],
            )
            memory = repository.get_memory_turns(
                plan["session_id"], plan["plan_id"]
            )
            self.assertEqual(["user", "assistant"], [m["role"] for m in memory])
            self.assertNotIn(
                "这个请求会失败",
                [message["content_text"] for message in memory],
            )

    def test_l2_bundle_keeps_all_users_latest_anchor_and_revision_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(Path(directory) / "memory.db")
            first_request = self._request()
            first_context = {
                "request": first_request,
                "attractions": "第一版景点研究",
                "weather": "第一版天气研究",
                "hotels": "第一版酒店研究",
            }
            first = self._complete(
                repository,
                first_request,
                _plan_payload(),
                first_context,
            )

            second_request = self._request(
                budget_cny=2000,
                additional_requirements="预算调整到2000",
                session_id=first["session_id"],
                previous_plan_id=first["plan_id"],
            )
            second_plan = deepcopy(_plan_payload())
            second_plan["request_summary"]["budget_cny"] = 2000
            second_plan["budget_summary"]["total_budget"] = 2000
            second_context = {
                **first_context,
                "request": second_request,
                "hotels": "第二版酒店研究",
            }
            second = self._complete(
                repository,
                second_request,
                second_plan,
                second_context,
            )

            current_request = self._request(
                budget_cny=2000,
                additional_requirements="每天十点后出发",
                session_id=second["session_id"],
                previous_plan_id=second["plan_id"],
            )
            current_task, _created = repository.create_task(
                current_request, None, "user_test"
            )
            manager = SessionMemoryManager(repository)
            bundle = manager.build(
                task_id=current_task["task_id"],
                session_id=second["session_id"],
                previous_envelope=second,
                current_request=current_request,
            )

            self.assertEqual("layered_l2", bundle["memory_mode"])
            self.assertEqual(2, len(bundle["all_user_messages"]))
            self.assertEqual(
                "预算调整到2000",
                bundle["all_user_messages"][1]["text"],
            )
            self.assertEqual(2, len(bundle["revision_ledger"]))
            self.assertEqual(
                {"from": 1500, "to": 2000},
                bundle["revision_ledger"][1]["plan_changes"]["budget_cny"],
            )
            self.assertEqual(
                second["plan_id"],
                bundle["latest_anchor"]["version"]["plan_id"],
            )
            self.assertEqual(
                "第二版酒店研究",
                bundle["latest_anchor"]["research"]["hotels"],
            )
            self.assertEqual(
                "每天十点后出发",
                bundle["current_request"]["additional_requirements"],
            )
            assemblies = repository.list_memory_assemblies(
                current_task["task_id"]
            )
            self.assertEqual(1, len(assemblies))
            self.assertEqual("layered_l2", assemblies[0]["mode"])

    def test_recent_assistant_plans_are_dropped_before_mandatory_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(Path(directory) / "memory.db")
            first_request = self._request()
            context = {
                "request": first_request,
                "attractions": "景点研究",
                "weather": "天气研究",
                "hotels": "酒店研究",
            }
            first = self._complete(
                repository, first_request, _plan_payload(), context
            )
            second_request = self._request(
                additional_requirements="第二版",
                session_id=first["session_id"],
                previous_plan_id=first["plan_id"],
            )
            second = self._complete(
                repository,
                second_request,
                _plan_payload(),
                {**context, "request": second_request},
            )
            current_request = self._request(
                additional_requirements="第三版",
                session_id=second["session_id"],
                previous_plan_id=second["plan_id"],
            )
            current_task, _created = repository.create_task(
                current_request, None, "user_test"
            )
            manager = SessionMemoryManager(
                repository,
                MemoryPolicy(
                    context_window_tokens=100_000,
                    input_limit_tokens=80_000,
                    target_tokens=1_000,
                    recent_assistant_tokens=10_000,
                ),
            )
            bundle = manager.build(
                task_id=current_task["task_id"],
                session_id=second["session_id"],
                previous_envelope=second,
                current_request=current_request,
            )

            self.assertEqual([], bundle["recent_assistant_plans"])
            self.assertEqual(1, bundle["memory_stats"]["omitted_assistant_count"])
            self.assertEqual(2, len(bundle["all_user_messages"]))
            self.assertEqual(second["plan"], bundle["latest_anchor"]["full_plan"])

    def test_manual_edit_and_fork_create_complete_message_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLitePlanRepository(Path(directory) / "memory.db")
            request = self._request()
            context = {
                "request": request,
                "attractions": "景点研究",
                "weather": "天气研究",
                "hotels": "酒店研究",
            }
            first = self._complete(
                repository, request, _plan_payload(), context
            )
            edited = repository.update_plan(
                first["plan_id"],
                1,
                _plan_payload(),
                "user_test",
            )
            source_messages = repository.list_session_messages(
                first["session_id"]
            )
            self.assertEqual(
                ["requirement", "plan", "manual_edit", "manual_edit"],
                [message["message_type"] for message in source_messages],
            )

            forked = repository.fork_session(
                first["session_id"], edited["plan_id"], "user_test"
            )
            fork_messages = repository.list_session_messages(
                forked["session_id"]
            )
            self.assertEqual(2, len(fork_messages))
            self.assertEqual(
                ["fork", "fork"],
                [message["message_type"] for message in fork_messages],
            )
            self.assertTrue(
                all(
                    message["state"] == "committed"
                    for message in fork_messages
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
