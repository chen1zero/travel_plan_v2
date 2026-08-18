"""Offline and opt-in live tests for WeatherQueryAgent."""

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
    AMAP_MCP_WEATHER_TOOL_NAME,
    AmapMCPClient,
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
from agent_app.agents.weather import (
    WEATHER_QUERY_SYSTEM_PROMPT,
    WeatherQueryAgent,
)


DIRECT_EXECUTION = __name__ == "__main__"
LIVE_TESTS_ENABLED = (
    DIRECT_EXECUTION or os.getenv("RUN_LIVE_API_TESTS") == "1"
)
WEATHER_TOOL_SCHEMA = mcp_tool_to_openai_schema(
    {
        "name": AMAP_MCP_WEATHER_TOOL_NAME,
        "description": "查询城市天气预报",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
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


class WeatherQueryAgentTests(unittest.TestCase):
    def test_inherits_simple_agent(self):
        self.assertTrue(issubclass(WeatherQueryAgent, SimpleAgent))

    def test_prompt_documents_tool_and_json_reply_contract(self):
        self.assertIn("修订模式", WEATHER_QUERY_SYSTEM_PROMPT)
        self.assertIn(
            'maps_weather({\n  "city": "张家口"',
            WEATHER_QUERY_SYSTEM_PROMPT,
        )
        self.assertIn(
            "不要附加 days、date、province",
            WEATHER_QUERY_SYSTEM_PROMPT,
        )
        example = json.loads(
            WEATHER_QUERY_SYSTEM_PROMPT.split(
                "严格按照以下 JSON 结构回复：\n",
                1,
            )[1]
        )
        self.assertEqual(3, example["forecast_scope"]["days"])
        self.assertIn("daily_forecasts", example)

    def test_run_uses_its_own_llm_tool_loop(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_amap",
                            name="maps_weather",
                            arguments='{"city":"张家口"}',
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content=json.dumps(
                        {
                            "city": "张家口",
                            "search_query": {"city": "张家口"},
                            "forecast_scope": {
                                "start_date": "2099-08-18",
                                "days": 3,
                            },
                            "daily_forecasts": [],
                            "data_notes": [],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        dispatched_calls = []

        def weather(city):
            dispatched_calls.append(city)
            return {"forecast_days": 3, "forecast": []}

        agent = WeatherQueryAgent(llm)
        agent.add_tool(weather, schema=WEATHER_TOOL_SCHEMA)
        with self.assertLogs(
            "agent_app.agents.base", level="INFO"
        ) as logs:
            result = agent.run("  张家口  ")

        self.assertEqual("张家口", json.loads(result)["city"])
        self.assertEqual(["张家口"], dispatched_calls)
        self.assertEqual([WEATHER_TOOL_SCHEMA], llm.tools)
        self.assertEqual(2, len(llm.messages))
        first_request = llm.messages[0]
        self.assertIn(
            "WeatherQueryAgent",
            first_request[0]["content"],
        )
        self.assertIn(
            "旅行需求或城市名称：张家口",
            first_request[1]["content"],
        )
        self.assertEqual("assistant", llm.messages[1][-2]["role"])
        self.assertEqual("tool", llm.messages[1][-1]["role"])
        log_output = "\n".join(logs.output)
        self.assertIn("WeatherQueryAgent 循环开始", log_output)
        self.assertIn("tool=maps_weather", log_output)
        self.assertIn("finish_reason=stop", log_output)

    def test_run_rejects_empty_query(self):
        agent = WeatherQueryAgent(_FakeLLM([]))

        with self.assertRaisesRegex(ValueError, "query 不能为空"):
            agent.run("  ")


@unittest.skipUnless(
    LIVE_TESTS_ENABLED,
    "设置 RUN_LIVE_API_TESTS=1 后才会发送真实 API 请求",
)
class WeatherQueryAgentEndToEndTests(unittest.TestCase):
    """Exercise WeatherQueryAgent, MCP, and Amap directly."""

    @classmethod
    def setUpClass(cls):
        configure_logging("INFO")

    def test_weather_agent_queries_amap_mcp_end_to_end(self):
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实天气测试")

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
        called_cities = []

        def call_maps_weather(**arguments):
            result = amap_mcp.call_tool(
                AMAP_MCP_WEATHER_TOOL_NAME,
                **arguments,
            )
            city = arguments["city"]
            called_cities.append(city)
            return result

        tool = next(
            tool
            for tool in amap_mcp.list_tools()
            if tool["name"] == AMAP_MCP_WEATHER_TOOL_NAME
        )
        agent = WeatherQueryAgent(
            OpenAICompatibleLLM(bounded_settings)
        )
        agent.add_tool(
            call_maps_weather,
            schema=tool,
        )
        try:
            result = agent.run("张家口")

            self.assertEqual(1, len(called_cities))
            self.assertIn("张家口", called_cities[0])
            self.assertIn("张家口", result)
            self.assertIn("未来", result)
        finally:
            amap_mcp.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
