"""Unit tests for JSON tool-call dispatching."""

import json
from pathlib import Path
import sys
import unittest
from urllib.error import URLError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.tools.dispatcher import dispatch_tool_call


class ToolDispatcherTests(unittest.TestCase):
    def test_dispatches_known_tool(self):
        output = dispatch_tool_call(
            "maps_weather",
            '{"city":"上海"}',
            handlers={
                "maps_weather": lambda **arguments: arguments,
            },
        )

        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual("上海", payload["result"]["city"])

    def test_returns_tool_error_as_json(self):
        output = dispatch_tool_call(
            "unknown_tool",
            "{}",
            handlers={},
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual("unknown_tool", payload["tool"])
        self.assertIn("未知工具", payload["error"])

    def test_returns_handler_exception_as_json(self):
        def failing_handler(**_arguments):
            raise RuntimeError("地图服务不可用")

        output = dispatch_tool_call(
            "maps_weather",
            '{"city":"北京"}',
            handlers={"maps_weather": failing_handler},
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual("maps_weather", payload["tool"])
        self.assertEqual("工具执行失败", payload["error"])

    def test_rejects_non_object_arguments(self):
        output = dispatch_tool_call(
            "maps_weather",
            '["北京"]',
            handlers={"maps_weather": lambda **arguments: arguments},
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertIn("JSON 对象", payload["error"])

    def test_rejects_invalid_json_with_structured_error(self):
        output = dispatch_tool_call(
            "maps_weather",
            '{"city":',
            handlers={"maps_weather": lambda **arguments: arguments},
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            "INVALID_ARGUMENTS",
            payload["error_details"]["code"],
        )
        self.assertFalse(payload["error_details"]["retryable"])

    def test_validates_arguments_against_tool_schema(self):
        output = dispatch_tool_call(
            "maps_weather",
            '{"city":3,"days":2}',
            handlers={"maps_weather": lambda **arguments: arguments},
            schemas={
                "maps_weather": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                    "additionalProperties": False,
                }
            },
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            "INVALID_ARGUMENTS",
            payload["error_details"]["code"],
        )

    def test_retries_timeout_once_then_returns_success(self):
        calls = []
        retries = []

        def sometimes_slow(**_arguments):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("服务超时")
            return {"city": "北京"}

        output = dispatch_tool_call(
            "maps_weather",
            '{"city":"北京"}',
            handlers={"maps_weather": sometimes_slow},
            retry_backoff_seconds=0,
            on_retry=lambda attempt, maximum, failure: retries.append(
                (attempt, maximum, failure.code)
            ),
        )

        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(2, len(calls))
        self.assertEqual([(2, 2, "TOOL_TIMEOUT")], retries)

    def test_retries_network_error_once_then_returns_failure(self):
        calls = []

        def offline(**_arguments):
            calls.append(1)
            raise URLError("offline")

        output = dispatch_tool_call(
            "maps_weather",
            '{"city":"北京"}',
            handlers={"maps_weather": offline},
            retry_backoff_seconds=0,
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(2, len(calls))
        self.assertEqual(
            "NETWORK_ERROR",
            payload["error_details"]["code"],
        )

    def test_rejects_non_json_serializable_tool_result(self):
        output = dispatch_tool_call(
            "bad_result",
            "{}",
            handlers={"bad_result": lambda: {object()}},
            retry_backoff_seconds=0,
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            "INVALID_TOOL_RESULT",
            payload["error_details"]["code"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
