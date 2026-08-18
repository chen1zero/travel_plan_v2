"""Unit tests for one-step Chat Completions decisions."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure.llm_client import OpenAICompatibleLLM
from agent_app.infrastructure.mcp_client import (
    mcp_tool_to_openai_schema,
)
from agent_app.shared.config import Settings


def _response(finish_reason, content=None, tool_calls=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(
        message=message,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(
        choices=[choice],
        _request_id="request_test",
    )


def _tool_call(call_id="call_weather"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name="query_weather_forecast",
            arguments='{"city":"上海"}',
        ),
    )


WEATHER_TOOL_SCHEMA = mcp_tool_to_openai_schema(
    {
        "name": "maps_weather",
        "description": "查询城市天气",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
)


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **arguments):
        self.calls.append(arguments)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(responses)
        )


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            api_key="test-key",
            model_id="test-model",
            base_url="https://example.invalid",
            amap_api_key="amap-test-key",
        )

    def test_returns_tool_calls_finish_reason(self):
        fake_client = _FakeClient(
            [
                _response(
                    "tool_calls",
                    tool_calls=[_tool_call()],
                )
            ]
        )
        llm = OpenAICompatibleLLM(
            self.settings,
            tool_schemas=[WEATHER_TOOL_SCHEMA],
            client=fake_client,
        )

        result = llm.complete(
            [{"role": "user", "content": "上海天气如何？"}]
        )

        self.assertEqual("tool_calls", result.finish_reason)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual(
            "query_weather_forecast",
            result.tool_calls[0].name,
        )
        self.assertEqual(1, len(fake_client.chat.completions.calls))
        request = fake_client.chat.completions.calls[0]
        self.assertEqual("auto", request["tool_choice"])
        self.assertEqual(
            "maps_weather",
            request["tools"][0]["function"]["name"],
        )
        self.assertEqual(1, len(request["tools"]))

    def test_add_tool_updates_future_requests(self):
        fake_client = _FakeClient(
            [_response("stop", content="完成")]
        )
        llm = OpenAICompatibleLLM(
            self.settings,
            client=fake_client,
        )

        llm.add_tool(WEATHER_TOOL_SCHEMA)
        llm.complete([{"role": "user", "content": "天气"}])

        request = fake_client.chat.completions.calls[0]
        self.assertEqual(
            "maps_weather",
            request["tools"][0]["function"]["name"],
        )

    def test_returns_stop_finish_reason_without_default_tools(self):
        fake_client = _FakeClient(
            [_response("stop", content="上海当前天气为晴。")]
        )
        llm = OpenAICompatibleLLM(
            self.settings,
            client=fake_client,
        )

        result = llm.complete(
            [{"role": "user", "content": "你好"}]
        )

        self.assertEqual("stop", result.finish_reason)
        self.assertEqual("上海当前天气为晴。", result.content)
        request = fake_client.chat.completions.calls[0]
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_settings_loads_and_hides_amap_key(self):
        settings = Settings.from_env(
            env_file=None,
            environ={
                "LLM_API_KEY": "llm-secret",
                "LLM_MODEL_ID": "test-model",
                "LLM_BASE_URL": "https://example.invalid",
                "AMAP_API_KEY": "amap-secret",
            },
        )

        self.assertEqual("amap-secret", settings.amap_api_key)
        self.assertEqual(
            ("uvx", "amap-mcp-server"),
            settings.amap_mcp_command,
        )
        self.assertEqual(30.0, settings.amap_mcp_timeout_seconds)
        self.assertNotIn("llm-secret", repr(settings))
        self.assertNotIn("amap-secret", repr(settings))

    def test_settings_reject_tracing_without_langsmith_key(self):
        with self.assertRaisesRegex(
            ValueError,
            "LANGSMITH_API_KEY",
        ):
            Settings.from_env(
                env_file=None,
                environ={
                    "LLM_API_KEY": "llm-secret",
                    "LLM_MODEL_ID": "test-model",
                    "LLM_BASE_URL": "https://example.invalid",
                    "LANGSMITH_TRACING": "true",
                },
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
