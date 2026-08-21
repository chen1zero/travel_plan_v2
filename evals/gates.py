"""Phase-one release gates. Hard safety/contract metrics require 100%."""

from __future__ import annotations

from typing import Mapping


GATES = {
    "run_completed": 1.0,
    "change_routing_exact": 1.0,
    "route_policy_exact": 1.0,
    "plan_contract_valid": 1.0,
    "request_coverage": 1.0,
    "attraction_unique": 1.0,
    "research_provenance": 1.0,
    "accommodation_constraint_handled": 1.0,
    "route_coverage": 1.0,
    "unsupported_facts_null": 1.0,
    "tool_discipline": 1.0,
    "revision_intent_satisfied": 1.0,
    "unchanged_field_retention": 1.0,
}

# Phase two starts in report-only mode. These are warning thresholds, not hard
# release gates. Promote them only after human calibration shows stable agreement.
JUDGE_WARNING_THRESHOLDS = {
    "judge_request_alignment": 0.50,
    "judge_itinerary_quality": 0.50,
    "judge_practical_feasibility": 0.50,
    "judge_presentation_quality": 0.50,
    "judge_revision_quality": 0.50,
    "judge_degradation_quality": 0.70,
    "judge_overall": 0.70,
}


def failed_gates(aggregates: Mapping[str, float]) -> list[str]:
    return [
        f"{key}: {aggregates.get(key, 0):.1%} < {threshold:.1%}"
        for key, threshold in GATES.items()
        if key in aggregates and aggregates[key] < threshold
    ]


def judge_warnings(aggregates: Mapping[str, float]) -> list[str]:
    """Return phase-two quality warnings without changing hard-gate behavior."""
    return [
        f"{key}: {aggregates.get(key, 0):.1%} < {threshold:.1%}"
        for key, threshold in JUDGE_WARNING_THRESHOLDS.items()
        if key in aggregates and aggregates[key] < threshold
    ]
