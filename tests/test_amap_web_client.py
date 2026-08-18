"""Tests for direct Amap Web Service route geometry."""

import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from agent_app.infrastructure.amap_web_client import (
    AmapWebServiceClient,
    AmapWebServiceError,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return None

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class AmapWebServiceClientTests(unittest.TestCase):
    @patch("agent_app.infrastructure.amap_web_client.urlopen")
    def test_retries_transient_network_error(self, urlopen):
        urlopen.side_effect = [
            URLError("offline"),
            _Response(
                {
                    "status": "1",
                    "route": {
                        "paths": [
                            {
                                "steps": [
                                    {
                                        "polyline": (
                                            "116.4,39.9;116.41,39.91"
                                        )
                                    }
                                ]
                            }
                        ]
                    },
                }
            ),
        ]
        client = AmapWebServiceClient("test-key")

        points = client.route_polyline(
            "walking",
            origin="116.4,39.9",
            destination="116.41,39.91",
            city="北京",
            destination_city="北京",
        )

        self.assertEqual(2, len(points))
        self.assertEqual(2, urlopen.call_count)

    @patch("agent_app.infrastructure.amap_web_client.urlopen")
    def test_returns_deduplicated_driving_polyline(self, urlopen):
        urlopen.return_value = _Response(
            {
                "status": "1",
                "route": {
                    "paths": [
                        {
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
                                        "116.410000,39.910000"
                                    )
                                },
                            ]
                        }
                    ]
                },
            }
        )
        client = AmapWebServiceClient("test-key")

        points = client.route_polyline(
            "driving",
            origin="116.4,39.9",
            destination="116.41,39.91",
            city="北京",
            destination_city="北京",
        )

        self.assertEqual(
            [
                [116.4, 39.9],
                [116.405, 39.905],
                [116.41, 39.91],
            ],
            points,
        )
        requested_url = urlopen.call_args.args[0].full_url
        self.assertIn("/v3/direction/driving?", requested_url)
        self.assertIn("strategy=0", requested_url)

    @patch("agent_app.infrastructure.amap_web_client.urlopen")
    def test_returns_transit_walk_and_bus_geometry(self, urlopen):
        urlopen.return_value = _Response(
            {
                "status": "1",
                "route": {
                    "transits": [
                        {
                            "segments": [
                                {
                                    "walking": {
                                        "steps": [
                                            {
                                                "polyline": (
                                                    "116.4,39.9;"
                                                    "116.401,39.901"
                                                )
                                            }
                                        ]
                                    },
                                    "bus": {
                                        "buslines": [
                                            {
                                                "polyline": (
                                                    "116.401,39.901;"
                                                    "116.41,39.91"
                                                )
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    ]
                },
            }
        )
        client = AmapWebServiceClient("test-key")

        points = client.route_polyline(
            "public_transit",
            origin="116.4,39.9",
            destination="116.41,39.91",
            city="北京",
            destination_city="北京",
        )

        self.assertEqual(3, len(points))
        requested_url = urlopen.call_args.args[0].full_url
        self.assertIn("/v3/direction/transit/integrated?", requested_url)
        self.assertIn("city=%E5%8C%97%E4%BA%AC", requested_url)

    def test_rejects_missing_key_without_network(self):
        client = AmapWebServiceClient("")

        with self.assertRaisesRegex(AmapWebServiceError, "Key"):
            client.route_polyline(
                "walking",
                origin="116.4,39.9",
                destination="116.41,39.91",
                city="北京",
                destination_city="北京",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
