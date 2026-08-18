"""Unit tests for compact multi-mode route comparison."""

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure.mcp_client import MCPClientError
from agent_app.infrastructure.amap_client import (
    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
    AMAP_MCP_GEO_TOOL_NAME,
    AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME,
    AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
)
from agent_app.tools.route import (
    ROUTE_OPTIONS_TOOL_SCHEMA,
    compare_route_options,
)


class _FakeAmapClient:
    def call_tool(self, tool_name, **arguments):
        if tool_name == AMAP_MCP_GEO_TOOL_NAME:
            longitude = (
                "116.40"
                if "饭店" in arguments["address"]
                else "116.41"
            )
            return {
                "return": [
                    {"location": f"{longitude},39.90"}
                ]
            }
        if tool_name == AMAP_MCP_WALKING_COORDINATE_TOOL_NAME:
            return {
                "route": {
                    "paths": [
                        {
                            "distance": "1500",
                            "duration": "1200",
                            "steps": [
                                {
                                    "polyline": (
                                        "116.400000,39.900000;"
                                        "116.405000,39.905000"
                                    )
                                },
                                {
                                    "polyline": (
                                        "116.405000,39.905000;"
                                        "116.410000,39.900000"
                                    )
                                },
                            ],
                        }
                    ]
                }
            }
        if tool_name == AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME:
            return {
                "route": {
                    "paths": [
                        {"distance": "2100", "duration": "480"}
                    ]
                }
            }
        if tool_name == AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME:
            return {
                "route": {
                    "distance": "560",
                    "transits": [
                        {
                            "duration": "900",
                            "walking_distance": "300",
                            "segments": [
                                {
                                    "walking": {
                                        "distance": "100",
                                        "steps": [
                                            {
                                                "polyline": (
                                                    "116.400000,39.900000;"
                                                    "116.402000,39.901000"
                                                )
                                            }
                                        ],
                                    },
                                    "bus": {
                                        "buslines": [
                                            {
                                                "distance": "1000",
                                                "name": "地铁1号线",
                                                "type": "地铁线路",
                                                "polyline": (
                                                    "116.402000,39.901000;"
                                                    "116.407000,39.902000"
                                                ),
                                            }
                                        ]
                                    },
                                },
                                {
                                    "walking": {"distance": "200"},
                                    "bus": {
                                        "buslines": [
                                            {
                                                "distance": "500",
                                                "name": "52路",
                                                "type": "普通公交线路",
                                            }
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected tool: {tool_name}")


class RouteToolTests(unittest.TestCase):
    def test_schema_requires_two_addresses_and_cities(self):
        function_schema = ROUTE_OPTIONS_TOOL_SCHEMA["function"]
        parameters = function_schema["parameters"]

        self.assertEqual(
            "compare_route_options",
            function_schema["name"],
        )
        self.assertEqual(
            [
                "origin_address",
                "destination_address",
                "origin_city",
                "destination_city",
            ],
            parameters["required"],
        )
        self.assertFalse(parameters["additionalProperties"])

    def test_compacts_all_route_modes(self):
        result = compare_route_options(
            " 北京饭店 ",
            " 故宫博物院 ",
            " 北京 ",
            " 北京 ",
            amap_client=_FakeAmapClient(),
        )

        self.assertEqual("北京饭店", result["origin"])
        self.assertEqual("故宫博物院", result["destination"])
        self.assertEqual("116.40,39.90", result["origin_location"])
        self.assertEqual(
            "116.41,39.90",
            result["destination_location"],
        )
        self.assertEqual(
            {
                "available": True,
                "distance_km": 1.5,
                "duration_minutes": 20,
                "error": None,
            },
            result["walking"],
        )
        self.assertEqual(8, result["driving"]["duration_minutes"])
        self.assertEqual(
            1.8,
            result["public_transit"]["distance_km"],
        )
        self.assertEqual(
            15,
            result["public_transit"]["duration_minutes"],
        )
        self.assertEqual(
            0.3,
            result["public_transit"]["walking_distance_km"],
        )
        self.assertEqual(
            1,
            result["public_transit"]["transfer_count"],
        )
        self.assertEqual(
            "mixed",
            result["public_transit"]["transit_type"],
        )
        self.assertEqual(
            ["地铁1号线", "52路"],
            result["public_transit"]["line_names"],
        )
        self.assertNotEqual(
            0.56,
            result["public_transit"]["distance_km"],
        )
        self.assertEqual(
            "public_transit",
            result["recommended_mode"],
        )

    def test_keeps_other_modes_when_one_mode_fails(self):
        class PartlyFailingClient(_FakeAmapClient):
            def call_tool(self, tool_name, **arguments):
                if (
                    tool_name
                    == AMAP_MCP_WALKING_COORDINATE_TOOL_NAME
                ):
                    raise MCPClientError("步行路线不可用")
                return super().call_tool(tool_name, **arguments)

        result = compare_route_options(
            "北京饭店",
            "故宫博物院",
            "北京",
            "北京",
            amap_client=PartlyFailingClient(),
        )

        self.assertFalse(result["walking"]["available"])
        self.assertEqual("工具执行失败", result["walking"]["error"])
        self.assertTrue(result["driving"]["available"])
        self.assertTrue(result["public_transit"]["available"])

    def test_can_include_amap_road_geometry_for_map_rendering(self):
        result = compare_route_options(
            "北京饭店",
            "故宫博物院",
            "北京",
            "北京",
            amap_client=_FakeAmapClient(),
            include_polyline=True,
        )

        self.assertEqual(
            [
                [116.4, 39.9],
                [116.405, 39.905],
                [116.41, 39.9],
            ],
            result["walking"]["polyline"],
        )
        self.assertEqual(
            [
                [116.4, 39.9],
                [116.402, 39.901],
                [116.407, 39.902],
            ],
            result["public_transit"]["polyline"],
        )

    def test_identifies_subway_only_transit_plan(self):
        class SubwayOnlyClient(_FakeAmapClient):
            def call_tool(self, tool_name, **arguments):
                if tool_name == AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME:
                    return {
                        "route": {
                            "transits": [
                                {
                                    "duration": "1080",
                                    "walking_distance": "420",
                                    "segments": [
                                        {
                                            "walking": {
                                                "distance": "420"
                                            },
                                            "bus": {
                                                "buslines": [
                                                    {
                                                        "name": "地铁6号线",
                                                        "type": "地铁线路",
                                                        "distance": "5200",
                                                    }
                                                ]
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                return super().call_tool(tool_name, **arguments)

        result = compare_route_options(
            "北京饭店",
            "故宫博物院",
            "北京",
            "北京",
            amap_client=SubwayOnlyClient(),
        )

        transit = result["public_transit"]
        self.assertEqual("subway", transit["transit_type"])
        self.assertEqual(["地铁6号线"], transit["line_names"])

    def test_marks_all_modes_unavailable_when_geocoding_fails(self):
        class GeocodingFailureClient(_FakeAmapClient):
            def call_tool(self, tool_name, **arguments):
                if tool_name == AMAP_MCP_GEO_TOOL_NAME:
                    raise MCPClientError("地点编码失败")
                return super().call_tool(tool_name, **arguments)

        result = compare_route_options(
            "北京饭店",
            "故宫博物院",
            "北京",
            "北京",
            amap_client=GeocodingFailureClient(),
        )

        self.assertFalse(result["walking"]["available"])
        self.assertFalse(result["driving"]["available"])
        self.assertFalse(result["public_transit"]["available"])
        self.assertEqual("工具执行失败", result["walking"]["error"])
        self.assertIsNone(result["walking"]["distance_km"])
        self.assertIsNone(
            result["public_transit"]["walking_distance_km"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
