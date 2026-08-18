"""Fill map coordinates in a completed plan with Amap geocoding."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, MutableMapping, Optional, Protocol

from agent_app.infrastructure.amap_client import (
    AMAP_MCP_GEO_TOOL_NAME,
    AmapMCPClient,
)
from agent_app.tools.route import compare_route_options
from agent_app.tools.errors import (
    InvalidToolResultError,
    execute_with_retry,
)


logger = logging.getLogger(__name__)


class RouteGeometryClient(Protocol):
    def route_polyline(
        self,
        mode: str,
        *,
        origin: str,
        destination: str,
        city: str,
        destination_city: str,
    ) -> list[list[float]]:
        ...


def enrich_plan_locations(
    plan: Dict[str, Any],
    *,
    amap_client: AmapMCPClient,
) -> Dict[str, Any]:
    """Geocode missing hotel and schedule coordinates in place."""
    city = str(
        plan.get("request_summary", {}).get("destination_city", "")
    ).strip()
    if not city:
        return plan

    points: list[MutableMapping[str, Any]] = []
    hotel = plan.get("selected_hotel")
    if isinstance(hotel, MutableMapping):
        points.append(hotel)

    days = plan.get("daily_itinerary")
    if isinstance(days, list):
        for day in days:
            if not isinstance(day, Mapping):
                continue
            schedule = day.get("schedule")
            if not isinstance(schedule, list):
                continue
            points.extend(
                item
                for item in schedule
                if isinstance(item, MutableMapping)
            )

    failures = 0
    for point in points:
        if _has_valid_location(point.get("location")):
            continue
        name = str(
            point.get("name") or point.get("place_name") or ""
        ).strip()
        address = str(point.get("address") or "").strip()
        qualified_address = "，".join(
            value for value in (name, address) if value
        )
        if not qualified_address:
            failures += 1
            continue
        try:
            point["location"] = _geocode(
                amap_client,
                qualified_address,
                city,
            )
        except Exception as exc:
            failures += 1
            logger.warning(
                "计划地点坐标补全失败 | place=%s | error=%s",
                qualified_address,
                exc,
            )

    if failures:
        notes = plan.setdefault("data_notes", [])
        if isinstance(notes, list):
            note = f"有 {failures} 个地点暂未获得地图坐标"
            if note not in notes:
                notes.append(note)
    return plan


def enrich_plan_map_data(
    plan: Dict[str, Any],
    *,
    amap_client: AmapMCPClient,
    route_geometry_client: Optional[RouteGeometryClient] = None,
) -> Dict[str, Any]:
    """Attach coordinates and road-aligned Amap route geometry."""
    enrich_plan_locations(plan, amap_client=amap_client)
    city = str(
        plan.get("request_summary", {}).get("destination_city", "")
    ).strip()
    days = plan.get("daily_itinerary")
    if not city or not isinstance(days, list):
        return plan

    failures = 0
    for day in days:
        if not isinstance(day, Mapping):
            continue
        routes = day.get("routes")
        if not isinstance(routes, list):
            continue
        for route in routes:
            if not isinstance(route, MutableMapping):
                continue
            origin = route.get("origin")
            destination = route.get("destination")
            if not isinstance(origin, Mapping) or not isinstance(
                destination,
                Mapping,
            ):
                failures += 1
                continue
            try:
                route_result = compare_route_options(
                    origin_address=_qualified_route_address(origin),
                    destination_address=_qualified_route_address(
                        destination
                    ),
                    origin_city=str(origin.get("city") or city),
                    destination_city=str(
                        destination.get("city") or city
                    ),
                    amap_client=amap_client,
                )
            except Exception as exc:
                failures += 1
                logger.warning(
                    "计划路线折线补全失败 | origin=%s | destination=%s "
                    "| error=%s",
                    origin.get("name"),
                    destination.get("name"),
                    exc,
                )
                continue
            recommended_mode = str(
                route.get("recommended_mode")
                or route_result.get("recommended_mode")
                or ""
            )
            target_mode = route.get(recommended_mode)
            origin_location = route_result.get("origin_location")
            destination_location = route_result.get(
                "destination_location"
            )
            geometry_added = False
            if (
                route_geometry_client is not None
                and isinstance(target_mode, MutableMapping)
                and isinstance(origin_location, str)
                and isinstance(destination_location, str)
            ):
                try:
                    polyline = route_geometry_client.route_polyline(
                        recommended_mode,
                        origin=origin_location,
                        destination=destination_location,
                        city=str(origin.get("city") or city),
                        destination_city=str(
                            destination.get("city") or city
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "高德道路折线查询失败 | origin=%s | "
                        "destination=%s | error=%s",
                        origin.get("name"),
                        destination.get("name"),
                        exc,
                    )
                else:
                    if len(polyline) >= 2:
                        target_mode["polyline"] = polyline
                        geometry_added = True
            if geometry_added:
                route["geometry_source"] = "amap"
            elif route_geometry_client is not None:
                failures += 1

    if failures:
        notes = plan.setdefault("data_notes", [])
        if isinstance(notes, list):
            note = f"有 {failures} 段路线暂未获得道路折线"
            if note not in notes:
                notes.append(note)
    return plan


def _qualified_route_address(point: Mapping[str, Any]) -> str:
    return "，".join(
        value
        for value in (
            str(point.get("name") or "").strip(),
            str(point.get("address") or "").strip(),
        )
        if value
    )


def _geocode(
    amap_client: AmapMCPClient,
    address: str,
    city: str,
) -> Dict[str, float]:
    def query_and_parse() -> Dict[str, float]:
        result = amap_client.call_tool(
            AMAP_MCP_GEO_TOOL_NAME,
            address=address,
            city=city,
        )
        try:
            if not isinstance(result, Mapping):
                raise ValueError("返回值不是对象")
            geocodes = result.get("return")
            if not isinstance(geocodes, list) or not geocodes:
                raise ValueError("未找到地点坐标")
            first = geocodes[0]
            if not isinstance(first, Mapping):
                raise ValueError("地理编码结果格式错误")
            raw_location = first.get("location")
            if not isinstance(raw_location, str):
                raise ValueError("地点缺少坐标")
            parts = [part.strip() for part in raw_location.split(",")]
            if len(parts) != 2:
                raise ValueError("地点坐标格式错误")
            longitude, latitude = (float(part) for part in parts)
            location = {
                "longitude": longitude,
                "latitude": latitude,
            }
            if not _has_valid_location(location):
                raise ValueError("地点坐标超出范围")
            return location
        except (TypeError, ValueError) as exc:
            raise InvalidToolResultError(
                f"地理编码结果无效：{address}"
            ) from exc

    return execute_with_retry(query_and_parse, max_attempts=2)


def _has_valid_location(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    longitude = value.get("longitude")
    latitude = value.get("latitude")
    return (
        isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and -180 <= longitude <= 180
        and isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and -90 <= latitude <= 90
    )
