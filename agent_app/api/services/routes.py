"""Recalculate edited itinerary route segments with Amap MCP."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping
from uuid import uuid4

from agent_app.infrastructure.amap_client import AmapMCPClient
from agent_app.infrastructure.amap_web_client import AmapWebServiceClient
from agent_app.shared.config import Settings
from agent_app.tools.route import compare_route_options
from agent_app.tools.route_policy import recommend_transport_mode


class AmapRouteRebuilder:
    """Rebuild routes server-side instead of trusting browser estimates."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._geometry_client = AmapWebServiceClient(
            settings.amap_api_key,
            timeout_seconds=settings.amap_mcp_timeout_seconds,
        )

    def __call__(
        self,
        plan: Dict[str, Any],
        daily_itinerary: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        days = deepcopy(daily_itinerary)
        city = str(
            plan.get("request_summary", {}).get(
                "destination_city",
                "",
            )
        ).strip()
        hotel = plan.get("selected_hotel", {})
        hotel_point = {
            "name": str(hotel.get("name", "")).strip(),
            "address": str(hotel.get("address", "")).strip(),
        }
        amap_client = AmapMCPClient(
            api_key=self._settings.amap_api_key,
            command=self._settings.amap_mcp_command,
            timeout_seconds=self._settings.amap_mcp_timeout_seconds,
        )
        try:
            for day in days:
                schedule = day.get("schedule", [])
                points = [
                    hotel_point,
                    *[
                        {
                            "name": str(item.get("place_name", "")).strip(),
                            "address": str(item.get("address", "")).strip(),
                        }
                        for item in schedule
                        if isinstance(item, dict)
                    ],
                ]
                routes: List[Dict[str, Any]] = []
                for index, (origin, destination) in enumerate(
                    zip(points, points[1:]),
                    start=1,
                ):
                    result = compare_route_options(
                        origin_address=_qualified_address(origin),
                        destination_address=_qualified_address(destination),
                        origin_city=city,
                        destination_city=city,
                        amap_client=amap_client,
                    )
                    route = _build_route(
                        result,
                        origin,
                        destination,
                        city,
                        index,
                    )
                    _attach_recommended_geometry(
                        route,
                        result,
                        city,
                        self._geometry_client,
                    )
                    routes.append(route)
                day["routes"] = routes
            return days
        finally:
            amap_client.close()


def _qualified_address(point: Mapping[str, Any]) -> str:
    name = str(point.get("name", "")).strip()
    address = str(point.get("address", "")).strip()
    return "，".join(part for part in (name, address) if part)


def _normalize_mode(value: Any) -> Dict[str, Any]:
    mode = value if isinstance(value, Mapping) else {}
    normalized = {
        "available": bool(mode.get("available", False)),
        "distance_km": mode.get("distance_km"),
        "duration_minutes": mode.get("duration_minutes"),
        "error": mode.get("error"),
    }
    for field in (
        "walking_distance_km",
        "transfer_count",
        "transit_type",
        "line_names",
        "polyline",
    ):
        if field in mode:
            normalized[field] = mode.get(field)
    return normalized


def _build_route(
    result: Mapping[str, Any],
    origin: Mapping[str, Any],
    destination: Mapping[str, Any],
    city: str,
    sequence: int,
) -> Dict[str, Any]:
    modes = {
        "walking": _normalize_mode(result.get("walking")),
        "driving": _normalize_mode(result.get("driving")),
        "public_transit": _normalize_mode(
            result.get("public_transit")
        ),
    }
    recommended, reason = recommend_transport_mode(
        modes["walking"],
        modes["driving"],
        modes["public_transit"],
    )
    return {
        "route_id": f"edited_{uuid4().hex}",
        "sequence": sequence,
        "origin": {
            "name": origin.get("name", ""),
            "address": origin.get("address", ""),
            "city": city,
        },
        "destination": {
            "name": destination.get("name", ""),
            "address": destination.get("address", ""),
            "city": city,
        },
        **modes,
        "recommended_mode": recommended,
        "recommendation_reason": reason,
    }


def _attach_recommended_geometry(
    route: Dict[str, Any],
    result: Mapping[str, Any],
    city: str,
    client: AmapWebServiceClient,
) -> None:
    mode_name = str(route.get("recommended_mode") or "")
    mode = route.get(mode_name)
    origin = result.get("origin_location")
    destination = result.get("destination_location")
    if (
        not isinstance(mode, dict)
        or not isinstance(origin, str)
        or not isinstance(destination, str)
    ):
        return
    try:
        polyline = client.route_polyline(
            mode_name,
            origin=origin,
            destination=destination,
            city=city,
            destination_city=city,
        )
    except Exception:
        return
    if len(polyline) >= 2:
        mode["polyline"] = polyline
        route["geometry_source"] = "amap"
