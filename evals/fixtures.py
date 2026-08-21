"""Deterministic evidence and reference plans for offline evaluation.

These helpers validate the evaluation plumbing without pretending to measure
model quality. Opt-in live experiments use the same frozen evidence so model
changes can be compared independently from changing map/search results.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agent_app.api.schemas import TravelPlanDocument
from agent_app.harness.travel_planning import analyze_request_changes
from agent_app.tools.route_policy import recommend_transport_mode


EVIDENCE_PATHS = {
    version: Path(__file__).resolve().parent / "fixtures" / filename
    for version, filename in {
        "core-evidence-v1": "core_evidence.v1.json",
        "core-evidence-v2": "core_evidence.v2.json",
    }.items()
}


def load_evidence(version: str = "core-evidence-v1") -> Dict[str, Any]:
    try:
        path = EVIDENCE_PATHS[version]
    except KeyError as exc:
        raise ValueError(f"未知冻结证据版本：{version}") from exc
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def research_context(
    city: str, version: str = "core-evidence-v1"
) -> Dict[str, str]:
    """Return the production-shaped reusable research strings."""
    evidence = city_evidence(city, version)
    return {
        "attractions": json.dumps(
            {"attractions": evidence["attractions"]}, ensure_ascii=False
        ),
        "weather": json.dumps(
            {"city": city, "forecast": evidence["weather"]},
            ensure_ascii=False,
        ),
        "hotels": json.dumps(
            {"hotels": evidence["hotels"]}, ensure_ascii=False
        ),
    }


def city_evidence(
    city: str, version: str = "core-evidence-v1"
) -> Dict[str, Any]:
    cities = load_evidence(version)["cities"]
    if city not in cities:
        raise ValueError(f"冻结证据不包含城市：{city}")
    return deepcopy(cities[city])


def build_fixture_output(inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a contract-valid, deterministic output for one plan case."""
    request = dict(inputs["request"])
    evidence_version = str(
        inputs.get("evidence_version") or "core-evidence-v1"
    )
    previous_request = inputs.get("previous_request")
    previous_plan: Optional[Dict[str, Any]] = None
    previous_context: Optional[Dict[str, Any]] = None
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

    behavior = inputs.get("revision_behavior")
    hotel_index = 1 if behavior == "hotel" else None
    plan = build_plan(
        request,
        city=str(inputs.get("fixture_city") or request["destination_city"]),
        route_profile=str(inputs.get("route_profile") or "normal"),
        hotel_index=hotel_index,
        add_night_attraction=behavior == "night_attraction",
        evidence_version=evidence_version,
    )
    change_analysis = analyze_request_changes(request, previous_context)
    return {
        "status": "completed",
        "plan": plan,
        "previous_plan": previous_plan,
        "change_analysis": change_analysis,
        "planning_context": {
            "request": request,
            **research_context(
                str(request["destination_city"]), evidence_version
            ),
        },
        "execution_mode": "offline_fixture",
        "tool_calls": [],
    }


def build_plan(
    request: Mapping[str, Any],
    *,
    city: str,
    route_profile: str = "normal",
    hotel_index: Optional[int] = None,
    add_night_attraction: bool = False,
    evidence_version: str = "core-evidence-v1",
) -> Dict[str, Any]:
    """Create a frozen reference plan from evidence, then validate it."""
    evidence = city_evidence(city, evidence_version)
    start = date.fromisoformat(str(request["start_date"]))
    end = date.fromisoformat(str(request["end_date"]))
    days = (end - start).days + 1
    attractions = evidence["attractions"]
    if len(attractions) < days + int(add_night_attraction):
        raise ValueError(f"{city} 的冻结景点不足以覆盖 {days} 天")
    requested_accommodation = str(request["accommodation_type"])
    hotels = evidence["hotels"]
    accommodation_matched = True
    if hotel_index is not None:
        hotel = hotels[hotel_index]
    else:
        hotel = next(
            (
                item
                for item in hotels
                if requested_accommodation == "不限"
                or item.get("type") == requested_accommodation
            ),
            hotels[0],
        )
        accommodation_matched = (
            requested_accommodation == "不限"
            or hotel.get("type") == requested_accommodation
        )

    unresolved_fields = ["实时票价", "实时酒店价格"]
    data_notes = [load_evidence(evidence_version)["notice"]]
    if not accommodation_matched:
        unresolved_fields.append(f"{requested_accommodation}住宿候选")
        data_notes.append(
            f"冻结证据中没有{requested_accommodation}住宿候选，当前酒店仅作备选，"
            "需要用户确认或补充搜索。"
        )

    weather_summary = []
    daily_itinerary = []
    for offset in range(days):
        travel_date = start + timedelta(days=offset)
        weather_summary.append(
            {
                "date": travel_date.isoformat(),
                **evidence["weather"],
                "advice": ["出发前再次确认实时天气"],
            }
        )
        day_attractions = [attractions[offset]]
        if add_night_attraction and offset == 0:
            day_attractions.append(attractions[days])
        schedule = []
        routes = []
        previous_place: Mapping[str, Any] = hotel
        for sequence, attraction in enumerate(day_attractions, start=1):
            evening = sequence > 1
            schedule.append(
                {
                    "schedule_item_id": f"day-{offset + 1}-item-{sequence}",
                    "order": sequence,
                    "time_slot": "19:00-21:00" if evening else "09:00-12:00",
                    "place_name": attraction["name"],
                    "address": attraction["address"],
                    "activity": "夜间游览" if evening else "参观游览",
                    "duration_minutes": None,
                    "notes": ["开放时间与预约要求请在出发前核对"],
                    "location": attraction["location"],
                }
            )
            routes.append(
                _route_segment(
                    sequence,
                    previous_place,
                    attraction,
                    city,
                    unavailable=route_profile == "partial_unavailable",
                )
            )
            previous_place = attraction
        daily_itinerary.append(
            {
                "day": offset + 1,
                "date": travel_date.isoformat(),
                "theme": f"{city}{'夜景与' if add_night_attraction and offset == 0 else ''}城市探索",
                "weather_advice": "按天气调整室内外活动，出发前复核预报",
                "schedule": schedule,
                "routes": routes,
                "estimated_cost_cny": {
                    "transport": None,
                    "tickets": None,
                    "food": None,
                    "hotel": None,
                    "subtotal": None,
                    "notes": ["冻结证据未提供可靠价格，费用保持未知"],
                },
            }
        )

    plan = {
        "plan_version": f"eval-fixture-{evidence_version}",
        "request_summary": {
            "destination_city": city,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": days,
            "budget_cny": request["budget_cny"],
            "preferences": list(request["preferences"]),
            "hotel_requirement": request["accommodation_type"],
            "unresolved_fields": unresolved_fields,
        },
        "weather_summary": weather_summary,
        "selected_hotel": {
            "name": hotel["name"],
            "address": hotel["address"],
            "selection_reason": (
                f"冻结候选中与{requested_accommodation}要求匹配"
                if accommodation_matched
                else f"冻结证据没有{requested_accommodation}候选，暂列为备选"
            ),
            "price_cny_per_night": hotel["price_cny_per_night"],
            "booking_note": "实时房价与库存需要在预订平台复核",
            "location": hotel["location"],
        },
        "daily_itinerary": daily_itinerary,
        "budget_summary": {
            "currency": "CNY",
            "total_budget": request["budget_cny"],
            "estimated_total": None,
            "remaining": None,
            "breakdown": {
                "transport": None,
                "tickets": None,
                "food": None,
                "hotel": None,
            },
            "notes": ["证据未提供可靠价格，不推测预算分项"],
        },
        "booking_and_safety_tips": ["出发前核对预约、营业时间和实时路线"],
        "data_notes": data_notes,
    }
    validated = TravelPlanDocument.model_validate(plan)
    return validated.model_dump(mode="json")


def _route_segment(
    sequence: int,
    origin: Mapping[str, Any],
    destination: Mapping[str, Any],
    city: str,
    *,
    unavailable: bool,
) -> Dict[str, Any]:
    origin_location = origin["location"]
    destination_location = destination["location"]
    straight_km = _distance_km(origin_location, destination_location)
    route_km = round(max(straight_km * 1.15, 0.2), 2)
    if unavailable:
        walking = _unavailable_mode("离线夹具模拟路线服务不可用")
        driving = _unavailable_mode("离线夹具模拟路线服务不可用")
        public_transit = {
            **_unavailable_mode("离线夹具模拟路线服务不可用"),
            "walking_distance_km": None,
            "transfer_count": None,
            "transit_type": "unknown",
            "line_names": [],
        }
    else:
        walking = {
            "available": True,
            "distance_km": route_km,
            "duration_minutes": round(route_km / 4.5 * 60, 1),
            "error": None,
            "polyline": None,
        }
        driving = {
            "available": True,
            "distance_km": route_km,
            "duration_minutes": round(max(route_km / 24 * 60, 5), 1),
            "error": None,
            "polyline": None,
        }
        public_transit = {
            "available": True,
            "distance_km": route_km,
            "duration_minutes": round(max(route_km / 18 * 60 + 8, 10), 1),
            "error": None,
            "polyline": None,
            "walking_distance_km": 0.4,
            "transfer_count": 0,
            "transit_type": "subway",
            "line_names": ["冻结证据示意线路"],
        }
    mode, reason = recommend_transport_mode(walking, driving, public_transit)
    return {
        "route_id": f"route-{sequence}",
        "sequence": sequence,
        "origin": {
            "name": origin["name"],
            "address": origin["address"],
            "city": city,
        },
        "destination": {
            "name": destination["name"],
            "address": destination["address"],
            "city": city,
        },
        "walking": walking,
        "driving": driving,
        "public_transit": public_transit,
        "recommended_mode": mode,
        "recommendation_reason": reason,
    }


def _unavailable_mode(message: str) -> Dict[str, Any]:
    return {
        "available": False,
        "distance_km": None,
        "duration_minutes": None,
        "error": message,
        "polyline": None,
    }


def _distance_km(
    first: Mapping[str, float], second: Mapping[str, float]
) -> float:
    """Approximate haversine distance for deterministic fixture metrics."""
    lat1 = math.radians(first["latitude"])
    lat2 = math.radians(second["latitude"])
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second["longitude"] - first["longitude"])
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
