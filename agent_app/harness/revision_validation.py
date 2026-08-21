"""Semantic acceptance checks for a revised travel plan."""

from __future__ import annotations

from datetime import date
import json
import math
import re
import unicodedata
from typing import Any, Dict, Mapping


_ATTRACTION_ADDITION_PATTERN = re.compile(
    r"(增加|新增|多加|加上|添加|安排).{0,18}"
    r"(景点|地点|夜景|博物馆|公园|古迹|街区)"
    r"|(景点|地点|夜景|博物馆|公园|古迹|街区).{0,18}"
    r"(增加|新增|多加|加上|添加|安排)"
)
_HOTEL_CHANGE_PATTERN = re.compile(
    r"换.{0,8}(酒店|住宿|住处|民宿)"
    r"|(?:酒店|住宿|住处|民宿).{0,8}(换|替换|更换)"
    r"|改住|换住"
)
_DAY_PATTERN = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*天")
UNSUPPORTED_FACT_PREFIX = "证据一致性："


def validate_revision_result(
    raw_plan: str,
    previous_plan: Mapping[str, Any],
    request_data: Mapping[str, Any],
) -> list[str]:
    """Return actionable unmet requirements; an empty list means pass."""
    try:
        plan = _parse_plan(raw_plan)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"修订结果不是合法完整 JSON：{exc}"]

    errors = [
        *validate_itinerary_uniqueness(plan),
        *validate_route_coverage(plan),
    ]
    instruction = str(
        request_data.get("additional_requirements") or ""
    ).strip()
    expected_budget = request_data.get("budget_cny")
    summary = plan.get("request_summary")
    budget_summary = plan.get("budget_summary")
    if isinstance(expected_budget, (int, float)):
        if not isinstance(summary, Mapping) or summary.get(
            "budget_cny"
        ) != expected_budget:
            errors.append(f"需求摘要预算必须更新为 {expected_budget:g} 元")
        if not isinstance(budget_summary, Mapping) or budget_summary.get(
            "total_budget"
        ) != expected_budget:
            errors.append(f"预算汇总总额必须更新为 {expected_budget:g} 元")

    expected_accommodation = request_data.get("accommodation_type")
    previous_summary = previous_plan.get("request_summary")
    previous_accommodation = (
        previous_summary.get("hotel_requirement")
        if isinstance(previous_summary, Mapping)
        else None
    )
    accommodation_changed = (
        expected_accommodation
        and expected_accommodation != previous_accommodation
    )
    if expected_accommodation:
        if not isinstance(summary, Mapping) or summary.get(
            "hotel_requirement"
        ) != expected_accommodation:
            errors.append(
                f"住宿要求必须更新为“{expected_accommodation}”"
            )

    hotel_change_requested = bool(
        accommodation_changed or _HOTEL_CHANGE_PATTERN.search(instruction)
    )
    selected_hotel = plan.get("selected_hotel")
    previous_hotel = previous_plan.get("selected_hotel")
    selected_name = (
        str(selected_hotel.get("name") or "")
        if isinstance(selected_hotel, Mapping)
        else ""
    )
    previous_name = (
        str(previous_hotel.get("name") or "")
        if isinstance(previous_hotel, Mapping)
        else ""
    )
    if hotel_change_requested and (
        not selected_name or selected_name == previous_name
    ):
        errors.append("用户要求更换住宿，selected_hotel 必须与上一版不同")

    if _is_budget_only_increase(
        request_data,
        previous_plan,
        instruction=instruction,
    ):
        if selected_hotel != previous_hotel:
            errors.append(
                "本轮仅提高预算且原住宿要求未变，"
                "selected_hotel 必须原样保留"
            )
        for field, label in (
            ("daily_itinerary", "每日行程和已有路线"),
            ("weather_summary", "天气摘要"),
        ):
            if plan.get(field) != previous_plan.get(field):
                errors.append(
                    f"本轮仅提高预算，{label}必须原样保留；"
                    "已有路线不得重新查询"
                )
    if expected_accommodation == "豪华型" and isinstance(
        selected_hotel, Mapping
    ):
        hotel_text = json.dumps(selected_hotel, ensure_ascii=False)
        if not re.search(r"豪华|五星|高端", hotel_text):
            errors.append("豪华型酒店的选择理由必须明确说明豪华型匹配")

    if _ATTRACTION_ADDITION_PATTERN.search(instruction):
        day_number = _extract_day_number(instruction) or 1
        previous_day = _find_day(previous_plan, day_number)
        revised_day = _find_day(plan, day_number)
        previous_schedule = _schedule(previous_day)
        revised_schedule = _schedule(revised_day)
        if len(revised_schedule) <= len(previous_schedule):
            errors.append(
                f"第 {day_number} 天必须比上一版至少多安排一个景点"
            )
        routes = (
            revised_day.get("routes", [])
            if isinstance(revised_day, Mapping)
            else []
        )
        if not isinstance(routes, list) or len(routes) < len(
            revised_schedule
        ):
            errors.append(
                f"第 {day_number} 天新增景点后必须补齐全部相邻路线"
            )
        if re.search(r"夜景|晚上|夜晚|夜间", instruction):
            previous_keys = {
                _item_key(item) for item in previous_schedule
            }
            added_items = [
                item
                for item in revised_schedule
                if _item_key(item) not in previous_keys
            ]
            if not any(_is_evening_item(item) for item in added_items):
                errors.append(
                    f"第 {day_number} 天新增景点必须安排在 17:00 之后的晚间时段"
                )
    return errors


def validate_generated_plan(raw_plan: str) -> list[str]:
    """Validate requirements shared by initial plans and revisions."""
    try:
        plan = _parse_plan(raw_plan)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"规划结果不是合法完整 JSON：{exc}"]
    return [
        *validate_itinerary_uniqueness(plan),
        *validate_route_coverage(plan),
    ]


def validate_supported_plan_facts(
    raw_plan: str,
    attractions: Any,
    hotels: Any,
) -> list[str]:
    """Reject monetary facts that are not supported by research outputs.

    A numeric zero is still a factual price claim. It is accepted only when
    the corresponding research item explicitly contains zero.
    """
    try:
        plan = _parse_plan(raw_plan)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"{UNSUPPORTED_FACT_PREFIX}规划结果不是合法 JSON：{exc}"]

    attraction_prices = _known_prices(attractions, "attractions", "price_cny")
    hotel_prices = _known_prices(hotels, "hotels", "price_cny_per_night")
    errors: list[str] = []

    selected_hotel = plan.get("selected_hotel")
    if isinstance(selected_hotel, Mapping):
        hotel_name = _normalize_place_name(
            str(selected_hotel.get("name") or "")
        )
        _check_optional_price(
            errors,
            "selected_hotel.price_cny_per_night",
            selected_hotel.get("price_cny_per_night"),
            hotel_prices.get(hotel_name),
        )

    all_attraction_names: list[str] = []
    days = plan.get("daily_itinerary", [])
    if isinstance(days, list):
        for fallback_day, day in enumerate(days, start=1):
            if not isinstance(day, Mapping):
                continue
            day_number = day.get("day")
            if not isinstance(day_number, int):
                day_number = fallback_day
            names = [
                _normalize_place_name(str(item.get("place_name") or ""))
                for item in _schedule(day)
            ]
            all_attraction_names.extend(names)
            costs = day.get("estimated_cost_cny")
            if not isinstance(costs, Mapping):
                continue
            _check_optional_price(
                errors,
                f"day{day_number}.estimated_cost_cny.tickets",
                costs.get("tickets"),
                _supported_sum(names, attraction_prices),
            )
            for field in ("transport", "food", "hotel", "subtotal"):
                _require_unknown(
                    errors,
                    f"day{day_number}.estimated_cost_cny.{field}",
                    costs.get(field),
                )

    budget = plan.get("budget_summary")
    if isinstance(budget, Mapping):
        for field in ("estimated_total", "remaining"):
            _require_unknown(
                errors,
                f"budget_summary.{field}",
                budget.get(field),
            )
        breakdown = budget.get("breakdown")
        if isinstance(breakdown, Mapping):
            _check_optional_price(
                errors,
                "budget_summary.breakdown.tickets",
                breakdown.get("tickets"),
                _supported_sum(all_attraction_names, attraction_prices),
            )
            for field in ("transport", "food", "hotel"):
                _require_unknown(
                    errors,
                    f"budget_summary.breakdown.{field}",
                    breakdown.get(field),
                )
    return errors


def sanitize_unsupported_plan_facts(
    raw_plan: str,
    attractions: Any,
    hotels: Any,
) -> tuple[str, list[str]]:
    """Conservatively replace unsupported monetary claims with ``null``."""
    try:
        plan = _parse_plan(raw_plan)
    except (json.JSONDecodeError, ValueError):
        return raw_plan, []

    attraction_prices = _known_prices(attractions, "attractions", "price_cny")
    hotel_prices = _known_prices(hotels, "hotels", "price_cny_per_night")
    changes: list[str] = []

    selected_hotel = plan.get("selected_hotel")
    if isinstance(selected_hotel, dict):
        hotel_name = _normalize_place_name(
            str(selected_hotel.get("name") or "")
        )
        if _unsupported_numeric(
            selected_hotel.get("price_cny_per_night"),
            hotel_prices.get(hotel_name),
        ):
            selected_hotel["price_cny_per_night"] = None
            changes.append("酒店价格缺少研究证据，已设为 null")

    all_attraction_names: list[str] = []
    days = plan.get("daily_itinerary", [])
    if isinstance(days, list):
        for fallback_day, day in enumerate(days, start=1):
            if not isinstance(day, dict):
                continue
            day_number = day.get("day")
            if not isinstance(day_number, int):
                day_number = fallback_day
            names = [
                _normalize_place_name(str(item.get("place_name") or ""))
                for item in _schedule(day)
            ]
            all_attraction_names.extend(names)
            costs = day.get("estimated_cost_cny")
            if not isinstance(costs, dict):
                continue
            day_changed = False
            expected_tickets = _supported_sum(names, attraction_prices)
            if _unsupported_numeric(costs.get("tickets"), expected_tickets):
                costs["tickets"] = None
                day_changed = True
                changes.append(f"第 {day_number} 天门票缺少研究证据，已设为 null")
            for field in ("transport", "food", "hotel", "subtotal"):
                if _is_number(costs.get(field)):
                    costs[field] = None
                    day_changed = True
                    changes.append(
                        f"第 {day_number} 天 {field} 缺少研究证据，已设为 null"
                    )
            if day_changed:
                notes = costs.get("notes")
                if isinstance(notes, list):
                    message = "无可靠价格证据的费用字段已保守设为 null"
                    if message not in notes:
                        notes.append(message)

    budget = plan.get("budget_summary")
    if isinstance(budget, dict):
        for field in ("estimated_total", "remaining"):
            if _is_number(budget.get(field)):
                budget[field] = None
                changes.append(f"预算 {field} 缺少完整证据，已设为 null")
        breakdown = budget.get("breakdown")
        if isinstance(breakdown, dict):
            expected_tickets = _supported_sum(
                all_attraction_names, attraction_prices
            )
            if _unsupported_numeric(
                breakdown.get("tickets"), expected_tickets
            ):
                breakdown["tickets"] = None
                changes.append("预算门票分项缺少研究证据，已设为 null")
            for field in ("transport", "food", "hotel"):
                if _is_number(breakdown.get(field)):
                    breakdown[field] = None
                    changes.append(f"预算 {field} 分项缺少研究证据，已设为 null")
        if changes:
            notes = budget.get("notes")
            if isinstance(notes, list):
                message = "费用只保留研究证据明确支持的数值"
                if message not in notes:
                    notes.append(message)

    if not changes:
        return raw_plan, []
    return json.dumps(plan, ensure_ascii=False), changes


def normalize_terminal_hotel_return_routes(
    raw_plan: str,
) -> tuple[str, list[str]]:
    """Remove only an extra final route that explicitly returns to the hotel.

    The browser contract models the outbound hotel-to-first-place segment and
    every place-to-place segment, so the route count equals the schedule count.
    Some models also add a final return-to-hotel segment. That segment is useful
    prose-wise but outside this contract and can be removed deterministically.
    """
    try:
        plan = _parse_plan(raw_plan)
    except (json.JSONDecodeError, ValueError):
        return raw_plan, []
    selected_hotel = plan.get("selected_hotel")
    if not isinstance(selected_hotel, Mapping):
        return raw_plan, []
    days = plan.get("daily_itinerary")
    if not isinstance(days, list):
        return raw_plan, []

    changes: list[str] = []
    for fallback_day, day in enumerate(days, start=1):
        if not isinstance(day, dict):
            continue
        schedule = day.get("schedule")
        routes = day.get("routes")
        if (
            not isinstance(schedule, list)
            or not isinstance(routes, list)
            or len(routes) != len(schedule) + 1
        ):
            continue
        final_route = routes[-1]
        if not isinstance(final_route, Mapping):
            continue
        destination = final_route.get("destination")
        if not isinstance(destination, Mapping) or not _same_place(
            destination,
            selected_hotel,
        ):
            continue
        day["routes"] = routes[:-1]
        day_number = day.get("day")
        if not isinstance(day_number, int):
            day_number = fallback_day
        changes.append(f"第 {day_number} 天移除契约外的返酒店尾段")
    if not changes:
        return raw_plan, []
    return json.dumps(plan, ensure_ascii=False), changes


def validate_route_coverage(plan: Mapping[str, Any]) -> list[str]:
    """Require one ordered route for every scheduled place on every day."""
    days = plan.get("daily_itinerary", [])
    if not isinstance(days, list):
        return ["daily_itinerary 必须是数组"]

    errors: list[str] = []
    for fallback_day, day in enumerate(days, start=1):
        if not isinstance(day, Mapping):
            errors.append(f"第 {fallback_day} 天行程必须是 JSON 对象")
            continue
        day_number = day.get("day")
        if not isinstance(day_number, int):
            day_number = fallback_day
        schedule = day.get("schedule")
        routes = day.get("routes")
        if not isinstance(schedule, list):
            errors.append(f"第 {day_number} 天 schedule 必须是数组")
            continue
        if not isinstance(routes, list):
            errors.append(
                f"第 {day_number} 天有 {len(schedule)} 个日程地点，"
                "但 routes 不是数组；必须为每个地点补齐相邻路线"
            )
            continue
        if len(routes) != len(schedule):
            errors.append(
                f"第 {day_number} 天有 {len(schedule)} 个日程地点，"
                f"但只有 {len(routes)} 条路线；"
                "必须从酒店到首个地点开始，为每个地点补齐一条相邻路线"
            )
            continue
        expected_sequences = list(range(1, len(routes) + 1))
        actual_sequences = [
            route.get("sequence") if isinstance(route, Mapping) else None
            for route in routes
        ]
        if all(value is not None for value in actual_sequences) and (
            actual_sequences != expected_sequences
        ):
            errors.append(
                f"第 {day_number} 天 routes.sequence 必须从 1 连续递增"
            )
    return errors


def validate_itinerary_uniqueness(
    plan: Mapping[str, Any],
) -> list[str]:
    """Reject the same scheduled place appearing on multiple days."""
    days = plan.get("daily_itinerary", [])
    if not isinstance(days, list):
        return []

    seen: dict[str, tuple[int, str]] = {}
    duplicates: list[str] = []
    for fallback_day, day in enumerate(days, start=1):
        if not isinstance(day, Mapping):
            continue
        day_number = day.get("day")
        if not isinstance(day_number, int):
            day_number = fallback_day
        for item in _schedule(day):
            place_name = str(item.get("place_name") or "").strip()
            normalized_name = _normalize_place_name(place_name)
            if not normalized_name:
                continue
            previous = seen.get(normalized_name)
            if previous is None:
                seen[normalized_name] = (day_number, place_name)
                continue
            previous_day, previous_name = previous
            if previous_day == day_number:
                continue
            description = (
                f"景点“{previous_name or place_name}”同时出现在"
                f"第 {previous_day} 天和第 {day_number} 天；"
                "每个景点只能安排一次，必须将后一次替换为不同景点并更新路线"
            )
            if description not in duplicates:
                duplicates.append(description)
    return duplicates


def _parse_plan(raw_plan: str) -> Dict[str, Any]:
    content = raw_plan.strip()
    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("顶层必须是 JSON 对象")
    return value


def _extract_day_number(instruction: str) -> int | None:
    match = _DAY_PATTERN.search(instruction)
    if not match:
        return None
    value = match.group(1)
    if value.isdigit():
        return int(value)
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value)


def _find_day(
    plan: Mapping[str, Any],
    day_number: int,
) -> Mapping[str, Any] | None:
    days = plan.get("daily_itinerary", [])
    if not isinstance(days, list):
        return None
    for day in days:
        if isinstance(day, Mapping) and day.get("day") == day_number:
            return day
    return None


def _schedule(
    day: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    if not isinstance(day, Mapping):
        return []
    schedule = day.get("schedule", [])
    if not isinstance(schedule, list):
        return []
    return [item for item in schedule if isinstance(item, Mapping)]


def _item_key(item: Mapping[str, Any]) -> str:
    return str(
        item.get("schedule_item_id") or item.get("place_name") or ""
    ).strip()


def _normalize_place_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _same_place(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    first_name = _normalize_place_name(str(first.get("name") or ""))
    second_name = _normalize_place_name(str(second.get("name") or ""))
    if first_name and second_name and first_name == second_name:
        return True
    first_address = _normalize_place_name(str(first.get("address") or ""))
    second_address = _normalize_place_name(str(second.get("address") or ""))
    return bool(
        first_address
        and second_address
        and first_address == second_address
    )


def _is_evening_item(item: Mapping[str, Any]) -> bool:
    time_slot = str(item.get("time_slot") or "")
    match = re.match(r"\s*(\d{1,2}):", time_slot)
    return bool(match and int(match.group(1)) >= 17)


def _known_prices(
    value: Any,
    list_field: str,
    price_field: str,
) -> dict[str, float]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(parsed, Mapping):
        return {}
    items = parsed.get(list_field)
    if not isinstance(items, list):
        return {}
    prices: dict[str, float] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = _normalize_place_name(str(item.get("name") or ""))
        price = item.get(price_field)
        if name and _is_number(price):
            prices[name] = float(price)
    return prices


def _supported_sum(
    names: list[str], prices: Mapping[str, float]
) -> float | None:
    if not names or any(name not in prices for name in names):
        return None
    return sum(prices[name] for name in names)


def _check_optional_price(
    errors: list[str],
    path: str,
    actual: Any,
    expected: float | None,
) -> None:
    if not _is_number(actual):
        return
    if expected is None:
        errors.append(
            f"{UNSUPPORTED_FACT_PREFIX}{path}={actual:g}，但研究结果没有价格证据；"
            "必须使用 null"
        )
        return
    if not math.isclose(float(actual), expected, rel_tol=0, abs_tol=0.01):
        errors.append(
            f"{UNSUPPORTED_FACT_PREFIX}{path}={actual:g} 与研究证据 "
            f"{expected:g} 不一致；必须使用证据值或 null"
        )


def _require_unknown(errors: list[str], path: str, actual: Any) -> None:
    if _is_number(actual):
        errors.append(
            f"{UNSUPPORTED_FACT_PREFIX}{path}={actual:g}，但当前工具未提供该类"
            "费用证据；必须使用 null"
        )


def _unsupported_numeric(actual: Any, expected: float | None) -> bool:
    return _is_number(actual) and (
        expected is None
        or not math.isclose(float(actual), expected, rel_tol=0, abs_tol=0.01)
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_budget_only_increase(
    request_data: Mapping[str, Any],
    previous_plan: Mapping[str, Any],
    *,
    instruction: str,
) -> bool:
    previous_summary = previous_plan.get("request_summary")
    if not isinstance(previous_summary, Mapping):
        return False
    current_budget = request_data.get("budget_cny")
    previous_budget = previous_summary.get("budget_cny")
    if (
        isinstance(current_budget, bool)
        or isinstance(previous_budget, bool)
        or not isinstance(current_budget, (int, float))
        or not isinstance(previous_budget, (int, float))
        or current_budget <= previous_budget
    ):
        return False
    if re.search(
        r"景点|地点|夜景|博物馆|公园|古迹|街区|"
        r"酒店|住宿|住处|民宿|天气|预报",
        instruction,
    ):
        return False
    comparisons = (
        ("destination_city", "destination_city"),
        ("start_date", "start_date"),
        ("end_date", "end_date"),
        ("preferences", "preferences"),
        ("accommodation_type", "hotel_requirement"),
    )
    for current_field, previous_field in comparisons:
        current_value = request_data.get(current_field)
        previous_value = previous_summary.get(previous_field)
        if isinstance(current_value, (str, date)) or isinstance(
            previous_value, (str, date)
        ):
            if str(current_value) != str(previous_value):
                return False
        elif current_value != previous_value:
            return False
    return True
