"""Opt-in real-LLM target backed by frozen, non-network evidence tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import os
from threading import Lock
from typing import Any, Dict, Mapping

from agent_app.agents.attraction import AttractionSearchAgent
from agent_app.agents.hotel import HotelAgent
from agent_app.agents.planner import PlannerAgent
from agent_app.agents.weather import WeatherQueryAgent
from agent_app.api.schemas import TravelPlanDocument, TravelRequest
from agent_app.api.services.task_manager import (
    _ensure_stable_ids,
    _parse_planner_json,
    _validate_request_coverage,
)
from agent_app.harness.travel_planning import TravelPlanningHarness
from agent_app.infrastructure.llm_client import OpenAICompatibleLLM
from agent_app.shared.config import Settings
from agent_app.tools.route import ROUTE_OPTIONS_TOOL_SCHEMA
from agent_app.tools.route_policy import recommend_transport_mode
from evals.fixtures import build_plan, city_evidence, research_context


TEXT_SEARCH_SCHEMA = {
    "name": "maps_text_search",
    "description": "在指定城市的冻结评估证据中搜索 POI。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "keywords": {"type": "string"},
            "city": {"type": "string"},
            "citylimit": {"type": "string", "enum": ["true", "false"]},
        },
        "required": ["keywords"],
        "additionalProperties": False,
    },
}
WEATHER_SCHEMA = {
    "name": "maps_weather",
    "description": "查询指定城市的冻结评估天气证据。",
    "inputSchema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
}


class FrozenTools:
    """Record calls and return evidence without accessing Amap or the web."""

    def __init__(
        self,
        request: Mapping[str, Any],
        route_profile: str,
        evidence_version: str = "core-evidence-v1",
    ) -> None:
        self.request = request
        self.city = str(request["destination_city"])
        self.evidence = city_evidence(self.city, evidence_version)
        self.evidence_version = evidence_version
        self.route_profile = route_profile
        self.calls: list[Dict[str, Any]] = []
        self._lock = Lock()

    def _record(
        self, name: str, arguments: Mapping[str, Any], *, stage: str
    ) -> None:
        with self._lock:
            self.calls.append(
                {"name": name, "stage": stage, "arguments": dict(arguments)}
            )

    def text_search(
        self, keywords: str, city: str = "", citylimit: str = "false"
    ) -> Mapping[str, Any]:
        arguments = {"keywords": keywords, "city": city, "citylimit": citylimit}
        is_hotel = any(word in keywords for word in ("酒店", "住宿", "民宿"))
        self._record(
            "maps_text_search",
            arguments,
            stage="hotel" if is_hotel else "attraction",
        )
        source = self.evidence["hotels" if is_hotel else "attractions"]
        pois = []
        for item in source:
            pois.append(
                {
                    **item,
                    "type": item.get("type") or ("酒店" if is_hotel else "风景名胜"),
                    "typecode": None,
                    "tel": None,
                }
            )
        return {"city": self.city, "pois": pois, "data_notes": []}

    def weather(self, city: str) -> Mapping[str, Any]:
        self._record("maps_weather", {"city": city}, stage="weather")
        start = date.fromisoformat(str(self.request["start_date"]))
        end = date.fromisoformat(str(self.request["end_date"]))
        days = (end - start).days + 1
        forecasts = [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                **self.evidence["weather"],
                "day_wind": None,
                "night_wind": None,
            }
            for offset in range(days)
        ]
        return {"city": self.city, "forecasts": forecasts, "data_notes": []}

    def route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: str,
        destination_city: str,
    ) -> Mapping[str, Any]:
        arguments = {
            "origin_address": origin_address,
            "destination_address": destination_address,
            "origin_city": origin_city,
            "destination_city": destination_city,
        }
        self._record("compare_route_options", arguments, stage="planner")
        origin = self._find_place(origin_address)
        destination = self._find_place(destination_address)
        if self.route_profile == "partial_unavailable":
            walking = _unavailable()
            driving = _unavailable()
            transit = {
                **_unavailable(),
                "walking_distance_km": None,
                "transfer_count": None,
                "transit_type": "unknown",
                "line_names": [],
            }
        else:
            walking = _mode(2.5, 34)
            driving = _mode(2.8, 16)
            transit = {
                **_mode(2.6, 28),
                "walking_distance_km": 0.4,
                "transfer_count": 0,
                "transit_type": "subway",
                "line_names": ["冻结证据示意线路"],
            }
        recommended, reason = recommend_transport_mode(walking, driving, transit)
        return {
            "origin": origin_address,
            "destination": destination_address,
            "origin_location": _location_text(origin["location"]),
            "destination_location": _location_text(destination["location"]),
            "walking": walking,
            "driving": driving,
            "public_transit": transit,
            "recommended_mode": recommended,
            "recommendation_reason": reason,
        }

    def _find_place(self, value: str) -> Mapping[str, Any]:
        candidates = self.evidence["hotels"] + self.evidence["attractions"]
        for item in candidates:
            if item["name"] in value or item["address"] in value:
                return item
        raise ValueError("路线参数中的地点不在冻结研究证据中")


def run_live_target(suite: str, inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """Run the production agent graph with a real LLM and frozen tools."""
    if os.environ.get("RUN_LANGSMITH_EVALS") != "1":
        raise RuntimeError("live eval 需要显式设置 RUN_LANGSMITH_EVALS=1")
    if suite not in {"initial_plan", "revision"}:
        from evals.targets import run_target

        return run_target(suite, inputs)

    request_data = dict(inputs["request"])
    evidence_version = str(
        inputs.get("evidence_version") or "core-evidence-v1"
    )
    request = TravelRequest.model_validate(request_data)
    settings = Settings.from_env(env_file=None)
    tools = FrozenTools(
        request_data,
        str(inputs.get("route_profile") or "normal"),
        evidence_version,
    )

    attraction = AttractionSearchAgent(OpenAICompatibleLLM(settings))
    attraction.add_tool(tools.text_search, schema=TEXT_SEARCH_SCHEMA)
    weather = WeatherQueryAgent(OpenAICompatibleLLM(settings))
    weather.add_tool(tools.weather, schema=WEATHER_SCHEMA)
    hotel = HotelAgent(OpenAICompatibleLLM(settings))
    hotel.add_tool(tools.text_search, schema=TEXT_SEARCH_SCHEMA)
    planner = PlannerAgent(OpenAICompatibleLLM(settings))
    planner.add_tool(tools.route, schema=ROUTE_OPTIONS_TOOL_SCHEMA)
    harness = TravelPlanningHarness(attraction, weather, hotel, planner)

    previous_plan = None
    previous_context = None
    previous_request = inputs.get("previous_request")
    if isinstance(previous_request, Mapping):
        previous_plan = build_plan(
            previous_request,
            city=str(previous_request["destination_city"]),
            evidence_version=evidence_version,
        )
        previous_context = {
            "request": dict(previous_request),
            **research_context(
                str(previous_request["destination_city"]), evidence_version
            ),
        }
    try:
        raw = harness.run(
            request.to_agent_prompt(),
            request_data=request.model_dump(mode="json"),
            previous_plan=previous_plan,
            previous_context=previous_context,
            trace_metadata={
                "eval_suite": suite,
                "frozen_evidence": True,
                "evidence_version": evidence_version,
            },
        )
        plan = _parse_planner_json(raw)
        _ensure_stable_ids(plan)
        _validate_request_coverage(plan, request)
        enrich_frozen_locations(plan, tools.evidence)
        plan = TravelPlanDocument.model_validate(plan).model_dump(mode="json")
        context = harness.planning_context()
        return {
            "status": "completed",
            "plan": plan,
            "previous_plan": previous_plan,
            "change_analysis": context.get("change_analysis", {}),
            "planning_context": context,
            "execution_mode": "live_llm_frozen_evidence",
            "tool_calls": tools.calls,
        }
    finally:
        harness.close()


def _mode(distance: float, duration: float) -> Dict[str, Any]:
    return {
        "available": True,
        "distance_km": distance,
        "duration_minutes": duration,
        "error": None,
    }


def _unavailable() -> Dict[str, Any]:
    return {
        "available": False,
        "distance_km": None,
        "duration_minutes": None,
        "error": "冻结夹具模拟路线服务不可用",
    }


def _location_text(location: Mapping[str, Any]) -> str:
    return f"{location['longitude']},{location['latitude']}"


def enrich_frozen_locations(
    plan: Dict[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    """Mirror production location enrichment using the frozen POI evidence."""
    candidates = [
        item
        for collection in (
            evidence.get("hotels", []),
            evidence.get("attractions", []),
        )
        if isinstance(collection, list)
        for item in collection
        if isinstance(item, Mapping)
    ]
    points: list[Dict[str, Any]] = []
    hotel = plan.get("selected_hotel")
    if isinstance(hotel, dict):
        points.append(hotel)
    for day in plan.get("daily_itinerary", []):
        if not isinstance(day, Mapping):
            continue
        points.extend(
            item
            for item in day.get("schedule", [])
            if isinstance(item, dict)
        )
    for point in points:
        if _valid_location(point.get("location")):
            continue
        point_name = str(point.get("name") or point.get("place_name") or "")
        point_address = str(point.get("address") or "")
        for candidate in candidates:
            if (
                point_name == str(candidate.get("name") or "")
                or (
                    point_address
                    and point_address == str(candidate.get("address") or "")
                )
            ):
                location = candidate.get("location")
                if _valid_location(location):
                    point["location"] = deepcopy(location)
                break


def _valid_location(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("longitude"), (int, float))
        and not isinstance(value.get("longitude"), bool)
        and isinstance(value.get("latitude"), (int, float))
        and not isinstance(value.get("latitude"), bool)
    )
