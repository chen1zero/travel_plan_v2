"""Unit coverage for the optional LangSmith integration."""

from unittest.mock import MagicMock, patch
import json
import os
import unittest

from agent_app.observability.langsmith import (
    is_langsmith_tracing_enabled,
    langsmith_status,
    run_traced_tool,
    wrap_openai_client,
)


class LangSmithObservabilityTests(unittest.TestCase):
    def test_status_never_exposes_the_api_key(self):
        status = langsmith_status(
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_API_KEY": "secret-key",
                "LANGSMITH_PROJECT": "travel-tests",
                "LANGSMITH_WORKSPACE_ID": "workspace-test",
            }
        )

        self.assertTrue(status["enabled"])
        self.assertTrue(status["ready"])
        self.assertEqual("travel-tests", status["project"])
        self.assertTrue(status["workspace_configured"])
        self.assertNotIn("secret-key", repr(status))

    def test_disabled_tool_trace_is_a_zero_network_noop(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
            with patch(
                "agent_app.observability.langsmith.ls.trace"
            ) as trace:
                result = run_traced_tool(
                    "maps_weather",
                    '{"city": "杭州"}',
                    lambda: '{"weather": "晴"}',
                )

        self.assertEqual('{"weather": "晴"}', result)
        trace.assert_not_called()

    def test_enabled_tool_trace_records_structured_input_and_output(self):
        run = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = run
        with patch.dict(
            os.environ,
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_API_KEY": "test-key",
            },
        ):
            with patch(
                "agent_app.observability.langsmith.ls.trace",
                return_value=context,
            ) as trace:
                result = run_traced_tool(
                    "maps_weather",
                    '{"city": "杭州"}',
                    lambda: '{"weather": "晴"}',
                )

        self.assertEqual('{"weather": "晴"}', result)
        self.assertEqual(
            {"arguments": {"city": "杭州"}},
            trace.call_args.kwargs["inputs"],
        )
        run.end.assert_called_once_with(
            outputs={"result": {"weather": "晴"}}
        )

    def test_failed_tool_result_marks_trace_as_error(self):
        run = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = run
        result_json = (
            '{"ok":false,"error":"工具执行失败",'
            '"error_details":{"message":"外部服务调用超时"}}'
        )
        with patch.dict(
            os.environ,
            {
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_API_KEY": "test-key",
            },
        ):
            with patch(
                "agent_app.observability.langsmith.ls.trace",
                return_value=context,
            ):
                run_traced_tool(
                    "maps_weather",
                    '{"city":"杭州"}',
                    lambda: result_json,
                )

        run.end.assert_called_once_with(
            outputs={"result": json.loads(result_json)},
            error="外部服务调用超时",
        )

    def test_openai_wrapper_is_only_applied_when_enabled(self):
        client = object()
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
            self.assertIs(
                client,
                wrap_openai_client(client, model_id="test-model"),
            )
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}):
            with patch(
                "agent_app.observability.langsmith.wrap_openai",
                return_value="wrapped",
            ) as wrapper:
                wrapped = wrap_openai_client(
                    client,
                    model_id="test-model",
                )

        self.assertEqual("wrapped", wrapped)
        wrapper.assert_called_once()

    def test_truthy_switches_are_supported(self):
        for value in ("1", "true", "YES", "on"):
            self.assertTrue(
                is_langsmith_tracing_enabled(
                    {"LANGSMITH_TRACING": value}
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
