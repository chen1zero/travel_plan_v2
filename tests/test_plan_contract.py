"""Regression coverage for the nested browser-facing plan contract."""

from copy import deepcopy
from datetime import date, timedelta
import unittest

from pydantic import ValidationError

from agent_app.api.schemas import TravelPlanDocument, TravelRequest
from agent_app.api.services.task_manager import (
    UnverifiedPlanDataError,
    _validate_research_provenance,
)
from tests.test_api import _plan_payload


class TravelPlanContractTests(unittest.TestCase):
    def test_rejects_missing_nested_route_mode(self):
        plan = _plan_payload()
        del plan["daily_itinerary"][0]["routes"][0]["walking"]

        with self.assertRaises(ValidationError):
            TravelPlanDocument.model_validate(plan)

    def test_rejects_route_count_that_does_not_cover_schedule(self):
        plan = _plan_payload()
        extra = deepcopy(plan["daily_itinerary"][0]["schedule"][0])
        extra["schedule_item_id"] = "item-2"
        extra["order"] = 2
        extra["place_name"] = "景山公园"
        plan["daily_itinerary"][0]["schedule"].append(extra)

        with self.assertRaisesRegex(ValidationError, "routes 数量"):
            TravelPlanDocument.model_validate(plan)

    def test_rejects_out_of_range_coordinates(self):
        plan = _plan_payload()
        plan["selected_hotel"]["location"]["latitude"] = 190

        with self.assertRaises(ValidationError):
            TravelPlanDocument.model_validate(plan)

    def test_rejects_plan_places_absent_from_structured_research(self):
        plan = _plan_payload()
        context = {
            "attractions": {
                "attractions": [{"name": "景山公园"}],
            },
            "hotels": {
                "hotels": [{"name": "测试酒店"}],
            },
        }

        with self.assertRaisesRegex(
            UnverifiedPlanDataError,
            "故宫博物院",
        ):
            _validate_research_provenance(plan, context)

    def test_rejects_past_start_date(self):
        with self.assertRaisesRegex(ValidationError, "不能早于今天"):
            TravelRequest(
                destination_city="北京",
                start_date=date.today() - timedelta(days=1),
                end_date=date.today(),
                preferences=["历史文化"],
                budget_cny=1500,
                accommodation_type="经济型",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
