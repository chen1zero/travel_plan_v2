"""Structured failures and bounded retries for external tool execution."""

from __future__ import annotations

from dataclasses import dataclass
import random
import socket
import time
from typing import Any, Callable, Optional, TypeVar
from urllib.error import HTTPError, URLError


TOOL_TIMEOUT = "TOOL_TIMEOUT"
NETWORK_ERROR = "NETWORK_ERROR"
TOOL_EXCEPTION = "TOOL_EXCEPTION"
INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
INVALID_TOOL_RESULT = "INVALID_TOOL_RESULT"
MODEL_REFUSAL = "MODEL_REFUSAL"
MODEL_INVALID_OUTPUT = "MODEL_INVALID_OUTPUT"
TASK_TIMEOUT = "TASK_TIMEOUT"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


@dataclass(frozen=True)
class FailureDetails:
    """Sanitized, JSON-safe description of one failed operation."""

    code: str
    retryable: bool
    message: str
    exception_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "retryable": self.retryable,
            "message": self.message,
            "exception_type": self.exception_type,
        }


class ToolExecutionError(RuntimeError):
    """Base class for failures with an explicit recovery policy."""

    error_code = TOOL_EXCEPTION
    retryable = False
    public_message = "工具调用失败"

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[str] = None,
        retryable: Optional[bool] = None,
        public_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        if retryable is not None:
            self.retryable = retryable
        if public_message is not None:
            self.public_message = public_message


class InvalidToolArgumentsError(ToolExecutionError, ValueError):
    error_code = INVALID_ARGUMENTS
    retryable = False
    public_message = "工具参数不符合要求"


class InvalidToolResultError(ToolExecutionError, ValueError):
    error_code = INVALID_TOOL_RESULT
    retryable = True
    public_message = "工具返回的数据格式异常"


class ModelToolRefusalError(ToolExecutionError):
    error_code = MODEL_REFUSAL
    retryable = True
    public_message = "模型未按要求调用必要工具"


class ModelInvalidOutputError(ToolExecutionError):
    error_code = MODEL_INVALID_OUTPUT
    retryable = True
    public_message = "模型返回的数据格式异常"


class TaskCancelledError(ToolExecutionError):
    error_code = TASK_TIMEOUT
    retryable = True
    public_message = "旅行规划已超时取消"


def classify_failure(exc: Exception) -> FailureDetails:
    """Classify an exception without exposing its raw text to end users."""
    explicit_code = getattr(exc, "error_code", None)
    explicit_retryable = getattr(exc, "retryable", None)
    explicit_message = getattr(exc, "public_message", None)
    if isinstance(explicit_code, str):
        return FailureDetails(
            code=explicit_code,
            retryable=bool(explicit_retryable),
            message=(
                str(explicit_message).strip()
                if explicit_message
                else "工具调用失败"
            ),
            exception_type=type(exc).__name__,
        )

    name = type(exc).__name__.lower()
    raw_message = str(exc).lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timeout" in name:
        return FailureDetails(
            TOOL_TIMEOUT,
            True,
            "外部服务调用超时",
            type(exc).__name__,
        )
    if "超时" in raw_message:
        return FailureDetails(
            TOOL_TIMEOUT,
            True,
            "外部服务调用超时",
            type(exc).__name__,
        )
    if isinstance(exc, HTTPError):
        retryable = exc.code == 429 or exc.code >= 500
        return FailureDetails(
            NETWORK_ERROR if retryable else TOOL_EXCEPTION,
            retryable,
            "外部服务暂时不可用" if retryable else "外部服务拒绝了请求",
            type(exc).__name__,
        )
    if isinstance(exc, (URLError, ConnectionError)) or any(
        marker in name
        for marker in ("connection", "ratelimit", "network")
    ):
        return FailureDetails(
            NETWORK_ERROR,
            True,
            "外部服务网络异常",
            type(exc).__name__,
        )
    if any(
        marker in raw_message
        for marker in ("连接已断开", "连接尚未建立", "提前退出", "暂时不可用")
    ):
        return FailureDetails(
            NETWORK_ERROR,
            True,
            "外部服务暂时不可用",
            type(exc).__name__,
        )
    return FailureDetails(
        TOOL_EXCEPTION,
        False,
        "工具执行失败",
        type(exc).__name__,
    )


T = TypeVar("T")
RetryCallback = Callable[[int, int, FailureDetails], None]


def execute_with_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 2,
    base_delay_seconds: float = 0.2,
    on_retry: Optional[RetryCallback] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Execute an operation with one bounded transient-failure policy."""
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于 0")
    if base_delay_seconds < 0:
        raise ValueError("base_delay_seconds 不能小于 0")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            failure = classify_failure(exc)
            if not failure.retryable or attempt >= max_attempts:
                raise
            next_attempt = attempt + 1
            if on_retry is not None:
                on_retry(next_attempt, max_attempts, failure)
            delay = base_delay_seconds * (2 ** (attempt - 1))
            if delay:
                sleep(delay * random.uniform(0.75, 1.25))

    raise AssertionError("retry loop exited unexpectedly")
