"""Regression tests for natural-language follow-up requirements."""

from copy import deepcopy
from datetime import date
import json
import unittest

from agent_app.api.schemas import TravelRequest
from agent_app.api.services.follow_up import normalize_follow_up_request
from agent_app.harness.revision_validation import (
    validate_generated_plan,
    validate_revision_result,
)
from agent_app.harness.travel_planning import analyze_request_changes
from tests.test_api import _plan_payload


class FollowUpRequirementTests(unittest.TestCase):
    def _request(self, instruction: str) -> TravelRequest:
        return TravelRequest(
            destination_city="北京",
            start_date=date(2099, 8, 1),
            end_date=date(2099, 8, 1),
            preferences=["历史文化"],
            budget_cny=5000,
            accommodation_type="经济型",
            additional_requirements=instruction,
            session_id="session_test",
            previous_plan_id="plan_previous",
        )

    @staticmethod
    def _context(request: TravelRequest):
        previous_request = request.model_dump(mode="json")
        previous_request["additional_requirements"] = "首次规划"
        previous_request["session_id"] = None
        previous_request["previous_plan_id"] = None
        return {
            "request": previous_request,
            "attractions": "上一版景点",
            "weather": "上一版天气",
            "hotels": "上一版酒店",
        }

    def test_night_view_and_add_attraction_rerun_attraction_research(self):
        for instruction in (
            "第一天晚上安排一个夜景的景点",
            "第一天多加一个景点",
        ):
            with self.subTest(instruction=instruction):
                request = self._request(instruction)
                analysis = analyze_request_changes(
                    request.model_dump(mode="json"),
                    self._context(request),
                )
                self.assertTrue(analysis["rerun"]["attraction"])
                self.assertFalse(analysis["rerun"]["weather"])
                self.assertFalse(analysis["rerun"]["hotel"])

    def test_hotel_budget_and_accommodation_follow_ups_are_normalized(self):
        luxury = normalize_follow_up_request(self._request("改住豪华型"))
        budget = normalize_follow_up_request(
            self._request("预算改成 8000")
        )
        hotel = self._request("换个酒店")

        self.assertEqual("豪华型", luxury.accommodation_type)
        self.assertEqual(8000, budget.budget_cny)
        self.assertTrue(
            analyze_request_changes(
                luxury.model_dump(mode="json"),
                self._context(self._request("改住豪华型")),
            )["rerun"]["hotel"]
        )
        self.assertTrue(
            analyze_request_changes(
                budget.model_dump(mode="json"),
                self._context(self._request("预算改成 8000")),
            )["rerun"]["hotel"]
        )
        self.assertTrue(
            analyze_request_changes(
                hotel.model_dump(mode="json"),
                self._context(hotel),
            )["rerun"]["hotel"]
        )

    def test_relative_budget_follow_ups_adjust_existing_total(self):
        increased = normalize_follow_up_request(
            self._request("预算增加1000")
        )
        decreased = normalize_follow_up_request(
            self._request("总预算降低 1,500")
        )
        absolute = normalize_follow_up_request(
            self._request("预算提高到 9000")
        )

        self.assertEqual(6000, increased.budget_cny)
        self.assertEqual(3500, decreased.budget_cny)
        self.assertEqual(9000, absolute.budget_cny)

    def test_revision_validator_rejects_unchanged_night_view_plan(self):
        previous = _plan_payload()
        request = self._request(
            "第一天晚上安排一个夜景的景点"
        ).model_dump(mode="json")
        request["budget_cny"] = 1500

        errors = validate_revision_result(
            json.dumps(previous, ensure_ascii=False),
            previous,
            request,
        )

        self.assertTrue(any("多安排一个景点" in error for error in errors))
        self.assertTrue(any("晚间时段" in error for error in errors))

        revised = deepcopy(previous)
        revised["daily_itinerary"][0]["schedule"].append(
            {
                "schedule_item_id": "night-view",
                "order": 2,
                "time_slot": "19:00-21:00",
                "place_name": "夜景测试地点",
                "address": "北京市测试地址",
                "activity": "观赏夜景",
                "duration_minutes": 120,
                "notes": [],
                "location": {"longitude": 116.4, "latitude": 39.9},
            }
        )
        revised["daily_itinerary"][0]["routes"].append(
            deepcopy(revised["daily_itinerary"][0]["routes"][0])
        )
        self.assertEqual(
            [],
            validate_revision_result(
                json.dumps(revised, ensure_ascii=False),
                previous,
                request,
            ),
        )

    def test_revision_validator_checks_all_requested_cases(self):
        previous = _plan_payload()
        unchanged = json.dumps(previous, ensure_ascii=False)

        hotel_request = self._request("换个酒店").model_dump(mode="json")
        self.assertTrue(
            any(
                "selected_hotel" in error
                for error in validate_revision_result(
                    unchanged,
                    previous,
                    hotel_request,
                )
            )
        )

        luxury_request = normalize_follow_up_request(
            self._request("改住豪华型")
        ).model_dump(mode="json")
        self.assertTrue(
            validate_revision_result(
                unchanged,
                previous,
                luxury_request,
            )
        )

        budget_request = normalize_follow_up_request(
            self._request("预算改成 8000")
        ).model_dump(mode="json")
        self.assertTrue(
            any(
                "8000" in error
                for error in validate_revision_result(
                    unchanged,
                    previous,
                    budget_request,
                )
            )
        )

    def test_plan_validator_rejects_cross_day_duplicate_places(self):
        plan = _plan_payload()
        second_day = deepcopy(plan["daily_itinerary"][0])
        second_day["day"] = 2
        second_day["date"] = "2099-08-02"
        second_day["schedule"][0]["schedule_item_id"] = "day-2-place"
        plan["daily_itinerary"].append(second_day)

        errors = validate_generated_plan(
            json.dumps(plan, ensure_ascii=False)
        )

        self.assertEqual(1, len(errors))
        self.assertIn("同时出现在第 1 天和第 2 天", errors[0])
        self.assertIn("必须将后一次替换为不同景点", errors[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
