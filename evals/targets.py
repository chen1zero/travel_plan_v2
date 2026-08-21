"""Evaluation targets. Local mode is deterministic and never uses network."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from agent_app.harness.travel_planning import analyze_request_changes
from agent_app.tools.route_policy import recommend_transport_mode
from evals.fixtures import build_fixture_output


def run_target(suite: str, inputs: Mapping[str, Any]) -> Dict[str, Any]:
    if suite == "change_routing":
        result = analyze_request_changes(
            inputs["current_request"], inputs.get("previous_context")
        )
        return {"status": "completed", "result": result}
    if suite == "route_policy":
        mode, reason = recommend_transport_mode(
            inputs["walking"], inputs["driving"], inputs["public_transit"]
        )
        return {
            "status": "completed",
            "result": {
                "recommended_mode": mode,
                "recommendation_reason": reason,
            },
        }
    if suite in {"initial_plan", "revision"}:
        return build_fixture_output(inputs)
    raise ValueError(f"未知评估套件：{suite}")
