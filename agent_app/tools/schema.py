"""Build OpenAI function-tool schemas from callables or MCP definitions."""

from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
import inspect
from typing import (
    Any,
    Dict,
    Literal,
    Mapping,
    Optional,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from agent_app.tools.dispatcher import ToolHandler


def _annotation_schema(annotation: Any) -> Dict[str, Any]:
    if annotation in (inspect.Parameter.empty, Any):
        return {}

    json_type = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
        dict: "object",
        list: "array",
    }.get(annotation)
    if json_type is not None:
        return {"type": json_type}

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Union:
        return {
            "anyOf": [
                _annotation_schema(argument)
                for argument in arguments
            ]
        }
    if origin is Literal:
        values = list(arguments)
        schema: Dict[str, Any] = {"enum": values}
        if values:
            schema.update(_annotation_schema(type(values[0])))
        return schema
    if origin in (list, tuple, set, ABCSequence):
        item_schema = (
            _annotation_schema(arguments[0])
            if arguments
            else {}
        )
        return {"type": "array", "items": item_schema}
    if origin in (dict, ABCMapping):
        schema = {"type": "object"}
        if len(arguments) == 2:
            schema["additionalProperties"] = _annotation_schema(
                arguments[1]
            )
        return schema
    return {}


def _schema_from_handler(
    handler: ToolHandler,
    name: Optional[str],
    description: Optional[str],
    parameters: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:
    tool_name_value = name or getattr(handler, "__name__", "")
    if (
        not isinstance(tool_name_value, str)
        or not tool_name_value.strip()
    ):
        raise ValueError(
            "无法从 handler 推断工具名称，请传入 name 或 schema"
        )

    if parameters is None:
        try:
            handler_signature = inspect.signature(handler)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "无法读取 handler 签名，请传入 parameters 或 schema"
            ) from exc
        try:
            type_hints = get_type_hints(handler)
        except (NameError, TypeError):
            type_hints = {}

        properties: Dict[str, Any] = {}
        required = []
        additional_properties = False
        for parameter in handler_signature.parameters.values():
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                raise ValueError(
                    "工具 handler 不能包含仅限位置参数"
                )
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                raise ValueError("工具 handler 不能包含 *args")
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                additional_properties = True
                continue

            annotation = type_hints.get(
                parameter.name,
                parameter.annotation,
            )
            property_schema = _annotation_schema(annotation)
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
            elif parameter.default is not None and isinstance(
                parameter.default,
                (str, int, float, bool, list, dict),
            ):
                property_schema["default"] = parameter.default
            properties[parameter.name] = property_schema

        parameter_schema: Mapping[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": additional_properties,
        }
    else:
        parameter_schema = dict(parameters)

    function: Dict[str, Any] = {
        "name": tool_name_value.strip(),
        "parameters": parameter_schema,
    }
    tool_description = (
        description
        if description is not None
        else inspect.getdoc(handler)
    )
    if (
        isinstance(tool_description, str)
        and tool_description.strip()
    ):
        function["description"] = tool_description.strip()
    return {"type": "function", "function": function}


def build_tool_schema(
    handler: ToolHandler,
    *,
    schema: Optional[Mapping[str, Any]] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    """Return an OpenAI tool from a callable, MCP tool, or schema."""
    if schema is not None and not isinstance(schema, Mapping):
        raise ValueError("schema 必须是对象")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise ValueError("parameters 必须是对象")
    if schema is None:
        return _schema_from_handler(
            handler,
            name,
            description,
            parameters,
        )

    function_value = schema.get("function")
    if isinstance(function_value, Mapping):
        function = dict(function_value)
    elif isinstance(schema.get("inputSchema"), Mapping):
        function = {
            "name": schema.get("name"),
            "description": schema.get("description"),
            "parameters": dict(schema["inputSchema"]),
        }
    elif isinstance(schema.get("parameters"), Mapping):
        function = dict(schema)
    else:
        raise ValueError(
            "工具 schema 必须是 OpenAI function tool、"
            "MCP tool definition 或 function schema"
        )

    if name is not None:
        function["name"] = name
    if description is not None:
        function["description"] = description
    if parameters is not None:
        function["parameters"] = dict(parameters)
    if not function.get("description"):
        function.pop("description", None)
    return {"type": "function", "function": function}
