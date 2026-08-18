"""LangSmith helpers that become no-ops when tracing is disabled."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Mapping, TypeVar

import langsmith as ls
from langsmith.wrappers import wrap_openai


T = TypeVar("T")
_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_langsmith_tracing_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the official LangSmith tracing switch is enabled."""
    source = os.environ if environ is None else environ
    return source.get("LANGSMITH_TRACING", "").strip().lower() in (
        _TRUE_VALUES
    )


def langsmith_status(
    environ: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """Expose safe configuration state without returning credentials."""
    source = os.environ if environ is None else environ
    enabled = is_langsmith_tracing_enabled(source)
    return {
        "provider": "langsmith",
        "enabled": enabled,
        "ready": enabled
        and bool(source.get("LANGSMITH_API_KEY", "").strip()),
        "project": source.get("LANGSMITH_PROJECT", "default").strip()
        or "default",
        "endpoint": source.get(
            "LANGSMITH_ENDPOINT",
            "https://api.smith.langchain.com",
        ).strip(),
        "workspace_configured": bool(
            source.get("LANGSMITH_WORKSPACE_ID", "").strip()
        ),
    }


def wrap_openai_client(client: T, *, model_id: str) -> T:
    """Trace OpenAI-compatible calls while preserving the client type."""
    if not is_langsmith_tracing_enabled():
        return client
    return wrap_openai(
        client,
        chat_name=f"LLM · {model_id}",
        completions_name=f"LLM · {model_id}",
    )


def run_traced_tool(
    tool_name: str,
    arguments: str,
    operation: Callable[[], str],
) -> str:
    """Record one native tool call as a child of the current graph node."""
    if not is_langsmith_tracing_enabled():
        return operation()
    inputs: Dict[str, Any]
    try:
        parsed_arguments = json.loads(arguments)
        inputs = {
            "arguments": (
                parsed_arguments
                if isinstance(parsed_arguments, dict)
                else arguments
            )
        }
    except json.JSONDecodeError:
        inputs = {"arguments": arguments}

    with ls.trace(
        name=f"Tool · {tool_name}",
        run_type="tool",
        inputs=inputs,
        metadata={"tool_name": tool_name},
        tags=["travel-planning", "tool"],
    ) as run:
        result = operation()
        try:
            parsed_result = json.loads(result)
            output: Any = parsed_result
        except json.JSONDecodeError:
            output = result
        if isinstance(output, Mapping) and output.get("ok") is False:
            details = output.get("error_details")
            error_message = (
                str(details.get("message") or "工具执行失败")
                if isinstance(details, Mapping)
                else "工具执行失败"
            )
            run.end(outputs={"result": output}, error=error_message)
        else:
            run.end(outputs={"result": output})
        return result
