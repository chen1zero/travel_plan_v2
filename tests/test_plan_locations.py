"""Tests for deterministic map-coordinate enrichment."""

import unittest

from agent_app.api.services.locations import (
    enrich_plan_locations,
    enrich_plan_map_data,
)
from agent_app.infrastructure.amap_client import (
    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
    AMAP_MCP_GEO_TOOL_NAME,
    AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME,
    AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
)


class _FakeAmapClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, tool_name, **arguments):
        self.calls.append((tool_name, arguments))
        longitude = 116.4 + len(self.calls) / 100
        return {
            "return": [
                {"location": f"{longitude},39.9"}
            ]
        }


class PlanLocationTests(unittest.TestCase):
    def test_fills_missing_hotel_and_all_schedule_locations(self):
        plan = {
            "request_summary": {"destination_city": "北京"},
            "selected_hotel": {
                "name": "测试酒店",
                "address": "测试路1号",
                "location": {
                    "longitude": None,
                    "latitude": None,
                },
            },
            "daily_itinerary": [
                {
                    "schedule": [
                        {
                            "place_name": "景点一",
                            "address": "景点路1号",
                            "location": {
                                "longitude": None,
                                "latitude": None,
                            },
                        },
                        {
                            "place_name": "景点二",
                            "address": "景点路2号",
                            "location": {
                                "longitude": 116.5,
                                "latitude": 39.95,
                            },
                        },
                    ]
                }
            ],
            "data_notes": [],
        }
        client = _FakeAmapClient()

        enriched = enrich_plan_locations(
            plan,
            amap_client=client,
        )

        self.assertEqual(2, len(client.calls))
        self.assertTrue(
            all(call[0] == AMAP_MCP_GEO_TOOL_NAME for call in client.calls)
        )
        self.assertIsInstance(
            enriched["selected_hotel"]["location"]["longitude"],
            float,
        )
        self.assertIsInstance(
            enriched["daily_itinerary"][0]["schedule"][0][
                "location"
            ]["latitude"],
            float,
        )
        self.assertEqual(
            116.5,
            enriched["daily_itinerary"][0]["schedule"][1][
                "location"
            ]["longitude"],
        )

    def test_adds_amap_polylines_to_each_route_mode(self):
        class RouteClient:
            def call_tool(self, tool_name, **_arguments):
                if tool_name == AMAP_MCP_GEO_TOOL_NAME:
                    return {
                        "return": [
                            {"location": "116.4,39.9"}
                        ]
                    }
                if tool_name == AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME:
                    return {
                        "route": {
                            "transits": [
                                {
                                    "duration": "600",
                                    "walking_distance": "100",
                                    "segments": [
                                        {
                                            "walking": {
                                                "distance": "100",
                                                "steps": [
                                                    {
                                                        "polyline": (
                                                            "116.4,39.9;"
                                                            "116.41,39.91"
                                                        )
                                                    }
                                                ],
                                            },
                                            "bus": {"buslines": []},
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                if tool_name in {
                    AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
                    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
                }:
                    return {
                        "route": {
                            "paths": [
                                {
                                    "distance": "1200",
                                    "duration": "600",
                                    "steps": [
                                        {
                                            "polyline": (
                                                "116.4,39.9;"
                                                "116.41,39.91"
                                            )
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                raise AssertionError(tool_name)

        class GeometryClient:
            calls = []

            def route_polyline(self, mode, **arguments):
                self.calls.append((mode, arguments))
                return [[116.4, 39.9], [116.41, 39.91]]

        mode = {
            "available": True,
            "distance_km": 1.2,
            "duration_minutes": 10,
            "error": None,
        }
        plan = {
            "request_summary": {"destination_city": "北京"},
            "selected_hotel": {
                "name": "测试酒店",
                "address": "测试路1号",
                "location": {"longitude": 116.4, "latitude": 39.9},
            },
            "daily_itinerary": [
                {
                    "schedule": [
                        {
                            "place_name": "景点一",
                            "address": "景点路1号",
                            "location": {
                                "longitude": 116.41,
                                "latitude": 39.91,
                            },
                        }
                    ],
                    "routes": [
                        {
                            "origin": {
                                "name": "测试酒店",
                                "address": "测试路1号",
                                "city": "北京",
                            },
                            "destination": {
                                "name": "景点一",
                                "address": "景点路1号",
                                "city": "北京",
                            },
                            "walking": dict(mode),
                            "driving": dict(mode),
                            "public_transit": {
                                **mode,
                                "walking_distance_km": 0.1,
                                "transfer_count": 0,
                                "transit_type": "bus",
                                "line_names": [],
                            },
                            "recommended_mode": "walking",
                        }
                    ],
                }
            ],
            "data_notes": [],
        }

        geometry_client = GeometryClient()
        enriched = enrich_plan_map_data(
            plan,
            amap_client=RouteClient(),
            route_geometry_client=geometry_client,
        )

        route = enriched["daily_itinerary"][0]["routes"][0]
        self.assertEqual("amap", route["geometry_source"])
        self.assertEqual(
            [[116.4, 39.9], [116.41, 39.91]],
            route["walking"]["polyline"],
        )
        self.assertNotIn("polyline", route["public_transit"])
        self.assertEqual("walking", geometry_client.calls[0][0])
        self.assertEqual(
            "116.4,39.9",
            geometry_client.calls[0][1]["origin"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
