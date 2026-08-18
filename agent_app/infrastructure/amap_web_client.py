"""Small server-side client for Amap road-aligned route geometry."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agent_app.tools.errors import (
    INVALID_TOOL_RESULT,
    NETWORK_ERROR,
    TOOL_EXCEPTION,
    TOOL_TIMEOUT,
    execute_with_retry,
)


AMAP_WEB_SERVICE_BASE_URL = "https://restapi.amap.com"


class AmapWebServiceError(RuntimeError):
    """Raised for sanitized Amap Web Service failures."""

    error_code = TOOL_EXCEPTION
    retryable = False
    public_message = "高德路线服务调用失败"

    def __init__(
        self,
        message: str,
        *,
        error_code: str = TOOL_EXCEPTION,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class AmapWebServiceClient:
    """Fetch route shapes without exposing the Web Service key."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds

    def route_polyline(
        self,
        mode: str,
        *,
        origin: str,
        destination: str,
        city: str,
        destination_city: str,
    ) -> list[list[float]]:
        if not self._api_key:
            raise AmapWebServiceError("未配置高德 Web 服务 Key")
        normalized_mode = mode.strip()
        if normalized_mode == "walking":
            path = "/v3/direction/walking"
        elif normalized_mode == "driving":
            path = "/v3/direction/driving"
        elif normalized_mode == "public_transit":
            path = "/v3/direction/transit/integrated"
        else:
            raise AmapWebServiceError("不支持的高德路线方式")

        parameters = {
            "origin": _coordinate_text(origin),
            "destination": _coordinate_text(destination),
            "output": "json",
            "key": self._api_key,
        }
        if normalized_mode == "driving":
            parameters["strategy"] = "0"
        elif normalized_mode == "public_transit":
            parameters.update(
                {
                    "city": city.strip(),
                    "cityd": destination_city.strip(),
                    "strategy": "0",
                    "nightflag": "0",
                }
            )

        payload = self._get_json(path, parameters)
        route = payload.get("route")
        if not isinstance(route, Mapping):
            raise AmapWebServiceError("高德路线响应缺少 route")
        options_key = (
            "transits"
            if normalized_mode == "public_transit"
            else "paths"
        )
        options = route.get(options_key)
        if not isinstance(options, list) or not options:
            raise AmapWebServiceError("高德路线响应没有可用方案")
        first = options[0]
        if not isinstance(first, Mapping):
            raise AmapWebServiceError("高德路线方案格式错误")
        points = _route_polyline(first, normalized_mode)
        if len(points) < 2:
            raise AmapWebServiceError("高德路线响应缺少道路折线")
        return points

    def _get_json(
        self,
        path: str,
        parameters: Mapping[str, str],
    ) -> Mapping[str, Any]:
        url = f"{AMAP_WEB_SERVICE_BASE_URL}{path}?{urlencode(parameters)}"
        request = Request(
            url,
            headers={"User-Agent": "travel-plan-v2/1.0"},
        )
        def request_once() -> Any:
            try:
                with urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                raise AmapWebServiceError(
                    "高德路线服务请求失败",
                    error_code=(NETWORK_ERROR if retryable else TOOL_EXCEPTION),
                    retryable=retryable,
                ) from None
            except TimeoutError:
                raise AmapWebServiceError(
                    "高德路线服务请求超时",
                    error_code=TOOL_TIMEOUT,
                    retryable=True,
                ) from None
            except URLError:
                raise AmapWebServiceError(
                    "高德路线服务网络异常",
                    error_code=NETWORK_ERROR,
                    retryable=True,
                ) from None
            except json.JSONDecodeError:
                raise AmapWebServiceError(
                    "高德路线服务返回格式错误",
                    error_code=INVALID_TOOL_RESULT,
                    retryable=True,
                ) from None

        payload = execute_with_retry(request_once, max_attempts=2)
        if not isinstance(payload, Mapping):
            raise AmapWebServiceError("高德路线服务返回格式错误")
        if str(payload.get("status")) != "1":
            info = str(payload.get("info") or "调用失败").strip()
            raise AmapWebServiceError(f"高德路线服务：{info}")
        return payload


def _coordinate_text(value: str) -> str:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise AmapWebServiceError("高德路线坐标格式错误")
    try:
        longitude, latitude = (float(part) for part in parts)
    except ValueError as exc:
        raise AmapWebServiceError("高德路线坐标格式错误") from exc
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise AmapWebServiceError("高德路线坐标超出范围")
    return f"{longitude:.6f},{latitude:.6f}"


def _polyline_points(value: Any) -> list[list[float]]:
    raw_points: Sequence[Any]
    if isinstance(value, str):
        raw_points = value.split(";")
    elif isinstance(value, list):
        raw_points = value
    else:
        return []
    points: list[list[float]] = []
    for raw_point in raw_points:
        if isinstance(raw_point, str):
            parts = raw_point.split(",")
        elif isinstance(raw_point, Sequence):
            parts = list(raw_point)
        else:
            continue
        if len(parts) != 2:
            continue
        try:
            point = [round(float(parts[0]), 6), round(float(parts[1]), 6)]
        except (TypeError, ValueError):
            continue
        if not points or point != points[-1]:
            points.append(point)
        if len(points) >= 1600:
            break
    return points


def _append_polyline(points: list[list[float]], value: Any) -> None:
    for point in _polyline_points(value):
        if not points or point != points[-1]:
            points.append(point)


def _append_steps(points: list[list[float]], value: Any) -> None:
    if not isinstance(value, list):
        return
    for step in value:
        if isinstance(step, Mapping):
            _append_polyline(points, step.get("polyline"))


def _route_polyline(
    option: Mapping[str, Any],
    mode: str,
) -> list[list[float]]:
    points: list[list[float]] = []
    if mode != "public_transit":
        _append_steps(points, option.get("steps"))
        _append_polyline(points, option.get("polyline"))
        return points

    segments = option.get("segments")
    if not isinstance(segments, list):
        return points
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        walking = segment.get("walking")
        if isinstance(walking, Mapping):
            _append_steps(points, walking.get("steps"))
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
