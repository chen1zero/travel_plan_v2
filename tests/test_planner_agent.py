"""Offline and opt-in live tests for PlannerAgent."""

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure.amap_client import AmapMCPClient
from agent_app.shared.config import Settings
from agent_app.infrastructure.llm_client import (
    LLMResponse,
    OpenAICompatibleLLM,
    ToolCall,
)
from agent_app.shared.logging import configure_logging
from agent_app.agents.planner import (
    PLANNER_SYSTEM_PROMPT,
    PlannerAgent,
)
from agent_app.tools.route import (
    ROUTE_OPTIONS_TOOL_NAME,
    ROUTE_OPTIONS_TOOL_SCHEMA,
    compare_route_options,
)
from agent_app.agents.base import SimpleAgent


DIRECT_EXECUTION = __name__ == "__main__"
LIVE_TESTS_ENABLED = (
    DIRECT_EXECUTION or os.getenv("RUN_LIVE_API_TESTS") == "1"
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


class PlannerAgentTests(unittest.TestCase):
    def test_inherits_simple_agent(self):
        self.assertTrue(issubclass(PlannerAgent, SimpleAgent))

    def test_prompt_defines_strict_final_json_contract(self):
        self.assertIn(
            'compare_route_options({\n  "origin_address"',
            PLANNER_SYSTEM_PROMPT,
        )
        self.assertIn(
            "只能输出一个可被 json.loads 直接解析的 JSON 对象",
            PLANNER_SYSTEM_PROMPT,
        )
        example = json.loads(
            PLANNER_SYSTEM_PROMPT.split(
                "严格 JSON 格式与示例：\n",
                1,
            )[1]
        )
        self.assertEqual(
            {
                "plan_version",
                "request_summary",
                "weather_summary",
                "selected_hotel",
                "daily_itinerary",
                "budget_summary",
                "booking_and_safety_tips",
                "data_notes",
            },
            set(example),
        )
        route = example["daily_itinerary"][0]["routes"][0]
        self.assertIn("walking", route)
        self.assertIn("driving", route)
        self.assertIn("public_transit", route)
        self.assertIn(
            "walking_distance_km",
            route["public_transit"],
        )
        self.assertIn("transfer_count", route["public_transit"])
        self.assertIn("transit_type", route["public_transit"])
        self.assertIn("line_names", route["public_transit"])
        self.assertIn(
            "1 至 10 公里",
            PLANNER_SYSTEM_PROMPT,
        )
        self.assertIn("600 米", PLANNER_SYSTEM_PROMPT)
        self.assertIn("previous_plan", PLANNER_SYSTEM_PROMPT)
        self.assertIn("修订模式", PLANNER_SYSTEM_PROMPT)
        self.assertIn("budget_only_increase", PLANNER_SYSTEM_PROMPT)
        self.assertIn("严禁调用", PLANNER_SYSTEM_PROMPT)

    def test_revision_mode_passes_previous_plan_and_change_analysis(self):
        llm = _FakeLLM(
            [LLMResponse(finish_reason="stop", content="{}")]
        )
        agent = PlannerAgent(llm)
        previous_plan = {
            "plan_version": "1.0",
            "selected_hotel": {"name": "上一版酒店"},
        }
        analysis = {
            "mode": "revision",
            "rerun": {
                "attraction": False,
                "weather": False,
                "hotel": True,
            },
        }

        agent.run(
            original_request="换个酒店，其他不变",
            attractions="上一版景点",
            weather="上一版天气",
            hotels="新酒店候选",
            previous_plan=previous_plan,
            change_analysis=analysis,
            revision_mode=True,
        )

        message = llm.messages[0][1]["content"]
        self.assertIn("基于上一版计划增量修订", message)
        payload = json.loads(message.split("：\n", 1)[1])
        self.assertEqual("revision", payload["mode"])
        self.assertEqual(previous_plan, payload["previous_plan"])
        self.assertEqual(analysis, payload["change_analysis"])

    def test_revision_mode_uses_l2_memory_anchor_without_plan_duplication(self):
        llm = _FakeLLM(
            [LLMResponse(finish_reason="stop", content="{}")]
        )
        agent = PlannerAgent(llm)
        previous_plan = {"selected_hotel": {"name": "上一版酒店"}}
        memory = {
            "memory_mode": "layered_l2",
            "all_user_messages": [{"sequence": 1, "text": "首次要求"}],
            "revision_ledger": [],
            "recent_assistant_plans": [],
            "latest_anchor": {"full_plan": previous_plan},
            "current_request": {"additional_requirements": "换个酒店"},
        }

        agent.run(
            original_request="换个酒店",
            attractions="景点",
            weather="天气",
            hotels="酒店",
            previous_plan=previous_plan,
            revision_mode=True,
            session_memory=memory,
        )

        payload = json.loads(llm.messages[0][1]["content"].split("：\n", 1)[1])
        self.assertEqual(memory, payload["session_memory"])
        self.assertNotIn("previous_plan", payload)
        self.assertEqual(
            "session_memory.latest_anchor.full_plan",
            payload["previous_plan_reference"],
        )

    def test_budget_only_revision_adds_hard_acceptance_constraints(self):
        llm = _FakeLLM([LLMResponse(finish_reason="stop", content="{}")])
        agent = PlannerAgent(llm)
        analysis = {
            "mode": "revision",
            "preservation": {
                "budget_only_increase": True,
                "forbid_new_route_queries": True,
            },
        }

        agent.run(
            original_request="预算提高到 8000",
            attractions="上一版景点",
            weather="上一版天气",
            hotels="刷新后的酒店候选",
            previous_plan={"selected_hotel": {"name": "上一版酒店"}},
            change_analysis=analysis,
            revision_mode=True,
        )

        payload = json.loads(llm.messages[0][1]["content"].split("：\n", 1)[1])
        constraints = payload["revision_acceptance_constraints"]
        self.assertTrue(any("selected_hotel" in value for value in constraints))
        self.assertTrue(any("不得调用 compare_route_options" in value for value in constraints))

    def test_budget_only_revision_blocks_route_handler_execution(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="blocked-route",
                            name=ROUTE_OPTIONS_TOOL_NAME,
                            arguments=(
                                '{"origin_address":"原酒店，原地址",'
                                '"destination_address":"原景点，原地址",'
                                '"origin_city":"北京",'
                                '"destination_city":"北京"}'
                            ),
                        ),
                    ),
                ),
                LLMResponse(finish_reason="stop", content="{}"),
            ]
        )
        calls = []
        agent = PlannerAgent(llm)
        agent.add_tool(
            lambda **arguments: calls.append(arguments),
            schema=ROUTE_OPTIONS_TOOL_SCHEMA,
        )

        result = agent.run(
            original_request="预算提高到 8000",
            attractions="上一版景点",
            weather="上一版天气",
            hotels="刷新后的酒店候选",
            previous_plan={"selected_hotel": {"name": "上一版酒店"}},
            change_analysis={
                "preservation": {
                    "budget_only_increase": True,
                    "forbid_new_route_queries": True,
                }
            },
            revision_mode=True,
        )

        self.assertEqual("{}", result)
        self.assertEqual([], calls)
        tool_message = next(
            message
            for message in llm.messages[1]
            if message.get("role") == "tool"
        )
        self.assertIn("不得重新查询", tool_message["content"])

    def test_run_queries_routes_then_returns_plan(self):
        llm = _FakeLLM(
            [
                LLMResponse(
                    finish_reason="tool_calls",
                    tool_calls=(
                        ToolCall(
                            id="call_route",
                            name=ROUTE_OPTIONS_TOOL_NAME,
                            arguments=(
                                '{"origin_address":"北京饭店，'
                                '东长安街33号",'
                                '"destination_address":"故宫博物院，'
                                '景山前街4号",'
                                '"origin_city":"北京",'
                                '"destination_city":"北京"}'
                            ),
                        ),
                    ),
                ),
                LLMResponse(
                    finish_reason="stop",
                    content=(
                        "北京饭店至故宫：步行2公里25分钟，"
                        "驾车3公里12分钟，公交3公里20分钟。"
                    ),
                ),
            ]
        )
        dispatched_calls = []

        def route_options(**arguments):
            dispatched_calls.append(arguments)
            return {
                "walking": {
                    "distance_km": 2,
                    "duration_minutes": 25,
                },
                "driving": {
                    "distance_km": 3,
                    "duration_minutes": 12,
                },
                "public_transit": {
                    "distance_km": 3,
                    "duration_minutes": 20,
                },
            }

        agent = PlannerAgent(llm)
        agent.add_tool(route_options, schema=ROUTE_OPTIONS_TOOL_SCHEMA)
        result = agent.run(
            original_request="北京一日游，预算1500元",
            attractions="故宫博物院，景山前街4号",
            weather="晴，30°C",
            hotels="北京饭店，东长安街33号",
        )

        self.assertIn("步行2公里25分钟", result)
        self.assertEqual(1, len(dispatched_calls))
        self.assertEqual(
            "北京",
            dispatched_calls[0]["origin_city"],
        )
        self.assertEqual([ROUTE_OPTIONS_TOOL_SCHEMA], llm.tools)
        first_user_message = llm.messages[0][1]["content"]
        self.assertIn("预算1500元", first_user_message)
        self.assertIn("故宫博物院", first_user_message)
        self.assertIn("晴，30°C", first_user_message)
        self.assertIn("北京饭店", first_user_message)

    def test_run_rejects_missing_specialist_output(self):
        agent = PlannerAgent(_FakeLLM([]))

        with self.assertRaisesRegex(ValueError, "weather"):
            agent.run(
                original_request="北京一日游",
                attractions="故宫",
                weather=" ",
                hotels="北京饭店",
            )


@unittest.skipUnless(
    LIVE_TESTS_ENABLED,
    "设置 RUN_LIVE_API_TESTS=1 后才会发送真实 API 请求",
)
class PlannerAgentEndToEndTests(unittest.TestCase):
    """Exercise the planner LLM and real Amap MCP route tools."""

    @classmethod
    def setUpClass(cls):
        configure_logging("INFO")

    def test_planner_agent_queries_real_routes_end_to_end(self):
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实规划测试")

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
        successful_routes = []

        def call_route_options(**arguments):
            result = compare_route_options(
                **arguments,
                amap_client=amap_mcp,
            )
            successful_routes.append(result)
            return result

        agent = PlannerAgent(
            OpenAICompatibleLLM(bounded_settings)
        )
        agent.add_tool(
            call_route_options,
            schema=ROUTE_OPTIONS_TOOL_SCHEMA,
        )
        try:
            result = agent.run(
                original_request=(
                    "2026年8月1日北京一日游，预算1500元，"
                    "游览故宫和国家博物馆"
                ),
                attractions=(
                    "故宫博物院，地址：景山前街4号；"
                    "中国国家博物馆，地址：东长安街16号"
                ),
                weather="晴，最高温度31°C，注意防晒。",
                hotels="北京饭店，地址：东长安街33号。",
            )

            self.assertGreaterEqual(len(successful_routes), 1)
            self.assertIn("步行", result)
            self.assertIn("驾车", result)
            self.assertIn("公共交通", result)
        finally:
            amap_mcp.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
