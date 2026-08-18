"""Compact route comparison built from Amap MCP direction tools."""

from __future__ import annotations

from math import ceil
from typing import Any, Callable, Dict, Mapping, Sequence

from agent_app.infrastructure.amap_client import (
    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
    AMAP_MCP_GEO_TOOL_NAME,
    AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME,
    AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
    AmapMCPClient,
)
from agent_app.infrastructure.mcp_client import MCPClientError
from agent_app.tools.errors import (
    InvalidToolResultError,
    classify_failure,
    execute_with_retry,
)
from agent_app.tools.route_policy import recommend_transport_mode


ROUTE_OPTIONS_TOOL_NAME = "compare_route_options"
ROUTE_OPTIONS_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ROUTE_OPTIONS_TOOL_NAME,
        "description": (
            "查询两个地点之间的步行、驾车和公共交通距离及时间。"
            "规划每一段相邻行程路线时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin_address": {
                    "type": "string",
                    "description": "起点名称和完整地址",
                },
                "destination_address": {
                    "type": "string",
                    "description": "终点名称和完整地址",
                },
                "origin_city": {
                    "type": "string",
                    "description": "起点城市",
                },
                "destination_city": {
                    "type": "string",
                    "description": "终点城市",
                },
            },
            "required": [
                "origin_address",
                "destination_address",
                "origin_city",
                "destination_city",
            ],
            "additionalProperties": False,
        },
    },
}


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("布尔值不是有效路线数值")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    raise ValueError("路线结果缺少有效数值")


def _optional_number(value: Any) -> float | None:
    try:
        return _number(value)
    except (TypeError, ValueError):
        return None


def _kilometers(value: float | None) -> float | None:
    return None if value is None else round(value / 1000, 2)


def _coordinate(value: Any) -> list[float] | None:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes),
    ):
        parts = list(value)
    else:
        return None
    if len(parts) != 2:
        return None
    try:
        longitude, latitude = (float(part) for part in parts)
    except (TypeError, ValueError):
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return [round(longitude, 6), round(latitude, 6)]


def _polyline_points(value: Any) -> list[list[float]]:
    raw_points: Sequence[Any]
    if isinstance(value, str):
        raw_points = value.split(";")
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes),
    ):
        raw_points = value
    else:
        return []
    points: list[list[float]] = []
    for raw_point in raw_points:
        point = _coordinate(raw_point)
        if point is not None and (not points or point != points[-1]):
            points.append(point)
        if len(points) >= 1200:
            break
    return points


def _append_polyline(
    points: list[list[float]],
    value: Any,
) -> None:
    for point in _polyline_points(value):
        if not points or point != points[-1]:
            points.append(point)
        if len(points) >= 1200:
            return


def _append_step_polylines(
    points: list[list[float]],
    value: Any,
) -> None:
    if not isinstance(value, list):
        return
    for step in value:
        if isinstance(step, Mapping):
            _append_polyline(points, step.get("polyline"))


def _route_polyline(
    route_option: Mapping[str, Any],
    mode: str,
) -> list[list[float]]:
    points: list[list[float]] = []
    if mode != "public_transit":
        _append_step_polylines(points, route_option.get("steps"))
        if not points:
            _append_polyline(points, route_option.get("polyline"))
        return points

    segments = route_option.get("segments")
    if not isinstance(segments, list):
        return points
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        walking = segment.get("walking")
        if isinstance(walking, Mapping):
            _append_step_polylines(points, walking.get("steps"))
            _append_polyline(points, walking.get("polyline"))
        bus = segment.get("bus")
        if isinstance(bus, Mapping):
            buslines = bus.get("buslines")
            if isinstance(buslines, list):
                busline = next(
                    (
                        item
                        for item in buslines
                        if isinstance(item, Mapping)
                    ),
                    None,
                )
                if busline is not None:
                    _append_polyline(points, busline.get("polyline"))
        for segment_type in ("railway", "taxi"):
            detail = segment.get(segment_type)
            if isinstance(detail, Mapping):
                _append_polyline(points, detail.get("polyline"))
    return points


def _is_subway_line(line: Mapping[str, Any]) -> bool:
    line_type = str(line.get("type") or "").strip().lower()
    if line_type:
        return any(
            keyword in line_type
            for keyword in ("地铁", "轨道交通", "轻轨", "subway", "metro")
        )
    line_name = str(line.get("name") or "").strip().lower()
    return line_name.startswith(("地铁", "轨道交通", "轻轨"))


def _transit_segment_metrics(
    transit: Mapping[str, Any],
) -> tuple[
    float | None,
    float | None,
    int | None,
    str,
    list[str],
]:
    """Summarize the selected transit option without misusing route.distance."""
    walking_distance = _optional_number(
        transit.get("walking_distance")
    )
    segments = transit.get("segments")
    if not isinstance(segments, list):
        return None, walking_distance, None, "unknown", []

    total_distance = 0.0
    summed_walking_distance = 0.0
    has_total_distance = False
    has_walking_distance = False
    ride_count = 0
    has_bus = False
    has_subway = False
    has_railway = False
    line_names: list[str] = []

    for segment in segments:
        if not isinstance(segment, Mapping):
            continue

        walking = segment.get("walking")
        if isinstance(walking, Mapping):
            distance = _optional_number(walking.get("distance"))
            if distance is not None:
                total_distance += distance
                summed_walking_distance += distance
                has_total_distance = True
                has_walking_distance = True

        bus = segment.get("bus")
        if isinstance(bus, Mapping):
            buslines = bus.get("buslines")
            if isinstance(buslines, list):
                first_busline = next(
                    (
                        item
                        for item in buslines
                        if isinstance(item, Mapping)
                    ),
                    None,
                )
                if first_busline is not None:
                    distance = _optional_number(
                        first_busline.get("distance")
                    )
                    if distance is not None:
                        total_distance += distance
                        has_total_distance = True
                    ride_count += 1
                    if _is_subway_line(first_busline):
                        has_subway = True
                    else:
                        has_bus = True
                    line_name = str(
                        first_busline.get("name") or ""
                    ).strip()
                    if line_name and line_name not in line_names:
                        line_names.append(line_name)

        railway = segment.get("railway")
        if isinstance(railway, Mapping) and railway:
            distance = _optional_number(railway.get("distance"))
            if distance is not None:
                total_distance += distance
                has_total_distance = True
            ride_count += 1
            has_railway = True
            railway_name = str(railway.get("name") or "").strip()
            if railway_name and railway_name not in line_names:
                line_names.append(railway_name)

        taxi = segment.get("taxi")
        if isinstance(taxi, Mapping) and taxi:
            distance = _optional_number(taxi.get("distance"))
            if distance is not None:
                total_distance += distance
                has_total_distance = True

    if walking_distance is None and has_walking_distance:
        walking_distance = summed_walking_distance
    transfer_count = max(0, ride_count - 1) if ride_count else None
    detected_mode_count = sum(
        (has_bus, has_subway, has_railway)
    )
    if detected_mode_count > 1:
        transit_type = "mixed"
    elif has_subway:
        transit_type = "subway"
    elif has_bus:
        transit_type = "bus"
    elif has_railway:
        transit_type = "rail"
    else:
        transit_type = "unknown"
    return (
        total_distance if has_total_distance else None,
        walking_distance,
        transfer_count,
        transit_type,
        line_names,
    )


def _compact_route(
    result: Mapping[str, Any],
    mode: str,
    *,
    include_polyline: bool = False,
) -> Dict[str, Any]:
    route = result.get("route")
    if not isinstance(route, Mapping):
        raise ValueError("路线结果缺少 route")

    if mode == "public_transit":
        options = route.get("transits")
        if not isinstance(options, list) or not options:
            raise ValueError("公共交通结果缺少 transits")
        first = options[0]
        if not isinstance(first, Mapping):
            raise ValueError("公共交通方案格式错误")
        duration = _number(first.get("duration"))
        (
            distance,
            walking_distance,
            transfer_count,
            transit_type,
            line_names,
        ) = _transit_segment_metrics(first)
        compact = {
            "available": True,
            "distance_km": _kilometers(distance),
            "duration_minutes": max(1, ceil(duration / 60)),
            "walking_distance_km": _kilometers(walking_distance),
            "transfer_count": transfer_count,
            "transit_type": transit_type,
            "line_names": line_names,
            "error": None,
        }
    else:
        options = route.get("paths")
        if not isinstance(options, list) or not options:
            raise ValueError("路线结果缺少 paths")
        first = options[0]
        if not isinstance(first, Mapping):
            raise ValueError("路线方案格式错误")
        distance = _number(first.get("distance"))
        duration = _number(first.get("duration"))

        compact = {
            "available": True,
            "distance_km": _kilometers(distance),
            "duration_minutes": max(1, ceil(duration / 60)),
            "error": None,
        }

    if include_polyline:
        compact["polyline"] = _route_polyline(first, mode)
    return compact


def _query_mode(
    query: Callable[[], Any],
    mode: str,
    *,
    include_polyline: bool = False,
) -> Dict[str, Any]:
    try:
        def query_and_compact() -> Dict[str, Any]:
            result = query()
            if not isinstance(result, Mapping):
                raise InvalidToolResultError("路线工具返回格式错误")
            try:
                return _compact_route(
                    result,
                    mode,
                    include_polyline=include_polyline,
                )
            except (ValueError, TypeError) as exc:
                raise InvalidToolResultError(
                    "路线工具返回结构错误"
                ) from exc

        return execute_with_retry(
            query_and_compact,
            max_attempts=2,
        )
    except (
        MCPClientError,
        InvalidToolResultError,
        ValueError,
        TypeError,
    ) as exc:
        failure = classify_failure(exc)
        unavailable = {
            "available": False,
            "distance_km": None,
            "duration_minutes": None,
            "error": failure.message,
        }
        if mode == "public_transit":
            unavailable.update(
                {
                    "walking_distance_km": None,
                    "transfer_count": None,
                    "transit_type": "unknown",
                    "line_names": [],
                }
            )
        if include_polyline:
            unavailable["polyline"] = []
        return unavailable


def _geocode(
    amap_client: AmapMCPClient,
    address: str,
    city: str,
) -> str:
    def query_and_parse() -> str:
        result = amap_client.call_tool(
            AMAP_MCP_GEO_TOOL_NAME,
            address=address,
            city=city,
        )
        if not isinstance(result, Mapping):
            raise InvalidToolResultError("地理编码工具返回格式错误")
        geocodes = result.get("return")
        if not isinstance(geocodes, list) or not geocodes:
            raise InvalidToolResultError(f"未找到地点坐标：{address}")
        first = geocodes[0]
        if not isinstance(first, Mapping):
            raise InvalidToolResultError("地理编码结果格式错误")
        location = first.get("location")
        if not isinstance(location, str) or not location.strip():
            raise InvalidToolResultError(f"地点缺少坐标：{address}")
        return location.strip()

    return execute_with_retry(query_and_parse, max_attempts=2)


def compare_route_options(
    origin_address: str,
    destination_address: str,
    origin_city: str,
    destination_city: str,
    amap_client: AmapMCPClient,
    *,
    include_polyline: bool = False,
) -> Mapping[str, Any]:
    """Return compact walking, driving, and transit route metrics."""
    normalized_origin = origin_address.strip()
    normalized_destination = destination_address.strip()
    normalized_origin_city = origin_city.strip()
    normalized_destination_city = destination_city.strip()
    if not normalized_origin or not normalized_destination:
        raise ValueError("路线起点和终点地址不能为空")
    if not normalized_origin_city or not normalized_destination_city:
        raise ValueError("路线起点和终点城市不能为空")

    try:
        origin_location = _geocode(
            amap_client,
            normalized_origin,
            normalized_origin_city,
        )
        destination_location = _geocode(
            amap_client,
            normalized_destination,
            normalized_destination_city,
        )
    except (MCPClientError, ValueError, TypeError) as exc:
        failure = classify_failure(exc)
        unavailable = {
            "available": False,
            "distance_km": None,
            "duration_minutes": None,
            "error": failure.message,
        }
        public_transit_unavailable = {
            **unavailable,
            "walking_distance_km": None,
            "transfer_count": None,
            "transit_type": "unknown",
            "line_names": [],
        }
        if include_polyline:
            unavailable["polyline"] = []
            public_transit_unavailable["polyline"] = []
        recommended, reason = recommend_transport_mode(
            unavailable,
            unavailable,
            public_transit_unavailable,
        )
        return {
            "origin": normalized_origin,
            "destination": normalized_destination,
            "walking": dict(unavailable),
            "driving": dict(unavailable),
            "public_transit": public_transit_unavailable,
            "recommended_mode": recommended,
            "recommendation_reason": reason,
        }

    walking = _query_mode(
        lambda: (
            amap_client.call_tool(
                AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
                origin=origin_location,
                destination=destination_location,
            )
        ),
        "walking",
        include_polyline=include_polyline,
    )
    driving = _query_mode(
        lambda: (
            amap_client.call_tool(
                AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
                origin=origin_location,
                destination=destination_location,
            )
        ),
        "driving",
        include_polyline=include_polyline,
    )
    public_transit = _query_mode(
        lambda: (
            amap_client.call_tool(
                AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME,
                origin=origin_location,
                destination=destination_location,
                city=normalized_origin_city,
                cityd=normalized_destination_city,
            )
        ),
        "public_transit",
        include_polyline=include_polyline,
    )
    recommended, reason = recommend_transport_mode(
        walking,
        driving,
        public_transit,
    )
    return {
        "origin": normalized_origin,
        "destination": normalized_destination,
        "origin_location": origin_location,
        "destination_location": destination_location,
        "walking": walking,
        "driving": driving,
        "public_transit": public_transit,
        "recommended_mode": recommended,
        "recommendation_reason": reason,
    }
