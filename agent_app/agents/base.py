"""Reusable LLM agent with a compact tool-calling loop."""

import json
import logging
from threading import Event
from time import perf_counter
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

from agent_app.observability.langsmith import run_traced_tool
from agent_app.shared.logging import preview
from agent_app.tools.dispatcher import ToolHandler, dispatch_tool_call
from agent_app.tools.errors import (
    ModelInvalidOutputError,
    ModelToolRefusalError,
    TaskCancelledError,
)
from agent_app.tools.schema import build_tool_schema
from agent_app.tools.tool_log import (
    summarize_tool_arguments,
    summarize_tool_result,
)


MAX_AGENT_ITERATIONS = 10
MAX_ITERATIONS_RESULT = "任务未完成，已达最大循环次数"

logger = logging.getLogger(__name__)
ChatMessage = Dict[str, Any]
AgentEventCallback = Callable[[str, Mapping[str, Any]], None]


class AgentResponse(Protocol):
    """One LLM decision consumed by the agent loop."""

    finish_reason: str
    content: str
    tool_calls: Sequence[Any]

    def to_assistant_message(self) -> ChatMessage:
        ...


class LLM(Protocol):
    """The LLM interface required by SimpleAgent."""

    def add_tool(self, tool_schema: Mapping[str, Any]) -> None:
        ...

    def complete(
        self, messages: Sequence[ChatMessage]
    ) -> AgentResponse:
        ...


class SimpleAgent:
    """Run an LLM until it returns a final answer or reaches the limit."""

    def __init__(
        self,
        llm: LLM,
        system_prompt: str,
        name: str = "SimpleAgent",
        max_iterations: int = MAX_AGENT_ITERATIONS,
        event_callback: Optional[AgentEventCallback] = None,
        required_tool_names: Optional[Sequence[str]] = None,
        required_output_fields: Optional[Sequence[str]] = None,
        max_recovery_attempts: int = 1,
    ) -> None:
        if not 1 <= max_iterations <= MAX_AGENT_ITERATIONS:
            raise ValueError(
                f"max_iterations 必须在 1 到 {MAX_AGENT_ITERATIONS} 之间"
            )
        self._llm = llm
        self._tool_handlers: Dict[str, ToolHandler] = {}
        self._tool_parameter_schemas: Dict[str, Mapping[str, Any]] = {}
        self._system_prompt = system_prompt.strip()
        self._name = name
        self._max_iterations = max_iterations
        self._event_callback = event_callback
        self._cancelled = Event()
        self._required_tool_names = frozenset(
            name.strip()
            for name in (required_tool_names or ())
            if name.strip()
        )
        self._required_output_fields = tuple(required_output_fields or ())
        if max_recovery_attempts < 0:
            raise ValueError("max_recovery_attempts 不能小于 0")
        self._max_recovery_attempts = max_recovery_attempts

    def set_event_callback(
        self,
        callback: Optional[AgentEventCallback],
    ) -> "SimpleAgent":
        """Attach a sanitized progress callback for API/SSE integrations."""
        self._event_callback = callback
        return self

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        try:
            self._event_callback(event_type, payload)
        except Exception:
            logger.exception(
                "%s 进度回调失败 | event_type=%s",
                self._name,
                event_type,
            )

    def add_tool(
        self,
        handler: ToolHandler,
        *,
        schema: Optional[Mapping[str, Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> "SimpleAgent":
        """Register a callable; infer its schema unless one is provided."""
        if not callable(handler):
            raise ValueError("工具 handler 必须可调用")
        tool_schema = build_tool_schema(
            handler,
            schema=schema,
            name=name,
            description=description,
            parameters=parameters,
        )
        function = tool_schema.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("工具 Schema 缺少 function")
        tool_name = function.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("工具 Schema 缺少 function.name")
        normalized_name = tool_name.strip()
        if normalized_name in self._tool_handlers:
            raise ValueError(f"工具已存在：{normalized_name}")

        self._llm.add_tool(tool_schema)
        self._tool_handlers[normalized_name] = handler
        parameters_value = function.get("parameters")
        if isinstance(parameters_value, Mapping):
            self._tool_parameter_schemas[normalized_name] = dict(
                parameters_value
            )
        logger.info(
            "%s 添加工具 | tool=%s",
            self._name,
            normalized_name,
        )
        return self

    def run(self, task: str) -> str:
        """Execute the reusable native-finish-reason agent loop."""
        self._ensure_active()
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("任务内容不能为空")

        messages: List[ChatMessage] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": normalized_task},
        ]
        iteration = 0
        successful_tool_calls: set[str] = set()
        tool_recovery_attempts = 0
        output_recovery_attempts = 0

        logger.info(
            "%s 任务开始 | max_iterations=%d | task=%s",
            self._name,
            self._max_iterations,
            preview(normalized_task),
        )

        while True:
            self._ensure_active()
            if iteration >= self._max_iterations:
                logger.warning(
                    "%s 达到最大循环次数 | iterations=%d | result=%s",
                    self._name,
                    iteration,
                    MAX_ITERATIONS_RESULT,
                )
                raise ModelInvalidOutputError(MAX_ITERATIONS_RESULT)

            iteration += 1
            logger.info(
                "%s 循环开始 | iteration=%d/%d | messages=%d",
                self._name,
                iteration,
                self._max_iterations,
                len(messages),
            )

            response = self._llm.complete(messages)
            self._ensure_active()
            self._emit_event(
                "agent.iteration",
                agent=self._name,
                iteration=iteration,
                finish_reason=response.finish_reason,
                message=(
                    f"{self._name} 正在进行第 {iteration} 轮决策"
                ),
            )
            logger.info(
                "%s LLM 决策 | iteration=%d/%d | finish_reason=%s",
                self._name,
                iteration,
                self._max_iterations,
                response.finish_reason,
            )

            if response.finish_reason == "tool_calls":
                if not response.tool_calls:
                    if tool_recovery_attempts >= self._max_recovery_attempts:
                        raise ModelToolRefusalError(
                            "LLM 声明调用工具但未返回任何 tool_calls"
                        )
                    tool_recovery_attempts += 1
                    self._append_recovery_message(
                        messages,
                        response.content,
                        "必须调用任务要求的工具后才能作答。"
                        "请立即调用工具，不要直接给出结论。",
                    )
                    self._emit_event(
                        "agent.recovering",
                        agent=self._name,
                        iteration=iteration,
                        error_code="MODEL_REFUSAL",
                        retryable=True,
                        message=f"{self._name} 未返回有效工具调用，正在纠正",
                    )
                    continue
                messages.append(response.to_assistant_message())
                for tool_call in response.tool_calls:
                    argument_summary = summarize_tool_arguments(
                        tool_call.name,
                        tool_call.arguments,
                    )
                    self._emit_event(
                        "tool.started",
                        agent=self._name,
                        iteration=iteration,
                        tool=tool_call.name,
                        tool_call_id=tool_call.id,
                        message=(
                            f"{self._name} 调用 {tool_call.name}："
                            f"{argument_summary}"
                        ),
                    )
                    logger.info(
                        "%s 执行工具 | iteration=%d | tool=%s "
                        "| tool_call_id=%s | arguments=%s",
                        self._name,
                        iteration,
                        tool_call.name,
                        tool_call.id,
                        preview(tool_call.arguments, 500),
                    )
                    started_at = perf_counter()

                    def emit_retry(next_attempt, max_attempts, failure):
                        self._emit_event(
                            "tool.retrying",
                            agent=self._name,
                            iteration=iteration,
                            tool=tool_call.name,
                            tool_call_id=tool_call.id,
                            attempt=next_attempt,
                            max_attempts=max_attempts,
                            error_code=failure.code,
                            retryable=failure.retryable,
                            message=(
                                f"{tool_call.name} 暂时失败，"
                                f"正在进行第 {next_attempt}/{max_attempts} 次尝试"
                            ),
                        )

                    result = run_traced_tool(
                        tool_call.name,
                        tool_call.arguments,
                        lambda: dispatch_tool_call(
                            tool_call.name,
                            tool_call.arguments,
                            handlers=self._tool_handlers,
                            schemas=self._tool_parameter_schemas,
                            on_retry=emit_retry,
                        ),
                    )
                    self._ensure_active()
                    elapsed_ms = round(
                        (perf_counter() - started_at) * 1000
                    )
                    logger.info(
                        "%s 工具返回 | iteration=%d | tool=%s "
                        "| tool_call_id=%s | result=%s",
                        self._name,
                        iteration,
                        tool_call.name,
                        tool_call.id,
                        preview(result, 1000),
                    )
                    result_summary = summarize_tool_result(
                        tool_call.name,
                        result,
                    )
                    result_payload = self._tool_result_payload(result)
                    succeeded = bool(result_payload.get("ok"))
                    if succeeded:
                        successful_tool_calls.add(tool_call.name)
                    error_details = result_payload.get("error_details")
                    event_context: Dict[str, Any] = {}
                    if isinstance(error_details, Mapping):
                        event_context = {
                            "error_code": error_details.get("code"),
                            "retryable": error_details.get("retryable"),
                            "exception_type": error_details.get(
                                "exception_type"
                            ),
                        }
                    self._emit_event(
                        "tool.completed" if succeeded else "tool.failed",
                        agent=self._name,
                        iteration=iteration,
                        tool=tool_call.name,
                        tool_call_id=tool_call.id,
                        duration_ms=elapsed_ms,
                        **event_context,
                        message=(
                            f"{tool_call.name} 返回："
                            f"{result_summary}"
                        ),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
                continue

            if response.finish_reason == "stop":
                final_answer = response.content.strip()
                missing_tools = sorted(
                    self._required_tool_names - successful_tool_calls
                )
                if missing_tools:
                    if tool_recovery_attempts >= self._max_recovery_attempts:
                        raise ModelToolRefusalError(
                            "模型未成功调用必要工具："
                            + "、".join(missing_tools)
                        )
                    tool_recovery_attempts += 1
                    self._append_recovery_message(
                        messages,
                        final_answer,
                        "当前结论不能被接受。必须先成功调用以下工具："
                        + "、".join(missing_tools),
                    )
                    self._emit_event(
                        "agent.recovering",
                        agent=self._name,
                        iteration=iteration,
                        error_code="MODEL_REFUSAL",
                        retryable=True,
                        message=f"{self._name} 未调用必要工具，正在纠正",
                    )
                    continue

                output_error = self._output_validation_error(final_answer)
                if output_error is not None:
                    if output_recovery_attempts >= self._max_recovery_attempts:
                        raise ModelInvalidOutputError(output_error)
                    output_recovery_attempts += 1
                    self._append_recovery_message(
                        messages,
                        final_answer,
                        "输出未通过结构校验："
                        f"{output_error}。请重新输出完整 JSON 对象。",
                    )
                    self._emit_event(
                        "agent.recovering",
                        agent=self._name,
                        iteration=iteration,
                        error_code="MODEL_INVALID_OUTPUT",
                        retryable=True,
                        message=f"{self._name} 输出格式异常，正在纠正",
                    )
                    continue
                logger.info(
                    "%s 任务完成 | iteration=%d | result=%s",
                    self._name,
                    iteration,
                    preview(final_answer, 1000),
                )
                return final_answer

            logger.error(
                "%s 收到未知 finish_reason | iteration=%d | "
                "finish_reason=%s",
                self._name,
                iteration,
                response.finish_reason,
            )
            raise ModelInvalidOutputError(
                "LLM 返回未知 finish_reason："
                f"{response.finish_reason}"
            )

    def cancel(self) -> None:
        """Stop the loop after the currently blocking operation returns."""
        self._cancelled.set()

    def _ensure_active(self) -> None:
        if self._cancelled.is_set():
            raise TaskCancelledError(f"{self._name} 已取消")

    @staticmethod
    def _append_recovery_message(
        messages: List[ChatMessage],
        assistant_content: str,
        instruction: str,
    ) -> None:
        if assistant_content.strip():
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content.strip(),
                }
            )
        messages.append({"role": "user", "content": instruction})

    @staticmethod
    def _tool_result_payload(result: str) -> Mapping[str, Any]:
        try:
            value = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, Mapping) else {}

    def _output_validation_error(self, value: str) -> Optional[str]:
        if not self._required_output_fields:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return "结果不是合法 JSON"
        if not isinstance(parsed, Mapping):
            return "结果顶层必须是 JSON 对象"
        missing = [
            field
            for field in self._required_output_fields
            if field not in parsed
        ]
        if missing:
            return "缺少字段：" + "、".join(missing)
        return None
