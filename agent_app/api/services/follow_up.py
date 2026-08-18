"""Normalize natural-language follow-up constraints before planning."""

from __future__ import annotations

import re

from agent_app.api.schemas import TravelRequest


_BUDGET_RELATIVE_PATTERN = re.compile(
    r"(?:总?预算)\s*(增加|提高|追加|上调|减少|降低|下调)(?!到)"
    r"\s*(?:了)?\s*[¥￥]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
)
_BUDGET_ABSOLUTE_PATTERN = re.compile(
    r"(?:总?预算)\s*(?:调整(?:为|到)?|修改(?:为|到)?|改成|改为|变成|"
    r"设为|增加到|提高到|降低到|减少到|到|为)?\s*"
    r"[¥￥]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
)
_ACCOMMODATION_PATTERN = re.compile(
    r"(?:改住|换住|改成|换成|住宿(?:调整|修改|改成|改为)?|酒店(?:调整|修改|改成|改为)?)"
    r"\s*(经济型|舒适型|豪华型|不限)"
)


def normalize_follow_up_request(request: TravelRequest) -> TravelRequest:
    """Project explicit natural-language overrides into typed fields."""
    instruction = request.additional_requirements or ""
    updates = {}
    budget_match = None

    relative_budget_match = _BUDGET_RELATIVE_PATTERN.search(instruction)
    if relative_budget_match:
        amount = float(relative_budget_match.group(2).replace(",", ""))
        direction = relative_budget_match.group(1)
        if direction in {"减少", "降低", "下调"}:
            amount = -amount
        updates["budget_cny"] = request.budget_cny + amount
    else:
        budget_match = _BUDGET_ABSOLUTE_PATTERN.search(instruction)
    if not relative_budget_match and budget_match:
        updates["budget_cny"] = float(
            budget_match.group(1).replace(",", "")
        )

    accommodation_match = _ACCOMMODATION_PATTERN.search(instruction)
    if accommodation_match:
        updates["accommodation_type"] = accommodation_match.group(1)

    if not updates:
        return request
    return TravelRequest.model_validate(
        {
            **request.model_dump(mode="json"),
            **updates,
        }
    )
