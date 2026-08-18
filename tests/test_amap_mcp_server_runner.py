"""Unit tests for the stdout-safe Amap MCP launcher."""

import builtins
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import call, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_app.infrastructure import amap_server_runner


class AmapMCPServerRunnerTests(unittest.TestCase):
    def test_suppresses_payload_and_keeps_stdout_clean(self):
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            amap_server_runner._stderr_print(
                {"route": "sensitive upstream payload"}
            )

        self.assertEqual("", stdout.getvalue())
        self.assertNotIn("sensitive", stderr.getvalue())
        self.assertIn("output suppressed", stderr.getvalue())

    def test_patches_server_print_before_starting(self):
        events = []
        fake_server = SimpleNamespace(print=builtins.print)
        fake_package = ModuleType("amap_mcp_server")
        fake_package.server = fake_server

        def run_server():
            events.append(fake_server.print)

        fake_package.main = run_server
        with patch.object(
            amap_server_runner,
            "import_module",
            side_effect=(fake_package, fake_server),
        ) as import_module:
            amap_server_runner.main()

        self.assertEqual(
            [
                call("amap_mcp_server"),
                call("amap_mcp_server.server"),
            ],
            import_module.call_args_list,
        )
        self.assertEqual(
            [amap_server_runner._stderr_print],
            events,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
