"""Command-line entrypoint for the LangGraph travel planning harness."""

import argparse
from functools import partial
import logging
import sys
from typing import Optional, Sequence

from agent_app.agents.attraction import AttractionSearchAgent
from agent_app.agents.base import MAX_AGENT_ITERATIONS
from agent_app.agents.hotel import HotelAgent
from agent_app.agents.planner import PlannerAgent
from agent_app.agents.weather import WeatherQueryAgent
from agent_app.api.services.locations import enrich_plan_map_data
from agent_app.harness.travel_planning import TravelPlanningHarness
from agent_app.infrastructure.amap_client import (
    AMAP_MCP_TEXT_SEARCH_TOOL_NAME,
    AMAP_MCP_WEATHER_TOOL_NAME,
    AmapMCPClient,
)
from agent_app.infrastructure.amap_web_client import AmapWebServiceClient
from agent_app.infrastructure.llm_client import OpenAICompatibleLLM
from agent_app.infrastructure.mcp_client import MCPClientError
from agent_app.shared.config import ConfigurationError, Settings
from agent_app.shared.logging import configure_logging
from agent_app.tools.route import (
    ROUTE_OPTIONS_TOOL_SCHEMA,
    compare_route_options,
)
from agent_app.tools.errors import execute_with_retry


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行并行研究与行程合成的 LangGraph 旅行 Harness"
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="需要 Agent 完成的任务；省略时会从标准输入读取",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="环境变量文件路径，默认：.env",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=MAX_AGENT_ITERATIONS,
        help="每个专家最大循环轮数，范围 1-10，默认：10",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="控制台日志级别，默认：INFO",
    )
    return parser


def _read_task(parts: Sequence[str]) -> str:
    if parts:
        return " ".join(parts).strip()
    if sys.stdin.isatty():
        return input("请输入任务：").strip()
    return sys.stdin.read().strip()


def build_travel_planning_harness(
    settings: Settings,
    max_iterations: int = MAX_AGENT_ITERATIONS,
    progress_callback=None,
) -> TravelPlanningHarness:
    """Build the production LangGraph travel planning harness."""
    amap_mcp = AmapMCPClient(
        api_key=settings.amap_api_key,
        command=settings.amap_mcp_command,
        timeout_seconds=settings.amap_mcp_timeout_seconds,
    )
    amap_web = AmapWebServiceClient(
        settings.amap_api_key,
        timeout_seconds=settings.amap_mcp_timeout_seconds,
    )
    try:
        available_tools = {
            tool["name"]: tool
            for tool in execute_with_retry(
                amap_mcp.list_tools,
                max_attempts=2,
            )
            if isinstance(tool.get("name"), str)
        }

        def add_mcp_tool(agent, tool_name):
            tool = available_tools.get(tool_name)
            if tool is None:
                raise MCPClientError(
                    f"高德 MCP Server 缺少工具：{tool_name}"
                )
            agent.add_tool(
                partial(amap_mcp.call_tool, tool_name),
                schema=tool,
            )

        weather_agent = WeatherQueryAgent(
            OpenAICompatibleLLM(settings),
            max_iterations=max_iterations,
        )
        if progress_callback is not None:
            weather_agent.set_event_callback(
                lambda event_type, payload: progress_callback(
                    event_type,
                    {**payload, "stage": "weather"},
                )
            )
        add_mcp_tool(weather_agent, AMAP_MCP_WEATHER_TOOL_NAME)

        attraction_agent = AttractionSearchAgent(
            OpenAICompatibleLLM(settings),
            max_iterations=max_iterations,
        )
        if progress_callback is not None:
            attraction_agent.set_event_callback(
                lambda event_type, payload: progress_callback(
                    event_type,
                    {**payload, "stage": "attraction"},
                )
            )
        add_mcp_tool(
            attraction_agent,
            AMAP_MCP_TEXT_SEARCH_TOOL_NAME,
        )

        hotel_agent = HotelAgent(
            OpenAICompatibleLLM(settings),
            max_iterations=max_iterations,
        )
        if progress_callback is not None:
            hotel_agent.set_event_callback(
                lambda event_type, payload: progress_callback(
                    event_type,
                    {**payload, "stage": "hotel"},
                )
            )
        add_mcp_tool(hotel_agent, AMAP_MCP_TEXT_SEARCH_TOOL_NAME)

        planner_agent = PlannerAgent(
            OpenAICompatibleLLM(settings),
            max_iterations=max_iterations,
        )
        if progress_callback is not None:
            planner_agent.set_event_callback(
                lambda event_type, payload: progress_callback(
                    event_type,
                    {**payload, "stage": "planner"},
                )
            )
        planner_agent.add_tool(
            partial(
                compare_route_options,
                amap_client=amap_mcp,
            ),
            schema=ROUTE_OPTIONS_TOOL_SCHEMA,
        )
        return TravelPlanningHarness(
            attraction_agent=attraction_agent,
            weather_agent=weather_agent,
            hotel_agent=hotel_agent,
            planner_agent=planner_agent,
            close_callback=amap_mcp.close,
            progress_callback=progress_callback,
            plan_enricher=partial(
                enrich_plan_map_data,
                amap_client=amap_mcp,
                route_geometry_client=amap_web,
            ),
        )
    except Exception:
        amap_mcp.close()
        raise


build_travel_plan_agent = build_travel_planning_harness


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    logger.info(
        "程序启动 | env_file=%s | log_level=%s",
        args.env_file,
        args.log_level,
    )

    try:
        settings = Settings.from_env(args.env_file)
        task = _read_task(args.task)
        if not task:
            raise ValueError("任务内容不能为空")

        with build_travel_planning_harness(
            settings,
            max_iterations=args.max_iterations,
        ) as agent:
            result = agent.run(task)
        print(result)
        logger.info("程序正常结束")
        return 0
    except (ConfigurationError, ValueError) as exc:
        logger.error("配置或输入错误 | error=%s", exc)
        print(f"配置或输入错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.warning("用户取消运行")
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
