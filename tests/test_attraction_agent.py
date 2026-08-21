"""Offline and opt-in live tests for AttractionSearchAgent."""

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
from agent_app.agents.attraction import (
    ATTRACTION_SEARCH_SYSTEM_PROMPT,
    AttractionSearchAgent,
)
from agent_app.shared.config import Settings
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


class AttractionSearchAgentTests(unittest.TestCase):
    def test_inherits_simple_agent(self):
        self.assertTrue(issubclass(AttractionSearchAgent, SimpleAgent))

    def test_prompt_documents_tool_and_json_reply_contract(self):
        self.assertIn(
            'maps_text_search({\n  "keywords": "博物馆 古迹"',
            ATTRACTION_SEARCH_SYSTEM_PROMPT,
        )
        self.assertIn(
            'citylimit：字符串，不是布尔值',
            ATTRACTION_SEARCH_SYSTEM_PROMPT,
        )
        self.assertIn("修订模式", ATTRACTION_SEARCH_SYSTEM_PROMPT)
        example = json.loads(
            ATTRACTION_SEARCH_SYSTEM_PROMPT.split(
                "严格按照以下 JSON 结构回复：\n",
                1,
            )[1]
        )
        self.assertEqual("北京", example["city"])
        self.assertIn("attractions", example)

    def test_run_uses_its_own_llm_poi_tool_loop(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_poi",
                            name="maps_text_search",
                            arguments=(
                                '{"keywords":"博物馆 古迹",'
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
                            "preferences": ["历史文化"],
                            "search_query": {
                                "keywords": "博物馆 古迹",
                                "city": "北京",
                                "citylimit": "true",
                            },
                            "attractions": [
                                {"name": "故宫博物院"},
                                {"name": "中国国家博物馆"},
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
            return {"pois": [{"name": "故宫博物院"}]}

        agent = AttractionSearchAgent(llm)
        agent.add_tool(search_poi, schema=TEXT_SEARCH_TOOL_SCHEMA)
        with self.assertLogs(
            "agent_app.agents.base", level="INFO"
        ) as logs:
            result = agent.run("  北京历史文化  ")

        self.assertIn("故宫博物院", result)
        self.assertEqual(1, len(dispatched_calls))
        self.assertIn("博物馆", dispatched_calls[0]["keywords"])
        self.assertEqual([TEXT_SEARCH_TOOL_SCHEMA], llm.tools)
        self.assertIn(
            "旅行需求：北京历史文化",
            llm.messages[0][1]["content"],
        )
        log_output = "\n".join(logs.output)
        self.assertIn("AttractionSearchAgent 循环开始", log_output)
        self.assertIn("tool=maps_text_search", log_output)
        self.assertIn("finish_reason=stop", log_output)

    def test_run_rejects_empty_query(self):
        agent = AttractionSearchAgent(_FakeLLM([]))

        with self.assertRaisesRegex(ValueError, "query 不能为空"):
            agent.run("  ")

    def test_long_trip_recovers_until_candidate_count_covers_each_day(self):
        def result(names):
            return json.dumps(
                {
                    "city": "北京",
                    "preferences": ["历史文化"],
                    "search_query": {},
                    "attractions": [{"name": name} for name in names],
                    "data_notes": [],
                },
                ensure_ascii=False,
            )

        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_poi",
                            name="maps_text_search",
                            arguments='{"keywords":"古迹"}',
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content=result(["景点一", "景点二"]),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content=result(
                        [
                            "景点一",
                            "景点二",
                            "景点三",
                            "景点四",
                            "景点五",
                            "景点六",
                        ]
                    ),
                ),
            ]
        )
        agent = AttractionSearchAgent(llm)
        agent.add_tool(
            lambda keywords: {"pois": [{"name": keywords}]},
            schema=TEXT_SEARCH_TOOL_SCHEMA,
        )

        response = agent.run("2099-08-01 至 2099-08-03 去北京")

        self.assertIn("景点六", response)
        self.assertIn("至少返回 6 个", llm.messages[0][1]["content"])
        self.assertIn("不同景点候选不足", llm.messages[2][-1]["content"])


@unittest.skipUnless(
    LIVE_TESTS_ENABLED,
    "设置 RUN_LIVE_API_TESTS=1 后才会发送真实 API 请求",
)
class AttractionSearchAgentEndToEndTests(unittest.TestCase):
    """Exercise the attraction LLM, MCP, and Amap POI search."""

    @classmethod
    def setUpClass(cls):
        configure_logging("INFO")

    def test_attraction_agent_searches_amap_mcp_end_to_end(self):
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实景点测试")

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
        agent = AttractionSearchAgent(
            OpenAICompatibleLLM(bounded_settings)
        )
        agent.add_tool(
            call_maps_text_search,
            schema=tool,
        )
        try:
            result = agent.run("北京历史文化")

            self.assertGreaterEqual(len(successful_searches), 1)
            self.assertTrue(successful_searches[0]["keywords"].strip())
            self.assertIn("北京", successful_searches[0]["city"])
            self.assertGreater(len(result.strip()), 20)
        finally:
            amap_mcp.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
