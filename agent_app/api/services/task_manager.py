"""Background execution adapter for the synchronous agent workflow."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
import json
import logging
from threading import Event, Timer
from typing import Any, Callable, Dict, Mapping, Optional, Protocol
import unicodedata
from uuid import uuid4

from pydantic import ValidationError

from agent_app.api.repository import SQLitePlanRepository
from agent_app.api.schemas import TravelPlanDocument, TravelRequest
from agent_app.api.services.follow_up import normalize_follow_up_request
from agent_app.api.services.memory import SessionMemoryManager
from agent_app.harness.revision_validation import (
    validate_itinerary_uniqueness,
)
from agent_app.tools.route_policy import apply_transport_policy_to_plan
from agent_app.tools.errors import (
    FailureDetails,
    MODEL_INVALID_OUTPUT,
    TASK_TIMEOUT,
    classify_failure,
)


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, Mapping[str, Any]], None]


class RunnableTravelHarness(Protocol):
    def run(
        self,
        request: str,
        *,
        request_data: Optional[Mapping[str, Any]] = None,
        previous_plan: Optional[Dict[str, Any]] = None,
        previous_context: Optional[Dict[str, Any]] = None,
        session_memory: Optional[Dict[str, Any]] = None,
        trace_metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        ...

    def close(self) -> None:
        ...


AgentFactory = Callable[[ProgressCallback], RunnableTravelHarness]


class TaskManager:
    """Submit and monitor long-running travel planning jobs."""

    def __init__(
        self,
        repository: SQLitePlanRepository,
        agent_factory: AgentFactory,
        max_workers: int = 2,
        task_timeout_seconds: int = 600,
        memory_manager: Optional[SessionMemoryManager] = None,
    ) -> None:
        self._repository = repository
        self._agent_factory = agent_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="travel-plan",
        )
        self._task_timeout_seconds = task_timeout_seconds
        self._memory_manager = memory_manager or SessionMemoryManager(
            repository
        )
        self._futures: Dict[str, Future[None]] = {}

    def restore_incomplete_tasks(self) -> None:
        for task in self._repository.list_incomplete_tasks():
            self.submit_existing(task["task_id"], task["request"])

    def create_task(
        self,
        request: TravelRequest,
        idempotency_key: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        request = normalize_follow_up_request(request)
        task, created = self._repository.create_task(
            request.model_dump(mode="json"),
            idempotency_key,
            user_id,
        )
        if created:
            self._repository.append_event(
                task["task_id"],
                "task.queued",
                "旅行规划任务已进入队列",
            )
            self.submit_existing(task["task_id"], task["request"])
        return task

    def submit_existing(
        self,
        task_id: str,
        request_data: Dict[str, Any],
    ) -> None:
        future = self._futures.get(task_id)
        if future is not None and not future.done():
            return
        self._futures[task_id] = self._executor.submit(
            self._run_task,
            task_id,
            request_data,
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run_task(
        self,
        task_id: str,
        request_data: Dict[str, Any],
    ) -> None:
        agent: Optional[RunnableTravelHarness] = None
        timed_out = Event()

        def mark_timeout() -> None:
            message = "旅行规划超时，请使用原需求重新尝试"
            error_id = f"err_{uuid4().hex}"
            failed = self._repository.fail_task(
                task_id,
                message,
                error_code=TASK_TIMEOUT,
                retryable=True,
                error_id=error_id,
            )
            if not failed:
                return
            timed_out.set()
            self._repository.append_event(
                task_id,
                "task.failed",
                message,
                metadata={
                    "error_code": TASK_TIMEOUT,
                    "retryable": True,
                    "error_id": error_id,
                },
            )
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    logger.exception(
                        "取消超时任务失败 | task_id=%s",
                        task_id,
                    )

        timeout_timer = Timer(
            self._task_timeout_seconds,
            mark_timeout,
        )
        timeout_timer.daemon = True
        timeout_timer.start()
        try:
            request = TravelRequest.model_validate(request_data)
            task = self._repository.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            previous_envelope = None
            previous_plan_id = task.get("previous_plan_id")
            if previous_plan_id:
                previous_envelope = self._repository.get_plan(
                    previous_plan_id
                )
                if previous_envelope is None:
                    raise ValueError("上一版旅行计划不存在")
            if not self._repository.set_task_running(task_id):
                return
            self._repository.append_event(
                task_id,
                "task.started",
                "旅行规划任务已开始",
            )
            session_memory = None
            if previous_envelope is not None:
                session_memory = self._memory_manager.build(
                    task_id=task_id,
                    session_id=str(task["session_id"]),
                    previous_envelope=previous_envelope,
                    current_request=request.model_dump(mode="json"),
                )

            def progress_callback(
                event_type: str,
                payload: Mapping[str, Any],
            ) -> None:
                if timed_out.is_set():
                    return
                stage = payload.get("stage")
                normalized_stage = (
                    stage
                    if stage
                    in {"attraction", "weather", "hotel", "planner"}
                    else None
                )
                if (
                    event_type in {"stage.started", "node.started"}
                    and normalized_stage is not None
                ):
                    self._repository.set_task_stage(
                        task_id,
                        normalized_stage,
                    )
                message = str(
                    payload.get("message") or "规划步骤已更新"
                )
                metadata_keys = {
                    "agent",
                    "node",
                    "tool",
                    "tool_call_id",
                    "iteration",
                    "attempt",
                    "max_attempts",
                    "duration_ms",
                    "error_code",
                    "retryable",
                    "exception_type",
                    "degraded",
                }
                self._repository.append_event(
                    task_id,
                    event_type,
                    message,
                    stage=normalized_stage,
                    metadata={
                        key: payload[key]
                        for key in metadata_keys
                        if payload.get(key) is not None
                    },
                )

            agent = self._agent_factory(progress_callback)
            raw_result = agent.run(
                request.to_agent_prompt(),
                request_data=request.model_dump(mode="json"),
                previous_plan=(
                    previous_envelope["plan"]
                    if previous_envelope
                    else None
                ),
                previous_context=(
                    previous_envelope.get("context", {})
                    if previous_envelope
                    else None
                ),
                session_memory=session_memory,
                trace_metadata={
                    "task_id": task_id,
                    "session_id": task["session_id"],
                    "previous_plan_id": previous_plan_id,
                    "target_revision": (
                        int(previous_envelope["revision"]) + 1
                        if previous_envelope
                        else 1
                    ),
                    "destination_city": request.destination_city,
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                    "source": "travel-api",
                    "memory_mode": (
                        session_memory.get("memory_mode")
                        if session_memory
                        else "initial"
                    ),
                    "memory_input_tokens": (
                        session_memory.get("memory_stats", {}).get(
                            "estimated_input_tokens"
                        )
                        if session_memory
                        else 0
                    ),
                },
            )
            if timed_out.is_set():
                return
            plan_data = _parse_planner_json(raw_result)
            _ensure_stable_ids(plan_data)
            _validate_request_coverage(plan_data, request)
            finalize_plan = getattr(agent, "finalize_plan", None)
            if callable(finalize_plan):
                plan_data = finalize_plan(plan_data)
            if timed_out.is_set():
                return
            validated_plan = TravelPlanDocument.model_validate(
                plan_data
            ).model_dump(mode="json")
            planning_context = {}
            get_planning_context = getattr(
                agent,
                "planning_context",
                None,
            )
            if callable(get_planning_context):
                planning_context = get_planning_context()
            _validate_research_provenance(
                validated_plan,
                planning_context,
            )
            if timed_out.is_set():
                return
            envelope = self._repository.complete_task(
                task_id,
                validated_plan,
                planning_context,
            )
            self._repository.append_event(
                task_id,
                "plan.completed",
                "旅行计划已生成",
                stage="planner",
                plan_id=envelope["plan_id"],
            )
        except Exception as exc:
            if timed_out.is_set():
                return
            logger.exception(
                "旅行规划任务失败 | task_id=%s",
                task_id,
            )
            message = _public_error_message(exc)
            failure = _public_failure_details(exc)
            error_id = f"err_{uuid4().hex}"
            failed = self._repository.fail_task(
                task_id,
                message,
                error_code=failure.code,
                retryable=failure.retryable,
                error_id=error_id,
            )
            if failed:
                self._repository.append_event(
                    task_id,
                    "task.failed",
                    message,
                    metadata={
                        "error_code": failure.code,
                        "retryable": failure.retryable,
                        "error_id": error_id,
                        "exception_type": failure.exception_type,
                    },
                )
        finally:
            timeout_timer.cancel()
            if agent is not None:
                try:
                    agent.close()
                except Exception:
                    logger.exception(
                        "关闭旅行规划资源失败 | task_id=%s",
                        task_id,
                    )


def _parse_planner_json(raw_result: str) -> Dict[str, Any]:
    content = raw_result.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("PlannerAgent 必须返回 JSON 对象")
    return value


def _ensure_stable_ids(plan: Dict[str, Any]) -> None:
    days = plan.get("daily_itinerary")
    if not isinstance(days, list):
        return
    for day_index, day in enumerate(days, start=1):
        if not isinstance(day, dict):
            continue
        schedule = day.get("schedule")
        if isinstance(schedule, list):
            for item_index, item in enumerate(schedule, start=1):
                if isinstance(item, dict) and not item.get(
                    "schedule_item_id"
                ):
                    item["schedule_item_id"] = (
                        f"item_{day_index}_{item_index}_{uuid4().hex[:8]}"
                    )
        routes = day.get("routes")
        if isinstance(routes, list):
            for route_index, route in enumerate(routes, start=1):
                if isinstance(route, dict) and not route.get("route_id"):
                    route["route_id"] = (
                        f"route_{day_index}_{route_index}_{uuid4().hex[:8]}"
                    )
                if isinstance(route, dict):
                    for mode_name in (
                        "walking",
                        "driving",
                        "public_transit",
                    ):
                        mode = route.get(mode_name)
                        if isinstance(mode, dict):
                            mode.setdefault("distance_km", None)
                            mode.setdefault("duration_minutes", None)
                            mode.setdefault("error", None)
    apply_transport_policy_to_plan(plan)


def _validate_request_coverage(
    plan: Dict[str, Any],
    request: TravelRequest,
) -> None:
    """Require one itinerary entry for every requested calendar day."""
    day_count = (request.end_date - request.start_date).days + 1
    expected_dates = [
        (request.start_date + timedelta(days=offset)).isoformat()
        for offset in range(day_count)
    ]
    days = plan.get("daily_itinerary")
    if not isinstance(days, list):
        raise IncompleteItineraryError("行程结果缺少每日安排")

    by_date: Dict[str, Dict[str, Any]] = {}
    for day in days:
        if not isinstance(day, dict):
            raise IncompleteItineraryError("每日行程格式错误")
        date_value = str(day.get("date") or "")
        if date_value in by_date:
            raise IncompleteItineraryError(
                f"行程日期重复：{date_value}"
            )
        by_date[date_value] = day

    missing_dates = [
        date_value
        for date_value in expected_dates
        if date_value not in by_date
    ]
    extra_dates = [
        date_value
        for date_value in by_date
        if date_value not in expected_dates
    ]
    if missing_dates or extra_dates:
        raise IncompleteItineraryError(
            "行程未完整覆盖所选日期"
            f"；缺少：{', '.join(missing_dates) or '无'}"
            f"；多出：{', '.join(extra_dates) or '无'}"
        )

    ordered_days = [by_date[date_value] for date_value in expected_dates]
    for index, day in enumerate(ordered_days, start=1):
        day["day"] = index
        schedule = day.get("schedule")
        if not isinstance(schedule, list) or not schedule:
            raise IncompleteItineraryError(
                f"第 {index} 天缺少景点安排"
            )
        for order, item in enumerate(schedule, start=1):
            if isinstance(item, dict):
                item["order"] = order
    plan["daily_itinerary"] = ordered_days

    duplicate_errors = validate_itinerary_uniqueness(plan)
    if duplicate_errors:
        raise InvalidItineraryError("；".join(duplicate_errors))

    summary = plan.get("request_summary")
    if not isinstance(summary, dict):
        raise IncompleteItineraryError("行程结果缺少需求摘要")
    summary.update(
        {
            "destination_city": request.destination_city,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "days": day_count,
            "budget_cny": request.budget_cny,
            "preferences": request.preferences,
            "hotel_requirement": request.accommodation_type,
        }
    )
    budget_summary = plan.get("budget_summary")
    if isinstance(budget_summary, dict):
        budget_summary["total_budget"] = request.budget_cny
        estimated_total = budget_summary.get("estimated_total")
        if isinstance(estimated_total, (int, float)) and not isinstance(
            estimated_total,
            bool,
        ):
            budget_summary["remaining"] = (
                request.budget_cny - estimated_total
            )


class IncompleteItineraryError(ValueError):
    """Raised when the Planner omits requested travel dates."""


class InvalidItineraryError(ValueError):
    """Raised when an itinerary violates cross-day quality rules."""


class UnverifiedPlanDataError(ValueError):
    """Raised when selected places are absent from specialist evidence."""


def _validate_research_provenance(
    plan: Mapping[str, Any],
    planning_context: Mapping[str, Any],
) -> None:
    """Reject model-invented places when structured research is available."""
    attraction_names = _structured_research_names(
        planning_context.get("attractions"),
        "attractions",
    )
    if attraction_names is not None:
        for day in plan.get("daily_itinerary", []):
            for item in day.get("schedule", []):
                name = str(item.get("place_name") or "")
                if _normalize_place_name(name) not in attraction_names:
                    raise UnverifiedPlanDataError(
                        f"景点未出现在研究结果中：{name}"
                    )

    hotel_names = _structured_research_names(
        planning_context.get("hotels"),
        "hotels",
    )
    if hotel_names is not None:
        selected_hotel = plan.get("selected_hotel", {})
        name = str(selected_hotel.get("name") or "")
        if _normalize_place_name(name) not in hotel_names:
            raise UnverifiedPlanDataError(
                f"酒店未出现在研究结果中：{name}"
            )


def _structured_research_names(
    value: Any,
    list_field: str,
) -> Optional[set[str]]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, Mapping):
        return None
    items = parsed.get(list_field)
    if not isinstance(items, list):
        return None
    names = {
        _normalize_place_name(str(item.get("name") or ""))
        for item in items
        if isinstance(item, Mapping)
    }
    return {name for name in names if name}


def _normalize_place_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] in {"L", "N"}
    )


def _public_error_message(exc: Exception) -> str:
    if isinstance(exc, IncompleteItineraryError):
        return "生成的行程未完整覆盖所选日期，请重新规划"
    if isinstance(exc, InvalidItineraryError):
        return "生成的行程存在跨天重复景点，请重新规划"
    if isinstance(exc, json.JSONDecodeError):
        return "行程规划结果格式错误，请重新尝试"
    if isinstance(exc, ValidationError):
        return "行程规划结果缺少必要字段，请重新尝试"
    failure = classify_failure(exc)
    if failure.code == "MODEL_REFUSAL":
        return "模型未能完成必要的数据查询，请重新尝试"
    if failure.code in {"TOOL_TIMEOUT", "NETWORK_ERROR"}:
        return "外部服务暂时不可用，请稍后重试"
    if failure.code == "INVALID_TOOL_RESULT":
        return "外部服务返回数据异常，请稍后重试"
    if failure.code == MODEL_INVALID_OUTPUT:
        return "行程规划结果格式错误，请重新尝试"
    return "旅行规划未完成，请稍后重试"


def _public_failure_details(exc: Exception) -> FailureDetails:
    if isinstance(
        exc,
        (
            IncompleteItineraryError,
            InvalidItineraryError,
            UnverifiedPlanDataError,
            json.JSONDecodeError,
            ValidationError,
        ),
    ):
        return FailureDetails(
            code=MODEL_INVALID_OUTPUT,
            retryable=True,
            message="模型返回的计划未通过结构校验",
            exception_type=type(exc).__name__,
        )
    return classify_failure(exc)
