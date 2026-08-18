"""HTTP end-to-end regression coverage for common follow-up cases."""

from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from agent_app.api.app import create_app
from agent_app.api.config import APISettings
from agent_app.api.repository import SQLitePlanRepository
from agent_app.harness.travel_planning import TravelPlanningHarness
from tests.test_api import _fake_route_rebuilder, _plan_payload


class _ResearchAgent:
    def __init__(self, name, calls):
        self._name = name
        self._calls = calls

    def run(self, query):
        self._calls.append((self._name, query))
        return json.dumps(
            {"stage": self._name, "query": query},
            ensure_ascii=False,
        )


class _RevisionPlanner:
    def __init__(self, calls):
        self._calls = calls

    def run(
        self,
        *,
        original_request,
        attractions,
        weather,
        hotels,
        previous_plan=None,
        change_analysis=None,
        revision_mode=False,
    ):
        self._calls.append(
            ("planner", original_request, deepcopy(change_analysis))
        )
        plan = deepcopy(previous_plan) if previous_plan else _plan_payload()
        if not revision_mode:
            return json.dumps(plan, ensure_ascii=False)

        if "换个酒店" in original_request:
            self._set_hotel(plan, "测试新酒店", "符合本轮更换酒店要求")
        if "豪华型" in original_request:
            self._set_hotel(
                plan,
                "测试豪华酒店",
                "属于豪华型高端酒店，符合本轮住宿要求",
            )
            plan["request_summary"]["hotel_requirement"] = "豪华型"
        if "夜景" in original_request:
            self._add_place(plan, "夜景测试地点", "19:00-21:00")
        elif "多加一个景点" in original_request:
            self._add_place(plan, "新增测试景点", "15:00-17:00")

        budget_match = re.search(r"总预算：([0-9.]+)", original_request)
        if budget_match:
            budget = float(budget_match.group(1))
            plan["request_summary"]["budget_cny"] = budget
            plan["budget_summary"]["total_budget"] = budget
        return json.dumps(plan, ensure_ascii=False)

    @staticmethod
    def _set_hotel(plan, name, reason):
        plan["selected_hotel"] = {
            **plan["selected_hotel"],
            "name": name,
            "address": f"北京市{name}测试地址",
            "selection_reason": reason,
        }
        for day in plan["daily_itinerary"]:
            if day["routes"]:
                day["routes"][0]["origin"] = {
                    "name": name,
                    "address": f"北京市{name}测试地址",
                    "city": "北京",
                }

    @staticmethod
    def _add_place(plan, name, time_slot):
        day = plan["daily_itinerary"][0]
        previous = day["schedule"][-1]
        sequence = len(day["schedule"]) + 1
        added = {
            "schedule_item_id": f"added-{sequence}",
            "order": sequence,
            "time_slot": time_slot,
            "place_name": name,
            "address": f"北京市{name}测试地址",
            "activity": "游览",
            "duration_minutes": 120,
            "notes": [],
            "location": {"longitude": 116.41, "latitude": 39.91},
        }
        day["schedule"].append(added)
        route = deepcopy(day["routes"][-1])
        route["route_id"] = f"added-route-{sequence}"
        route["sequence"] = sequence
        route["origin"] = {
            "name": previous["place_name"],
            "address": previous["address"],
            "city": "北京",
        }
        route["destination"] = {
            "name": name,
            "address": added["address"],
            "city": "北京",
        }
        day["routes"].append(route)


class FollowUpCasesEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self._temporary_directory.name) / "cases.db"
        repository = SQLitePlanRepository(database_path)
        api_settings = APISettings(
            database_path=database_path,
            cors_origins=("http://127.0.0.1:5173",),
            max_workers=1,
            sse_poll_interval_seconds=0.01,
            sse_heartbeat_seconds=0.05,
        )
        self.calls = []

        def harness_factory(callback):
            return TravelPlanningHarness(
                attraction_agent=_ResearchAgent("attraction", self.calls),
                weather_agent=_ResearchAgent("weather", self.calls),
                hotel_agent=_ResearchAgent("hotel", self.calls),
                planner_agent=_RevisionPlanner(self.calls),
                progress_callback=callback,
            )

        app = create_app(
            api_settings=api_settings,
            repository=repository,
            agent_factory=harness_factory,
            route_rebuilder=_fake_route_rebuilder,
        )
        self._client_context = TestClient(app)
        self.client = self._client_context.__enter__()
        registered = self.client.post(
            "/api/auth/register",
            json={
                "username": "follow_up_test_user",
                "password": "test-password-123",
            },
        )
        self.assertEqual(201, registered.status_code, registered.text)

    def tearDown(self):
        self._client_context.__exit__(None, None, None)
        self._temporary_directory.cleanup()

    def _submit(self, request):
        response = self.client.post("/api/travel-plans", json=request)
        self.assertEqual(202, response.status_code, response.text)
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.client.get(
                f"/api/travel-plans/{task_id}"
            ).json()
            if task["status"] == "failed":
                self.fail(task["error_message"])
            if task["status"] == "completed":
                return self.client.get(
                    f"/api/plans/{task['plan_id']}"
                ).json()
            time.sleep(0.01)
        self.fail("规划任务超时")

    def test_all_required_follow_up_cases_update_the_plan(self):
        request = {
            "destination_city": "北京",
            "start_date": "2099-08-01",
            "end_date": "2099-08-01",
            "preferences": ["历史文化"],
            "budget_cny": 5000,
            "accommodation_type": "经济型",
        }
        envelope = self._submit(request)
        original_count = len(
            envelope["plan"]["daily_itinerary"][0]["schedule"]
        )

        for instruction in (
            "第一天晚上安排一个夜景的景点",
            "换个酒店",
            "第一天多加一个景点",
            "改住豪华型",
            "预算改成 8000",
        ):
            request = {
                **request,
                "session_id": envelope["session_id"],
                "previous_plan_id": envelope["plan_id"],
                "additional_requirements": instruction,
            }
            envelope = self._submit(request)
            request["budget_cny"] = envelope["plan"]["request_summary"][
                "budget_cny"
            ]
            request["accommodation_type"] = envelope["plan"][
                "request_summary"
            ]["hotel_requirement"]

            if "夜景" in instruction:
                schedule = envelope["plan"]["daily_itinerary"][0][
                    "schedule"
                ]
                self.assertEqual(original_count + 1, len(schedule))
                self.assertTrue(
                    any(item["time_slot"].startswith("19:") for item in schedule)
                )
            elif instruction == "换个酒店":
                self.assertEqual(
                    "测试新酒店",
                    envelope["plan"]["selected_hotel"]["name"],
                )
            elif "多加一个景点" in instruction:
                self.assertEqual(
                    original_count + 2,
                    len(
                        envelope["plan"]["daily_itinerary"][0][
                            "schedule"
                        ]
                    ),
                )
            elif "豪华型" in instruction:
                self.assertEqual(
                    "豪华型",
                    envelope["plan"]["request_summary"][
                        "hotel_requirement"
                    ],
                )
                self.assertEqual(
                    "测试豪华酒店",
                    envelope["plan"]["selected_hotel"]["name"],
                )
            elif "8000" in instruction:
                self.assertEqual(
                    8000,
                    envelope["plan"]["budget_summary"]["total_budget"],
                )

        self.assertEqual(6, envelope["revision"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
