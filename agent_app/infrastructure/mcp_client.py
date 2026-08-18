"""Small synchronous MCP client for stdio-based servers."""

from collections import deque
from dataclasses import dataclass
import importlib.util
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
from threading import RLock, Thread
from time import monotonic
from typing import Any, Deque, List, Mapping, Optional, Sequence

from agent_app.shared.logging import preview


MCP_PROTOCOL_VERSION = "2025-03-26"
_STREAM_CLOSED = object()
logger = logging.getLogger(__name__)


class MCPClientError(RuntimeError):
    """Raised when an MCP server cannot be started or queried."""


class MCPInvalidResponseError(MCPClientError):
    """Raised when an MCP server returns a malformed tool response."""

    error_code = "INVALID_TOOL_RESULT"
    retryable = True
    public_message = "工具返回的数据格式异常"


@dataclass(frozen=True)
class _InvalidJSONLine:
    preview: str


def mcp_tool_to_openai_schema(
    tool: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Convert one MCP tool definition into an OpenAI function tool."""
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MCPClientError("MCP 工具定义缺少 name")
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, Mapping):
        raise MCPClientError(f"MCP 工具 {name} 缺少 inputSchema")

    function = {
        "name": name.strip(),
        "parameters": dict(input_schema),
    }
    description = tool.get("description")
    if isinstance(description, str) and description.strip():
        function["description"] = description.strip()
    return {
        "type": "function",
        "function": function,
    }


class StdioMCPClient:
    """Keep one stdio MCP server process alive across tool calls."""

    def __init__(
        self,
        command: Sequence[str],
        env: Optional[Mapping[str, str]] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        normalized_command = tuple(
            part.strip() for part in command if part.strip()
        )
        if not normalized_command:
            raise ValueError("MCP command 不能为空")
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout_seconds 必须大于 0")

        self._command = self._resolve_command(normalized_command)
        self._env = dict(env or {})
        self._timeout_seconds = timeout_seconds
        self._process: Optional[subprocess.Popen] = None
        self._messages: Optional[Queue] = None
        self._stderr_lines: Deque[str] = deque(maxlen=20)
        self._request_id = 0
        self._lock = RLock()

    @staticmethod
    def _resolve_command(command: Sequence[str]) -> Sequence[str]:
        """Resolve uvx without accessing PyPI during every MCP call."""
        normalized_command = tuple(command)
        if normalized_command[0] != "uvx":
            return normalized_command

        uvx_arguments = normalized_command[1:]
        uvx_on_path = shutil.which("uvx")
        if uvx_on_path:
            return (uvx_on_path, "--offline", *uvx_arguments)

        if importlib.util.find_spec("uv") is not None:
            return (
                sys.executable,
                "-m",
                "uv",
                "tool",
                "run",
                "--offline",
                *uvx_arguments,
            )

        executable_name = "uvx.exe" if os.name == "nt" else "uvx"
        project_root = Path(__file__).resolve().parents[2]
        candidates = (
            Path(sys.executable).resolve().parent / executable_name,
            project_root
            / ".venv"
            / ("Scripts" if os.name == "nt" else "bin")
            / executable_name,
        )
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return (str(candidate), "--offline", *uvx_arguments)

        uv_on_path = shutil.which("uv")
        if uv_on_path:
            return (
                uv_on_path,
                "tool",
                "run",
                "--offline",
                *uvx_arguments,
            )
        return normalized_command

    @property
    def is_connected(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def connect(self) -> "StdioMCPClient":
        """Start and initialize the MCP process once."""
        with self._lock:
            if self.is_connected:
                return self

            self._close_unlocked()
            process = self._start_server()
            messages: Queue = Queue()
            stderr_lines: Deque[str] = deque(maxlen=20)
            self._process = process
            self._messages = messages
            self._stderr_lines = stderr_lines
            self._request_id = 0
            self._start_reader_threads(
                process,
                messages,
                stderr_lines,
            )

            try:
                initialization = self._request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "travel-plan-agent",
                            "version": "1.0.0",
                        },
                    },
                    ensure_connected=False,
                )
                self._send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                )
            except Exception:
                self._close_unlocked()
                raise

            logger.info(
                "MCP 连接建立 | command=%s | server=%s | protocol=%s",
                " ".join(self._command),
                initialization.get("serverInfo", {}).get(
                    "name", "unknown"
                )
                if isinstance(initialization, dict)
                else "unknown",
                initialization.get("protocolVersion", "unknown")
                if isinstance(initialization, dict)
                else "unknown",
            )
            return self

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """Return every tool advertised by the connected MCP server."""
        with self._lock:
            tools: List[Mapping[str, Any]] = []
            cursor: Optional[str] = None
            while True:
                params = {"cursor": cursor} if cursor else {}
                result = self._request("tools/list", params)
                if not isinstance(result, Mapping):
                    raise MCPClientError("MCP tools/list 返回格式错误")
                page = result.get("tools")
                if not isinstance(page, list):
                    raise MCPClientError("MCP tools/list 缺少 tools")
                for tool in page:
                    if not isinstance(tool, Mapping):
                        raise MCPClientError("MCP 工具定义格式错误")
                    tools.append(dict(tool))
                next_cursor = result.get("nextCursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                cursor = next_cursor

            logger.info(
                "MCP 工具列表 | count=%d | tools=%s",
                len(tools),
                ", ".join(
                    str(tool.get("name", "<unnamed>"))
                    for tool in tools
                ),
            )
            return tuple(tools)

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Call one tool through the persistent MCP connection."""
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            raise ValueError("MCP tool_name 不能为空")

        with self._lock:
            result = self._request(
                "tools/call",
                {
                    "name": normalized_tool_name,
                    "arguments": dict(arguments),
                },
            )
            normalized_result = self._normalize_tool_result(result)
            logger.info(
                "MCP 工具返回 | tool=%s | result=%s",
                normalized_tool_name,
                preview(normalized_result, 1000),
            )
            return normalized_result

    def close(self) -> None:
        """Stop the persistent MCP process."""
        with self._lock:
            self._close_unlocked()

    def __enter__(self) -> "StdioMCPClient":
        return self.connect()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        ensure_connected: bool = True,
    ) -> Any:
        if ensure_connected:
            self.connect()
        process = self._process
        messages = self._messages
        if process is None or messages is None:
            raise MCPClientError("MCP 连接尚未建立")

        self._request_id += 1
        request_id = self._request_id
        try:
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                },
            )
            return self._receive_response(
                messages,
                request_id=request_id,
                stderr_lines=self._stderr_lines,
            )
        except MCPClientError:
            self._close_unlocked()
            raise

    def _close_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._messages = None
        self._stderr_lines = deque(maxlen=20)
        self._request_id = 0
        if process is not None:
            self._stop_server(process)
            logger.info("MCP 连接关闭")

    def _start_server(self) -> subprocess.Popen:
        process_env = os.environ.copy()
        process_env.update(self._env)
        try:
            return subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=process_env,
            )
        except FileNotFoundError as exc:
            raise MCPClientError(
                f"找不到 MCP 启动命令：{self._command[0]}；"
                "请先运行 python -m pip install -r requirements.txt，"
                "或配置 AMAP_MCP_COMMAND"
            ) from exc
        except OSError as exc:
            raise MCPClientError(
                f"无法启动 MCP Server：{exc}"
            ) from exc

    @staticmethod
    def _start_reader_threads(
        process: subprocess.Popen,
        messages: Queue,
        stderr_lines: Deque[str],
    ) -> None:
        def read_stdout() -> None:
            if process.stdout is None:
                messages.put(_STREAM_CLOSED)
                return
            for line in process.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    messages.put(json.loads(stripped))
                except json.JSONDecodeError:
                    messages.put(_InvalidJSONLine(preview(stripped)))
                    logger.debug(
                        "忽略 MCP 非 JSON 输出 | output=%s",
                        preview(stripped),
                    )
            messages.put(_STREAM_CLOSED)

        def read_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                stripped = line.strip()
                if stripped:
                    stderr_lines.append(stripped)
                    logger.debug(
                        "MCP Server 日志 | output=%s",
                        preview(stripped),
                    )

        Thread(target=read_stdout, daemon=True).start()
        Thread(target=read_stderr, daemon=True).start()

    @staticmethod
    def _send(
        process: subprocess.Popen,
        message: Mapping[str, Any],
    ) -> None:
        if process.stdin is None:
            raise MCPClientError("MCP Server stdin 不可用")
        try:
            process.stdin.write(
                json.dumps(message, ensure_ascii=False) + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPClientError("MCP Server 连接已断开") from exc

    def _receive_response(
        self,
        messages: Queue,
        request_id: int,
        stderr_lines: Deque[str],
    ) -> Any:
        deadline = monotonic() + self._timeout_seconds
        invalid_json_line: Optional[_InvalidJSONLine] = None
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                if invalid_json_line is not None:
                    raise MCPInvalidResponseError(
                        "MCP Server 返回了非法 JSON"
                    )
                raise MCPClientError(
                    f"MCP 请求超时（{self._timeout_seconds:g} 秒）"
                )
            try:
                message = messages.get(timeout=remaining)
            except Empty as exc:
                if invalid_json_line is not None:
                    raise MCPInvalidResponseError(
                        "MCP Server 返回了非法 JSON"
                    ) from exc
                raise MCPClientError(
                    f"MCP 请求超时（{self._timeout_seconds:g} 秒）"
                ) from exc

            if message is _STREAM_CLOSED:
                if invalid_json_line is not None:
                    raise MCPInvalidResponseError(
                        "MCP Server 返回了非法 JSON 后提前退出"
                    )
                details = " | ".join(stderr_lines)
                if (
                    "offline" in details.lower()
                    and "amap-mcp-server" in details.lower()
                ):
                    raise MCPClientError(
                        "本机尚未安装高德 MCP Server；请先执行 "
                        "`python3 -m uv tool install amap-mcp-server`"
                    )
                suffix = f"：{details}" if details else ""
                raise MCPClientError(
                    f"MCP Server 提前退出{suffix}"
                )
            if isinstance(message, _InvalidJSONLine):
                invalid_json_line = message
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                logger.debug(
                    "忽略 MCP 通知或其他响应 | message=%s",
                    preview(message),
                )
                continue
            if "error" in message:
                error = message["error"]
                raise MCPClientError(f"MCP 返回错误：{error}")
            if "result" not in message:
                raise MCPClientError("MCP 响应缺少 result")
            return message["result"]

    @staticmethod
    def _normalize_tool_result(result: Any) -> Any:
        if not isinstance(result, dict):
            return result

        content = result.get("content")
        text_values = []
        if isinstance(content, list):
            text_values = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]

        if result.get("isError"):
            error_text = " ".join(text_values) or "MCP 工具执行失败"
            raise MCPClientError(error_text)

        structured = result.get("structuredContent")
        if structured is not None:
            return structured

        if len(text_values) == 1:
            try:
                return json.loads(text_values[0])
            except json.JSONDecodeError as exc:
                raise MCPInvalidResponseError(
                    "MCP 工具返回了无法解析的 JSON 文本"
                ) from exc
        if text_values:
            return text_values
        return result

    @staticmethod
    def _stop_server(process: subprocess.Popen) -> None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
