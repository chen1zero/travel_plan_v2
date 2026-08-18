"""Persistent Amap MCP connection and project tool names."""

from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from agent_app.infrastructure.mcp_client import (
    MCPClientError,
    StdioMCPClient,
)
from agent_app.tools.errors import InvalidToolResultError


AMAP_MCP_WEATHER_TOOL_NAME = "maps_weather"
AMAP_MCP_TEXT_SEARCH_TOOL_NAME = "maps_text_search"
AMAP_MCP_GEO_TOOL_NAME = "maps_geo"
AMAP_MCP_WALKING_COORDINATE_TOOL_NAME = (
    "maps_direction_walking_by_coordinates"
)
AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME = (
    "maps_direction_driving_by_coordinates"
)
AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME = (
    "maps_direction_transit_integrated_by_coordinates"
)
_CACHEABLE_DIRECTION_TOOLS = {
    AMAP_MCP_WALKING_COORDINATE_TOOL_NAME,
    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
    AMAP_MCP_TRANSIT_COORDINATE_TOOL_NAME,
}


class MCPToolCaller(Protocol):
    """MCP capabilities required by AmapMCPClient."""

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        ...

    def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        ...

    def close(self) -> None:
        ...


class AmapMCPClient:
    """Discover and call Amap tools through one persistent MCP session."""

    def __init__(
        self,
        api_key: str,
        command: Sequence[str] = ("uvx", "amap-mcp-server"),
        timeout_seconds: float = 30.0,
        mcp_client: Optional[MCPToolCaller] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._geocode_cache: Dict[Tuple[str, str], Any] = {}
        self._direction_cache: Dict[
            Tuple[str, Tuple[Tuple[str, str], ...]],
            Any,
        ] = {}
        self._client = (
            mcp_client
            if mcp_client is not None
            else StdioMCPClient(
                command=self._stdout_safe_command(command),
                env={"AMAP_MAPS_API_KEY": self._api_key},
                timeout_seconds=timeout_seconds,
            )
        )

    @staticmethod
    def _stdout_safe_command(
        command: Sequence[str],
    ) -> Sequence[str]:
        normalized_command = tuple(command)
        if normalized_command[:2] != (
            "uvx",
            "amap-mcp-server",
        ):
            return normalized_command

        runner = (
            Path(__file__).resolve().parent
            / "amap_server_runner.py"
        )
        return (
            sys.executable,
            "-m",
            "uv",
            "run",
            "--isolated",
            "--offline",
            "--with",
            "amap-mcp-server",
            "python",
            str(runner),
            *normalized_command[2:],
        )

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """List tools advertised by the Amap MCP server."""
        self._require_api_key()
        return self._client.list_tools()

    def call_tool(self, tool_name: str, **arguments: Any) -> Any:
        """Dynamically call any tool advertised by the MCP server."""
        self._require_api_key()
        normalized_name = tool_name.strip()
        if not normalized_name:
            raise ValueError("MCP tool_name 不能为空")

        if normalized_name == AMAP_MCP_GEO_TOOL_NAME:
            address = str(arguments.get("address", "")).strip()
            city = str(arguments.get("city", "")).strip()
            if not address:
                raise ValueError("address 不能为空")
            cache_key = (address, city)
            cached = self._geocode_cache.get(cache_key)
            if cached is not None:
                return cached
            normalized_arguments = {"address": address}
            if city:
                normalized_arguments["city"] = city
            result = self._call_tool(
                normalized_name,
                normalized_arguments,
            )
            self._geocode_cache[cache_key] = result
            return result

        if normalized_name in _CACHEABLE_DIRECTION_TOOLS:
            normalized_arguments = {
                str(key): str(value).strip()
                for key, value in arguments.items()
            }
            cache_key = (
                normalized_name,
                tuple(sorted(normalized_arguments.items())),
            )
            cached = self._direction_cache.get(cache_key)
            if cached is not None:
                return cached
            result = self._call_tool(
                normalized_name,
                normalized_arguments,
            )
            self._direction_cache[cache_key] = result
            return result

        return self._call_tool(normalized_name, arguments)

    def close(self) -> None:
        """Close the persistent MCP connection."""
        self._client.close()

    def __enter__(self) -> "AmapMCPClient":
        self.list_tools()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        result = self._client.call_tool(tool_name, arguments)
        if not isinstance(result, Mapping):
            raise InvalidToolResultError(
                f"高德工具 {tool_name} 返回格式错误"
            )
        if isinstance(result, Mapping) and result.get("error"):
            raise MCPClientError(str(result["error"]))
        return result

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise MCPClientError(
                "未配置 AMAP_API_KEY，无法启动高德 MCP Server"
            )
