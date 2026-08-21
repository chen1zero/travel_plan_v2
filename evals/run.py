"""Run the 32 phase-one cases locally; no network is used by default."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable

from evals.evaluators import aggregate_scores, evaluate_case
from evals.gates import failed_gates
from evals.loader import load_cases
from evals.schemas import EvalCase, SuiteName
from evals.targets import run_target


SUITE_ALIASES: dict[str, tuple[SuiteName, ...]] = {
    "all": ("change_routing", "route_policy", "initial_plan", "revision"),
    "components": ("change_routing", "route_policy"),
    "smoke": ("initial_plan", "revision"),
    "change-routing": ("change_routing",),
    "route-policy": ("route_policy",),
    "initial-plan": ("initial_plan",),
    "revision": ("revision",),
}


def run_cases(cases: Iterable[EvalCase]) -> tuple[list[dict], dict[str, float]]:
    rows: list[dict] = []
    all_scores = []
    for case in cases:
        try:
            output = run_target(case.suite, case.inputs)
        except Exception as exc:  # Evaluation must report failures, not abort.
            output = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        scores = evaluate_case(case, output)
        all_scores.append(scores)
        rows.append(
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "passed": all(float(score["score"]) == 1.0 for score in scores),
                "scores": scores,
            }
        )
    return rows, aggregate_scores(all_scores)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITE_ALIASES), default="all")
    parser.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON，而不是简表"
    )
    args = parser.parse_args(argv)
    cases = load_cases(SUITE_ALIASES[args.suite])
    rows, aggregates = run_cases(cases)
    failures = failed_gates(aggregates)
    if args.json:
        print(
            json.dumps(
                {"cases": rows, "aggregates": aggregates, "failed_gates": failures},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for row in rows:
            print(f"{'PASS' if row['passed'] else 'FAIL'}  {row['case_id']}  {row['suite']}")
        print(f"\n共 {len(rows)} 条：{sum(row['passed'] for row in rows)} 通过")
        for key, score in aggregates.items():
            print(f"  {key:<30} {score:>7.1%}")
        if failures:
            print("\n门禁失败：", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
    return 1 if failures or any(not row["passed"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
