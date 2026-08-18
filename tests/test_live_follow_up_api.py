"""Opt-in live HTTP regression for multi-turn follow-up requirements."""

from __future__ import annotations

import json
import os
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.getenv("LIVE_API_BASE_URL", "http://127.0.0.1:8000/api")
PREVIOUS_PLAN_ID = os.getenv("LIVE_PREVIOUS_PLAN_ID")
LIVE_ENABLED = os.getenv("RUN_LIVE_API_TESTS") == "1"


def _request_json(method: str, path: str, payload=None):
    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": f"live-follow-up-{uuid4().hex}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise AssertionError(f"{method} {path} 返回 {exc.code}: {detail}") from exc


@unittest.skipUnless(
    LIVE_ENABLED and PREVIOUS_PLAN_ID,
    "设置 RUN_LIVE_API_TESTS=1 和 LIVE_PREVIOUS_PLAN_ID 后运行真实追问测试",
)
class LiveFollowUpAPITests(unittest.TestCase):
    def _follow_up(self, envelope, instruction):
        summary = envelope["plan"]["request_summary"]
        payload = {
            "destination_city": summary["destination_city"],
            "start_date": summary["start_date"],
            "end_date": summary["end_date"],
            "preferences": summary["preferences"],
            "budget_cny": summary["budget_cny"],
            "accommodation_type": summary["hotel_requirement"],
            "additional_requirements": instruction,
            "session_id": envelope["session_id"],
            "previous_plan_id": envelope["plan_id"],
        }
        task = _request_json("POST", "/travel-plans", payload)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            current = _request_json(
                "GET", f"/travel-plans/{task['task_id']}"
            )
            if current["status"] == "failed":
                self.fail(
                    f"追问“{instruction}”失败：{current['error_message']}"
                )
            if current["status"] == "completed":
                return _request_json(
                    "GET", f"/plans/{current['plan_id']}"
                )
            time.sleep(1)
        self.fail(f"追问“{instruction}”在 600 秒内未完成")

    def test_live_follow_up_cases(self):
        envelope = _request_json("GET", f"/plans/{PREVIOUS_PLAN_ID}")
        results = []

        previous_schedule = envelope["plan"]["daily_itinerary"][0][
            "schedule"
        ]
        previous_keys = {
            item.get("schedule_item_id") or item.get("place_name")
            for item in previous_schedule
        }
        envelope = self._follow_up(
            envelope,
            "第一天晚上安排一个夜景的景点",
        )
        schedule = envelope["plan"]["daily_itinerary"][0]["schedule"]
        added = [
            item
            for item in schedule
            if (item.get("schedule_item_id") or item.get("place_name"))
            not in previous_keys
        ]
        self.assertGreater(len(schedule), len(previous_schedule))
        self.assertTrue(
            any(int(item["time_slot"].split(":", 1)[0]) >= 17 for item in added)
        )
        results.append(("夜景", envelope["plan_id"], "通过"))

        previous_hotel = envelope["plan"]["selected_hotel"]["name"]
        envelope = self._follow_up(envelope, "换个酒店")
        self.assertNotEqual(
            previous_hotel,
            envelope["plan"]["selected_hotel"]["name"],
        )
        results.append(("换个酒店", envelope["plan_id"], "通过"))

        previous_count = len(
            envelope["plan"]["daily_itinerary"][0]["schedule"]
        )
        envelope = self._follow_up(envelope, "第一天多加一个景点")
        self.assertGreater(
            len(envelope["plan"]["daily_itinerary"][0]["schedule"]),
            previous_count,
        )
        results.append(("第一天多加一个景点", envelope["plan_id"], "通过"))

        previous_hotel = envelope["plan"]["selected_hotel"]["name"]
        envelope = self._follow_up(envelope, "改住豪华型")
        self.assertEqual(
            "豪华型",
            envelope["plan"]["request_summary"]["hotel_requirement"],
        )
        self.assertNotEqual(
            previous_hotel,
            envelope["plan"]["selected_hotel"]["name"],
        )
        results.append(("改住豪华型", envelope["plan_id"], "通过"))

        envelope = self._follow_up(envelope, "预算改成 8000")
        self.assertEqual(
            8000,
            envelope["plan"]["request_summary"]["budget_cny"],
        )
        self.assertEqual(
            8000,
            envelope["plan"]["budget_summary"]["total_budget"],
        )
        results.append(("预算改成 8000", envelope["plan_id"], "通过"))

        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
