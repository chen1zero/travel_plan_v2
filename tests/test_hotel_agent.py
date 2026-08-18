"""Offline and opt-in live tests for HotelAgent."""

from dataclasses import replace
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
    AmapMCPClient,
)
from agent_app.shared.config import Settings
from agent_app.agents.hotel import HOTEL_SYSTEM_PROMPT, HotelAgent
from agent_app.infrastructure.llm_client import (
    LLMResponse,
    OpenAICompatibleLLM,
    ToolCall,
)
from agent_app.shared.logging import configure_logging
from agent_app.infrastructure.mcp_client import (
    mcp_tool_to_openai_schema,
)
from agent_app.agents.base import SimpleAgent


DIRECT_EXECUTION = __name__ == "__main__"
LIVE_TESTS_ENABLED = (
    DIRECT_EXECUTION or os.getenv("RUN_LIVE_API_TESTS") == "1"
)
TEXT_SEARCH_TOOL_SCHEMA = mcp_tool_to_openai_schema(
    {
        "name": AMAP_MCP_TEXT_SEARCH_TOOL_NAME,
        "description": "根据关键词和城市搜索 POI",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string"},
                "city": {"type": "string"},
                "citylimit": {"type": "string"},
            },
            "required": ["keywords"],
        },
    }
)


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.messages = []
        self.tools = []

    def add_tool(self, tool_schema):
        self.tools.append(tool_schema)

    def complete(self, messages):
        self.messages.append([dict(message) for message in messages])
        return self._responses.pop(0)


class HotelAgentTests(unittest.TestCase):
    def test_inherits_simple_agent(self):
        self.assertTrue(issubclass(HotelAgent, SimpleAgent))

    def test_prompt_documents_tool_and_json_reply_contract(self):
        self.assertIn("修订模式", HOTEL_SYSTEM_PROMPT)
        self.assertIn(
            'maps_text_search({\n  "keywords": "经济型酒店 快捷酒店"',
            HOTEL_SYSTEM_PROMPT,
        )
        self.assertIn(
            'citylimit：字符串，不是布尔值',
            HOTEL_SYSTEM_PROMPT,
        )
        example = json.loads(
            HOTEL_SYSTEM_PROMPT.split(
                "严格按照以下 JSON 结构回复：\n",
                1,
            )[1]
        )
        self.assertEqual("经济型", example["accommodation_requirement"])
        self.assertIsNone(
            example["hotels"][0]["price_cny_per_night"]
        )

    def test_run_uses_its_own_llm_poi_tool_loop(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_hotel",
                            name="maps_text_search",
                            arguments=(
                                '{"keywords":"豪华酒店",'
                                '"city":"北京",'
                                '"citylimit":"true"}'
                            ),
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content=json.dumps(
                        {
                            "city": "北京",
                            "accommodation_requirement": "豪华型",
                            "search_query": {
                                "keywords": "豪华酒店",
                                "city": "北京",
                                "citylimit": "true",
                            },
                            "hotels": [
                                {"name": "北京饭店"},
                                {"name": "北京贵宾楼饭店"},
                            ],
                            "data_notes": [],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        dispatched_calls = []

        def search_poi(keywords, city="", citylimit="false"):
            dispatched_calls.append(
                {
                    "keywords": keywords,
                    "city": city,
                    "citylimit": citylimit,
                }
            )
            return {
                "pois": [
                    {"name": "北京饭店", "address": "东长安街33号"}
                ]
            }

        agent = HotelAgent(llm)
        agent.add_tool(search_poi, schema=TEXT_SEARCH_TOOL_SCHEMA)
        with self.assertLogs(
            "agent_app.agents.base", level="INFO"
        ) as logs:
            result = agent.run("  北京豪华型  ")

        self.assertIn("北京饭店", result)
        self.assertEqual(1, len(dispatched_calls))
        self.assertIn("豪华酒店", dispatched_calls[0]["keywords"])
        self.assertEqual([TEXT_SEARCH_TOOL_SCHEMA], llm.tools)
        self.assertIn(
            "旅行需求：北京豪华型",
            llm.messages[0][1]["content"],
        )
        log_output = "\n".join(logs.output)
        self.assertIn("HotelAgent 循环开始", log_output)
        self.assertIn("tool=maps_text_search", log_output)
        self.assertIn("finish_reason=stop", log_output)

    def test_run_rejects_empty_query(self):
        agent = HotelAgent(_FakeLLM([]))

        with self.assertRaisesRegex(
            ValueError, "query 不能为空"
        ):
            agent.run("  ")


@unittest.skipUnless(
    LIVE_TESTS_ENABLED,
    "设置 RUN_LIVE_API_TESTS=1 后才会发送真实 API 请求",
)
class HotelAgentEndToEndTests(unittest.TestCase):
    """Exercise the hotel LLM, MCP, and Amap POI search."""

    @classmethod
    def setUpClass(cls):
        configure_logging("INFO")

    def test_hotel_agent_searches_amap_mcp_end_to_end(self):
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实酒店测试")

        bounded_settings = replace(
            settings,
            timeout_seconds=90.0,
            max_retries=0,
        )
        amap_mcp = AmapMCPClient(
            api_key=settings.amap_api_key,
            command=settings.amap_mcp_command,
            timeout_seconds=max(
                settings.amap_mcp_timeout_seconds,
                180.0,
            ),
        )
        successful_searches = []

        def call_maps_text_search(**arguments):
            result = amap_mcp.call_tool(
                AMAP_MCP_TEXT_SEARCH_TOOL_NAME,
                **arguments,
            )
            successful_searches.append(dict(arguments))
            return result

        tool = next(
            tool
            for tool in amap_mcp.list_tools()
            if tool["name"] == AMAP_MCP_TEXT_SEARCH_TOOL_NAME
        )
        agent = HotelAgent(
            OpenAICompatibleLLM(bounded_settings)
        )
        agent.add_tool(
            call_maps_text_search,
            schema=tool,
        )
        try:
            result = agent.run("北京豪华型酒店")

            self.assertGreaterEqual(len(successful_searches), 1)
            self.assertIn("酒店", successful_searches[0]["keywords"])
            self.assertIn("北京", successful_searches[0]["city"])
            self.assertGreater(len(result.strip()), 20)
        finally:
            amap_mcp.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
