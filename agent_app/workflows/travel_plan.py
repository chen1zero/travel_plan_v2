"""Backward-compatible import for the LangGraph travel harness."""

from agent_app.harness.travel_planning import TravelPlanningHarness


TravelPlanAgent = TravelPlanningHarness

__all__ = ["TravelPlanAgent", "TravelPlanningHarness"]
