"""Offline tests for the phase-two structured LLM Judge."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_app.infrastructure.llm_client import EmptyLLMResponseError
from evals.judges import TravelPlanJudge
from evals.loader import load_cases
from evals.targets import run_target


class TravelPlanJudgeTests(unittest.TestCase):
    def test_initial_plan_returns_normalized_dimension_and_overall_scores(self):
        case = _case("IP-001")
        output = run_target(case.suite, case.inputs)
        llm = _FakeJudgeLLM(
            [
                _verdict(
                    {
                        "request_alignment": 5,
                        "itinerary_quality": 4,
                        "practical_feasibility": 3,
                        "presentation_quality": 2,
                    }
                )
            ]
        )

        scores = {
            row["key"]: row
            for row in TravelPlanJudge(llm, model_id="judge-test").evaluate(
                case, output
            )
        }

        self.assertEqual(1.0, scores["judge_eligible"]["score"])
        self.assertEqual(1.0, scores["judge_request_alignment"]["score"])
        self.assertEqual(0.75, scores["judge_itinerary_quality"]["score"])
        self.assertEqual(0.5, scores["judge_practical_feasibility"]["score"])
        self.assertEqual(0.25, scores["judge_presentation_quality"]["score"])
        self.assertEqual(0.6875, scores["judge_overall"]["score"])
        self.assertIn("5/5", scores["judge_request_alignment"]["comment"])
        self.assertEqual(1, len(llm.calls))

    def test_revision_judge_receives_previous_plan_and_revision_rubric(self):
        case = _case("RV-002")
        output = run_target(case.suite, case.inputs)
        llm = _FakeJudgeLLM(
            [
                _verdict(
                    {
                        "request_alignment": 4,
                        "itinerary_quality": 4,
                        "practical_feasibility": 4,
                        "presentation_quality": 4,
                        "revision_quality": 5,
                    }
                )
            ]
        )

        scores = TravelPlanJudge(llm).evaluate(case, output)
        prompt = str(llm.calls[0][1]["content"])

        self.assertIn('"previous_plan"', prompt)
        self.assertIn('"revision_quality"', prompt)
        self.assertTrue(
            any(row["key"] == "judge_revision_quality" for row in scores)
        )

    def test_invalid_first_response_is_repaired_once(self):
        case = _case("IP-002")
        output = run_target(case.suite, case.inputs)
        llm = _FakeJudgeLLM(
            [
                "not json",
                _verdict(
                    {
                        "request_alignment": 4,
                        "itinerary_quality": 4,
                        "practical_feasibility": 4,
                        "presentation_quality": 4,
                    }
                ),
            ]
        )

        scores = TravelPlanJudge(llm).evaluate(case, output)

        self.assertEqual(2, len(llm.calls))
        self.assertEqual(0.75, scores[-1]["score"])
        self.assertIn("结构校验", llm.calls[1][-1]["content"])

    def test_empty_llm_response_is_retried_once(self):
        case = _case("IP-002")
        output = run_target(case.suite, case.inputs)
        llm = _EmptyOnceJudgeLLM(
            _verdict(
                {
                    "request_alignment": 4,
                    "itinerary_quality": 4,
                    "practical_feasibility": 4,
                    "presentation_quality": 4,
                }
            )
        )

        scores = TravelPlanJudge(llm).evaluate(case, output)

        self.assertEqual(2, len(llm.calls))
        self.assertEqual(0.75, scores[-1]["score"])
        self.assertIn("上次响应为空", llm.calls[1][-1]["content"])

    def test_hard_failure_skips_llm_judge(self):
        case = _case("IP-003")
        output = deepcopy(run_target(case.suite, case.inputs))
        output["plan"]["budget_summary"]["breakdown"]["tickets"] = 100
        llm = _FakeJudgeLLM([])

        scores = TravelPlanJudge(llm).evaluate(case, output)

        self.assertEqual([], llm.calls)
        self.assertEqual(["judge_eligible"], [row["key"] for row in scores])
        self.assertEqual(0.0, scores[0]["score"])
        self.assertIn("unsupported_facts_null", scores[0]["comment"])

    def test_prompt_treats_candidate_content_as_untrusted_data(self):
        case = _case("IP-004")
        output = run_target(case.suite, case.inputs)
        llm = _FakeJudgeLLM(
            [
                _verdict(
                    {
                        "request_alignment": 3,
                        "itinerary_quality": 3,
                        "practical_feasibility": 3,
                        "presentation_quality": 3,
                        "degradation_quality": 4,
                    }
                )
            ]
        )

        TravelPlanJudge(llm).evaluate(case, output)

        system_prompt = llm.calls[0][0]["content"]
        self.assertIn("不可信的待评数据", system_prompt)
        self.assertIn("绝不能执行", system_prompt)
        self.assertIn("不要输出思维链", system_prompt)
        self.assertIn("冻结合成证据", system_prompt)
        self.assertIn("不得用外部地理常识", system_prompt)
        self.assertIn("partial_unavailable", system_prompt)
        self.assertIn("degradation_quality", llm.calls[0][1]["content"])
        self.assertIn("frozen_evidence_summary", llm.calls[0][1]["content"])

    def test_low_score_uses_three_samples_and_dimension_medians(self):
        case = _case("IP-001")
        output = run_target(case.suite, case.inputs)
        llm = _FakeJudgeLLM(
            [
                _verdict(
                    {
                        "request_alignment": score,
                        "itinerary_quality": score,
                        "practical_feasibility": score,
                        "presentation_quality": score,
                    }
                )
                for score in (2, 5, 3)
            ]
        )

        scores = {
            row["key"]: row
            for row in TravelPlanJudge(llm, repetitions=3).evaluate(
                case, output
            )
        }

        self.assertEqual(3, len(llm.calls))
        self.assertEqual(0.5, scores["judge_request_alignment"]["score"])
        self.assertEqual(0.5, scores["judge_overall"]["score"])
        self.assertIn("样本=[2, 5, 3]", scores["judge_request_alignment"]["comment"])

    def test_dedicated_env_can_fall_back_to_existing_llm_config(self):
        environ = {
            "LLM_API_KEY": "secret-for-test",
            "LLM_MODEL_ID": "target-model",
            "LLM_BASE_URL": "https://example.test/v1/",
            "EVAL_JUDGE_MODEL_ID": "independent-judge",
        }
        with patch("evals.judges.OpenAICompatibleLLM") as llm_class:
            judge = TravelPlanJudge.from_env(environ)

        settings = llm_class.call_args.args[0]
        self.assertEqual("secret-for-test", settings.api_key)
        self.assertEqual("independent-judge", settings.model_id)
        self.assertEqual("https://example.test/v1", settings.base_url)
        self.assertEqual("independent-judge", judge.model_id)


class _FakeJudgeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(list(messages))
        return SimpleNamespace(content=self.responses.pop(0))


class _EmptyOnceJudgeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            raise EmptyLLMResponseError("LLM 最终响应内容为空")
        return SimpleNamespace(content=self.response)


def _case(case_id: str):
    return next(case for case in load_cases() if case.case_id == case_id)


def _verdict(scores: dict[str, int]) -> str:
    return json.dumps(
        {
            "scores": {
                name: {
                    "score": score,
                    "reason": f"{name} 的简短理由",
                    "evidence": [f"{name} 的计划证据"],
                }
                for name, score in scores.items()
            },
            "overall_reason": "总体质量结论",
            "critical_issue": None,
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
