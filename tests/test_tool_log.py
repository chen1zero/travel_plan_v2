"""Tests for user-visible tool argument and result summaries."""

import json
import unittest

from agent_app.tools.tool_log import (
    summarize_tool_arguments,
    summarize_tool_result,
)


class ToolLogTests(unittest.TestCase):
    def test_summarizes_poi_names_without_secrets(self):
        result = json.dumps(
            {
                "ok": True,
                "result": {
                    "api_key": "hidden-value",
                    "pois": [
                        {"name": "故宫博物院"},
                        {"name": "景山公园"},
                    ],
                },
            },
            ensure_ascii=False,
        )

        summary = summarize_tool_result(
            "maps_text_search",
            result,
        )

        self.assertIn("故宫博物院", summary)
        self.assertIn("景山公园", summary)
        self.assertNotIn("hidden-value", summary)

    def test_summarizes_route_modes_and_recommendation(self):
        result = json.dumps(
            {
                "ok": True,
                "result": {
                    "origin": "故宫博物院",
                    "destination": "景山公园",
                    "walking": mode(2.3, 31),
                    "driving": mode(2.8, 12),
                    "public_transit": {
                        **mode(None, 28),
                        "walking_distance_km": 0.56,
                        "transfer_count": 1,
                        "transit_type": "subway",
                        "line_names": ["地铁1号线"],
                    },
                    "recommended_mode": "public_transit",
                    "recommendation_reason": "公共交通时间适中",
                },
            },
            ensure_ascii=False,
        )

        summary = summarize_tool_result(
            "compare_route_options",
            result,
        )

        self.assertIn("故宫博物院 → 景山公园", summary)
        self.assertIn("步行2.3公里/31分钟", summary)
        self.assertIn("地铁28分钟/接驳步行0.56公里/换乘1次", summary)
        self.assertIn("线路地铁1号线", summary)
        self.assertNotIn("None公里", summary)
        self.assertIn("推荐地铁", summary)
        self.assertIn("公共交通时间适中", summary)

    def test_summarizes_route_arguments_as_place_pair(self):
        summary = summarize_tool_arguments(
            "compare_route_options",
            json.dumps(
                {
                    "origin_address": "北京饭店，东长安街33号",
                    "destination_address": "故宫博物院，景山前街4号",
                },
                ensure_ascii=False,
            ),
        )

        self.assertEqual("北京饭店 → 故宫博物院", summary)


def mode(distance, minutes):
    return {
        "available": True,
        "distance_km": distance,
        "duration_minutes": minutes,
    }


if __name__ == "__main__":
    unittest.main(verbosity=2)
