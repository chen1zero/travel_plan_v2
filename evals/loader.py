"""Load the repository-owned JSONL datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from evals.schemas import EvalCase, SuiteName


ROOT = Path(__file__).resolve().parent
CASE_DIR = ROOT / "cases"
CASE_FILES_BY_VERSION = {
    "v1": (
        CASE_DIR / "change_routing.v1.jsonl",
        CASE_DIR / "route_policy.v1.jsonl",
        CASE_DIR / "initial_plan.v1.jsonl",
        CASE_DIR / "revision.v1.jsonl",
    ),
    "v2": (
        CASE_DIR / "change_routing.v1.jsonl",
        CASE_DIR / "route_policy.v1.jsonl",
        CASE_DIR / "initial_plan.v2.jsonl",
        CASE_DIR / "revision.v1.jsonl",
    ),
}


def load_cases(
    suites: Optional[Iterable[SuiteName]] = None, *, version: str = "v2"
) -> list[EvalCase]:
    """Return cases in stable file/line order and reject duplicate IDs."""
    try:
        case_files = CASE_FILES_BY_VERSION[version]
    except KeyError as exc:
        raise ValueError(f"未知评估数据集版本：{version}") from exc
    selected = set(suites) if suites is not None else None
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in case_files:
        with path.open(encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    case = EvalCase.from_dict(json.loads(line))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"{path.name}:{line_number}: {exc}") from exc
                if case.case_id in seen:
                    raise ValueError(f"评估用例 ID 重复：{case.case_id}")
                seen.add(case.case_id)
                if selected is None or case.suite in selected:
                    cases.append(case)
    return cases
