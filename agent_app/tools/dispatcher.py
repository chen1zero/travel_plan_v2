"""Dispatch JSON tool calls requested by a model."""

import json
from typing import Any, Callable, Mapping, Optional

from agent_app.tools.errors import (
    FailureDetails,
    INVALID_ARGUMENTS,
    InvalidToolArgumentsError,
    InvalidToolResultError,
    classify_failure,
    execute_with_retry,
)


ToolHandler = Callable[..., Any]


def dispatch_tool_call(
    tool_name: str,
    arguments: str,
    handlers: Mapping[str, ToolHandler],
    *,
    schemas: Optional[Mapping[str, Mapping[str, Any]]] = None,
    max_attempts: int = 2,
    retry_backoff_seconds: float = 0.2,
    on_retry: Optional[Callable[[int, int, FailureDetails], None]] = None,
) -> str:
    """Execute one tool call and serialize its result for the LLM."""
    try:
        try:
            parsed_arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidToolArgumentsError(
                "工具参数不是合法 JSON"
            ) from exc
        if not isinstance(parsed_arguments, dict):
            raise InvalidToolArgumentsError("工具参数必须是 JSON 对象")

        handler = handlers.get(tool_name)
        if handler is None:
            raise InvalidToolArgumentsError(f"未知工具：{tool_name}")

        schema = (schemas or {}).get(tool_name)
        if schema is not None:
            _validate_value(parsed_arguments, schema, path="参数")

        result = execute_with_retry(
            lambda: handler(**parsed_arguments),
            max_attempts=max_attempts,
            base_delay_seconds=retry_backoff_seconds,
            on_retry=on_retry,
        )
        try:
            return json.dumps(
                {"ok": True, "result": result},
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidToolResultError(
                "工具结果无法序列化为 JSON"
            ) from exc
    except Exception as exc:
        failure = classify_failure(exc)
        safe_error = (
            str(exc)
            if failure.code == INVALID_ARGUMENTS
            else failure.message
        )
        return json.dumps(
            {
                "ok": False,
                "tool": tool_name,
                "error": safe_error,
                "error_details": failure.as_dict(),
            },
            ensure_ascii=False,
        )


def _validate_value(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str,
) -> None:
    """Validate the JSON-Schema subset used by this project's tools."""
    if "anyOf" in schema:
        choices = schema.get("anyOf")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                try:
                    _validate_value(value, choice, path=path)
                    return
                except InvalidToolArgumentsError:
                    continue
            raise InvalidToolArgumentsError(f"{path}类型不符合要求")

    expected_type = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected_type, True)
    if not valid:
        raise InvalidToolArgumentsError(
            f"{path}必须是 {_type_label(expected_type)}"
        )

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise InvalidToolArgumentsError(f"{path}不在允许范围内")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [
                str(name)
                for name in required
                if isinstance(name, str) and name not in value
            ]
            if missing:
                raise InvalidToolArgumentsError(
                    f"{path}缺少必填字段：{'、'.join(missing)}"
                )
        properties = schema.get("properties", {})
        property_map = properties if isinstance(properties, Mapping) else {}
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(property_map))
            if extras:
                raise InvalidToolArgumentsError(
                    f"{path}包含未定义字段：{'、'.join(extras)}"
                )
        for key, child in value.items():
            child_schema = property_map.get(key)
            if isinstance(child_schema, Mapping):
                _validate_value(child, child_schema, path=f"{path}.{key}")
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, child in enumerate(value):
                _validate_value(child, item_schema, path=f"{path}[{index}]")


def _type_label(value: Any) -> str:
    return {
        "object": "JSON 对象",
        "array": "数组",
        "string": "字符串",
        "integer": "整数",
        "number": "数字",
        "boolean": "布尔值",
        "null": "null",
    }.get(value, "正确类型")
