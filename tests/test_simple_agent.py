"""Unit tests for the reusable SimpleAgent base class."""

import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.agents.base import SimpleAgent
from agent_app.infrastructure.llm_client import LLMResponse, ToolCall
from agent_app.tools.errors import (
    ModelToolRefusalError,
    TaskCancelledError,
)


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = None
        self.tools = []

    def add_tool(self, tool_schema):
        self.tools.append(tool_schema)

    def complete(self, messages):
        self.messages = list(messages)
        return self.responses.pop(0)


class SimpleAgentTests(unittest.TestCase):
    def test_cancel_prevents_future_llm_calls(self):
        llm = _FakeLLM([])
        agent = SimpleAgent(llm, "测试")

        agent.cancel()

        with self.assertRaises(TaskCancelledError):
            agent.run("任务")
        self.assertIsNone(llm.messages)

    def test_recovers_when_model_initially_refuses_required_tool(self):
        llm = _FakeLLM(
            [
                LLMResponse(finish_reason="stop", content="我无法查询"),
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_required",
                            name="lookup",
                            arguments='{"city":"北京"}',
                        ),
                    ),
                ),
                LLMResponse(finish_reason="stop", content="查询完成"),
            ]
        )
        events = []
        agent = SimpleAgent(
            llm,
            "必须查询",
            required_tool_names=("lookup",),
            event_callback=lambda event_type, payload: events.append(
                (event_type, payload)
            ),
        )
        agent.add_tool(lambda city: {"city": city}, name="lookup")

        result = agent.run("查询北京")

        self.assertEqual("查询完成", result)
        self.assertIn(
            "agent.recovering",
            [event_type for event_type, _payload in events],
        )
        self.assertTrue(
            any(
                "必须先成功调用" in str(message.get("content") or "")
                for message in llm.messages
            )
        )

    def test_fails_after_bounded_required_tool_refusal(self):
        agent = SimpleAgent(
            _FakeLLM(
                [
                    LLMResponse(finish_reason="stop", content="不调用"),
                    LLMResponse(finish_reason="stop", content="仍不调用"),
                ]
            ),
            "必须查询",
            required_tool_names=("lookup",),
        )
        agent.add_tool(lambda: {}, name="lookup")

        with self.assertRaises(ModelToolRefusalError):
            agent.run("查询")

    def test_repairs_invalid_json_output_once(self):
        agent = SimpleAgent(
            _FakeLLM(
                [
                    LLMResponse(finish_reason="stop", content="不是 JSON"),
                    LLMResponse(
                        finish_reason="stop",
                        content='{"city":"北京","items":[]}',
                    ),
                ]
            ),
            "输出 JSON",
            required_output_fields=("city", "items"),
        )

        result = agent.run("生成")

        self.assertEqual("北京", json.loads(result)["city"])

    def test_emits_tool_failed_for_handler_exception(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_failed",
                            name="lookup",
                            arguments="{}",
                        ),
                    ),
                ),
                LLMResponse(finish_reason="stop", content="已降级"),
            ]
        )
        events = []
        agent = SimpleAgent(
            llm,
            "查询",
            event_callback=lambda event_type, payload: events.append(
                (event_type, payload)
            ),
        )

        def fail():
            raise RuntimeError("内部实现详情")

        agent.add_tool(fail, name="lookup")

        self.assertEqual("已降级", agent.run("查询"))
        failed = [
            payload
            for event_type, payload in events
            if event_type == "tool.failed"
        ]
        self.assertEqual(1, len(failed))
        self.assertEqual("TOOL_EXCEPTION", failed[0]["error_code"])
        self.assertNotIn("内部实现详情", failed[0]["message"])

    def test_can_be_configured_for_another_agent(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="stop",
                    content="分析完成",
                )
            ]
        )
        agent = SimpleAgent(
            llm=llm,
            system_prompt="你是一个分析专家。",
            name="AnalysisAgent",
        )

        result = agent.run("分析这个问题")

        self.assertEqual("分析完成", result)
        self.assertEqual(
            {"role": "system", "content": "你是一个分析专家。"},
            llm.messages[0],
        )
        self.assertEqual(
            {"role": "user", "content": "分析这个问题"},
            llm.messages[1],
        )

    def test_add_tool_infers_schema_and_dispatches_handler(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_time",
                            name="get_time",
                            arguments='{"timezone":"Asia/Shanghai"}',
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content="当前时间已查询。",
                ),
            ]
        )
        calls = []

        def get_time(
            timezone: str,
            include_seconds: bool = False,
        ):
            """查询指定时区的当前时间。"""
            calls.append((timezone, include_seconds))
            return {"ok": True}

        agent = SimpleAgent(
            llm=llm,
            system_prompt="你是一个时间助手。",
        )
        returned_agent = agent.add_tool(get_time)

        result = agent.run("查询上海时间")

        self.assertIs(agent, returned_agent)
        self.assertEqual(1, len(llm.tools))
        function_schema = llm.tools[0]["function"]
        self.assertEqual("get_time", function_schema["name"])
        self.assertEqual(
            "查询指定时区的当前时间。",
            function_schema["description"],
        )
        self.assertEqual(
            ["timezone"],
            function_schema["parameters"]["required"],
        )
        self.assertEqual(
            {"type": "string"},
            function_schema["parameters"]["properties"]["timezone"],
        )
        self.assertEqual(
            {"type": "boolean", "default": False},
            function_schema["parameters"]["properties"][
                "include_seconds"
            ],
        )
        self.assertEqual([("Asia/Shanghai", False)], calls)
        self.assertEqual("当前时间已查询。", result)
        self.assertEqual("tool", llm.messages[-1]["role"])
        self.assertEqual(
            "call_time",
            llm.messages[-1]["tool_call_id"],
        )

    def test_add_tool_rejects_duplicate_names(self):
        llm = _FakeLLM([])
        schema = {
            "type": "function",
            "function": {
                "name": "get_time",
                "parameters": {"type": "object"},
            },
        }
        agent = SimpleAgent(llm, "你是时间助手。")
        agent.add_tool(lambda: None, schema=schema)

        with self.assertRaisesRegex(ValueError, "工具已存在"):
            agent.add_tool(lambda: None, schema=schema)

        self.assertEqual([schema], llm.tools)

    def test_add_tool_accepts_native_mcp_definition(self):
        llm = _FakeLLM([])
        mcp_tool = {
            "name": "maps_weather",
            "description": "查询城市天气",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
        agent = SimpleAgent(llm, "你是天气助手。")

        agent.add_tool(
            lambda **arguments: arguments,
            schema=mcp_tool,
        )

        self.assertEqual(
            "maps_weather",
            llm.tools[0]["function"]["name"],
        )
        self.assertEqual(
            ["city"],
            llm.tools[0]["function"]["parameters"]["required"],
        )

    def test_add_tool_allows_concise_schema_overrides(self):
        llm = _FakeLLM([])

        def lookup(place):
            return place

        parameters = {
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "description": "地点名称",
                }
            },
            "required": ["place"],
        }
        agent = SimpleAgent(llm, "你是搜索助手。")

        agent.add_tool(
            lookup,
            name="search_place",
            description="查询地点",
            parameters=parameters,
        )

        function = llm.tools[0]["function"]
        self.assertEqual("search_place", function["name"])
        self.assertEqual("查询地点", function["description"])
        self.assertEqual(parameters, function["parameters"])

    def test_emits_sanitized_iteration_and_tool_events(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_weather",
                            name="weather",
                            arguments='{"city":"北京"}',
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content="查询完成",
                ),
            ]
        )
        events = []
        agent = SimpleAgent(
            llm=llm,
            system_prompt="天气助手",
            name="WeatherAgent",
            event_callback=lambda event_type, payload: events.append(
                (event_type, payload)
            ),
        )
        agent.add_tool(
            lambda city: {
                "city": city,
                "api_key": "should-not-be-visible",
            },
            name="weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )

        result = agent.run("查询北京天气")

        self.assertEqual("查询完成", result)
        self.assertEqual(
            [
                "agent.iteration",
                "tool.started",
                "tool.completed",
                "agent.iteration",
            ],
            [event_type for event_type, _payload in events],
        )
        event_text = json.dumps(events, ensure_ascii=False)
        self.assertIn("WeatherAgent 调用 weather", event_text)
        self.assertIn("weather 返回", event_text)
        self.assertIn("北京", event_text)
        self.assertNotIn("should-not-be-visible", event_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
