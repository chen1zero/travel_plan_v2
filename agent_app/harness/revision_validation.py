"""Semantic acceptance checks for a revised travel plan."""

from __future__ import annotations

import json
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

    errors = validate_itinerary_uniqueness(plan)
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
    return validate_itinerary_uniqueness(plan)


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


def _is_evening_item(item: Mapping[str, Any]) -> bool:
    time_slot = str(item.get("time_slot") or "")
    match = re.match(r"\s*(\d{1,2}):", time_slot)
    return bool(match and int(match.group(1)) >= 17)
