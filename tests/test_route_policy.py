"""Tests for deterministic transport recommendation rules."""

import unittest

from agent_app.tools.route_policy import recommend_transport_mode


def mode(distance, minutes, available=True, **extra):
    return {
        "available": available,
        "distance_km": distance,
        "duration_minutes": minutes,
        **extra,
    }


def transit(
    distance,
    minutes,
    *,
    transit_type="subway",
    access_walk=0.4,
    transfers=0,
    available=True,
):
    return mode(
        distance,
        minutes,
        available,
        transit_type=transit_type,
        walking_distance_km=access_walk,
        transfer_count=transfers,
    )


class RoutePolicyTests(unittest.TestCase):
    def test_walks_at_one_kilometer_boundary(self):
        recommended, reason = recommend_transport_mode(
            mode(1.0, 15),
            mode(1.2, 5),
            transit(1.1, 10),
        )
        self.assertEqual("walking", recommended)
        self.assertIn("不超过1公里", reason)

    def test_mid_distance_direct_subway_with_short_access(self):
        recommended, reason = recommend_transport_mode(
            mode(5.0, 70),
            mode(5.8, 22),
            transit(5.2, 28, transit_type="subway"),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("地铁直达", reason)
        self.assertIn("600米", reason)

    def test_mid_distance_direct_bus_with_600_meter_access(self):
        recommended, reason = recommend_transport_mode(
            mode(8.0, 105),
            mode(8.8, 25),
            transit(
                8.2,
                38,
                transit_type="bus",
                access_walk=0.6,
            ),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("公交直达", reason)

    def test_mid_distance_allows_one_transfer_when_access_is_short(self):
        recommended, reason = recommend_transport_mode(
            mode(6.0, 80),
            mode(7.0, 24),
            transit(6.5, 33, transit_type="mixed", transfers=1),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("仅换乘1次", reason)

    def test_mid_distance_taxis_for_two_transfers(self):
        recommended, reason = recommend_transport_mode(
            mode(7.0, 95),
            mode(8.0, 26),
            transit(7.5, 42, transfers=2),
        )
        self.assertEqual("driving", recommended)
        self.assertIn("换乘2次", reason)

    def test_mid_distance_taxis_when_access_walk_exceeds_600_meters(self):
        recommended, reason = recommend_transport_mode(
            mode(4.0, 55),
            mode(4.8, 18),
            transit(4.2, 30, access_walk=0.61),
        )
        self.assertEqual("driving", recommended)
        self.assertIn("超过600米", reason)

    def test_long_distance_prioritizes_direct_subway(self):
        recommended, reason = recommend_transport_mode(
            mode(12.0, 160),
            mode(14.0, 36),
            transit(12.5, 48, transit_type="subway"),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("优先选择地铁", reason)

    def test_long_distance_accepts_convenient_direct_bus(self):
        recommended, reason = recommend_transport_mode(
            mode(15.0, 200),
            mode(17.0, 42),
            transit(15.5, 58, transit_type="bus"),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("选择直达公交", reason)

    def test_long_distance_taxis_for_mixed_or_transfer_route(self):
        recommended, reason = recommend_transport_mode(
            mode(13.0, 175),
            mode(15.0, 38),
            transit(13.5, 55, transit_type="mixed", transfers=1),
        )
        self.assertEqual("driving", recommended)
        self.assertIn("混合换乘", reason)

    def test_falls_back_to_transit_when_taxi_is_unavailable(self):
        recommended, reason = recommend_transport_mode(
            mode(8.0, 110),
            mode(None, None, available=False),
            transit(8.5, 50, transfers=2),
        )
        self.assertEqual("public_transit", recommended)
        self.assertIn("打车路线不可用", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
