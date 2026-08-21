"""Small, dependency-light contracts for local and LangSmith evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal


SuiteName = Literal[
    "change_routing",
    "route_policy",
    "initial_plan",
    "revision",
]


@dataclass(frozen=True)
class EvalCase:
    """One versioned example shared by local CI and LangSmith."""

    case_id: str
    suite: SuiteName
    description: str
    inputs: Dict[str, Any]
    reference_outputs: Dict[str, Any]
    metadata: Dict[str, Any]

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "EvalCase":
        required = {
            "case_id",
            "suite",
            "description",
            "inputs",
            "reference_outputs",
            "metadata",
        }
        missing = sorted(required.difference(value))
        if missing:
            raise ValueError(f"评估用例缺少字段：{', '.join(missing)}")
        suite = value["suite"]
        if suite not in {
            "change_routing",
            "route_policy",
            "initial_plan",
            "revision",
        }:
            raise ValueError(f"未知评估套件：{suite}")
        if not isinstance(value["inputs"], dict):
            raise ValueError("inputs 必须是 JSON 对象")
        if not isinstance(value["reference_outputs"], dict):
            raise ValueError("reference_outputs 必须是 JSON 对象")
        if not isinstance(value["metadata"], dict):
            raise ValueError("metadata 必须是 JSON 对象")
        return cls(
            case_id=str(value["case_id"]),
            suite=suite,
            description=str(value["description"]),
            inputs=dict(value["inputs"]),
            reference_outputs=dict(value["reference_outputs"]),
            metadata=dict(value["metadata"]),
        )
