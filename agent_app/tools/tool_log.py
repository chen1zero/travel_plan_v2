"""Compact, secret-safe summaries for user-visible tool events."""

from __future__ import annotations

import json
from typing import Any, Mapping

from agent_app.shared.logging import preview


_SECRET_PARTS = ("key", "token", "secret", "password", "authorization")


def summarize_tool_arguments(tool_name: str, raw_arguments: str) -> str:
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return "参数格式无法解析"
    if not isinstance(arguments, Mapping):
        return "参数不是对象"

    if tool_name == "maps_text_search":
        keywords = str(arguments.get("keywords") or "").strip()
        city = str(arguments.get("city") or "").strip()
        return f"在{city or '目标城市'}搜索“{keywords or '相关地点'}”"
    if tool_name == "maps_weather":
        city = str(arguments.get("city") or "").strip()
        return f"查询{city or '目标城市'}天气"
    if tool_name == "compare_route_options":
        origin = _short_place(arguments.get("origin_address"))
        destination = _short_place(
            arguments.get("destination_address")
        )
        return f"{origin} → {destination}"

    return preview(_safe_value(arguments), 180)


def summarize_tool_result(tool_name: str, raw_result: str) -> str:
    try:
        payload = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return preview(raw_result, 240)
    if not isinstance(payload, Mapping):
        return preview(payload, 240)
    if not payload.get("ok"):
        details = payload.get("error_details")
        safe_message = (
            details.get("message")
            if isinstance(details, Mapping)
            else None
        )
        return f"调用失败：{safe_message or '工具执行失败'}"

    result = payload.get("result")
    if tool_name == "maps_text_search":
        names = _collect_named_items(result)
        if names:
            return (
                f"找到 {len(names)} 个地点："
                + "、".join(names[:5])
            )
        return "地点搜索完成，但未解析到地点名称"
    if tool_name == "maps_weather":
        forecasts = _collect_forecasts(result)
        if forecasts:
            return "；".join(forecasts[:4])
        return f"天气查询完成：{preview(_safe_value(result), 220)}"
    if tool_name == "compare_route_options":
        return _route_summary(result)

    return preview(_safe_value(result), 240)


def _route_summary(value: Any) -> str:
    if not isinstance(value, Mapping):
        return f"路线查询完成：{preview(value, 220)}"
    origin = _short_place(value.get("origin"))
    destination = _short_place(value.get("destination"))
    modes = []
    for key, label in (
        ("walking", "步行"),
        ("driving", "打车"),
        ("public_transit", "公交"),
    ):
        mode = value.get(key)
        if not isinstance(mode, Mapping) or not mode.get("available"):
            modes.append(f"{label}不可用")
            continue
        if key == "public_transit":
            label = _transit_label(mode)
        distance = mode.get("distance_km")
        minutes = mode.get("duration_minutes")
        metrics = []
        if distance is not None:
            metrics.append(f"{distance}公里")
        if minutes is not None:
            metrics.append(f"{minutes}分钟")
        if key == "public_transit":
            walking_distance = mode.get("walking_distance_km")
            if walking_distance is not None:
                metrics.append(f"接驳步行{walking_distance}公里")
            transfers = mode.get("transfer_count")
            if transfers is not None:
                metrics.append(f"换乘{transfers}次")
            line_names = mode.get("line_names")
            if isinstance(line_names, list):
                normalized_lines = [
                    str(item).strip()
                    for item in line_names
                    if str(item).strip()
                ]
                if normalized_lines:
                    metrics.append(
                        "线路" + "、".join(normalized_lines[:3])
                    )
        modes.append(
            f"{label}{'/'.join(metrics)}"
            if metrics
            else f"{label}指标待确认"
        )
    recommended = {
        "walking": "步行",
        "driving": "打车",
        "public_transit": _transit_label(
            value.get("public_transit")
            if isinstance(value.get("public_transit"), Mapping)
            else {}
        ),
    }.get(str(value.get("recommended_mode")), "")
    reason = str(value.get("recommendation_reason") or "").strip()
    suffix = f"；推荐{recommended}" if recommended else ""
    if reason:
        suffix += f"（{reason}）"
    return f"{origin} → {destination}：" + "，".join(modes) + suffix


def _transit_label(mode: Mapping[str, Any]) -> str:
    return {
        "subway": "地铁",
        "bus": "公交",
        "mixed": "公交+地铁",
        "rail": "轨道交通",
    }.get(str(mode.get("transit_type") or ""), "公共交通")


def _collect_named_items(value: Any) -> list[str]:
    names: list[str] = []

    def visit(item: Any) -> None:
        if len(names) >= 20:
            return
        if isinstance(item, Mapping):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                normalized = name.strip()
                if normalized not in names:
                    names.append(normalized)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return names


def _collect_forecasts(value: Any) -> list[str]:
    forecasts: list[str] = []

    def visit(item: Any) -> None:
        if len(forecasts) >= 10:
            return
        if isinstance(item, Mapping):
            date = item.get("date")
            weather = (
                item.get("dayweather")
                or item.get("day_weather")
                or item.get("weather")
            )
            low = item.get("nighttemp") or item.get("min_temperature_c")
            high = item.get("daytemp") or item.get("max_temperature_c")
            if date and weather:
                temperature = (
                    f"，{low}–{high}℃"
                    if low is not None and high is not None
                    else ""
                )
                text = f"{date} {weather}{temperature}"
                if text not in forecasts:
                    forecasts.append(text)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return forecasts


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(child)
            for key, child in value.items()
            if not any(
                secret_part in str(key).lower()
                for secret_part in _SECRET_PARTS
            )
        }
    if isinstance(value, list):
        return [_safe_value(child) for child in value[:10]]
    return value


def _short_place(value: Any) -> str:
    text = str(value or "未知地点").strip()
    for separator in ("，", ","):
        if separator in text:
            return text.split(separator, 1)[0]
    return text
