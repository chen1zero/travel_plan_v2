"""End-to-end HTTP tests for task, SSE, plan, and edit APIs."""

import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from agent_app.api.app import create_app
from agent_app.api.config import APISettings
from agent_app.api.repository import SQLitePlanRepository
from agent_app.harness.travel_planning import TravelPlanningHarness


def _plan_payload():
    return {
        "plan_version": "1.0",
        "request_summary": {
            "destination_city": "北京",
            "start_date": "2099-08-01",
            "end_date": "2099-08-01",
            "days": 1,
            "budget_cny": 1500,
            "preferences": ["历史文化"],
            "hotel_requirement": "经济型",
            "unresolved_fields": [],
        },
        "weather_summary": [],
        "selected_hotel": {
            "name": "测试酒店",
            "address": "北京市东城区测试路1号",
            "selection_reason": "便于测试",
            "price_cny_per_night": None,
            "booking_note": "测试数据",
            "location": {
                "longitude": 116.4,
                "latitude": 39.9,
            },
        },
        "daily_itinerary": [
            {
                "day": 1,
                "date": "2099-08-01",
                "theme": "历史文化",
                "weather_advice": "注意防晒",
                "schedule": [
                    {
                        "schedule_item_id": "item-1",
                        "order": 1,
                        "time_slot": "09:00-11:00",
                        "place_name": "故宫博物院",
                        "address": "北京市东城区景山前街4号",
                        "activity": "参观",
                        "duration_minutes": 120,
                        "notes": [],
                        "location": {
                            "longitude": 116.397,
                            "latitude": 39.918,
                        },
                    }
                ],
                "routes": [
                    {
                        "route_id": "generated-route-1",
                        "sequence": 1,
                        "origin": {
                            "name": "测试酒店",
                            "address": "北京市东城区测试路1号",
                            "city": "北京",
                        },
                        "destination": {
                            "name": "故宫博物院",
                            "address": "北京市东城区景山前街4号",
                            "city": "北京",
                        },
                        "walking": {
                            "available": True,
                            "distance_km": 2.3,
                            "duration_minutes": 31,
                            "error": None,
                        },
                        "driving": {
                            "available": True,
                            "distance_km": 2.8,
                            "duration_minutes": 20,
                            "error": None,
                        },
                        "public_transit": {
                            "available": True,
                            "distance_km": 2.5,
                            "duration_minutes": 35,
                            "walking_distance_km": 0.4,
                            "transfer_count": 0,
                            "transit_type": "bus",
                            "line_names": ["测试公交线路"],
                            "error": None,
                        },
                        "recommended_mode": "walking",
                        "recommendation_reason": "模型错误推荐",
                    }
                ],
                "estimated_cost_cny": {
                    "transport": None,
                    "tickets": None,
                    "food": None,
                    "hotel": None,
                    "subtotal": None,
                    "notes": [],
                },
            }
        ],
        "budget_summary": {
            "currency": "CNY",
            "total_budget": 1500,
            "estimated_total": None,
            "remaining": None,
            "breakdown": {
                "transport": None,
                "tickets": None,
                "food": None,
                "hotel": None,
            },
            "notes": [],
        },
        "booking_and_safety_tips": ["出发前确认开放信息"],
        "data_notes": ["API 测试数据"],
    }


class _FakeQueryAgent:
    def __init__(self, result):
        self._result = result

    def run(self, _request):
        return self._result


class _FakePlannerAgent:
    def __init__(self, memory_sink=None):
        self._memory_sink = memory_sink

    def run(self, **_inputs):
        if self._memory_sink is not None:
            self._memory_sink.append(_inputs.get("session_memory"))
        return json.dumps(_plan_payload(), ensure_ascii=False)


def _fake_harness(callback, memory_sink=None):
    return TravelPlanningHarness(
        attraction_agent=_FakeQueryAgent("景点研究结果"),
        weather_agent=_FakeQueryAgent("天气研究结果"),
        hotel_agent=_FakeQueryAgent("住宿研究结果"),
        planner_agent=_FakePlannerAgent(memory_sink),
        progress_callback=callback,
    )


def _fake_route_rebuilder(plan, days):
    rebuilt = json.loads(json.dumps(days))
    for day in rebuilt:
        day["routes"] = [
            {
                "route_id": "server-route-1",
                "sequence": 1,
                "origin": {
                    "name": plan["selected_hotel"]["name"],
                    "address": plan["selected_hotel"]["address"],
                    "city": "北京",
                },
                "destination": {
                    "name": day["schedule"][0]["place_name"],
                    "address": day["schedule"][0]["address"],
                    "city": "北京",
                },
                "walking": {
                    "available": True,
                    "distance_km": 1.2,
                    "duration_minutes": 18,
                    "error": None,
                },
                "driving": {
                    "available": True,
                    "distance_km": 1.8,
                    "duration_minutes": 8,
                    "error": None,
                },
                "public_transit": {
                    "available": True,
                    "distance_km": 1.5,
                    "duration_minutes": 12,
                    "walking_distance_km": 0.3,
                    "transfer_count": 0,
                    "transit_type": "subway",
                    "line_names": ["测试地铁线"],
                    "error": None,
                },
                "recommended_mode": "driving",
                "recommendation_reason": "服务端测试路线",
            }
        ]
    return rebuilt


class TravelPlanningAPITests(unittest.TestCase):
    def setUp(self):
        self._temp_directory = tempfile.TemporaryDirectory()
        database_path = (
            Path(self._temp_directory.name) / "travel-plan-test.db"
        )
        repository = SQLitePlanRepository(database_path)
        api_settings = APISettings(
            database_path=database_path,
            cors_origins=("http://127.0.0.1:5173",),
            max_workers=1,
            sse_poll_interval_seconds=0.01,
            sse_heartbeat_seconds=0.05,
        )
        self.harness_calls = 0
        self.planner_memories = []

        def counted_harness(callback):
            self.harness_calls += 1
            return _fake_harness(callback, self.planner_memories)

        self.app = create_app(
            api_settings=api_settings,
            repository=repository,
            agent_factory=counted_harness,
            route_rebuilder=_fake_route_rebuilder,
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        registered = self.client.post(
            "/api/auth/register",
            json={
                "username": "api_test_user",
                "password": "test-password-123",
            },
        )
        self.assertEqual(201, registered.status_code, registered.text)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self._temp_directory.cleanup()

    def _create_request(self):
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
            response = self.client.get(
                f"/api/travel-plans/{task_id}"
            )
            self.assertEqual(200, response.status_code)
            task = response.json()
            if task["status"] in {"completed", "failed"}:
                return task
            time.sleep(0.01)
        self.fail("规划任务未在测试期限内完成")

    def test_health_create_sse_result_and_edit_flow(self):
        health = self.client.get("/api/health")
        self.assertEqual({"status": "ok"}, health.json())

        observability = self.client.get("/api/observability")
        self.assertEqual(200, observability.status_code)
        self.assertEqual(
            "langsmith",
            observability.json()["provider"],
        )
        self.assertNotIn("api_key", observability.json())

        topology = self.client.get("/api/harness")
        self.assertEqual(200, topology.status_code)
        self.assertEqual(
            "change-analysis-then-selective-research",
            topology.json()["execution_model"],
        )
        self.assertEqual(5, len(topology.json()["nodes"]))

        created = self.client.post(
            "/api/travel-plans",
            headers={"Idempotency-Key": "api-flow-1"},
            json=self._create_request(),
        )
        self.assertEqual(202, created.status_code)
        task_id = created.json()["task_id"]
        self.assertEqual(
            f"travel-plans/{task_id}/events",
            created.json()["events_url"],
        )

        task = self._wait_for_completion(task_id)
        self.assertEqual("completed", task["status"])
        self.assertIsNotNone(task["plan_id"])

        events = self.client.get(
            f"/api/travel-plans/{task_id}/events"
        )
        self.assertEqual(200, events.status_code)
        self.assertIn("event: task.queued", events.text)
        self.assertIn("event: harness.started", events.text)
        self.assertEqual(5, events.text.count("event: node.started"))
        self.assertEqual(5, events.text.count("event: node.completed"))
        self.assertIn("event: change.analysis", events.text)
        self.assertIn("event: harness.completed", events.text)
        self.assertIn("event: plan.completed", events.text)

        result = self.client.get(f"/api/plans/{task['plan_id']}")
        self.assertEqual(200, result.status_code)
        envelope = result.json()
        self.assertEqual(1, envelope["revision"])
        self.assertEqual(task["session_id"], envelope["session_id"])
        self.assertIsNone(envelope["previous_plan_id"])
        self.assertEqual(
            "北京",
            envelope["plan"]["request_summary"]["destination_city"],
        )
        self.assertEqual(
            "public_transit",
            envelope["plan"]["daily_itinerary"][0]["routes"][0][
                "recommended_mode"
            ],
        )

        edited = self.client.patch(
            f"/api/plans/{task['plan_id']}/itinerary",
            json={
                "base_revision": 1,
                "daily_itinerary": envelope["plan"][
                    "daily_itinerary"
                ],
            },
        )
        self.assertEqual(200, edited.status_code)
        edited_envelope = edited.json()
        self.assertEqual(2, edited_envelope["revision"])
        self.assertNotEqual(task["plan_id"], edited_envelope["plan_id"])
        self.assertEqual(task["plan_id"], edited_envelope["previous_plan_id"])
        self.assertEqual("manual_edit", edited_envelope["source_type"])
        self.assertEqual(
            "server-route-1",
            edited_envelope["plan"]["daily_itinerary"][0][
                "routes"
            ][0]["route_id"],
        )
        self.assertEqual(
            "public_transit",
            edited_envelope["plan"]["daily_itinerary"][0]["routes"][0][
                "recommended_mode"
            ],
        )
        immutable_original = self.client.get(
            f"/api/plans/{task['plan_id']}"
        ).json()
        self.assertEqual(1, immutable_original["revision"])
        self.assertEqual(
            "generated-route-1",
            immutable_original["plan"]["daily_itinerary"][0]["routes"][0][
                "route_id"
            ],
        )

        conflict = self.client.patch(
            f"/api/plans/{task['plan_id']}/itinerary",
            json={
                "base_revision": 1,
                "daily_itinerary": envelope["plan"][
                    "daily_itinerary"
                ],
            },
        )
        self.assertEqual(409, conflict.status_code)

    def test_idempotency_key_reuses_existing_task(self):
        first = self.client.post(
            "/api/travel-plans",
            headers={"Idempotency-Key": "same-request"},
            json=self._create_request(),
        )
        second = self.client.post(
            "/api/travel-plans",
            headers={"Idempotency-Key": "same-request"},
            json=self._create_request(),
        )

        self.assertEqual(
            first.json()["task_id"],
            second.json()["task_id"],
        )

    def test_follow_up_uses_session_and_selectively_reuses_research(self):
        created = self.client.post(
            "/api/travel-plans",
            json=self._create_request(),
        )
        task = self._wait_for_completion(created.json()["task_id"])
        self.assertEqual("completed", task["status"])
        original = self.client.get(
            f"/api/plans/{task['plan_id']}"
        ).json()
        self.assertEqual(1, self.harness_calls)

        request = self._create_request()
        request["additional_requirements"] = "每天十点后出发，节奏放松"
        request["session_id"] = original["session_id"]
        request["previous_plan_id"] = original["plan_id"]
        created_follow_up = self.client.post(
            "/api/travel-plans",
            json=request,
        )
        self.assertEqual(
            202,
            created_follow_up.status_code,
            created_follow_up.text,
        )
        follow_up_task = self._wait_for_completion(
            created_follow_up.json()["task_id"]
        )
        self.assertEqual("completed", follow_up_task["status"])
        self.assertEqual(original["session_id"], follow_up_task["session_id"])
        self.assertEqual(original["plan_id"], follow_up_task["previous_plan_id"])
        self.assertEqual(2, self.harness_calls)

        events = self.client.get(
            f"/api/travel-plans/{follow_up_task['task_id']}/events"
        )
        self.assertIn("event: change.analysis", events.text)
        self.assertEqual(3, events.text.count("event: node.skipped"))
        self.assertNotIn("event: tool.started", events.text)

        refined = self.client.get(
            f"/api/plans/{follow_up_task['plan_id']}"
        )
        self.assertEqual(200, refined.status_code)
        envelope = refined.json()
        self.assertNotEqual(original["plan_id"], envelope["plan_id"])
        self.assertEqual(2, envelope["revision"])
        self.assertEqual(original["session_id"], envelope["session_id"])
        self.assertEqual(original["plan_id"], envelope["previous_plan_id"])
        memory = self.planner_memories[-1]
        self.assertEqual("layered_l2", memory["memory_mode"])
        self.assertEqual(
            original["plan_id"],
            memory["latest_anchor"]["version"]["plan_id"],
        )
        self.assertEqual(1, len(memory["all_user_messages"]))
        self.assertEqual(
            "每天十点后出发，节奏放松",
            memory["current_request"]["additional_requirements"],
        )

        session = self.client.get(
            f"/api/sessions/{original['session_id']}"
        )
        self.assertEqual(200, session.status_code)
        self.assertEqual(envelope["plan_id"], session.json()["current_plan_id"])
        self.assertEqual(2, len(session.json()["plans"]))

        stale_request = {**request, "additional_requirements": "再调整一次"}
        stale = self.client.post(
            "/api/travel-plans",
            json=stale_request,
        )
        self.assertEqual(409, stale.status_code)

    def test_history_turns_search_and_fork_old_version(self):
        created = self.client.post(
            "/api/travel-plans",
            json=self._create_request(),
        )
        original_task = self._wait_for_completion(
            created.json()["task_id"]
        )
        original = self.client.get(
            f"/api/plans/{original_task['plan_id']}"
        ).json()

        follow_up = {
            **self._create_request(),
            "additional_requirements": "每天十点后出发，节奏放松",
            "session_id": original["session_id"],
            "previous_plan_id": original["plan_id"],
        }
        next_task = self.client.post(
            "/api/travel-plans", json=follow_up
        ).json()
        refined_task = self._wait_for_completion(next_task["task_id"])
        refined = self.client.get(
            f"/api/plans/{refined_task['plan_id']}"
        ).json()

        history = self.client.get(
            "/api/sessions", params={"query": "北京"}
        )
        self.assertEqual(200, history.status_code, history.text)
        self.assertEqual(1, len(history.json()["items"]))
        summary = history.json()["items"][0]
        self.assertEqual(original["session_id"], summary["session_id"])
        self.assertEqual(2, summary["current_revision"])
        self.assertEqual(2, summary["revision_count"])
        self.assertEqual(
            "每天十点后出发，节奏放松",
            summary["latest_requirement"],
        )

        turns = self.client.get(
            f"/api/sessions/{original['session_id']}/turns"
        )
        self.assertEqual(200, turns.status_code, turns.text)
        self.assertEqual(2, len(turns.json()["turns"]))
        self.assertEqual(
            "每天十点后出发，节奏放松",
            turns.json()["turns"][1]["user_text"],
        )
        self.assertEqual(2, turns.json()["turns"][1]["revision"])
        self.assertTrue(turns.json()["turns"][0]["events"])

        forked = self.client.post(
            f"/api/sessions/{original['session_id']}/fork",
            json={"source_plan_id": original["plan_id"]},
        )
        self.assertEqual(201, forked.status_code, forked.text)
        forked_plan = forked.json()
        self.assertNotEqual(original["session_id"], forked_plan["session_id"])
        self.assertEqual(1, forked_plan["revision"])
        self.assertEqual("fork", forked_plan["source_type"])
        self.assertIsNone(forked_plan["previous_plan_id"])
        self.assertEqual(original["plan"], forked_plan["plan"])

        original_session = self.client.get(
            f"/api/sessions/{original['session_id']}"
        ).json()
        self.assertEqual(refined["plan_id"], original_session["current_plan_id"])
        self.assertEqual(2, len(original_session["plans"]))

        first_page = self.client.get(
            "/api/sessions", params={"limit": 1}
        ).json()
        self.assertEqual(1, len(first_page["items"]))
        self.assertIsNotNone(first_page["next_cursor"])
        second_page = self.client.get(
            "/api/sessions",
            params={"limit": 1, "cursor": first_page["next_cursor"]},
        )
        self.assertEqual(200, second_page.status_code, second_page.text)
        self.assertEqual(1, len(second_page.json()["items"]))

    def test_rejects_invalid_date_range(self):
        payload = self._create_request()
        payload["start_date"] = "2099-08-03"
        payload["end_date"] = "2099-08-01"

        response = self.client.post(
            "/api/travel-plans",
            json=payload,
        )

        self.assertEqual(422, response.status_code)

    def test_rejects_incomplete_plan_for_three_day_request(self):
        payload = self._create_request()
        payload["end_date"] = "2099-08-03"

        created = self.client.post(
            "/api/travel-plans",
            json=payload,
        )
        task = self._wait_for_completion(created.json()["task_id"])

        self.assertEqual("failed", task["status"])
        self.assertEqual(
            "生成的行程未完整覆盖所选日期，请重新规划",
            task["error_message"],
        )
        self.assertEqual("MODEL_INVALID_OUTPUT", task["error_code"])
        self.assertTrue(task["retryable"])
        self.assertTrue(task["error_id"].startswith("err_"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
