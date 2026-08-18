"""Unit tests for the generic stdio MCP and Amap adapters."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure.amap_client import (
    AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
    AMAP_MCP_GEO_TOOL_NAME,
    AMAP_MCP_WEATHER_TOOL_NAME,
    AmapMCPClient,
)
from agent_app.infrastructure.mcp_client import (
    MCPClientError,
    MCPInvalidResponseError,
    StdioMCPClient,
    mcp_tool_to_openai_schema,
)


FAKE_SERVER = (
    PROJECT_ROOT / "tests" / "fixtures" / "fake_mcp_server.py"
)


class MCPClientTests(unittest.TestCase):
    def test_rejects_non_json_text_tool_result(self):
        with self.assertRaisesRegex(
            MCPInvalidResponseError,
            "无法解析的 JSON",
        ):
            StdioMCPClient._normalize_tool_result(
                {
                    "content": [
                        {"type": "text", "text": "not-json"}
                    ]
                }
            )

    def test_stdio_client_lists_tools_and_reuses_one_process(self):
        client = StdioMCPClient(
            command=(sys.executable, "-u", str(FAKE_SERVER)),
            env={"AMAP_MAPS_API_KEY": "test-key"},
            timeout_seconds=2,
        )

        with client:
            tools = client.list_tools()
            first = client.call_tool(
                AMAP_MCP_WEATHER_TOOL_NAME,
                {"city": "张家口"},
            )
            second = client.call_tool(
                AMAP_MCP_WEATHER_TOOL_NAME,
                {"city": "北京"},
            )

            self.assertTrue(client.is_connected)
            self.assertEqual(
                ["maps_weather", "maps_text_search"],
                [tool["name"] for tool in tools],
            )
            self.assertEqual(first["server_pid"], second["server_pid"])
            self.assertEqual("张家口", first["city"])
            self.assertTrue(first["api_key_configured"])
            self.assertEqual(
                "2026-07-31",
                first["forecasts"][0]["date"],
            )

        self.assertFalse(client.is_connected)

    def test_missing_stdio_command_has_clear_error(self):
        client = StdioMCPClient(
            command=("definitely-not-a-real-mcp-command",),
        )

        with self.assertRaisesRegex(
            MCPClientError, "找不到 MCP 启动命令"
        ):
            client.list_tools()

    @patch(
        "agent_app.infrastructure.mcp_client.shutil.which",
        return_value=None,
    )
    @patch(
        "agent_app.infrastructure.mcp_client.importlib.util.find_spec"
    )
    def test_uvx_falls_back_to_current_python_uv_module(
        self,
        find_spec,
        _which,
    ):
        find_spec.return_value = object()

        client = StdioMCPClient(
            command=("uvx", "amap-mcp-server"),
        )

        self.assertEqual(
            (
                sys.executable,
                "-m",
                "uv",
                "tool",
                "run",
                "--offline",
                "amap-mcp-server",
            ),
            client._command,
        )

    @patch(
        "agent_app.infrastructure.mcp_client.shutil.which",
        side_effect=("/usr/local/bin/uvx",),
    )
    def test_uvx_uses_offline_tool_cache(self, _which):
        client = StdioMCPClient(
            command=("uvx", "amap-mcp-server"),
        )

        self.assertEqual(
            (
                "/usr/local/bin/uvx",
                "--offline",
                "amap-mcp-server",
            ),
            client._command,
        )

    def test_mcp_schema_converts_to_openai_function_tool(self):
        schema = mcp_tool_to_openai_schema(
            {
                "name": "maps_weather",
                "description": "查询天气",
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        )

        self.assertEqual("function", schema["type"])
        self.assertEqual(
            "maps_weather",
            schema["function"]["name"],
        )
        self.assertEqual(
            ["city"],
            schema["function"]["parameters"]["required"],
        )

    def test_amap_adapter_lists_and_dynamically_calls_tools(self):
        mcp_client = Mock()
        advertised = (
            {
                "name": "maps_weather",
                "inputSchema": {"type": "object"},
            },
        )
        mcp_client.list_tools.return_value = advertised
        mcp_client.call_tool.return_value = {
            "city": "北京市",
            "forecasts": [],
        }
        client = AmapMCPClient(
            api_key="test-key",
            mcp_client=mcp_client,
        )

        self.assertEqual(advertised, client.list_tools())
        result = client.call_tool("maps_weather", city="北京")

        self.assertEqual("北京市", result["city"])
        mcp_client.list_tools.assert_called_once_with()
        mcp_client.call_tool.assert_called_once_with(
            "maps_weather",
            {"city": "北京"},
        )

    def test_amap_default_uvx_command_uses_stdout_safe_runner(self):
        client = AmapMCPClient(
            api_key="test-key",
            command=("uvx", "amap-mcp-server"),
        )

        command = client._client._command
        self.assertEqual(sys.executable, command[0])
        self.assertEqual(
            (
                "-m",
                "uv",
                "run",
                "--isolated",
                "--offline",
                "--with",
                "amap-mcp-server",
                "python",
            ),
            tuple(command[1:9]),
        )
        self.assertTrue(
            command[9].endswith("amap_server_runner.py")
        )
        self.assertNotIn("--python", command)

    def test_geocode_is_normalized_and_cached(self):
        mcp_client = Mock()
        mcp_client.call_tool.return_value = {
            "return": [{"location": "116.40,39.90"}]
        }
        client = AmapMCPClient(
            api_key="test-key",
            mcp_client=mcp_client,
        )

        first = client.call_tool(
            AMAP_MCP_GEO_TOOL_NAME,
            address=" 北京饭店 ",
            city=" 北京 ",
        )
        second = client.call_tool(
            AMAP_MCP_GEO_TOOL_NAME,
            address="北京饭店",
            city="北京",
        )

        self.assertIs(first, second)
        mcp_client.call_tool.assert_called_once_with(
            AMAP_MCP_GEO_TOOL_NAME,
            {"address": "北京饭店", "city": "北京"},
        )

    def test_direction_calls_are_normalized_and_cached(self):
        mcp_client = Mock()
        mcp_client.call_tool.return_value = {
            "route": {"paths": []}
        }
        client = AmapMCPClient(
            api_key="test-key",
            mcp_client=mcp_client,
        )

        first = client.call_tool(
            AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
            origin=" 116.40,39.90 ",
            destination="116.41,39.91",
        )
        second = client.call_tool(
            AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
            destination=" 116.41,39.91 ",
            origin="116.40,39.90",
        )

        self.assertIs(first, second)
        mcp_client.call_tool.assert_called_once_with(
            AMAP_MCP_DRIVING_COORDINATE_TOOL_NAME,
            {
                "origin": "116.40,39.90",
                "destination": "116.41,39.91",
            },
        )

    def test_amap_close_delegates_to_persistent_client(self):
        mcp_client = Mock()
        client = AmapMCPClient(
            api_key="test-key",
            mcp_client=mcp_client,
        )

        client.close()

        mcp_client.close.assert_called_once_with()

    def test_amap_adapter_requires_key_when_called_or_listed(self):
        client = AmapMCPClient(
            api_key="",
            mcp_client=Mock(),
        )

        with self.assertRaisesRegex(
            MCPClientError, "AMAP_API_KEY"
        ):
            client.list_tools()
        with self.assertRaisesRegex(
            MCPClientError, "AMAP_API_KEY"
        ):
            client.call_tool("maps_weather", city="北京")


if __name__ == "__main__":
    unittest.main(verbosity=2)
