"""Deterministic L2 session memory assembly for the planning harness."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import ceil
from typing import Any, Dict, Iterable, List, Mapping, Optional

from agent_app.api.repository import SQLitePlanRepository


class MemoryContextTooLargeError(ValueError):
    """Raised when the mandatory immutable anchor exceeds the input budget."""


@dataclass(frozen=True)
class MemoryPolicy:
    """Token allocation for a model with a one-million-token window."""

    context_window_tokens: int = 1_000_000
    input_limit_tokens: int = 650_000
    target_tokens: int = 500_000
    recent_assistant_tokens: int = 150_000
    tokenizer_id: str = "conservative-utf8-v1"

    def __post_init__(self) -> None:
        values = (
            self.context_window_tokens,
            self.input_limit_tokens,
            self.target_tokens,
            self.recent_assistant_tokens,
        )
        if any(value <= 0 for value in values):
            raise ValueError("记忆 token 配置必须大于 0")
        if self.target_tokens > self.input_limit_tokens:
            raise ValueError("MEMORY_TARGET_TOKENS 不能超过输入上限")
        if self.input_limit_tokens >= self.context_window_tokens:
            raise ValueError("记忆输入上限必须小于模型上下文窗口")


def estimate_tokens(value: Any) -> int:
    """Conservatively estimate mixed Chinese/JSON tokens without model SDKs."""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return max(1, ceil(len(text.encode("utf-8")) / 3))


class SessionMemoryManager:
    """Build latest-anchor-first memory without model-generated summaries."""

    def __init__(
        self,
        repository: SQLitePlanRepository,
        policy: Optional[MemoryPolicy] = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or MemoryPolicy()

    @property
    def policy(self) -> MemoryPolicy:
        return self._policy

    def build(
        self,
        *,
        task_id: str,
        session_id: str,
        previous_envelope: Mapping[str, Any],
        current_request: Mapping[str, Any],
    ) -> Dict[str, Any]:
        anchor_plan_id = str(previous_envelope["plan_id"])
        messages = self._repository.get_memory_turns(
            session_id,
            anchor_plan_id,
        )
        user_messages = [
            {
                "sequence": message["sequence"],
                "task_id": message["task_id"],
                "plan_id": message["plan_id"],
                "revision": message["revision"],
                "source_type": message["source_type"],
                "text": message["content_text"] or "",
            }
            for message in messages
            if message["role"] == "user"
        ]
        assistant_messages = [
            message for message in messages if message["role"] == "assistant"
        ]
        assistant_messages.sort(key=lambda value: value["sequence"])
        if not assistant_messages:
            raise ValueError("Session 缺少可用的已提交助手计划")

        latest_context = dict(previous_envelope.get("context") or {})
        latest_request = latest_context.get("request")
        if not isinstance(latest_request, Mapping):
            latest_request = assistant_messages[-1]["request"]
        anchor = {
            "structured_request": dict(latest_request),
            "full_plan": previous_envelope["plan"],
            "research": {
                "attractions": latest_context.get("attractions", ""),
                "weather": latest_context.get("weather", ""),
                "hotels": latest_context.get("hotels", ""),
            },
            "version": {
                "session_id": session_id,
                "plan_id": anchor_plan_id,
                "previous_plan_id": previous_envelope.get(
                    "previous_plan_id"
                ),
                "revision": int(previous_envelope["revision"]),
                "source_type": previous_envelope.get("source_type", "agent"),
                "generated_at": previous_envelope.get("generated_at"),
            },
        }
        revision_ledger = _build_revision_ledger(assistant_messages)
        bundle: Dict[str, Any] = {
            "memory_mode": "layered_l2",
            "policy": {
                "context_window_tokens": self._policy.context_window_tokens,
                "input_limit_tokens": self._policy.input_limit_tokens,
                "target_tokens": self._policy.target_tokens,
                "tokenizer_id": self._policy.tokenizer_id,
            },
            "all_user_messages": user_messages,
            "revision_ledger": revision_ledger,
            "recent_assistant_plans": [],
            "latest_anchor": anchor,
            "current_request": dict(current_request),
        }

        mandatory_tokens = estimate_tokens(bundle)
        if mandatory_tokens > self._policy.input_limit_tokens:
            raise MemoryContextTooLargeError(
                "最新完整计划、研究结果和用户消息超过记忆输入上限"
            )

        remaining = min(
            self._policy.recent_assistant_tokens,
            max(0, self._policy.target_tokens - mandatory_tokens),
        )
        latest_revision = int(previous_envelope["revision"])
        older_assistants = [
            message
            for message in assistant_messages
            if int(message["revision"]) < latest_revision
        ]
        retained: List[Dict[str, Any]] = []
        for message in reversed(older_assistants):
            projection = {
                "sequence": message["sequence"],
                "plan_id": message["plan_id"],
                "revision": message["revision"],
                "source_type": message["source_type"],
                "generated_at": message["generated_at"],
                "full_plan": message["plan"],
            }
            projection_tokens = estimate_tokens(projection)
            if projection_tokens <= remaining:
                retained.append(projection)
                remaining -= projection_tokens
        retained.reverse()
        bundle["recent_assistant_plans"] = retained
        omitted_count = len(older_assistants) - len(retained)
        input_tokens = estimate_tokens(bundle)
        bundle["memory_stats"] = {
            "estimated_input_tokens": input_tokens,
            "user_message_count": len(user_messages),
            "revision_count": len(revision_ledger),
            "retained_assistant_count": len(retained),
            "omitted_assistant_count": omitted_count,
        }
        input_tokens = estimate_tokens(bundle)
        bundle["memory_stats"]["estimated_input_tokens"] = input_tokens

        if input_tokens > self._policy.input_limit_tokens:
            bundle["recent_assistant_plans"] = []
            bundle["memory_stats"]["retained_assistant_count"] = 0
            bundle["memory_stats"]["omitted_assistant_count"] = len(
                older_assistants
            )
            input_tokens = estimate_tokens(bundle)
            bundle["memory_stats"]["estimated_input_tokens"] = input_tokens
        if input_tokens > self._policy.input_limit_tokens:
            raise MemoryContextTooLargeError(
                "L2 记忆锚点超过配置的模型输入上限"
            )

        self._repository.record_memory_assembly(
            session_id=session_id,
            task_id=task_id,
            mode="layered_l2",
            input_tokens=input_tokens,
            retained_assistant_count=len(
                bundle["recent_assistant_plans"]
            ),
            omitted_assistant_count=bundle["memory_stats"][
                "omitted_assistant_count"
            ],
            anchor_plan_id=anchor_plan_id,
            metadata={
                "user_message_count": len(user_messages),
                "revision_count": len(revision_ledger),
                "tokenizer_id": self._policy.tokenizer_id,
            },
        )
        return bundle


def _build_revision_ledger(
    assistant_messages: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    ledger: List[Dict[str, Any]] = []
    previous: Optional[Mapping[str, Any]] = None
    for message in assistant_messages:
        request = message["request"]
        plan = message["plan"]
        previous_request = previous["request"] if previous else None
        previous_plan = previous["plan"] if previous else None
        ledger.append(
            {
                "revision": int(message["revision"]),
                "plan_id": message["plan_id"],
                "previous_plan_id": message["previous_plan_id"],
                "source_type": message["source_type"],
                "generated_at": message["generated_at"],
                "request_changes": _request_changes(
                    previous_request,
                    request,
                ),
                "plan_changes": _plan_changes(previous_plan, plan),
            }
        )
        previous = message
    return ledger


def _request_changes(
    previous: Optional[Mapping[str, Any]],
    current: Mapping[str, Any],
) -> Dict[str, Any]:
    ignored = {"session_id", "previous_plan_id"}
    fields = [key for key in current if key not in ignored]
    if previous is None:
        return {"changed_fields": ["initial"]}
    changed = sorted(
        key
        for key in set(fields) | (set(previous) - ignored)
        if previous.get(key) != current.get(key)
    )
    return {
        "changed_fields": changed,
        "values": {
            key: {"from": previous.get(key), "to": current.get(key)}
            for key in changed
        },
    }


def _plan_changes(
    previous: Optional[Mapping[str, Any]],
    current: Mapping[str, Any],
) -> Dict[str, Any]:
    if previous is None:
        return {"changed_sections": ["initial"]}
    changed_sections = sorted(
        key
        for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    )
    previous_days = _days_by_number(previous)
    current_days = _days_by_number(current)
    changed_days = sorted(
        day
        for day in set(previous_days) | set(current_days)
        if previous_days.get(day) != current_days.get(day)
    )
    previous_places = _place_names(previous)
    current_places = _place_names(current)
    previous_hotel = _hotel_name(previous)
    current_hotel = _hotel_name(current)
    return {
        "changed_sections": changed_sections,
        "changed_itinerary_days": changed_days,
        "places_added": sorted(current_places - previous_places),
        "places_removed": sorted(previous_places - current_places),
        "selected_hotel": (
            {"from": previous_hotel, "to": current_hotel}
            if previous_hotel != current_hotel
            else None
        ),
        "budget_cny": _budget_change(previous, current),
    }


def _days_by_number(plan: Mapping[str, Any]) -> Dict[int, Any]:
    result: Dict[int, Any] = {}
    for day in plan.get("daily_itinerary", []):
        if isinstance(day, Mapping) and isinstance(day.get("day"), int):
            result[int(day["day"])] = day
    return result


def _place_names(plan: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for day in plan.get("daily_itinerary", []):
        if not isinstance(day, Mapping):
            continue
        for item in day.get("schedule", []):
            if isinstance(item, Mapping) and item.get("place_name"):
                names.add(str(item["place_name"]))
    return names


def _hotel_name(plan: Mapping[str, Any]) -> Optional[str]:
    hotel = plan.get("selected_hotel")
    if not isinstance(hotel, Mapping) or not hotel.get("name"):
        return None
    return str(hotel["name"])


def _budget_change(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    previous_budget = previous.get("request_summary", {})
    current_budget = current.get("request_summary", {})
    before = (
        previous_budget.get("budget_cny")
        if isinstance(previous_budget, Mapping)
        else None
    )
    after = (
        current_budget.get("budget_cny")
        if isinstance(current_budget, Mapping)
        else None
    )
    return {"from": before, "to": after} if before != after else None
