"""Regression tests for the layered package structure."""

from importlib import import_module
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

AGENT_APP_ROOT = PROJECT_ROOT / "agent_app"

LAYERED_MODULES = (
    "agent_app.agents.base",
    "agent_app.agents.attraction",
    "agent_app.agents.weather",
    "agent_app.agents.hotel",
    "agent_app.agents.planner",
    "agent_app.harness.state",
    "agent_app.harness.travel_planning",
    "agent_app.workflows.travel_plan",
    "agent_app.tools.dispatcher",
    "agent_app.tools.schema",
    "agent_app.tools.route",
    "agent_app.infrastructure.llm_client",
    "agent_app.infrastructure.mcp_client",
    "agent_app.infrastructure.amap_client",
    "agent_app.infrastructure.amap_web_client",
    "agent_app.infrastructure.amap_server_runner",
    "agent_app.shared.config",
    "agent_app.shared.logging",
    "agent_app.api.app",
    "agent_app.api.repository",
    "agent_app.api.schemas",
    "agent_app.api.services.task_manager",
    "agent_app.api.services.routes",
    "agent_app.main",
)

LEGACY_FLAT_MODULES = (
    "simple_agent.py",
    "attraction_agent.py",
    "weather_agent.py",
    "hotel_agent.py",
    "planner_agent.py",
    "travel_plan_agent.py",
    "tool_dispatcher.py",
    "tool_schema.py",
    "route_tool.py",
    "llm_client.py",
    "mcp_client.py",
    "amap_mcp.py",
    "amap_mcp_server_runner.py",
    "config.py",
    "logging_utils.py",
)


class ProjectStructureTests(unittest.TestCase):
    def test_layered_modules_are_importable(self):
        for module_name in LAYERED_MODULES:
            with self.subTest(module=module_name):
                self.assertIsNotNone(import_module(module_name))

    def test_legacy_flat_modules_are_removed(self):
        for filename in LEGACY_FLAT_MODULES:
            with self.subTest(filename=filename):
                self.assertFalse(
                    (AGENT_APP_ROOT / filename).exists(),
                    f"旧模块仍存在：agent_app/{filename}",
                )

    def test_infrastructure_does_not_shadow_mcp_dependency(self):
        self.assertFalse(
            (AGENT_APP_ROOT / "infrastructure" / "mcp.py").exists(),
            "请使用 mcp_client.py，避免遮蔽第三方 mcp 包",
        )

    def test_console_script_and_wheel_target_real_application(self):
        configuration = (PROJECT_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'travel-plan-v2 = "agent_app.main:main"',
            configuration,
        )
        self.assertIn('include = ["agent_app*"]', configuration)


if __name__ == "__main__":
    unittest.main(verbosity=2)
