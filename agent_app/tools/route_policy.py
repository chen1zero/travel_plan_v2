"""Deterministic transport recommendation policy."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Tuple


TransportMode = str
MAX_WALK_DISTANCE_KM = 1.0
MAX_MID_DISTANCE_KM = 10.0
MAX_TRANSIT_ACCESS_WALK_KM = 0.6
MAX_ACCEPTABLE_TRANSFERS = 1


def recommend_transport_mode(
    walking: Mapping[str, Any],
    driving: Mapping[str, Any],
    public_transit: Mapping[str, Any],
) -> Tuple[TransportMode, str]:
    """Choose walking, transit, or taxi from distance and transit quality."""
    route_distance = _route_distance(walking, driving, public_transit)
    walking_available = (
        _available_number(walking, "duration_minutes") is not None
    )
    driving_available = (
        _available_number(driving, "duration_minutes") is not None
    )
    transit_available = (
        _available_number(public_transit, "duration_minutes") is not None
    )

    if (
        route_distance is not None
        and route_distance <= MAX_WALK_DISTANCE_KM
        and walking_available
    ):
        return "walking", "距离不超过1公里，推荐步行"

    transit_label = _public_transit_label(public_transit)
    access_walk = _available_number(
        public_transit,
        "walking_distance_km",
    )
    transfers = _available_number(public_transit, "transfer_count")
    transit_type = str(
        public_transit.get("transit_type") or "unknown"
    )

    if route_distance is not None and route_distance <= MAX_MID_DISTANCE_KM:
        if transit_available and _mid_distance_transit_is_convenient(
            access_walk,
            transfers,
            transit_type,
        ):
            transfer_text = "直达" if transfers == 0 else "仅换乘1次"
            return (
                "public_transit",
                f"1至10公里，{transit_label}{transfer_text}且"
                f"接驳步行合计不超过600米，推荐{transit_label}",
            )
        return _prefer_taxi_for_inconvenient_transit(
            public_transit,
            driving_available=driving_available,
            transit_available=transit_available,
            transit_label=transit_label,
            access_walk=access_walk,
            transfers=transfers,
            distance_band="1至10公里",
        )

    if route_distance is not None and route_distance > MAX_MID_DISTANCE_KM:
        if transit_available and _long_distance_transit_is_convenient(
            access_walk,
            transfers,
            transit_type,
        ):
            priority = (
                "优先选择地铁"
                if transit_type in {"subway", "rail"}
                else "选择直达公交"
            )
            return (
                "public_transit",
                f"距离超过10公里，{transit_label}直达且接驳步行"
                f"合计不超过600米，{priority}",
            )
        return _prefer_taxi_for_inconvenient_transit(
            public_transit,
            driving_available=driving_available,
            transit_available=transit_available,
            transit_label=transit_label,
            access_walk=access_walk,
            transfers=transfers,
            distance_band="距离超过10公里",
        )

    # When distance is unavailable, only recommend transit if its access and
    # transfer data still prove it convenient.
    if transit_available and _mid_distance_transit_is_convenient(
        access_walk,
        transfers,
        transit_type,
    ):
        return (
            "public_transit",
            f"距离数据暂缺，但{transit_label}换乘不超过1次且"
            f"接驳步行合计不超过600米，推荐{transit_label}",
        )
    if driving_available:
        return (
            "driving",
            f"距离或{transit_label}接驳数据不完整，推荐打车",
        )
    if transit_available:
        return (
            "public_transit",
            f"打车路线不可用，选择{transit_label}并在出发前确认换乘",
        )
    if walking_available:
        return "walking", "仅步行路线可用"
    return "walking", "路线数据暂不可用，请出发前再次确认"


def apply_transport_policy_to_plan(plan: MutableMapping[str, Any]) -> None:
    """Overwrite every stored route recommendation with this policy."""
    days = plan.get("daily_itinerary")
    if not isinstance(days, list):
        return
    for day in days:
        if not isinstance(day, Mapping):
            continue
        routes = day.get("routes")
        if not isinstance(routes, list):
            continue
        for route in routes:
            if not isinstance(route, MutableMapping):
                continue
            walking = route.get("walking")
            driving = route.get("driving")
            public_transit = route.get("public_transit")
            if not all(
                isinstance(mode, Mapping)
                for mode in (walking, driving, public_transit)
            ):
                continue
            recommended, reason = recommend_transport_mode(
                walking,
                driving,
                public_transit,
            )
            route["recommended_mode"] = recommended
            route["recommendation_reason"] = reason


def _route_distance(*modes: Mapping[str, Any]) -> float | None:
    for mode in modes:
        distance = _available_number(mode, "distance_km")
        if distance is not None:
            return distance
    return None


def _mid_distance_transit_is_convenient(
    access_walk: float | None,
    transfers: float | None,
    transit_type: str,
) -> bool:
    return (
        transit_type in {"subway", "bus", "mixed", "rail"}
        and access_walk is not None
        and access_walk <= MAX_TRANSIT_ACCESS_WALK_KM
        and transfers is not None
        and transfers <= MAX_ACCEPTABLE_TRANSFERS
    )


def _long_distance_transit_is_convenient(
    access_walk: float | None,
    transfers: float | None,
    transit_type: str,
) -> bool:
    return (
        transit_type in {"subway", "bus", "rail"}
        and access_walk is not None
        and access_walk <= MAX_TRANSIT_ACCESS_WALK_KM
        and transfers == 0
    )


def _prefer_taxi_for_inconvenient_transit(
    public_transit: Mapping[str, Any],
    *,
    driving_available: bool,
    transit_available: bool,
    transit_label: str,
    access_walk: float | None,
    transfers: float | None,
    distance_band: str,
) -> Tuple[TransportMode, str]:
    issue = _transit_issue(
        public_transit,
        access_walk=access_walk,
        transfers=transfers,
    )
    if driving_available:
        return "driving", f"{distance_band}，{issue}，推荐打车"
    if transit_available:
        return (
            "public_transit",
            f"{issue}，但打车路线不可用，选择{transit_label}",
        )
    return "walking", "公共交通与打车路线均不可用，请出发前再次确认"


def _transit_issue(
    mode: Mapping[str, Any],
    *,
    access_walk: float | None,
    transfers: float | None,
) -> str:
    label = _public_transit_label(mode)
    if not mode.get("available"):
        return f"{label}不可用"
    if transfers is not None and transfers >= 2:
        return f"{label}需要换乘{_format_number(transfers)}次"
    if access_walk is not None and access_walk > MAX_TRANSIT_ACCESS_WALK_KM:
        return (
            f"{label}接驳步行约{_format_number(access_walk)}公里，"
            "超过600米"
        )
    if str(mode.get("transit_type") or "unknown") == "mixed":
        return "需要公交与地铁混合换乘"
    if transfers is None or access_walk is None:
        return f"{label}换乘或接驳步行数据不完整"
    return f"{label}不够便利"


def _available_number(
    mode: Mapping[str, Any],
    field: str,
) -> float | None:
    if not mode.get("available"):
        return None
    value = mode.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _format_number(value: float) -> str:
    return f"{value:g}"


def _public_transit_label(mode: Mapping[str, Any]) -> str:
    return {
        "subway": "地铁",
        "bus": "公交",
        "mixed": "公交与地铁",
        "rail": "轨道交通",
    }.get(str(mode.get("transit_type") or ""), "公共交通")
