"""Typed state exchanged by the LangGraph travel planning harness."""

from typing import Any, Dict

from typing_extensions import TypedDict


class TravelPlanState(TypedDict, total=False):
    """State produced incrementally by specialist graph nodes."""

    request: str
    request_data: Dict[str, Any]
    revision_mode: bool
    previous_plan: Dict[str, Any]
    previous_context: Dict[str, Any]
    change_analysis: Dict[str, Any]
    attractions: str
    weather: str
    hotels: str
    final_plan: str
