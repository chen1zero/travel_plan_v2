"""Unit tests for the LangGraph travel planning harness."""

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys
from threading import Barrier
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.main import build_travel_plan_agent
from agent_app.shared.config import Settings
from agent_app.shared.logging import configure_logging
from agent_app.harness.travel_planning import (
    TravelPlanningHarness,
    harness_topology,
)
from agent_app.tools.errors import TaskCancelledError


logger = logging.getLogger(__name__)
DIRECT_EXECUTION = __name__ == "__main__"
LIVE_TESTS_ENABLED = (
    DIRECT_EXECUTION or os.getenv("RUN_LIVE_API_TESTS") == "1"
)
STEP_LABELS = {
    "attraction": "1/4 景点搜索",
    "weather": "2/4 天气查询",
    "hotel": "3/4 酒店推荐",
    "planner": "4/4 行程与路线规划",
}


class _RecordingQueryAgent:
    def __init__(self, name, result, calls):
        self._name = name
        self._result = result
        self._calls = calls

    def run(self, query):
        self._calls.append((self._name, query))
        logger.info(
            "TravelPlanningHarnessTests 节点结果 | step=%s | result=%s",
            STEP_LABELS[self._name],
            self._result,
        )
        return self._result


class _RecordingPlannerAgent:
    def __init__(self, result, calls):
        self._result = result
        self._calls = calls

    def run(self, **inputs):
        self._calls.append(("planner", inputs))
        logger.info(
            "TravelPlanningHarnessTests 节点结果 | step=%s | result=%s",
            STEP_LABELS["planner"],
            self._result,
        )
        return self._result


class _SequencePlannerAgent:
    def __init__(self, results, calls):
        self._results = list(results)
        self._calls = calls

    def run(self, **inputs):
        self._calls.append(("planner", inputs))
        return self._results.pop(0)


class _BarrierQueryAgent:
    def __init__(self, name, barrier, calls):
        self._name = name
        self._barrier = barrier
        self._calls = calls

    def run(self, query):
        self._calls.append((self._name, query))
        self._barrier.wait(timeout=1)
        return f"{self._name}结果"


class _FailingQueryAgent:
    def __init__(self, exc):
        self._exc = exc

    def run(self, _query):
        raise self._exc


class _RecordingGraph:
    def __init__(self):
        self.input = None
        self.config = None

    def invoke(self, graph_input, config=None):
        self.input = graph_input
        self.config = config
        return {**graph_input, "final_plan": "观测测试计划"}


class TravelPlanningHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Show this test's per-step result logs without changing root logs."""
        cls._previous_log_level = logger.level
        cls._previous_propagate = logger.propagate
        cls._log_handler = logging.StreamHandler()
        cls._log_handler.setFormatter(
            logging.Formatter("%(levelname)s | %(name)s | %(message)s")
        )
        logger.addHandler(cls._log_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    @classmethod
    def tearDownClass(cls):
        logger.removeHandler(cls._log_handler)
        logger.setLevel(cls._previous_log_level)
        logger.propagate = cls._previous_propagate

    def test_runs_parallel_research_then_synthesis_and_returns_plan(self):
        calls = []
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction",
                "景点结果",
                calls,
            ),
            weather_agent=_RecordingQueryAgent(
                "weather",
                "天气结果",
                calls,
            ),
            hotel_agent=_RecordingQueryAgent(
                "hotel",
                "酒店结果",
                calls,
            ),
            planner_agent=_RecordingPlannerAgent(
                "最终行程",
                calls,
            ),
        )

        with self.assertLogs(
            "agent_app.harness.travel_planning",
            level="INFO",
        ) as logs:
            result = harness.run("  北京历史文化三日游，经济型酒店  ")

        request = "北京历史文化三日游，经济型酒店"
        self.assertEqual("最终行程", result)
        self.assertEqual(
            {
                ("attraction", request),
                ("weather", request),
                ("hotel", request),
            },
            set(calls[:3]),
        )
        self.assertEqual(
            (
                "planner",
                {
                    "original_request": request,
                    "attractions": "景点结果",
                    "weather": "天气结果",
                    "hotels": "酒店结果",
                },
            ),
            calls[-1],
        )
        log_output = "\n".join(logs.output)
        self.assertIn("node=attraction_research", log_output)
        self.assertIn("node=weather_research", log_output)
        self.assertIn("node=hotel_research", log_output)
        self.assertIn("node=itinerary_synthesis", log_output)
        self.assertIn("TravelPlanningHarness 完成", log_output)

    def test_weather_failure_uses_safe_degraded_result(self):
        calls = []
        events = []
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点结果", calls
            ),
            weather_agent=_FailingQueryAgent(TimeoutError("天气超时")),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店结果", calls),
            planner_agent=_RecordingPlannerAgent(
                '{"daily_itinerary": []}', calls
            ),
            progress_callback=lambda event_type, payload: events.append(
                (event_type, payload)
            ),
        )

        harness.run(
            "北京一日游",
            request_data={
                "destination_city": "北京",
                "start_date": "2099-08-18",
                "end_date": "2099-08-18",
            },
        )

        planner_input = calls[-1][1]
        weather = json.loads(planner_input["weather"])
        self.assertEqual([], weather["daily_forecasts"])
        self.assertIn("天气服务暂时不可用", weather["data_notes"][0])
        degraded = [
            payload
            for event_type, payload in events
            if event_type == "node.degraded"
        ]
        self.assertEqual(1, len(degraded))
        self.assertEqual("TOOL_TIMEOUT", degraded[0]["error_code"])

    def test_cancel_prevents_additional_graph_work(self):
        calls = []
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点", calls
            ),
            weather_agent=_RecordingQueryAgent("weather", "天气", calls),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店", calls),
            planner_agent=_RecordingPlannerAgent("计划", calls),
        )

        harness.cancel()

        with self.assertRaises(TaskCancelledError):
            harness.run("北京一日游")
        self.assertEqual([], calls)

    def test_compiles_expected_langgraph_topology(self):
        calls = []
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "天气", calls
            ),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店", calls),
            planner_agent=_RecordingPlannerAgent("计划", calls),
        )

        graph = harness.graph.get_graph()
        self.assertEqual(
            {
                "__start__",
                "change_analysis",
                "attraction_research",
                "weather_research",
                "hotel_research",
                "itinerary_synthesis",
                "__end__",
            },
            set(graph.nodes),
        )
        topology = harness_topology()
        self.assertEqual(
            "change-analysis-then-selective-research",
            topology["execution_model"],
        )
        self.assertEqual(5, len(topology["nodes"]))

    def test_trace_metadata_groups_turns_by_session(self):
        calls = []
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "天气", calls
            ),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店", calls),
            planner_agent=_RecordingPlannerAgent("计划", calls),
        )
        graph = _RecordingGraph()
        harness.graph = graph

        result = harness.run(
            "调整第二天行程",
            previous_plan={"plan_version": "1.0"},
            trace_metadata={
                "task_id": "task_trace",
                "session_id": "session_trace",
                "target_revision": 2,
            },
        )

        self.assertEqual("观测测试计划", result)
        self.assertEqual(
            "Travel Planning Harness",
            graph.config["run_name"],
        )
        self.assertIn("revision", graph.config["tags"])
        self.assertEqual(
            "session_trace",
            graph.config["metadata"]["thread_id"],
        )
        self.assertEqual(
            "task_trace",
            graph.config["metadata"]["task_id"],
        )
        self.assertEqual(
            2,
            graph.config["metadata"]["target_revision"],
        )

    def test_revision_reruns_only_changed_research_nodes(self):
        calls = []
        events = []
        revised_plan_json = json.dumps(
            {
                "request_summary": {
                    "budget_cny": 5000,
                    "hotel_requirement": "经济型",
                },
                "budget_summary": {"total_budget": 5000},
                "selected_hotel": {"name": "新酒店"},
            },
            ensure_ascii=False,
        )
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "新景点", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "新天气", calls
            ),
            hotel_agent=_RecordingQueryAgent(
                "hotel", "新酒店", calls
            ),
            planner_agent=_RecordingPlannerAgent(
                revised_plan_json,
                calls,
            ),
            progress_callback=lambda event_type, payload: events.append(
                (event_type, payload.get("stage"), payload.get("message"))
            ),
        )
        previous_request = {
            "destination_city": "北京",
            "start_date": "2099-08-01",
            "end_date": "2099-08-03",
            "preferences": ["历史文化"],
            "budget_cny": 5000,
            "accommodation_type": "经济型",
        }
        current_request = {
            **previous_request,
            "additional_requirements": "换个酒店，其他安排不变",
            "previous_plan_id": "plan_previous",
            "session_id": "session_test",
        }
        previous_plan = {
            "selected_hotel": {"name": "上一版酒店"},
            "daily_itinerary": [{"day": 1}],
            "weather_summary": [{"date": "2099-08-01"}],
        }
        previous_context = {
            "request": previous_request,
            "attractions": "上一版景点结果",
            "weather": "上一版天气结果",
            "hotels": "上一版酒店结果",
        }

        result = harness.run(
            "换个酒店，其他安排不变",
            request_data=current_request,
            previous_plan=previous_plan,
            previous_context=previous_context,
        )

        self.assertEqual(revised_plan_json, result)
        specialist_calls = [
            call for call in calls if call[0] != "planner"
        ]
        self.assertEqual(1, len(specialist_calls))
        self.assertEqual("hotel", specialist_calls[0][0])
        self.assertIn('"mode": "revision"', specialist_calls[0][1])
        planner_inputs = calls[-1][1]
        self.assertEqual("上一版景点结果", planner_inputs["attractions"])
        self.assertEqual("上一版天气结果", planner_inputs["weather"])
        self.assertEqual("新酒店", planner_inputs["hotels"])
        self.assertEqual(previous_plan, planner_inputs["previous_plan"])
        self.assertTrue(planner_inputs["revision_mode"])
        self.assertFalse(
            planner_inputs["change_analysis"]["rerun"]["attraction"]
        )
        self.assertTrue(
            planner_inputs["change_analysis"]["rerun"]["hotel"]
        )
        skipped_stages = {
            stage
            for event_type, stage, _message in events
            if event_type == "node.skipped"
        }
        self.assertEqual({"attraction", "weather"}, skipped_stages)
        context = harness.planning_context()
        self.assertEqual("上一版景点结果", context["attractions"])
        self.assertEqual("新酒店", context["hotels"])

    def test_initial_plan_retries_when_days_repeat_a_place(self):
        calls = []
        events = []
        duplicate_plan = json.dumps(
            {
                "daily_itinerary": [
                    {
                        "day": 1,
                        "schedule": [{"place_name": "故宫博物院"}],
                        "routes": [{"sequence": 1}],
                    },
                    {
                        "day": 2,
                        "schedule": [{"place_name": "故宫博物院"}],
                        "routes": [{"sequence": 1}],
                    },
                ]
            },
            ensure_ascii=False,
        )
        corrected_plan = json.dumps(
            {
                "daily_itinerary": [
                    {
                        "day": 1,
                        "schedule": [{"place_name": "故宫博物院"}],
                        "routes": [{"sequence": 1}],
                    },
                    {
                        "day": 2,
                        "schedule": [{"place_name": "颐和园"}],
                        "routes": [{"sequence": 1}],
                    },
                ]
            },
            ensure_ascii=False,
        )
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "天气", calls
            ),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店", calls),
            planner_agent=_SequencePlannerAgent(
                [duplicate_plan, corrected_plan], calls
            ),
            progress_callback=lambda event_type, payload: events.append(
                (event_type, payload.get("message"))
            ),
        )

        result = harness.run(
            "北京两日游",
            request_data={"destination_city": "北京"},
        )

        self.assertEqual(corrected_plan, result)
        planner_calls = [call for call in calls if call[0] == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "同时出现在第 1 天和第 2 天",
            planner_calls[1][1]["original_request"],
        )
        self.assertTrue(
            any(event_type == "plan.validation" for event_type, _ in events)
        )

    def test_initial_plan_retries_when_routes_do_not_cover_schedule(self):
        calls = []
        incomplete = json.dumps(
            {
                "daily_itinerary": [
                    {
                        "day": 1,
                        "schedule": [
                            {"place_name": "故宫博物院"},
                            {"place_name": "景山公园"},
                        ],
                        "routes": [{"sequence": 1}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        corrected = json.dumps(
            {
                "daily_itinerary": [
                    {
                        "day": 1,
                        "schedule": [
                            {"place_name": "故宫博物院"},
                            {"place_name": "景山公园"},
                        ],
                        "routes": [{"sequence": 1}, {"sequence": 2}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        harness = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent("attraction", "景点", calls),
            weather_agent=_RecordingQueryAgent("weather", "天气", calls),
            hotel_agent=_RecordingQueryAgent("hotel", "酒店", calls),
            planner_agent=_SequencePlannerAgent([incomplete, corrected], calls),
        )

        result = harness.run(
            "北京一日游", request_data={"destination_city": "北京"}
        )

        self.assertEqual(corrected, result)
        planner_calls = [call for call in calls if call[0] == "planner"]
        self.assertEqual(2, len(planner_calls))
        self.assertIn(
            "2 个日程地点，但只有 1 条路线",
            planner_calls[1][1]["original_request"],
        )

    def test_research_nodes_execute_concurrently_before_synthesis(self):
        calls = []
        barrier = Barrier(3)
        harness = TravelPlanningHarness(
            attraction_agent=_BarrierQueryAgent(
                "attraction", barrier, calls
            ),
            weather_agent=_BarrierQueryAgent("weather", barrier, calls),
            hotel_agent=_BarrierQueryAgent("hotel", barrier, calls),
            planner_agent=_RecordingPlannerAgent("最终计划", calls),
        )

        result = harness.run("北京一日游")

        self.assertEqual("最终计划", result)
        self.assertEqual(
            {"attraction", "weather", "hotel"},
            {name for name, _value in calls[:3]},
        )
        self.assertEqual("planner", calls[-1][0])

    def test_rejects_empty_request_before_calling_specialists(self):
        calls = []
        agent = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction",
                "",
                calls,
            ),
            weather_agent=_RecordingQueryAgent(
                "weather",
                "",
                calls,
            ),
            hotel_agent=_RecordingQueryAgent(
                "hotel",
                "",
                calls,
            ),
            planner_agent=_RecordingPlannerAgent("", calls),
        )

        with self.assertRaisesRegex(ValueError, "旅行需求不能为空"):
            agent.run("   ")

        self.assertEqual([], calls)

    def test_close_releases_shared_resources_once(self):
        calls = []
        close_calls = []
        agent = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "", calls
            ),
            hotel_agent=_RecordingQueryAgent("hotel", "", calls),
            planner_agent=_RecordingPlannerAgent("", calls),
            close_callback=lambda: close_calls.append("closed"),
        )

        agent.close()
        agent.close()

        self.assertEqual(["closed"], close_calls)

    def test_finalize_plan_uses_shared_location_enricher(self):
        calls = []
        agent = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "", calls
            ),
            hotel_agent=_RecordingQueryAgent("hotel", "", calls),
            planner_agent=_RecordingPlannerAgent("", calls),
            plan_enricher=lambda plan: {
                **plan,
                "locations_enriched": True,
            },
        )

        result = agent.finalize_plan({"plan_version": "1.0"})

        self.assertTrue(result["locations_enriched"])

    def test_emits_harness_and_node_progress_events(self):
        calls = []
        events = []
        agent = TravelPlanningHarness(
            attraction_agent=_RecordingQueryAgent(
                "attraction", "景点", calls
            ),
            weather_agent=_RecordingQueryAgent(
                "weather", "天气", calls
            ),
            hotel_agent=_RecordingQueryAgent(
                "hotel", "酒店", calls
            ),
            planner_agent=_RecordingPlannerAgent("计划", calls),
            progress_callback=lambda event_type, payload: events.append(
                (event_type, payload.get("stage"))
            ),
        )

        result = agent.run("北京一日游")

        self.assertEqual("计划", result)
        self.assertEqual(("harness.started", None), events[0])
        self.assertEqual(("harness.completed", None), events[-1])
        for stage in ("attraction", "weather", "hotel", "planner"):
            self.assertIn(("node.started", stage), events)
            self.assertIn(("node.completed", stage), events)

    @unittest.skipUnless(
        LIVE_TESTS_ENABLED,
        "设置 RUN_LIVE_API_TESTS=1 后才会发送真实 API 请求",
    )
    def test_travel_plan_agent_runs_end_to_end(self):
        """Call all four agents, the LLM, and Amap MCP services."""
        settings = Settings.from_env(PROJECT_ROOT / ".env")
        if not settings.amap_api_key:
            self.skipTest("未配置 AMAP_API_KEY，跳过真实工作流测试")

        configure_logging("INFO")
        bounded_settings = replace(
            settings,
            timeout_seconds=90.0,
            max_retries=max(settings.max_retries, 1),
        )
        with build_travel_plan_agent(
            bounded_settings,
            max_iterations=10,
        ) as agent:
            with self.assertLogs(
                "agent_app.harness.travel_planning",
                level="INFO",
            ) as workflow_logs:
                result = agent.run(
                    "请制定北京一日历史文化游计划，"
                    "只安排故宫和中国国家博物馆，"
                    "预算1500元，住宿选择经济型酒店。"
                )

        completion_logs = [
            entry
            for entry in workflow_logs.output
            if "LangGraph 节点完成" in entry
        ]
        for entry in completion_logs:
            logger.info(
                "TravelPlanningHarnessTests 端到端节点结果 | %s",
                entry,
            )

        self.assertEqual(4, len(completion_logs))
        self.assertTrue(
            any("node=attraction_research" in entry for entry in completion_logs)
        )
        self.assertTrue(
            any("node=weather_research" in entry for entry in completion_logs)
        )
        self.assertTrue(
            any("node=hotel_research" in entry for entry in completion_logs)
        )
        self.assertIn("node=itinerary_synthesis", completion_logs[-1])

        try:
            plan = json.loads(result)
        except json.JSONDecodeError as exc:
            self.fail(f"PlannerAgent 未返回合法 JSON：{exc}")
        self.assertEqual(
            "北京",
            plan["request_summary"]["destination_city"],
        )
        self.assertEqual(1, plan["request_summary"]["days"])
        self.assertGreaterEqual(len(plan["daily_itinerary"]), 1)
        routes = [
            route
            for day in plan["daily_itinerary"]
            for route in day["routes"]
        ]
        self.assertGreaterEqual(len(routes), 1)
        for route in routes:
            self.assertIn("walking", route)
            self.assertIn("driving", route)
            self.assertIn("public_transit", route)


if __name__ == "__main__":
    unittest.main(verbosity=2)
