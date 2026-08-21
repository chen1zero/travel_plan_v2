"""Offline regression coverage for the phase-one evaluation system."""

from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evals.evaluators import evaluate_case
from evals.live_target import run_live_target
from evals.live_target import enrich_frozen_locations
from evals.fixtures import city_evidence
from agent_app.api.schemas import TravelPlanDocument
from evals.loader import load_cases
from evals.run import run_cases
from evals.sync_datasets import DATASET_NAMES, sync_datasets
from evals.targets import run_target


class EvaluationDatasetTests(unittest.TestCase):
    def test_v1_dataset_remains_exactly_32_unique_cases(self):
        cases = load_cases(version="v1")

        self.assertEqual(32, len(cases))
        self.assertEqual(32, len({case.case_id for case in cases}))

    def test_default_v2_dataset_contains_33_unique_versioned_cases(self):
        cases = load_cases()

        self.assertEqual(33, len(cases))
        self.assertEqual(33, len({case.case_id for case in cases}))
        self.assertEqual(
            {
                "change_routing": 12,
                "route_policy": 12,
                "initial_plan": 5,
                "revision": 4,
            },
            {
                suite: sum(case.suite == suite for case in cases)
                for suite in DATASET_NAMES
            },
        )

    def test_all_cases_pass_the_offline_reference_target(self):
        rows, aggregates = run_cases(load_cases())

        self.assertTrue(all(row["passed"] for row in rows))
        self.assertTrue(aggregates)
        self.assertTrue(all(score == 1.0 for score in aggregates.values()))

    def test_duplicate_attraction_is_caught(self):
        case = next(case for case in load_cases() if case.case_id == "IP-001")
        output = run_target(case.suite, case.inputs)
        mutated = deepcopy(output)
        first = mutated["plan"]["daily_itinerary"][0]["schedule"][0]
        second = mutated["plan"]["daily_itinerary"][1]["schedule"][0]
        second["place_name"] = first["place_name"]

        scores = {score["key"]: score for score in evaluate_case(case, mutated)}

        self.assertEqual(0.0, scores["attraction_unique"]["score"])

    def test_wrong_route_recommendation_is_caught(self):
        case = next(case for case in load_cases() if case.case_id == "IP-001")
        output = run_target(case.suite, case.inputs)
        mutated = deepcopy(output)
        route = mutated["plan"]["daily_itinerary"][0]["routes"][0]
        route["recommended_mode"] = (
            "driving" if route["recommended_mode"] != "driving" else "walking"
        )

        scores = {score["key"]: score for score in evaluate_case(case, mutated)}

        self.assertEqual(0.0, scores["route_policy_exact"]["score"])

    def test_accommodation_type_mismatch_is_caught_when_candidate_exists(self):
        case = next(case for case in load_cases() if case.case_id == "IP-001")
        mutated = deepcopy(run_target(case.suite, case.inputs))
        mutated["plan"]["selected_hotel"]["name"] = "北京前门经济酒店"

        scores = {score["key"]: score for score in evaluate_case(case, mutated)}

        self.assertEqual(
            0.0, scores["accommodation_constraint_handled"]["score"]
        )

    def test_missing_accommodation_type_must_be_disclosed(self):
        case = next(case for case in load_cases() if case.case_id == "IP-005")
        output = run_target(case.suite, case.inputs)
        passing = {
            score["key"]: score for score in evaluate_case(case, output)
        }
        self.assertEqual(
            1.0, passing["accommodation_constraint_handled"]["score"]
        )

        mutated = deepcopy(output)
        mutated["plan"]["request_summary"]["unresolved_fields"] = []
        mutated["plan"]["data_notes"] = ["价格信息未知"]
        failing = {
            score["key"]: score for score in evaluate_case(case, mutated)
        }
        self.assertEqual(
            0.0, failing["accommodation_constraint_handled"]["score"]
        )

    def test_live_target_enriches_missing_locations_from_frozen_evidence(self):
        case = next(case for case in load_cases() if case.case_id == "IP-002")
        output = run_target(case.suite, case.inputs)
        plan = deepcopy(output["plan"])
        del plan["daily_itinerary"][0]["schedule"][0]["location"]

        enrich_frozen_locations(plan, city_evidence("上海"))

        validated = TravelPlanDocument.model_validate(plan)
        self.assertIsNotNone(
            validated.daily_itinerary[0].schedule[0].location
        )

    def test_live_target_requires_explicit_consent(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RUN_LANGSMITH_EVALS=1"):
                run_live_target("change_routing", {})

    def test_sync_upserts_stable_examples_only_after_consent(self):
        client = _FakeLangSmithClient()
        with patch.dict(os.environ, {"RUN_LANGSMITH_EVALS": "1"}, clear=True):
            counts = sync_datasets(client)
            repeated_counts = sync_datasets(client)

        self.assertEqual(
            {
                "travel-change-routing-v1": 12,
                "travel-route-policy-v1": 12,
                "travel-initial-plan-v2": 5,
                "travel-revision-v1": 4,
            },
            counts,
        )
        self.assertEqual(counts, repeated_counts)
        self.assertEqual(4, len(client.created_datasets))
        self.assertEqual(33, sum(len(batch) for batch in client.examples.values()))


class _FakeLangSmithClient:
    def __init__(self) -> None:
        self.created_datasets: list[str] = []
        self.examples: dict[str, list[dict]] = {}

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self.created_datasets

    def create_dataset(self, dataset_name: str, **_kwargs):
        self.created_datasets.append(dataset_name)
        return {"name": dataset_name}

    def create_examples(self, *, dataset_name: str, examples: list[dict]):
        self.examples.setdefault(dataset_name, []).extend(examples)
        return {"count": len(examples)}

    def list_examples(self, *, dataset_name: str):
        return [
            SimpleNamespace(id=example["id"])
            for example in self.examples.get(dataset_name, [])
        ]

    def update_example(
        self, example_id, *, inputs: dict, outputs: dict, metadata: dict
    ):
        for examples in self.examples.values():
            for example in examples:
                if example["id"] == example_id:
                    example.update(
                        inputs=inputs, outputs=outputs, metadata=metadata
                    )
                    return {"id": example_id}
        raise KeyError(example_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
