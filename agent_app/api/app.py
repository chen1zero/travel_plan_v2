"""FastAPI application exposing the LangGraph travel harness."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, AsyncIterator, Callable, Dict, Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from agent_app.api.auth import (
    SQLiteAuthRepository,
    UsernameAlreadyExistsError,
)
from agent_app.api.config import APISettings
from agent_app.api.repository import (
    RevisionConflictError,
    SessionContextError,
    SQLitePlanRepository,
)
from agent_app.api.schemas import (
    AuthCredentials,
    AuthUserResponse,
    ForkSessionRequest,
    HealthResponse,
    ItineraryUpdateRequest,
    PlanEnvelope,
    PlanningSessionListResponse,
    PlanningSessionResponse,
    PlanningSessionTurnsResponse,
    PlanningEvent,
    PlanningTaskResponse,
    SessionUpdateRequest,
    TravelPlanDocument,
    TravelRequest,
)
from agent_app.api.services.routes import AmapRouteRebuilder
from agent_app.api.services.task_manager import (
    AgentFactory,
    TaskManager,
)
from agent_app.harness.travel_planning import harness_topology
from agent_app.harness.revision_validation import (
    validate_itinerary_uniqueness,
)
from agent_app.main import build_travel_planning_harness
from agent_app.observability.langsmith import langsmith_status
from agent_app.shared.config import Settings
from agent_app.tools.route_policy import apply_transport_policy_to_plan


RouteRebuilder = Callable[
    [Dict[str, Any], list[Dict[str, Any]]],
    list[Dict[str, Any]],
]


def create_app(
    *,
    settings: Optional[Settings] = None,
    api_settings: Optional[APISettings] = None,
    repository: Optional[SQLitePlanRepository] = None,
    agent_factory: Optional[AgentFactory] = None,
    route_rebuilder: Optional[RouteRebuilder] = None,
) -> FastAPI:
    """Build an injectable API application for production and tests."""
    resolved_api_settings = api_settings or APISettings.from_env()
    resolved_repository = repository or SQLitePlanRepository(
        resolved_api_settings.database_path
    )
    auth_repository = SQLiteAuthRepository(
        resolved_repository.database_path
    )

    resolved_settings = settings
    if agent_factory is None or route_rebuilder is None:
        resolved_settings = resolved_settings or Settings.from_env()
    if agent_factory is None:
        agent_factory = lambda callback: build_travel_planning_harness(
            resolved_settings,
            progress_callback=callback,
        )
    if route_rebuilder is None:
        route_rebuilder = AmapRouteRebuilder(resolved_settings)

    task_manager = TaskManager(
        resolved_repository,
        agent_factory,
        max_workers=resolved_api_settings.max_workers,
        task_timeout_seconds=(
            resolved_api_settings.task_timeout_seconds
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task_manager.restore_incomplete_tasks()
        yield
        task_manager.shutdown()

    app = FastAPI(
        title="智能旅行助手 API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_api_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
        ],
    )
    app.state.repository = resolved_repository
    app.state.auth_repository = auth_repository
    app.state.task_manager = task_manager
    app.state.route_rebuilder = route_rebuilder

    @app.get(
        "/api/health",
        response_model=HealthResponse,
        tags=["system"],
    )
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/harness", tags=["system"])
    def get_harness_topology() -> Dict[str, Any]:
        return harness_topology()

    @app.get("/api/observability", tags=["system"])
    def get_observability_status() -> Dict[str, Any]:
        return langsmith_status()

    def optional_user(request: Request) -> Optional[Dict[str, Any]]:
        token = request.cookies.get(
            resolved_api_settings.auth_cookie_name
        )
        if not token:
            return None
        return auth_repository.get_user_for_session(token)

    def require_user(
        user: Optional[Dict[str, Any]] = Depends(optional_user),
    ) -> Dict[str, Any]:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="请先登录",
            )
        return user

    @app.post(
        "/api/auth/register",
        response_model=AuthUserResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["auth"],
    )
    def register(
        payload: AuthCredentials,
        response: Response,
    ) -> AuthUserResponse:
        try:
            user = auth_repository.create_user(
                payload.username,
                payload.password,
            )
        except UsernameAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        token = auth_repository.create_session(
            user["user_id"],
            ttl_days=resolved_api_settings.auth_session_days,
        )
        _set_auth_cookie(response, token, resolved_api_settings)
        return AuthUserResponse.model_validate(user)

    @app.post(
        "/api/auth/login",
        response_model=AuthUserResponse,
        tags=["auth"],
    )
    def login(
        payload: AuthCredentials,
        response: Response,
    ) -> AuthUserResponse:
        user = auth_repository.authenticate(
            payload.username,
            payload.password,
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        token = auth_repository.create_session(
            user["user_id"],
            ttl_days=resolved_api_settings.auth_session_days,
        )
        _set_auth_cookie(response, token, resolved_api_settings)
        return AuthUserResponse.model_validate(user)

    @app.get(
        "/api/auth/me",
        response_model=AuthUserResponse,
        tags=["auth"],
    )
    def current_user(
        user: Dict[str, Any] = Depends(require_user),
    ) -> AuthUserResponse:
        return AuthUserResponse.model_validate(user)

    @app.post(
        "/api/auth/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["auth"],
    )
    def logout(request: Request, response: Response) -> Response:
        token = request.cookies.get(
            resolved_api_settings.auth_cookie_name
        )
        if token:
            auth_repository.delete_session(token)
        response.delete_cookie(
            resolved_api_settings.auth_cookie_name,
            path="/api",
            secure=resolved_api_settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.post(
        "/api/travel-plans",
        response_model=PlanningTaskResponse,
        status_code=202,
        tags=["travel-plans"],
    )
    def create_travel_plan(
        payload: TravelRequest,
        request: Request,
        user: Dict[str, Any] = Depends(require_user),
        idempotency_key: Optional[str] = Header(
            default=None,
            alias="Idempotency-Key",
            max_length=200,
        ),
    ) -> PlanningTaskResponse:
        try:
            task = task_manager.create_task(
                payload,
                idempotency_key,
                user["user_id"],
            )
        except RevisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionContextError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _task_response(task, request)

    @app.get(
        "/api/travel-plans/{task_id}",
        response_model=PlanningTaskResponse,
        tags=["travel-plans"],
    )
    def get_travel_plan_task(
        task_id: str,
        request: Request,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanningTaskResponse:
        task = resolved_repository.get_task(task_id, user["user_id"])
        if task is None:
            raise HTTPException(status_code=404, detail="规划任务不存在")
        return _task_response(task, request)

    @app.get(
        "/api/travel-plans/{task_id}/events",
        name="stream_task_events",
        tags=["travel-plans"],
    )
    async def stream_task_events(
        task_id: str,
        request: Request,
        user: Dict[str, Any] = Depends(require_user),
        last_event_id: Optional[str] = Header(
            default=None,
            alias="Last-Event-ID",
        ),
    ) -> StreamingResponse:
        if resolved_repository.get_task(task_id, user["user_id"]) is None:
            raise HTTPException(status_code=404, detail="规划任务不存在")
        try:
            after_event_id = int(last_event_id or "0")
        except ValueError:
            after_event_id = 0
        return StreamingResponse(
            _event_stream(
                request=request,
                repository=resolved_repository,
                task_id=task_id,
                after_event_id=after_event_id,
                poll_interval=(
                    resolved_api_settings.sse_poll_interval_seconds
                ),
                heartbeat_interval=(
                    resolved_api_settings.sse_heartbeat_seconds
                ),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get(
        "/api/plans/{plan_id}",
        response_model=PlanEnvelope,
        tags=["plans"],
    )
    def get_plan(
        plan_id: str,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanEnvelope:
        envelope = resolved_repository.get_plan(plan_id, user["user_id"])
        if envelope is None:
            raise HTTPException(status_code=404, detail="旅行计划不存在")
        apply_transport_policy_to_plan(envelope["plan"])
        return PlanEnvelope.model_validate(envelope)

    @app.get(
        "/api/sessions",
        response_model=PlanningSessionListResponse,
        tags=["sessions"],
    )
    def list_sessions(
        limit: int = Query(default=20, ge=1, le=50),
        cursor: Optional[str] = Query(default=None, max_length=500),
        query: Optional[str] = Query(default=None, max_length=80),
        include_archived: bool = Query(default=False),
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanningSessionListResponse:
        try:
            result = resolved_repository.list_sessions(
                limit=limit,
                cursor=cursor,
                query=query,
                include_archived=include_archived,
                user_id=user["user_id"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlanningSessionListResponse.model_validate(result)

    @app.get(
        "/api/sessions/{session_id}",
        response_model=PlanningSessionResponse,
        tags=["sessions"],
    )
    def get_session(
        session_id: str,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanningSessionResponse:
        session = resolved_repository.get_session(
            session_id,
            user["user_id"],
        )
        if session is None:
            raise HTTPException(status_code=404, detail="规划会话不存在")
        return PlanningSessionResponse.model_validate(session)

    @app.get(
        "/api/sessions/{session_id}/turns",
        response_model=PlanningSessionTurnsResponse,
        tags=["sessions"],
    )
    def get_session_turns(
        session_id: str,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanningSessionTurnsResponse:
        turns = resolved_repository.get_session_turns(
            session_id,
            user["user_id"],
        )
        if turns is None:
            raise HTTPException(status_code=404, detail="规划会话不存在")
        return PlanningSessionTurnsResponse.model_validate(
            {"session_id": session_id, "turns": turns}
        )

    @app.post(
        "/api/sessions/{session_id}/fork",
        response_model=PlanEnvelope,
        status_code=201,
        tags=["sessions"],
    )
    def fork_session(
        session_id: str,
        payload: ForkSessionRequest,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanEnvelope:
        try:
            envelope = resolved_repository.fork_session(
                session_id,
                payload.source_plan_id,
                user["user_id"],
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="指定的历史计划版本不存在",
            ) from exc
        return PlanEnvelope.model_validate(envelope)

    @app.patch(
        "/api/sessions/{session_id}",
        response_model=PlanningSessionResponse,
        tags=["sessions"],
    )
    def update_session(
        session_id: str,
        payload: SessionUpdateRequest,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanningSessionResponse:
        session = resolved_repository.update_session(
            session_id,
            title=payload.title,
            archived=payload.archived,
            user_id=user["user_id"],
        )
        if session is None:
            raise HTTPException(status_code=404, detail="规划会话不存在")
        return PlanningSessionResponse.model_validate(session)

    @app.patch(
        "/api/plans/{plan_id}/itinerary",
        response_model=PlanEnvelope,
        tags=["plans"],
    )
    async def update_itinerary(
        plan_id: str,
        payload: ItineraryUpdateRequest,
        user: Dict[str, Any] = Depends(require_user),
    ) -> PlanEnvelope:
        envelope = resolved_repository.get_plan(
            plan_id,
            user["user_id"],
        )
        if envelope is None:
            raise HTTPException(status_code=404, detail="旅行计划不存在")
        if envelope["revision"] != payload.base_revision:
            raise HTTPException(
                status_code=409,
                detail="行程已被更新，请刷新后再编辑",
            )

        plan_data = deepcopy(envelope["plan"])
        try:
            rebuilt_days = await run_in_threadpool(
                route_rebuilder,
                plan_data,
                payload.daily_itinerary,
            )
            plan_data["daily_itinerary"] = rebuilt_days
            apply_transport_policy_to_plan(plan_data)
            duplicate_errors = validate_itinerary_uniqueness(plan_data)
            if duplicate_errors:
                raise HTTPException(
                    status_code=422,
                    detail=duplicate_errors[0],
                )
            validated_plan = TravelPlanDocument.model_validate(
                plan_data
            ).model_dump(mode="json")
            updated = resolved_repository.update_plan(
                plan_id,
                payload.base_revision,
                validated_plan,
                user["user_id"],
            )
        except RevisionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="路线重新计算失败，请稍后重试",
            ) from exc
        return PlanEnvelope.model_validate(updated)

    return app


def _set_auth_cookie(
    response: Response,
    token: str,
    settings: APISettings,
) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        path="/api",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _task_response(
    task: Dict[str, Any],
    _request: Request,
) -> PlanningTaskResponse:
    # Keep the SSE URL relative to the configured browser API base. This
    # preserves the login cookie when development traffic uses Vite's proxy.
    events_url = f"travel-plans/{task['task_id']}/events"
    return PlanningTaskResponse(
        task_id=task["task_id"],
        session_id=task["session_id"],
        status=task["status"],
        events_url=events_url,
        plan_id=task.get("plan_id"),
        previous_plan_id=task.get("previous_plan_id"),
        current_stage=task.get("current_stage"),
        error_message=task.get("error_message"),
        error_code=task.get("error_code"),
        retryable=task.get("retryable"),
        error_id=task.get("error_id"),
    )


async def _event_stream(
    *,
    request: Request,
    repository: SQLitePlanRepository,
    task_id: str,
    after_event_id: int,
    poll_interval: float,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    latest_event_id = after_event_id
    seconds_since_heartbeat = 0.0
    while not await request.is_disconnected():
        events = await run_in_threadpool(
            repository.list_events,
            task_id,
            latest_event_id,
        )
        for event in events:
            latest_event_id = int(event["event_id"])
            validated = PlanningEvent.model_validate(event)
            yield (
                f"id: {validated.event_id}\n"
                f"event: {validated.type}\n"
                f"data: {validated.model_dump_json()}\n\n"
            )
        task = await run_in_threadpool(repository.get_task, task_id)
        if (
            task is None
            or task["status"] in {"completed", "failed"}
        ) and not events:
            break
        await asyncio.sleep(poll_interval)
        seconds_since_heartbeat += poll_interval
        if seconds_since_heartbeat >= heartbeat_interval:
            yield ": heartbeat\n\n"
            seconds_since_heartbeat = 0.0
