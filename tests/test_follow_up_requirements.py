"""Regression tests for natural-language follow-up requirements."""

from copy import deepcopy
from datetime import date
import json
import unittest

from agent_app.api.schemas import TravelRequest
from agent_app.api.services.follow_up import normalize_follow_up_request
from agent_app.harness.revision_validation import (
    normalize_terminal_hotel_return_routes,
    sanitize_unsupported_plan_facts,
    validate_generated_plan,
    validate_revision_result,
    validate_supported_plan_facts,
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
        revised["daily_itinerary"][0]["routes"][1]["sequence"] = 2
        revised["daily_itinerary"][0]["routes"][1]["route_id"] = (
            "night-view-route"
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

    def test_plan_validator_rejects_incomplete_route_coverage(self):
        plan = _plan_payload()
        plan["daily_itinerary"][0]["schedule"].append(
            {
                **deepcopy(plan["daily_itinerary"][0]["schedule"][0]),
                "schedule_item_id": "second-place",
                "order": 2,
                "place_name": "景山公园",
            }
        )

        errors = validate_generated_plan(json.dumps(plan, ensure_ascii=False))

        self.assertTrue(any("2 个日程地点" in error for error in errors))
        self.assertTrue(any("只有 1 条路线" in error for error in errors))

    def test_price_zero_requires_explicit_research_evidence(self):
        plan = _plan_payload()
        plan["daily_itinerary"][0]["estimated_cost_cny"]["tickets"] = 0
        plan["budget_summary"]["breakdown"]["tickets"] = 0
        attractions_without_price = {
            "attractions": [{"name": "故宫博物院", "price_cny": None}]
        }

        errors = validate_supported_plan_facts(
            json.dumps(plan, ensure_ascii=False),
            attractions_without_price,
            {"hotels": [{"name": "测试酒店"}]},
        )

        self.assertEqual(2, len(errors))
        self.assertTrue(all("必须使用 null" in error for error in errors))

    def test_explicit_zero_price_is_supported_but_wrong_value_is_not(self):
        plan = _plan_payload()
        plan["daily_itinerary"][0]["estimated_cost_cny"]["tickets"] = 0
        plan["budget_summary"]["breakdown"]["tickets"] = 0
        attractions = {
            "attractions": [{"name": "故宫博物院", "price_cny": 0}]
        }

        self.assertEqual(
            [],
            validate_supported_plan_facts(
                json.dumps(plan, ensure_ascii=False),
                attractions,
                {"hotels": [{"name": "测试酒店"}]},
            ),
        )
        plan["daily_itinerary"][0]["estimated_cost_cny"]["tickets"] = 10
        errors = validate_supported_plan_facts(
            json.dumps(plan, ensure_ascii=False),
            attractions,
            {"hotels": [{"name": "测试酒店"}]},
        )
        self.assertTrue(any("与研究证据 0 不一致" in error for error in errors))

    def test_sanitizer_nulls_only_unsupported_monetary_claims(self):
        plan = _plan_payload()
        plan["daily_itinerary"][0]["estimated_cost_cny"].update(
            {"tickets": 0, "food": 80, "subtotal": 80}
        )
        plan["budget_summary"].update(
            {"estimated_total": 80, "remaining": 1420}
        )
        plan["budget_summary"]["breakdown"].update(
            {"tickets": 0, "food": 80}
        )

        sanitized, changes = sanitize_unsupported_plan_facts(
            json.dumps(plan, ensure_ascii=False),
            {"attractions": [{"name": "故宫博物院", "price_cny": None}]},
            {"hotels": [{"name": "测试酒店"}]},
        )
        value = json.loads(sanitized)

        self.assertTrue(changes)
        costs = value["daily_itinerary"][0]["estimated_cost_cny"]
        self.assertIsNone(costs["tickets"])
        self.assertIsNone(costs["food"])
        self.assertIsNone(costs["subtotal"])
        self.assertIsNone(value["budget_summary"]["estimated_total"])
        self.assertIsNone(value["budget_summary"]["remaining"])
        self.assertEqual(
            [],
            validate_supported_plan_facts(
                sanitized,
                {"attractions": [{"name": "故宫博物院"}]},
                {"hotels": [{"name": "测试酒店"}]},
            ),
        )

    def test_normalizes_only_an_extra_terminal_hotel_return_route(self):
        plan = _plan_payload()
        return_route = deepcopy(plan["daily_itinerary"][0]["routes"][0])
        return_route["route_id"] = "return-to-hotel"
        return_route["sequence"] = 2
        return_route["origin"] = deepcopy(return_route["destination"])
        return_route["destination"] = {
            "name": plan["selected_hotel"]["name"],
            "address": plan["selected_hotel"]["address"],
            "city": plan["request_summary"]["destination_city"],
        }
        plan["daily_itinerary"][0]["routes"].append(return_route)

        normalized, notes = normalize_terminal_hotel_return_routes(
            json.dumps(plan, ensure_ascii=False)
        )
        normalized_plan = json.loads(normalized)

        self.assertEqual(1, len(normalized_plan["daily_itinerary"][0]["routes"]))
        self.assertEqual(["第 1 天移除契约外的返酒店尾段"], notes)
        self.assertEqual([], validate_generated_plan(normalized))

    def test_does_not_trim_an_unrelated_extra_route(self):
        plan = _plan_payload()
        extra_route = deepcopy(plan["daily_itinerary"][0]["routes"][0])
        extra_route["route_id"] = "unrelated-extra"
        extra_route["sequence"] = 2
        extra_route["destination"]["name"] = "其他地点"
        plan["daily_itinerary"][0]["routes"].append(extra_route)

        normalized, notes = normalize_terminal_hotel_return_routes(
            json.dumps(plan, ensure_ascii=False)
        )

        self.assertEqual([], notes)
        self.assertTrue(validate_generated_plan(normalized))

    def test_budget_only_increase_must_preserve_hotel_and_itinerary(self):
        previous = _plan_payload()
        revised = deepcopy(previous)
        revised["request_summary"]["budget_cny"] = 8000
        revised["budget_summary"]["total_budget"] = 8000
        revised["selected_hotel"]["name"] = "不应更换的酒店"
        request = self._request("预算提高到 8000").model_dump(mode="json")
        request["budget_cny"] = 8000

        errors = validate_revision_result(
            json.dumps(revised, ensure_ascii=False),
            previous,
            request,
        )

        self.assertTrue(any("selected_hotel 必须原样保留" in error for error in errors))
        revised["selected_hotel"] = deepcopy(previous["selected_hotel"])
        self.assertEqual(
            [],
            validate_revision_result(
                json.dumps(revised, ensure_ascii=False),
                previous,
                request,
            ),
        )

    def test_budget_only_increase_sets_planner_preservation_constraints(self):
        request = self._request("预算提高到 8000")
        current = request.model_dump(mode="json")
        current["budget_cny"] = 8000
        context = self._context(request)

        analysis = analyze_request_changes(current, context)

        self.assertTrue(analysis["preservation"]["budget_only_increase"])
        self.assertTrue(analysis["preservation"]["forbid_new_route_queries"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
