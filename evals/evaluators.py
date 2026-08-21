"""Deterministic evaluators used identically in local CI and LangSmith."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

from pydantic import ValidationError

from agent_app.api.schemas import TravelPlanDocument
from agent_app.harness.revision_validation import validate_itinerary_uniqueness
from agent_app.tools.route_policy import recommend_transport_mode
from evals.fixtures import load_evidence
from evals.schemas import EvalCase


Score = Dict[str, Any]


def evaluate_case(case: EvalCase, outputs: Mapping[str, Any]) -> list[Score]:
    """Select the relevant hard evaluators for one suite."""
    scores = [_score("run_completed", outputs.get("status") == "completed")]
    if case.suite == "change_routing":
        scores.append(_change_routing_exact(case.reference_outputs, outputs))
        return scores
    if case.suite == "route_policy":
        scores.append(_route_policy_case_exact(case.reference_outputs, outputs))
        return scores

    plan = outputs.get("plan")
    if not isinstance(plan, Mapping):
        return scores + [
            _score(key, False, "输出缺少 plan JSON 对象")
            for key in (
                "plan_contract_valid",
                "request_coverage",
                "attraction_unique",
                "research_provenance",
                "accommodation_constraint_handled",
                "route_coverage",
                "route_policy_exact",
                "unsupported_facts_null",
                "tool_discipline",
            )
        ]
    scores.extend(
        [
            _plan_contract_valid(plan),
            _request_coverage(case, plan),
            _attraction_unique(plan),
            _research_provenance(case, plan),
            _accommodation_constraint_handled(case, plan),
            _route_coverage(plan),
            _all_route_policies_exact(plan),
            _unsupported_facts_null(plan),
            _tool_discipline(case, outputs),
        ]
    )
    if case.suite == "revision":
        scores.extend(
            [
                _change_routing_exact(
                    {"rerun": case.reference_outputs["expected_rerun"]},
                    {"result": outputs.get("change_analysis")},
                ),
                _revision_intent(case, outputs),
                _unchanged_field_retention(case, outputs),
            ]
        )
    return scores


def _change_routing_exact(
    reference: Mapping[str, Any], outputs: Mapping[str, Any]
) -> Score:
    result = outputs.get("result")
    if not isinstance(result, Mapping):
        return _score("change_routing_exact", False, "缺少变更分析结果")
    expected_rerun = reference.get("rerun")
    actual_rerun = result.get("rerun")
    mode_matches = (
        "mode" not in reference or result.get("mode") == reference["mode"]
    )
    passed = actual_rerun == expected_rerun and mode_matches
    return _score(
        "change_routing_exact",
        passed,
        f"expected={expected_rerun}; actual={actual_rerun}",
    )


def _route_policy_case_exact(
    reference: Mapping[str, Any], outputs: Mapping[str, Any]
) -> Score:
    result = outputs.get("result")
    if not isinstance(result, Mapping):
        return _score("route_policy_exact", False, "缺少交通推荐结果")
    mode = result.get("recommended_mode")
    reason = str(result.get("recommendation_reason") or "")
    expected_mode = reference.get("recommended_mode")
    expected_reason = str(reference.get("reason_contains") or "")
    passed = mode == expected_mode and expected_reason in reason
    return _score(
        "route_policy_exact",
        passed,
        f"mode={mode}; reason={reason}",
    )


def _plan_contract_valid(plan: Mapping[str, Any]) -> Score:
    try:
        TravelPlanDocument.model_validate(plan)
    except ValidationError as exc:
        return _score("plan_contract_valid", False, str(exc))
    return _score("plan_contract_valid", True)


def _request_coverage(case: EvalCase, plan: Mapping[str, Any]) -> Score:
    summary = plan.get("request_summary")
    if not isinstance(summary, Mapping):
        return _score("request_coverage", False, "缺少 request_summary")
    expected = case.reference_outputs
    passed = (
        summary.get("destination_city") == expected.get("expected_city")
        and summary.get("budget_cny") == expected.get("expected_budget")
        and len(plan.get("daily_itinerary", []))
        == expected.get(
            "expected_days", len(plan.get("daily_itinerary", []))
        )
    )
    return _score(
        "request_coverage",
        passed,
        f"city={summary.get('destination_city')}; budget={summary.get('budget_cny')}",
    )


def _attraction_unique(plan: Mapping[str, Any]) -> Score:
    errors = validate_itinerary_uniqueness(plan)
    return _score("attraction_unique", not errors, "；".join(errors))


def _research_provenance(case: EvalCase, plan: Mapping[str, Any]) -> Score:
    city = str(case.inputs.get("fixture_city") or "")
    evidence_version = str(
        case.inputs.get("evidence_version") or "core-evidence-v1"
    )
    evidence_city = load_evidence(evidence_version)["cities"].get(city, {})
    allowed_attractions = {
        item["name"] for item in evidence_city.get("attractions", [])
    }
    allowed_hotels = {item["name"] for item in evidence_city.get("hotels", [])}
    used_attractions = {
        item.get("place_name")
        for day in plan.get("daily_itinerary", [])
        for item in day.get("schedule", [])
        if isinstance(item, Mapping)
    }
    hotel = plan.get("selected_hotel")
    hotel_name = hotel.get("name") if isinstance(hotel, Mapping) else None
    unknown = sorted(str(name) for name in used_attractions - allowed_attractions)
    passed = not unknown and hotel_name in allowed_hotels
    comment = f"unknown_attractions={unknown}; hotel={hotel_name}"
    return _score("research_provenance", passed, comment)


def _accommodation_constraint_handled(
    case: EvalCase, plan: Mapping[str, Any]
) -> Score:
    request = case.inputs.get("request")
    if not isinstance(request, Mapping):
        return _score(
            "accommodation_constraint_handled", False, "缺少结构化 request"
        )
    expected_type = str(request.get("accommodation_type") or "不限")
    if expected_type == "不限":
        return _score("accommodation_constraint_handled", True, "住宿类型不限")

    city = str(
        case.inputs.get("fixture_city")
        or request.get("destination_city")
        or ""
    )
    evidence_version = str(
        case.inputs.get("evidence_version") or "core-evidence-v1"
    )
    hotels = load_evidence(evidence_version)["cities"].get(city, {}).get(
        "hotels", []
    )
    selected = plan.get("selected_hotel")
    selected_name = (
        str(selected.get("name") or "")
        if isinstance(selected, Mapping)
        else ""
    )
    selected_evidence = next(
        (item for item in hotels if item.get("name") == selected_name), None
    )
    selected_type = (
        str(selected_evidence.get("type") or "")
        if isinstance(selected_evidence, Mapping)
        else ""
    )
    matching_candidates = [
        item for item in hotels if item.get("type") == expected_type
    ]
    if selected_type == expected_type:
        return _score(
            "accommodation_constraint_handled",
            True,
            f"selected={selected_name}; type={selected_type}",
        )
    if matching_candidates:
        return _score(
            "accommodation_constraint_handled",
            False,
            f"存在{expected_type}候选但选择了 {selected_name}({selected_type or '未知'})",
        )

    summary = plan.get("request_summary")
    unresolved = (
        summary.get("unresolved_fields", [])
        if isinstance(summary, Mapping)
        else []
    )
    disclosure = "；".join(
        str(value)
        for value in [*unresolved, *plan.get("data_notes", [])]
        if value is not None
    )
    disclosed = (
        expected_type in disclosure or "住宿" in disclosure or "酒店" in disclosure
    ) and any(
        marker in disclosure
        for marker in ("无", "没有", "不足", "未解决", "待确认", "候选")
    )
    return _score(
        "accommodation_constraint_handled",
        disclosed,
        f"无{expected_type}候选；constraint_disclosed={disclosed}",
    )


def _route_coverage(plan: Mapping[str, Any]) -> Score:
    errors: list[str] = []
    for day in plan.get("daily_itinerary", []):
        schedule = day.get("schedule", [])
        routes = day.get("routes", [])
        if len(schedule) != len(routes):
            errors.append(
                f"day={day.get('day')}: schedule={len(schedule)}, routes={len(routes)}"
            )
        for index, route in enumerate(routes):
            if route.get("sequence") != index + 1:
                errors.append(f"day={day.get('day')}: 路线序号不连续")
    return _score("route_coverage", not errors, "；".join(errors))


def _all_route_policies_exact(plan: Mapping[str, Any]) -> Score:
    mismatches: list[str] = []
    for day in plan.get("daily_itinerary", []):
        for route in day.get("routes", []):
            expected_mode, expected_reason = recommend_transport_mode(
                route.get("walking", {}),
                route.get("driving", {}),
                route.get("public_transit", {}),
            )
            if (
                route.get("recommended_mode") != expected_mode
                or route.get("recommendation_reason") != expected_reason
            ):
                mismatches.append(str(route.get("route_id")))
    return _score(
        "route_policy_exact",
        not mismatches,
        f"不符合确定性规则的路线：{mismatches}",
    )


def _unsupported_facts_null(plan: Mapping[str, Any]) -> Score:
    """The frozen core evidence intentionally has no reliable price facts."""
    non_null: list[str] = []
    hotel = plan.get("selected_hotel", {})
    if hotel.get("price_cny_per_night") is not None:
        non_null.append("selected_hotel.price_cny_per_night")
    budget = plan.get("budget_summary", {})
    for field in ("estimated_total", "remaining"):
        if budget.get(field) is not None:
            non_null.append(f"budget_summary.{field}")
    for field, value in budget.get("breakdown", {}).items():
        if value is not None:
            non_null.append(f"budget_summary.breakdown.{field}")
    for day in plan.get("daily_itinerary", []):
        for field, value in day.get("estimated_cost_cny", {}).items():
            if field != "notes" and value is not None:
                non_null.append(f"day{day.get('day')}.estimated_cost_cny.{field}")
        for route in day.get("routes", []):
            for mode_name in ("walking", "driving", "public_transit"):
                mode = route.get(mode_name, {})
                if not mode.get("available") and (
                    mode.get("distance_km") is not None
                    or mode.get("duration_minutes") is not None
                ):
                    non_null.append(f"{route.get('route_id')}.{mode_name}")
    return _score(
        "unsupported_facts_null",
        not non_null,
        f"无证据但非空的字段：{non_null}",
    )


def _tool_discipline(case: EvalCase, outputs: Mapping[str, Any]) -> Score:
    calls = outputs.get("tool_calls")
    if outputs.get("execution_mode") == "offline_fixture":
        return _score(
            "tool_discipline",
            True,
            "离线夹具不执行工具；此指标在 live LangSmith 实验中检查",
        )
    if not isinstance(calls, list):
        return _score("tool_discipline", False, "缺少 tool_calls 轨迹")
    actual = {
        call.get("name") for call in calls if isinstance(call, Mapping)
    }
    required = set(case.reference_outputs.get("required_tools", []))
    forbidden = set(case.reference_outputs.get("forbidden_tools", []))
    missing = sorted(required - actual)
    unexpected = sorted(forbidden & actual)
    invalid = [
        call
        for call in calls
        if isinstance(call, Mapping)
        and not _valid_tool_call(call)
    ]
    routing_mismatches: list[str] = []
    expected_rerun = case.reference_outputs.get("expected_rerun")
    if isinstance(expected_rerun, Mapping):
        actual_stages = {
            call.get("stage") for call in calls if isinstance(call, Mapping)
        }
        for stage, should_run in expected_rerun.items():
            did_run = stage in actual_stages
            if bool(should_run) != did_run:
                routing_mismatches.append(
                    f"{stage}: expected={bool(should_run)}, actual={did_run}"
                )
    return _score(
        "tool_discipline",
        not missing and not unexpected and not invalid and not routing_mismatches,
        f"missing={missing}; unexpected={unexpected}; invalid_count={len(invalid)}; "
        f"routing_mismatches={routing_mismatches}",
    )


def _valid_tool_call(call: Mapping[str, Any]) -> bool:
    name = call.get("name")
    arguments = call.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        return False
    if name == "maps_weather":
        return set(arguments) == {"city"} and _non_empty_strings(
            arguments, ("city",)
        )
    if name == "maps_text_search":
        if not set(arguments).issubset({"keywords", "city", "citylimit"}):
            return False
        if not _non_empty_strings(arguments, ("keywords",)):
            return False
        citylimit = arguments.get("citylimit", "false")
        return citylimit in {"true", "false"}
    if name == "compare_route_options":
        required = {
            "origin_address",
            "destination_address",
            "origin_city",
            "destination_city",
        }
        return set(arguments) == required and _non_empty_strings(
            arguments, tuple(required)
        )
    return False


def _non_empty_strings(
    arguments: Mapping[str, Any], fields: Iterable[str]
) -> bool:
    return all(
        isinstance(arguments.get(field), str)
        and bool(str(arguments[field]).strip())
        for field in fields
    )


def _revision_intent(case: EvalCase, outputs: Mapping[str, Any]) -> Score:
    plan = outputs["plan"]
    previous = outputs.get("previous_plan")
    if not isinstance(previous, Mapping):
        return _score("revision_intent_satisfied", False, "缺少 previous_plan")
    behavior = case.inputs.get("revision_behavior")
    passed = False
    if behavior == "budget":
        passed = (
            plan["request_summary"]["budget_cny"]
            == case.reference_outputs["expected_budget"]
            and plan["budget_summary"]["total_budget"]
            == case.reference_outputs["expected_budget"]
        )
    elif behavior == "night_attraction":
        before = previous["daily_itinerary"][0]["schedule"]
        after = plan["daily_itinerary"][0]["schedule"]
        passed = len(after) > len(before) and any(
            int(item["time_slot"].split(":", 1)[0]) >= 17
            for item in after[len(before) :]
        )
    elif behavior == "hotel":
        passed = (
            plan["selected_hotel"]["name"]
            != previous["selected_hotel"]["name"]
            and all(
                day["routes"][0]["origin"]["name"]
                == plan["selected_hotel"]["name"]
                for day in plan["daily_itinerary"]
            )
        )
    elif behavior == "destination":
        passed = (
            plan["request_summary"]["destination_city"]
            == case.reference_outputs["expected_city"]
            and plan["selected_hotel"]["name"]
            != previous["selected_hotel"]["name"]
        )
    return _score(
        "revision_intent_satisfied", passed, f"behavior={behavior}"
    )


def _unchanged_field_retention(
    case: EvalCase, outputs: Mapping[str, Any]
) -> Score:
    plan = outputs["plan"]
    previous = outputs.get("previous_plan")
    paths = list(case.reference_outputs.get("preserve_paths", []))
    if not isinstance(previous, Mapping):
        return _score("unchanged_field_retention", False, "缺少 previous_plan")
    if not paths:
        return _score("unchanged_field_retention", True, "无要求保留的字段")
    matches = [
        path
        for path in paths
        if _get_path(plan, path) == _get_path(previous, path)
    ]
    ratio = len(matches) / len(paths)
    return {
        "key": "unchanged_field_retention",
        "score": ratio,
        "comment": f"保留 {len(matches)}/{len(paths)}：{matches}",
    }


def _get_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _score(key: str, passed: bool, comment: str = "") -> Score:
    return {"key": key, "score": 1.0 if passed else 0.0, "comment": comment}


def aggregate_scores(results: Iterable[Iterable[Score]]) -> Dict[str, float]:
    buckets: Dict[str, list[float]] = {}
    for case_scores in results:
        for score in case_scores:
            buckets.setdefault(str(score["key"]), []).append(float(score["score"]))
    return {key: sum(values) / len(values) for key, values in sorted(buckets.items())}
