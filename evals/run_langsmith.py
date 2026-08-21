"""Run a versioned LangSmith experiment after explicit data-send consent."""

from __future__ import annotations

import argparse
from datetime import datetime
from functools import partial
from typing import Any, Mapping

from langsmith import evaluate

from evals.evaluators import evaluate_case
from evals.judges import JUDGE_PROMPT_VERSION, TravelPlanJudge
from evals.live_target import run_live_target
from evals.schemas import EvalCase
from evals.sync_datasets import (
    DATASET_NAMES,
    DATASET_VERSIONS,
    require_remote_consent,
    sync_datasets,
)
from evals.targets import run_target


def _case_from_langsmith(
    suite: str,
    inputs: Mapping[str, Any],
    reference_outputs: Mapping[str, Any],
) -> EvalCase:
    return EvalCase(
        case_id=str(inputs.get("_case_id") or "langsmith-example"),
        suite=suite,  # type: ignore[arg-type]
        description="",
        inputs={
            key: value for key, value in inputs.items() if not key.startswith("_")
        },
        reference_outputs=dict(reference_outputs),
        metadata={},
    )


def _langsmith_evaluator(
    suite: str,
    *,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
    reference_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    case = _case_from_langsmith(suite, inputs, reference_outputs)
    return {"results": evaluate_case(case, outputs)}


def _langsmith_judge_evaluator(
    suite: str,
    judge: TravelPlanJudge,
    *,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
    reference_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    case = _case_from_langsmith(suite, inputs, reference_outputs)
    return {"results": judge.evaluate(case, outputs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=sorted(DATASET_NAMES),
        action="append",
        help="可重复指定；省略时运行四个套件",
    )
    parser.add_argument(
        "--target",
        choices=("fixture", "live"),
        default="fixture",
        help="live 使用真实 LLM + 冻结工具证据；fixture 只验证评估链路",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="为 initial_plan/revision 添加第二阶段 LLM Judge",
    )
    parser.add_argument(
        "--judge-repetitions",
        type=int,
        default=None,
        help="低于 0.70 时的 Judge 最大采样数；设为 3 启用中位数复核",
    )
    args = parser.parse_args(argv)
    require_remote_consent()
    if not args.no_sync:
        sync_datasets()
    suites = args.suite or list(DATASET_NAMES)
    judge = None
    if args.judge:
        if not any(suite in {"initial_plan", "revision"} for suite in suites):
            parser.error("LLM Judge 只适用于 initial_plan 和 revision 套件")
        judge = TravelPlanJudge.from_env(repetitions=args.judge_repetitions)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    for suite in suites:
        target_function = run_live_target if args.target == "live" else run_target
        target = partial(target_function, suite)
        evaluator = partial(_langsmith_evaluator, suite)
        evaluators = [evaluator]
        if judge is not None and suite in {"initial_plan", "revision"}:
            evaluators.append(
                partial(_langsmith_judge_evaluator, suite, judge)
            )
        evaluate(
            target,
            data=DATASET_NAMES[suite],
            evaluators=evaluators,
            experiment_prefix=f"travel-{suite}-{args.target}-{stamp}",
            description=(
                "Travel Plan V2 第一阶段硬指标 + 第二阶段 LLM Judge"
                if len(evaluators) > 1
                else "Travel Plan V2 第一阶段核心评估"
            ),
            metadata={
                "suite": suite,
                "target": args.target,
                "dataset_version": DATASET_VERSIONS[suite],
                "judge_enabled": len(evaluators) > 1,
                "judge_prompt_version": (
                    JUDGE_PROMPT_VERSION if len(evaluators) > 1 else None
                ),
                "judge_model_id": (
                    judge.model_id if len(evaluators) > 1 and judge else None
                ),
            },
            num_repetitions=args.repetitions,
            max_concurrency=args.max_concurrency,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
