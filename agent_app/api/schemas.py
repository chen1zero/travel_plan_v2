"""Pydantic request and response contracts shared by API routes."""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


PlanningStage = Literal["attraction", "weather", "hotel", "planner"]
TaskStatus = Literal["queued", "running", "completed", "failed"]
PlanSourceType = Literal["agent", "manual_edit", "fork"]


class TravelRequest(BaseModel):
    """Structured browser input converted into the existing agent prompt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    destination_city: str = Field(min_length=1, max_length=80)
    destination_adcode: Optional[str] = Field(default=None, max_length=20)
    start_date: date
    end_date: date
    preferences: List[str] = Field(min_length=1, max_length=5)
    budget_cny: float = Field(gt=0, le=10_000_000)
    accommodation_type: Literal["经济型", "舒适型", "豪华型", "不限"]
    additional_requirements: Optional[str] = Field(
        default=None,
        max_length=500,
    )
    session_id: Optional[str] = Field(default=None, max_length=80)
    previous_plan_id: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_planning_context(self) -> "TravelRequest":
        if self.session_id and not self.previous_plan_id:
            raise ValueError("session_id 必须与 previous_plan_id 一起提交")
        return self

    @field_validator("end_date")
    @classmethod
    def validate_date_range(
        cls,
        value: date,
        info,
    ) -> date:
        start_date = info.data.get("start_date")
        if start_date is not None and value < start_date:
            raise ValueError("end_date 不能早于 start_date")
        if start_date is not None and (value - start_date).days > 30:
            raise ValueError("单次旅行日期不能超过 31 天")
        return value

    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("start_date 不能早于今天")
        return value

    @field_validator("preferences")
    @classmethod
    def normalize_preferences(cls, values: List[str]) -> List[str]:
        normalized: List[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("preferences 不能包含空值")
            if len(item) > 40:
                raise ValueError("单个旅行偏好不能超过 40 个字符")
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("至少需要一个旅行偏好")
        return normalized

    def to_agent_prompt(self) -> str:
        requirements = self.additional_requirements or "无"
        prompt = (
            "请根据以下结构化需求制定旅行计划：\n"
            f"- 目的地城市：{self.destination_city}\n"
            f"- 旅行日期：{self.start_date.isoformat()} 至 "
            f"{self.end_date.isoformat()}\n"
            f"- 旅行偏好：{'、'.join(self.preferences)}\n"
            f"- 总预算：{self.budget_cny:g} 元人民币\n"
            f"- 住宿类型：{self.accommodation_type}\n"
            f"- 其他要求：{requirements}"
        )
        if self.previous_plan_id:
            prompt += "\n- 规划模式：基于上一版计划进行多轮修订"
        return prompt


class PlanningTaskResponse(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    events_url: str
    plan_id: Optional[str] = None
    previous_plan_id: Optional[str] = None
    current_stage: Optional[PlanningStage] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    retryable: Optional[bool] = None
    error_id: Optional[str] = None


class PlanningEvent(BaseModel):
    event_id: str
    type: Literal[
        "task.queued",
        "task.started",
        "harness.started",
        "node.started",
        "stage.started",
        "agent.iteration",
        "agent.recovering",
        "tool.started",
        "tool.retrying",
        "tool.completed",
        "tool.failed",
        "stage.completed",
        "node.completed",
        "node.skipped",
        "node.degraded",
        "node.failed",
        "change.analysis",
        "plan.validation",
        "revision.validation",
        "harness.completed",
        "plan.completed",
        "task.failed",
    ]
    timestamp: datetime
    stage: Optional[PlanningStage] = None
    message: str
    plan_id: Optional[str] = None
    agent: Optional[str] = None
    node: Optional[str] = None
    tool: Optional[str] = None
    tool_call_id: Optional[str] = None
    iteration: Optional[int] = None
    attempt: Optional[int] = None
    max_attempts: Optional[int] = None
    duration_ms: Optional[int] = None
    error_code: Optional[str] = None
    retryable: Optional[bool] = None
    error_id: Optional[str] = None
    degraded: Optional[bool] = None


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class RequestSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    destination_city: str = Field(min_length=1)
    start_date: date
    end_date: date
    days: int = Field(ge=1, le=31)
    budget_cny: float = Field(gt=0, le=10_000_000)
    preferences: List[str] = Field(min_length=1)
    hotel_requirement: str = Field(min_length=1)
    unresolved_fields: List[str]

    @model_validator(mode="after")
    def validate_summary_dates(self) -> "RequestSummary":
        expected_days = (self.end_date - self.start_date).days + 1
        if expected_days != self.days:
            raise ValueError("request_summary.days 与日期范围不一致")
        return self


class WeatherSummaryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: date
    day_weather: Optional[str]
    night_weather: Optional[str]
    min_temperature_c: Optional[float]
    max_temperature_c: Optional[float]
    advice: List[str]


class SelectedHotel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    price_cny_per_night: Optional[float] = Field(ge=0)
    booking_note: str
    location: Coordinate


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    schedule_item_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    time_slot: str = Field(min_length=1)
    place_name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    activity: str = Field(min_length=1)
    duration_minutes: Optional[float] = Field(ge=0)
    notes: List[str]
    location: Coordinate


class RouteEndpoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    city: str = Field(min_length=1)


class RouteMode(BaseModel):
    model_config = ConfigDict(extra="allow")

    available: bool
    distance_km: Optional[float] = Field(ge=0)
    duration_minutes: Optional[float] = Field(ge=0)
    error: Optional[str]
    polyline: Optional[List[Tuple[float, float]]] = None

    @model_validator(mode="after")
    def validate_unavailable_metrics(self) -> "RouteMode":
        if not self.available and (
            self.distance_km is not None
            or self.duration_minutes is not None
        ):
            raise ValueError("不可用路线的距离和时间必须为 null")
        if self.polyline:
            for longitude, latitude in self.polyline:
                if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError("路线折线坐标超出有效范围")
        return self


class PublicTransitRouteMode(RouteMode):
    walking_distance_km: Optional[float] = Field(ge=0)
    transfer_count: Optional[int] = Field(ge=0)
    transit_type: Literal["subway", "bus", "mixed", "rail", "unknown"]
    line_names: List[str]


class RouteSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    route_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    origin: RouteEndpoint
    destination: RouteEndpoint
    walking: RouteMode
    driving: RouteMode
    public_transit: PublicTransitRouteMode
    recommended_mode: Literal["walking", "driving", "public_transit"]
    recommendation_reason: str = Field(min_length=1)


class DailyCost(BaseModel):
    model_config = ConfigDict(extra="allow")

    transport: Optional[float] = Field(ge=0)
    tickets: Optional[float] = Field(ge=0)
    food: Optional[float] = Field(ge=0)
    hotel: Optional[float] = Field(ge=0)
    subtotal: Optional[float] = Field(ge=0)
    notes: List[str]


class DailyItinerary(BaseModel):
    model_config = ConfigDict(extra="allow")

    day: int = Field(ge=1, le=31)
    date: date
    theme: str
    weather_advice: str
    schedule: List[ScheduleItem] = Field(min_length=1)
    routes: List[RouteSegment]
    estimated_cost_cny: DailyCost

    @model_validator(mode="after")
    def validate_route_coverage(self) -> "DailyItinerary":
        if len(self.routes) != len(self.schedule):
            raise ValueError("每天的 routes 数量必须与 schedule 数量一致")
        expected_orders = list(range(1, len(self.schedule) + 1))
        if [item.order for item in self.schedule] != expected_orders:
            raise ValueError("schedule.order 必须从 1 连续递增")
        if [route.sequence for route in self.routes] != expected_orders:
            raise ValueError("routes.sequence 必须从 1 连续递增")
        return self


class BudgetBreakdown(BaseModel):
    model_config = ConfigDict(extra="allow")

    transport: Optional[float] = Field(ge=0)
    tickets: Optional[float] = Field(ge=0)
    food: Optional[float] = Field(ge=0)
    hotel: Optional[float] = Field(ge=0)


class BudgetSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    currency: Literal["CNY"]
    total_budget: float = Field(gt=0, le=10_000_000)
    estimated_total: Optional[float] = Field(ge=0)
    remaining: Optional[float]
    breakdown: BudgetBreakdown
    notes: List[str]


class TravelPlanDocument(BaseModel):
    """Validate every frontend-consumed field in the Planner contract."""

    model_config = ConfigDict(extra="allow")

    plan_version: str = Field(min_length=1)
    request_summary: RequestSummary
    weather_summary: List[WeatherSummaryItem]
    selected_hotel: SelectedHotel
    daily_itinerary: List[DailyItinerary] = Field(min_length=1, max_length=31)
    budget_summary: BudgetSummary
    booking_and_safety_tips: List[str]
    data_notes: List[str]

    @model_validator(mode="after")
    def validate_plan_coverage(self) -> "TravelPlanDocument":
        if len(self.daily_itinerary) != self.request_summary.days:
            raise ValueError("daily_itinerary 数量必须等于旅行天数")
        expected_days = list(range(1, self.request_summary.days + 1))
        if [day.day for day in self.daily_itinerary] != expected_days:
            raise ValueError("daily_itinerary.day 必须从 1 连续递增")
        expected_dates = [
            date.fromordinal(
                self.request_summary.start_date.toordinal() + offset
            )
            for offset in range(self.request_summary.days)
        ]
        if [day.date for day in self.daily_itinerary] != expected_dates:
            raise ValueError("daily_itinerary.date 必须完整覆盖旅行日期")
        if self.budget_summary.total_budget != self.request_summary.budget_cny:
            raise ValueError("预算摘要与请求摘要不一致")
        return self


class PlanEnvelope(BaseModel):
    plan_id: str
    task_id: str
    session_id: str
    previous_plan_id: Optional[str] = None
    revision: int = Field(ge=1)
    source_type: PlanSourceType = "agent"
    status: Literal["completed"] = "completed"
    generated_at: datetime
    plan: TravelPlanDocument


class ItineraryUpdateRequest(BaseModel):
    base_revision: int = Field(ge=1)
    daily_itinerary: List[Dict[str, Any]] = Field(min_length=1)

    @field_validator("daily_itinerary")
    @classmethod
    def validate_days(
        cls,
        days: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        for day in days:
            if not isinstance(day.get("day"), int):
                raise ValueError("每个行程日必须包含整数 day")
            schedule = day.get("schedule")
            if not isinstance(schedule, list) or not schedule:
                raise ValueError("每天至少保留一个景点")
            for item in schedule:
                ScheduleItem.model_validate(item)
        return days


class SessionPlanVersion(BaseModel):
    plan_id: str
    task_id: str
    previous_plan_id: Optional[str] = None
    revision: int = Field(ge=1)
    source_type: PlanSourceType = "agent"
    generated_at: datetime


class PlanningSessionResponse(BaseModel):
    session_id: str
    current_plan_id: Optional[str] = None
    title: Optional[str] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    plans: List[SessionPlanVersion]


class PlanningSessionSummary(BaseModel):
    session_id: str
    current_plan_id: str
    title: str
    destination_city: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_cny: Optional[float] = None
    accommodation_type: Optional[str] = None
    latest_requirement: Optional[str] = None
    current_revision: int = Field(ge=1)
    revision_count: int = Field(ge=1)
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class PlanningSessionListResponse(BaseModel):
    items: List[PlanningSessionSummary]
    next_cursor: Optional[str] = None


class PlanningSessionTurn(BaseModel):
    task_id: str
    status: TaskStatus
    user_text: str
    request: Dict[str, Any]
    plan_id: Optional[str] = None
    revision: Optional[int] = None
    source_type: Optional[PlanSourceType] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    retryable: Optional[bool] = None
    error_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    events: List[PlanningEvent]


class PlanningSessionTurnsResponse(BaseModel):
    session_id: str
    turns: List[PlanningSessionTurn]


class ForkSessionRequest(BaseModel):
    source_plan_id: str = Field(min_length=1, max_length=80)


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=80)
    archived: Optional[bool] = None

    @model_validator(mode="after")
    def validate_changes(self) -> "SessionUpdateRequest":
        if self.title is None and self.archived is None:
            raise ValueError("至少需要提交一项会话变更")
        return self


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class AuthCredentials(BaseModel):
    """Small local-account contract used for registration and login."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not 3 <= len(normalized) <= 32:
            raise ValueError("用户名长度必须为 3 到 32 位")
        if not all(
            character.isascii()
            and (character.isalnum() or character == "_")
            for character in normalized
        ):
            raise ValueError("用户名只能包含英文字母、数字和下划线")
        return normalized


class AuthUserResponse(BaseModel):
    user_id: str
    username: str
    created_at: datetime
