"""Upsert the repository datasets to LangSmith (explicit opt-in only)."""

from __future__ import annotations

import os
from uuid import NAMESPACE_URL, uuid5

from langsmith import Client

from evals.loader import load_cases


DATASET_NAMES = {
    "change_routing": "travel-change-routing-v1",
    "route_policy": "travel-route-policy-v1",
    "initial_plan": "travel-initial-plan-v2",
    "revision": "travel-revision-v1",
}
DATASET_VERSIONS = {
    "change_routing": "v1",
    "route_policy": "v1",
    "initial_plan": "v2",
    "revision": "v1",
}


def require_remote_consent() -> None:
    if os.environ.get("RUN_LANGSMITH_EVALS") != "1":
        raise RuntimeError(
            "为防止意外上传旅行要求，请先显式设置 RUN_LANGSMITH_EVALS=1"
        )


def sync_datasets(client: Client | None = None) -> dict[str, int]:
    require_remote_consent()
    langsmith = client or Client()
    counts: dict[str, int] = {}
    for suite, dataset_name in DATASET_NAMES.items():
        if not langsmith.has_dataset(dataset_name=dataset_name):
            langsmith.create_dataset(
                dataset_name,
                description=(
                    "Travel Plan V2 第一阶段核心评估；本地 JSONL 是事实来源，"
                    "冻结证据不代表实时高德数据。"
                ),
                metadata={
                    "version": DATASET_VERSIONS[suite],
                    "suite": suite,
                    "case_source": "repo",
                },
            )
        examples = []
        for case in load_cases([suite]):
            inputs = {**case.inputs, "_eval_suite": suite, "_case_id": case.case_id}
            examples.append(
                {
                    "id": uuid5(NAMESPACE_URL, f"{dataset_name}:{case.case_id}"),
                    "inputs": inputs,
                    "outputs": case.reference_outputs,
                    "metadata": {
                        **case.metadata,
                        "case_id": case.case_id,
                        "description": case.description,
                    },
                }
            )
        existing_ids = {
            str(example.id)
            for example in langsmith.list_examples(dataset_name=dataset_name)
        }
        new_examples = []
        for example in examples:
            example_id = example["id"]
            if str(example_id) not in existing_ids:
                new_examples.append(example)
                continue
            langsmith.update_example(
                example_id,
                inputs=example["inputs"],
                outputs=example["outputs"],
                metadata=example["metadata"],
            )
        if new_examples:
            langsmith.create_examples(
                dataset_name=dataset_name, examples=new_examples
            )
        counts[dataset_name] = len(examples)
    return counts


def main() -> int:
    counts = sync_datasets()
    for name, count in counts.items():
        print(f"{name}: {count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
