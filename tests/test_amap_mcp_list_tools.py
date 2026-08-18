"""Print tool definitions returned by the real Amap MCP Server."""

import json
import os
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure.amap_client import (
    AMAP_MCP_TEXT_SEARCH_TOOL_NAME,
    AMAP_MCP_WEATHER_TOOL_NAME,
    AmapMCPClient,
)
from agent_app.shared.config import Settings
from agent_app.shared.logging import configure_logging


DIRECT_EXECUTION = __name__ == "__main__"
LIVE_TESTS_ENABLED = (
    DIRECT_EXECUTION or os.getenv("RUN_LIVE_API_TESTS") == "1"
)
TOOLS_OUTPUT_FILE = (
    PROJECT_ROOT / "tests" / "fixtures" / "amap_mcp_tools.json"
)


@unittest.skipUnless(
    LIVE_TESTS_ENABLED,
    "设置 RUN_LIVE_API_TESTS=1 后才会连接真实高德 MCP Server",
)
class AmapMCPListToolsTests(unittest.TestCase):
    """Inspect names, descriptions, and schemas from tools/list."""

    @classmethod
    def setUpClass(cls):
        configure_logging("INFO")

    def test_list_tools_prints_real_amap_tool_definitions(self):
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实 MCP 测试")

        client = AmapMCPClient(
            api_key=settings.amap_api_key,
            command=settings.amap_mcp_command,
            timeout_seconds=settings.amap_mcp_timeout_seconds,
        )
        try:
            tools = client.list_tools()
        finally:
            client.close()

        printable_tools = []
        for tool in tools:
            self.assertIsInstance(tool.get("name"), str)
            self.assertIsInstance(tool.get("description"), str)
            self.assertIsInstance(tool.get("inputSchema"), dict)
            printable_tools.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["inputSchema"],
                }
            )

        tool_names = {tool["name"] for tool in printable_tools}
        self.assertIn(AMAP_MCP_WEATHER_TOOL_NAME, tool_names)
        self.assertIn(AMAP_MCP_TEXT_SEARCH_TOOL_NAME, tool_names)

        formatted_tools = json.dumps(
            printable_tools,
            ensure_ascii=False,
            indent=2,
        )
        TOOLS_OUTPUT_FILE.write_text(
            formatted_tools + "\n",
            encoding="utf-8",
        )
        print(
            "\n高德 MCP list_tools() 返回：\n"
            f"{formatted_tools}\n"
            f"结果已保存至：{TOOLS_OUTPUT_FILE}",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
