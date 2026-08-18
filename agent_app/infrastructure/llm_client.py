"""OpenAI-compatible LLM client."""

from dataclasses import dataclass
import logging
from time import perf_counter
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from openai import OpenAI

from agent_app.observability.langsmith import wrap_openai_client
from agent_app.shared.config import Settings
from agent_app.shared.logging import preview


ChatMessage = Dict[str, Any]
logger = logging.getLogger(__name__)


class EmptyLLMResponseError(RuntimeError):
    """Raised when an LLM response has no usable choice or content."""


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model."""

    id: str
    name: str
    arguments: str
    type: str = "function"

    def to_message_value(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


@dataclass(frozen=True)
class LLMResponse:
    """One model decision consumed by the ReAct loop."""

    finish_reason: str
    content: str = ""
    tool_calls: Tuple[ToolCall, ...] = ()

    def to_assistant_message(self) -> ChatMessage:
        message: ChatMessage = {
            "role": "assistant",
            "content": self.content or None,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                tool_call.to_message_value()
                for tool_call in self.tool_calls
            ]
        return message


class OpenAICompatibleLLM:
    """Make one Chat Completions request and expose its finish reason."""

    def __init__(
        self,
        settings: Settings,
        client: Optional[Any] = None,
        tool_schemas: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        self._model_id = settings.model_id
        self._request_count = 0
        self._client = client
        if self._client is None:
            native_client = OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.timeout_seconds,
                max_retries=settings.max_retries,
            )
            self._client = wrap_openai_client(
                native_client,
                model_id=settings.model_id,
            )
        self._tool_schemas = []
        for tool_schema in tool_schemas or ():
            self.add_tool(tool_schema)

        logger.info(
            "LLM 客户端初始化完成 | model=%s | base_url=%s "
            "| timeout=%.1fs | max_retries=%d | tools=%d",
            settings.model_id,
            settings.base_url,
            settings.timeout_seconds,
            settings.max_retries,
            len(self._tool_schemas),
        )

    def add_tool(self, tool_schema: Mapping[str, Any]) -> None:
        """Add one OpenAI function-tool schema to future requests."""
        function = tool_schema.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("工具 Schema 缺少 function")
        tool_name = function.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("工具 Schema 缺少 function.name")
        normalized_name = tool_name.strip()
        existing_names = {
            tool["function"]["name"]
            for tool in self._tool_schemas
        }
        if normalized_name in existing_names:
            raise ValueError(f"工具已存在：{normalized_name}")
        self._tool_schemas.append(dict(tool_schema))

    def complete(
        self, messages: Sequence[ChatMessage]
    ) -> LLMResponse:
        """Return exactly one model decision."""
        self._request_count += 1
        request_number = self._request_count
        started_at = perf_counter()

        logger.info(
            "LLM 请求开始 | request=%d | model=%s | messages=%d "
            "| tools=%d",
            request_number,
            self._model_id,
            len(messages),
            len(self._tool_schemas),
        )
        for index, message in enumerate(messages, start=1):
            logger.debug(
                "LLM 请求消息 | request=%d | index=%d | role=%s "
                "| content=%s",
                request_number,
                index,
                message.get("role", "unknown"),
                preview(message.get("content", ""), 500),
            )

        request_arguments: Dict[str, Any] = {
            "model": self._model_id,
            "messages": list(messages),
        }
        if self._tool_schemas:
            request_arguments.update(
                {
                    "tools": self._tool_schemas,
                    "tool_choice": "auto",
                }
            )

        try:
            response = self._client.chat.completions.create(
                **request_arguments
            )
        except Exception:
            logger.exception(
                "LLM 请求失败 | request=%d | elapsed=%.2fs",
                request_number,
                perf_counter() - started_at,
            )
            raise

        if not response.choices:
            raise EmptyLLMResponseError("LLM 响应中没有 choices")

        choice = response.choices[0]
        response_message = choice.message
        tool_calls = tuple(
            ToolCall(
                id=tool_call.id,
                type=tool_call.type,
                name=tool_call.function.name,
                arguments=tool_call.function.arguments,
            )
            for tool_call in (response_message.tool_calls or [])
        )
        content = response_message.content or ""
        finish_reason = choice.finish_reason or "unknown"

        if finish_reason == "stop" and not content.strip():
            raise EmptyLLMResponseError("LLM 最终响应内容为空")

        logger.info(
            "LLM 请求完成 | request=%d | elapsed=%.2fs "
            "| provider_request_id=%s | finish_reason=%s "
            "| tool_calls=%d | output=%s",
            request_number,
            perf_counter() - started_at,
            getattr(response, "_request_id", None) or "-",
            finish_reason,
            len(tool_calls),
            preview(content),
        )
        return LLMResponse(
            finish_reason=finish_reason,
            content=content,
            tool_calls=tool_calls,
        )
