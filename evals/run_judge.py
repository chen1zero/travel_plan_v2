"""Add phase-two LLM Judge feedback to an existing LangSmith experiment."""

from __future__ import annotations

import argparse
import warnings
from functools import partial
from uuid import UUID

from langsmith import Client, evaluate

from evals.judges import JUDGE_PROMPT_VERSION, TravelPlanJudge
from evals.run_langsmith import _langsmith_judge_evaluator
from evals.sync_datasets import require_remote_consent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        required=True,
        help="已有 LangSmith 实验名称或 ID；不会重新运行 Agent",
    )
    parser.add_argument(
        "--suite",
        required=True,
        choices=("initial_plan", "revision"),
    )
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument(
        "--judge-repetitions",
        type=int,
        default=None,
        help="低于 0.70 时的 Judge 最大采样数；设为 3 启用中位数复核",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="删除该实验已有的 judge_* 反馈后重评；用于升级 Judge 提示词",
    )
    args = parser.parse_args(argv)

    require_remote_consent()
    judge = TravelPlanJudge.from_env(repetitions=args.judge_repetitions)
    client = Client()
    try:
        experiment_id = UUID(args.experiment)
    except ValueError:
        project = client.read_project(project_name=args.experiment)
    else:
        project = client.read_project(project_id=str(experiment_id))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"list_runs\(\) is deprecated.*",
            category=DeprecationWarning,
        )
        runs = list(client.list_runs(project_id=project.id, is_root=True))
    if not runs:
        raise RuntimeError("实验没有可评估的根 Run，未读取或删除任何反馈")
    run_ids = {run.id for run in runs}
    existing = [
        feedback
        for feedback in client.list_feedback(run_ids=list(run_ids))
        if feedback.key.startswith("judge_")
    ]
    unexpected_run_ids = {
        feedback.run_id
        for feedback in existing
        if feedback.run_id not in run_ids
    }
    if unexpected_run_ids:
        raise RuntimeError("反馈范围校验失败，未删除任何反馈")
    if existing and not args.replace_existing:
        raise RuntimeError(
            f"实验已有 {len(existing)} 条 judge_* 反馈。为避免重复平均，"
            "请确认后使用 --replace-existing，或选择另一实验。"
        )
    if args.replace_existing:
        for feedback in existing:
            client.delete_feedback(feedback.id)
        print(f"已清理旧 Judge 反馈：{len(existing)} 条")

    results = evaluate(
        args.experiment,
        evaluators=[
            partial(_langsmith_judge_evaluator, args.suite, judge)
        ],
        metadata={
            "judge_prompt_version": JUDGE_PROMPT_VERSION,
            "judge_model_id": judge.model_id,
            "judge_repetitions": judge.repetitions,
            "judge_mode": "report_only",
        },
        max_concurrency=args.max_concurrency,
        client=client,
    )
    print(f"Judge 评分完成：{results.experiment_name}")
    if results.url:
        print(results.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
