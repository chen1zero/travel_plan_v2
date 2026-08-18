"""LangGraph harness for the multi-agent travel planning workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import re
from threading import Event
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, cast

from langgraph.graph import END, START, StateGraph

from agent_app.harness.state import TravelPlanState
from agent_app.harness.revision_validation import (
    validate_generated_plan,
    validate_revision_result,
)
from agent_app.shared.logging import preview
from agent_app.tools.errors import (
    TaskCancelledError,
    classify_failure,
)


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, Mapping[str, Any]], None]


class QueryAgent(Protocol):
    """One specialist that accepts the original travel request."""

    def run(self, query: str) -> str:
        ...


class PlanningAgent(Protocol):
    """Planner capability required by the synthesis node."""

    def run(
        self,
        original_request: str,
        attractions: str,
        weather: str,
        hotels: str,
        previous_plan: Optional[Dict[str, Any]] = None,
        change_analysis: Optional[Dict[str, Any]] = None,
        revision_mode: bool = False,
    ) -> str:
        ...


@dataclass(frozen=True)
class HarnessNode:
    """Stable graph metadata shared with API and browser clients."""

    key: str
    stage: str
    title: str
    description: str
    group: str


HARNESS_NODES = (
    HarnessNode(
        key="change_analysis",
        stage="analysis",
        title="变更分析",
        description="比较本轮要求与上一版计划，确定需要重跑的研究节点",
        group="control",
    ),
    HarnessNode(
        key="attraction_research",
        stage="attraction",
        title="景点研究",
        description="按旅行偏好检索并筛选目的地景点",
        group="research",
    ),
    HarnessNode(
        key="weather_research",
        stage="weather",
        title="天气研究",
        description="核对旅行日期的天气与出行条件",
        group="research",
    ),
    HarnessNode(
        key="hotel_research",
        stage="hotel",
        title="住宿研究",
        description="结合预算、住宿类型和位置搜索酒店",
        group="research",
    ),
    HarnessNode(
        key="itinerary_synthesis",
        stage="planner",
        title="行程合成",
        description="汇总研究结果并逐段比较交通路线",
        group="synthesis",
    ),
)
HARNESS_EDGES = (
    {"source": "start", "target": "change_analysis"},
    {"source": "change_analysis", "target": "attraction_research"},
    {"source": "change_analysis", "target": "weather_research"},
    {"source": "change_analysis", "target": "hotel_research"},
    {"source": "attraction_research", "target": "itinerary_synthesis"},
    {"source": "weather_research", "target": "itinerary_synthesis"},
    {"source": "hotel_research", "target": "itinerary_synthesis"},
    {"source": "itinerary_synthesis", "target": "complete"},
)


def harness_topology() -> Dict[str, Any]:
    """Return a JSON-safe description of the production graph."""
    return {
        "name": "travel-planning-harness",
        "execution_model": "change-analysis-then-selective-research",
        "nodes": [asdict(node) for node in HARNESS_NODES],
        "edges": [dict(edge) for edge in HARNESS_EDGES],
    }


_ATTRACTION_CHANGE_PATTERN = re.compile(
    r"(景点|地点|夜景|博物馆|公园|古迹|街区).{0,18}"
    r"(增加|新增|多加|加上|添加|安排|替换|换|删除|减少|去掉|取消|重查)"
    r"|(增加|新增|多加|加上|添加|安排|替换|换|删除|减少|去掉|取消|重查)"
    r".{0,18}(景点|地点|夜景|博物馆|公园|古迹|街区)"
)
_HOTEL_CHANGE_PATTERN = re.compile(
    r"(酒店|住宿|住处|民宿).{0,18}(换|替换|调整|重查|重新|不要|改)"
    r"|(换|替换|调整|重查|重新|不要|改住|换住).{0,18}"
    r"(酒店|住宿|住处|民宿|经济型|舒适型|豪华型)"
)
_WEATHER_CHANGE_PATTERN = re.compile(
    r"(天气|预报).*(重查|重新|更新|查询)"
    r"|(重查|重新|更新|查询).*(天气|预报)"
)


def analyze_request_changes(
    current_request: Mapping[str, Any],
    previous_context: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return a deterministic selective-execution decision."""
    if not previous_context:
        return _initial_change_analysis()
    previous_request = previous_context.get("request")
    if not isinstance(previous_request, Mapping):
        return _initial_change_analysis(
            "上一版缺少结构化研究上下文，需要补齐全部研究结果"
        )

    rerun = {
        "attraction": False,
        "weather": False,
        "hotel": False,
    }
    reasons: Dict[str, list[str]] = {
        "attraction": [],
        "weather": [],
        "hotel": [],
    }

    def changed(field: str) -> bool:
        return current_request.get(field) != previous_request.get(field)

    if changed("destination_city") or changed("destination_adcode"):
        for stage in rerun:
            rerun[stage] = True
            reasons[stage].append("目的地发生变化")
    if changed("preferences"):
        rerun["attraction"] = True
        reasons["attraction"].append("旅行偏好发生变化")
    if changed("start_date") or changed("end_date"):
        rerun["weather"] = True
        reasons["weather"].append("旅行日期发生变化")
    if changed("accommodation_type"):
        rerun["hotel"] = True
        reasons["hotel"].append("住宿类型发生变化")
    if changed("budget_cny"):
        rerun["hotel"] = True
        reasons["hotel"].append("旅行预算发生变化")

    instruction = str(
        current_request.get("additional_requirements") or ""
    ).strip()
    if _ATTRACTION_CHANGE_PATTERN.search(instruction):
        rerun["attraction"] = True
        reasons["attraction"].append("新增要求涉及景点候选变化")
    if _HOTEL_CHANGE_PATTERN.search(instruction):
        rerun["hotel"] = True
        reasons["hotel"].append("新增要求涉及住宿变化")
    if _WEATHER_CHANGE_PATTERN.search(instruction):
        rerun["weather"] = True
        reasons["weather"].append("用户要求更新天气信息")

    context_keys = {
        "attraction": "attractions",
        "weather": "weather",
        "hotel": "hotels",
    }
    for stage, context_key in context_keys.items():
        value = previous_context.get(context_key)
        if not isinstance(value, str) or not value.strip():
            rerun[stage] = True
            reasons[stage].append("上一版缺少可复用研究结果")

    rerun_titles = [
        title
        for stage, title in (
            ("attraction", "景点"),
            ("weather", "天气"),
            ("hotel", "住宿"),
        )
        if rerun[stage]
    ]
    reused_titles = [
        title
        for stage, title in (
            ("attraction", "景点"),
            ("weather", "天气"),
            ("hotel", "住宿"),
        )
        if not rerun[stage]
    ]
    summary = (
        f"将重跑：{'、'.join(rerun_titles) or '无'}；"
        f"复用上一版：{'、'.join(reused_titles) or '无'}；"
        "行程合成节点始终运行"
    )
    return {
        "mode": "revision",
        "rerun": rerun,
        "reasons": reasons,
        "summary": summary,
    }


def _initial_change_analysis(reason: str = "首次规划需要完整研究") -> Dict[str, Any]:
    return {
        "mode": "initial",
        "rerun": {
            "attraction": True,
            "weather": True,
            "hotel": True,
        },
        "reasons": {
            "attraction": [reason],
            "weather": [reason],
            "hotel": [reason],
        },
        "summary": "首次规划：并行执行景点、天气和住宿研究",
    }


class TravelPlanningHarness:
    """Run travel specialists through a compiled LangGraph StateGraph."""

    def __init__(
        self,
        attraction_agent: QueryAgent,
        weather_agent: QueryAgent,
        hotel_agent: QueryAgent,
        planner_agent: PlanningAgent,
        close_callback: Optional[Callable[[], None]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        plan_enricher: Optional[
            Callable[[Dict[str, Any]], Dict[str, Any]]
        ] = None,
    ) -> None:
        self._attraction_agent = attraction_agent
        self._weather_agent = weather_agent
        self._hotel_agent = hotel_agent
        self._planner_agent = planner_agent
        self._close_callback = close_callback
        self._progress_callback = progress_callback
        self._plan_enricher = plan_enricher
        self._closed = False
        self._cancelled = Event()
        self._last_state: TravelPlanState = {}
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(TravelPlanState)
        builder.add_node("change_analysis", self._analyze_changes)
        builder.add_node("attraction_research", self._research_attractions)
        builder.add_node("weather_research", self._research_weather)
        builder.add_node("hotel_research", self._research_hotels)
        builder.add_node("itinerary_synthesis", self._synthesize_itinerary)
        builder.add_edge(START, "change_analysis")
        builder.add_edge("change_analysis", "attraction_research")
        builder.add_edge("change_analysis", "weather_research")
        builder.add_edge("change_analysis", "hotel_research")
        builder.add_edge(
            [
                "attraction_research",
                "weather_research",
                "hotel_research",
            ],
            "itinerary_synthesis",
        )
        builder.add_edge("itinerary_synthesis", END)
        return builder.compile()

    def _emit(
        self,
        event_type: str,
        *,
        message: str,
        node: Optional[str] = None,
        stage: Optional[str] = None,
        **context: Any,
    ) -> None:
        if self._progress_callback is None:
            return
        payload = {"message": message}
        if node is not None:
            payload["node"] = node
        if stage is not None:
            payload["stage"] = stage
        payload.update(
            {
                key: value
                for key, value in context.items()
                if value is not None
            }
        )
        try:
            self._progress_callback(event_type, payload)
        except Exception:
            logger.exception(
                "TravelPlanningHarness 进度回调失败 | event_type=%s",
                event_type,
            )

    def _analyze_changes(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        node = "change_analysis"
        self._emit(
            "node.started",
            node=node,
            message="正在比较本轮要求与上一版规划",
        )
        analysis = analyze_request_changes(
            state.get("request_data", {}),
            state.get("previous_context"),
        )
        self._emit(
            "change.analysis",
            node=node,
            message=str(analysis["summary"]),
        )
        self._emit(
            "node.completed",
            node=node,
            message="变更分析已完成",
        )
        logger.info(
            "LangGraph 变更分析完成 | analysis=%s",
            preview(analysis, 1000),
        )
        return {"change_analysis": analysis}

    @staticmethod
    def _should_rerun(
        state: TravelPlanState,
        stage: str,
    ) -> bool:
        analysis = state.get("change_analysis", {})
        rerun = analysis.get("rerun")
        return not isinstance(rerun, Mapping) or bool(
            rerun.get(stage, True)
        )

    def _reuse_previous_result(
        self,
        state: TravelPlanState,
        *,
        node: str,
        stage: str,
        context_key: str,
        output_key: str,
        label: str,
    ) -> TravelPlanState:
        previous = state.get("previous_context", {}).get(context_key)
        if not isinstance(previous, str) or not previous.strip():
            raise RuntimeError(f"上一版缺少可复用的{label}研究结果")
        self._emit(
            "node.skipped",
            node=node,
            stage=stage,
            message=f"{label}条件未变化，复用上一版研究结果",
        )
        logger.info(
            "LangGraph 节点跳过并复用历史结果 | node=%s | stage=%s",
            node,
            stage,
        )
        return cast(TravelPlanState, {output_key: previous})

    @staticmethod
    def _revision_query(
        state: TravelPlanState,
        *,
        stage: str,
        context_key: str,
    ) -> str:
        analysis = state.get("change_analysis", {})
        reasons = analysis.get("reasons", {})
        stage_reasons = (
            reasons.get(stage, []) if isinstance(reasons, Mapping) else []
        )
        previous_plan = state.get("previous_plan", {})
        historical_plan_context: Any
        if stage == "attraction":
            historical_plan_context = previous_plan.get(
                "daily_itinerary", []
            )
        elif stage == "weather":
            historical_plan_context = previous_plan.get(
                "weather_summary", []
            )
        else:
            historical_plan_context = previous_plan.get(
                "selected_hotel", {}
            )
        payload = {
            "mode": "revision",
            "current_request": state["request"],
            "change_reasons": stage_reasons,
            "previous_specialist_result": state.get(
                "previous_context", {}
            ).get(context_key),
            "historical_plan_context": historical_plan_context,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _run_node(
        self,
        *,
        node: str,
        stage: str,
        start_message: str,
        complete_message: str,
        operation: Callable[[], str],
        output_key: str,
        fallback: Optional[Callable[[Exception], Optional[str]]] = None,
    ) -> TravelPlanState:
        logger.info("LangGraph 节点开始 | node=%s | stage=%s", node, stage)
        self._emit(
            "node.started",
            node=node,
            stage=stage,
            message=start_message,
        )
        try:
            self._ensure_active()
            result = operation()
            self._ensure_active()
        except Exception as exc:
            fallback_result = (
                fallback(exc)
                if fallback is not None
                and not isinstance(exc, TaskCancelledError)
                else None
            )
            if isinstance(fallback_result, str) and fallback_result.strip():
                failure = classify_failure(exc)
                logger.warning(
                    "LangGraph 节点降级 | node=%s | stage=%s "
                    "| error_code=%s | error=%s",
                    node,
                    stage,
                    failure.code,
                    exc,
                )
                self._emit(
                    "node.degraded",
                    node=node,
                    stage=stage,
                    error_code=failure.code,
                    retryable=failure.retryable,
                    degraded=True,
                    message=f"{complete_message}遇到异常，已使用安全降级结果",
                )
                return cast(
                    TravelPlanState,
                    {output_key: fallback_result},
                )
            failure = classify_failure(exc)
            self._emit(
                "node.failed",
                node=node,
                stage=stage,
                error_code=failure.code,
                retryable=failure.retryable,
                message=f"{complete_message}失败",
            )
            raise
        logger.info(
            "LangGraph 节点完成 | node=%s | result=%s",
            node,
            preview(result, 1000),
        )
        self._emit(
            "node.completed",
            node=node,
            stage=stage,
            message=complete_message,
        )
        return cast(TravelPlanState, {output_key: result})

    def _research_attractions(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        if not self._should_rerun(state, "attraction"):
            return self._reuse_previous_result(
                state,
                node="attraction_research",
                stage="attraction",
                context_key="attractions",
                output_key="attractions",
                label="景点",
            )
        query = (
            self._revision_query(
                state,
                stage="attraction",
                context_key="attractions",
            )
            if state.get("revision_mode")
            else state["request"]
        )
        return self._run_node(
            node="attraction_research",
            stage="attraction",
            start_message="正在根据偏好研究景点",
            complete_message="景点研究已完成",
            operation=lambda: self._attraction_agent.run(query),
            output_key="attractions",
        )

    def _research_weather(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        if not self._should_rerun(state, "weather"):
            return self._reuse_previous_result(
                state,
                node="weather_research",
                stage="weather",
                context_key="weather",
                output_key="weather",
                label="天气",
            )
        query = (
            self._revision_query(
                state,
                stage="weather",
                context_key="weather",
            )
            if state.get("revision_mode")
            else state["request"]
        )
        return self._run_node(
            node="weather_research",
            stage="weather",
            start_message="正在核对旅行日期天气",
            complete_message="天气研究已完成",
            operation=lambda: self._weather_agent.run(query),
            output_key="weather",
            fallback=lambda exc: self._weather_fallback(state, exc),
        )

    def _research_hotels(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        if not self._should_rerun(state, "hotel"):
            return self._reuse_previous_result(
                state,
                node="hotel_research",
                stage="hotel",
                context_key="hotels",
                output_key="hotels",
                label="住宿",
            )
        query = (
            self._revision_query(
                state,
                stage="hotel",
                context_key="hotels",
            )
            if state.get("revision_mode")
            else state["request"]
        )
        return self._run_node(
            node="hotel_research",
            stage="hotel",
            start_message="正在根据预算和位置研究住宿",
            complete_message="住宿研究已完成",
            operation=lambda: self._hotel_agent.run(query),
            output_key="hotels",
        )

    def _synthesize_itinerary(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        def run_planner(original_request: str) -> str:
            common = {
                "original_request": original_request,
                "attractions": state["attractions"],
                "weather": state["weather"],
                "hotels": state["hotels"],
            }
            if not state.get("revision_mode"):
                return self._planner_agent.run(**common)
            return self._planner_agent.run(
                **common,
                previous_plan=state.get("previous_plan", {}),
                change_analysis=state.get("change_analysis", {}),
                revision_mode=True,
            )

        def synthesize() -> str:
            result = run_planner(state["request"])
            if state.get("revision_mode"):
                errors = validate_revision_result(
                    result,
                    state.get("previous_plan", {}),
                    state.get("request_data", {}),
                )
            elif state.get("request_data"):
                errors = validate_generated_plan(result)
            else:
                return result
            if not errors:
                return result
            feedback = "；".join(errors)
            self._emit(
                (
                    "revision.validation"
                    if state.get("revision_mode")
                    else "plan.validation"
                ),
                node="itinerary_synthesis",
                stage="planner",
                message=(
                    "首次计划未通过行程质量检查，正在自动纠偏："
                    if not state.get("revision_mode")
                    else "首次修订未完全满足新增要求，正在纠偏："
                )
                + feedback,
            )
            retry_request = (
                f"{state['request']}\n"
                "上一份规划结果未通过服务端验收。必须逐项修正以下问题，"
                f"并重新输出完整计划 JSON：{feedback}"
            )
            retried = run_planner(retry_request)
            remaining_errors = (
                validate_revision_result(
                    retried,
                    state.get("previous_plan", {}),
                    state.get("request_data", {}),
                )
                if state.get("revision_mode")
                else validate_generated_plan(retried)
            )
            if remaining_errors:
                raise RuntimeError(
                    "规划结果未通过行程质量检查："
                    + "；".join(remaining_errors)
                )
            return retried

        return self._run_node(
            node="itinerary_synthesis",
            stage="planner",
            start_message="正在汇总研究结果并编排行程路线",
            complete_message="行程合成已完成",
            operation=synthesize,
            output_key="final_plan",
        )

    def run(
        self,
        request: str,
        *,
        request_data: Optional[Mapping[str, Any]] = None,
        previous_plan: Optional[Dict[str, Any]] = None,
        previous_context: Optional[Dict[str, Any]] = None,
        trace_metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Invoke the compiled graph and return the synthesized plan."""
        self._ensure_active()
        normalized_request = request.strip()
        if not normalized_request:
            raise ValueError("旅行需求不能为空")
        revision_mode = previous_plan is not None
        normalized_request_data = dict(request_data or {})
        normalized_trace_metadata = {
            key: value
            for key, value in dict(trace_metadata or {}).items()
            if value is not None
        }
        session_id = normalized_trace_metadata.get("session_id")
        if session_id:
            normalized_trace_metadata.setdefault(
                "thread_id", session_id
            )
        normalized_trace_metadata.update(
            {
                "graph": "travel-planning-harness",
                "planning_mode": (
                    "revision" if revision_mode else "initial"
                ),
            }
        )

        logger.info(
            "TravelPlanningHarness 启动 | request=%s",
            preview(normalized_request),
        )
        self._emit(
            "harness.started",
            message=(
                "LangGraph Harness 已进入修订模式，先分析本轮变化"
                if revision_mode
                else "LangGraph Harness 已启动，先分析规划需求"
            ),
        )
        final_state = self.graph.invoke(
            {
                "request": normalized_request,
                "request_data": normalized_request_data,
                "revision_mode": revision_mode,
                "previous_plan": previous_plan or {},
                "previous_context": previous_context or {},
            },
            config={
                "run_name": "Travel Planning Harness",
                "tags": [
                    "travel-planning",
                    "langgraph",
                    (
                        "revision"
                        if revision_mode
                        else "initial"
                    ),
                ],
                "metadata": normalized_trace_metadata,
            },
        )
        self._ensure_active()
        self._last_state = cast(TravelPlanState, final_state)
        final_plan = final_state.get("final_plan")
        if not isinstance(final_plan, str) or not final_plan.strip():
            raise RuntimeError("LangGraph Harness 未生成最终旅行计划")
        self._emit(
            "harness.completed",
            message="LangGraph Harness 已完成全部节点",
        )
        logger.info(
            "TravelPlanningHarness 完成 | result=%s",
            preview(final_plan, 1000),
        )
        return final_plan

    def planning_context(self) -> Dict[str, Any]:
        """Return reusable research state for the next session turn."""
        if not self._last_state:
            return {}
        return {
            "request": dict(self._last_state.get("request_data", {})),
            "attractions": self._last_state.get("attractions", ""),
            "weather": self._last_state.get("weather", ""),
            "hotels": self._last_state.get("hotels", ""),
            "change_analysis": dict(
                self._last_state.get("change_analysis", {})
            ),
        }

    def finalize_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Apply deterministic post-processing before API persistence."""
        self._ensure_active()
        if self._plan_enricher is None:
            return plan
        enriched = self._plan_enricher(plan)
        self._ensure_active()
        return enriched

    def cancel(self) -> None:
        """Cooperatively stop future work and release external resources."""
        self._cancelled.set()
        for agent in (
            self._attraction_agent,
            self._weather_agent,
            self._hotel_agent,
            self._planner_agent,
        ):
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                cancel()
        self.close()

    def _ensure_active(self) -> None:
        if self._cancelled.is_set():
            raise TaskCancelledError("旅行规划任务已取消")

    @staticmethod
    def _weather_fallback(
        state: TravelPlanState,
        _exc: Exception,
    ) -> str:
        request_data = state.get("request_data", {})
        city = str(request_data.get("destination_city") or "").strip()
        start_date = request_data.get("start_date")
        end_date = request_data.get("end_date")
        return json.dumps(
            {
                "city": city,
                "search_query": {"city": city},
                "forecast_scope": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "daily_forecasts": [],
                "travel_advice": [],
                "data_notes": [
                    "天气服务暂时不可用，本轮未使用未经验证的天气数据"
                ],
            },
            ensure_ascii=False,
        )

    def close(self) -> None:
        """Release resources owned by the harness exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            self._close_callback()

    def __enter__(self) -> "TravelPlanningHarness":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
