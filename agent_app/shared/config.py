"""Shared environment-based application configuration."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
from typing import Mapping, Optional, Tuple, Union

from dotenv import load_dotenv

from agent_app.observability.langsmith import (
    is_langsmith_tracing_enabled,
)


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    api_key: str = field(repr=False)
    model_id: str
    base_url: str
    timeout_seconds: float = 60.0
    max_retries: int = 2
    amap_api_key: str = field(default="", repr=False)
    amap_mcp_command: Tuple[str, ...] = (
        "uvx",
        "amap-mcp-server",
    )
    amap_mcp_timeout_seconds: float = 30.0

    @classmethod
    def from_env(
        cls,
        env_file: Optional[Union[str, Path]] = ".env",
        environ: Optional[Mapping[str, str]] = None,
    ) -> "Settings":
        """Load settings from an optional .env file and process environment."""
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)

        source = os.environ if environ is None else environ
        required = {
            "LLM_API_KEY": source.get("LLM_API_KEY", "").strip(),
            "LLM_MODEL_ID": source.get("LLM_MODEL_ID", "").strip(),
            "LLM_BASE_URL": source.get("LLM_BASE_URL", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                "缺少必需的环境变量: " + ", ".join(sorted(missing))
            )
        if is_langsmith_tracing_enabled(source) and not source.get(
            "LANGSMITH_API_KEY", ""
        ).strip():
            raise ConfigurationError(
                "已启用 LANGSMITH_TRACING，但缺少 LANGSMITH_API_KEY"
            )

        timeout_seconds = cls._read_positive_float(
            source, "LLM_TIMEOUT_SECONDS", 60.0
        )
        max_retries = cls._read_non_negative_int(source, "LLM_MAX_RETRIES", 2)
        amap_mcp_command = tuple(
            shlex.split(
                source.get(
                    "AMAP_MCP_COMMAND",
                    "uvx amap-mcp-server",
                )
            )
        )
        if not amap_mcp_command:
            raise ConfigurationError("AMAP_MCP_COMMAND 不能为空")
        amap_mcp_timeout_seconds = cls._read_positive_float(
            source,
            "AMAP_MCP_TIMEOUT_SECONDS",
            30.0,
        )
        return cls(
            api_key=required["LLM_API_KEY"],
            model_id=required["LLM_MODEL_ID"],
            base_url=required["LLM_BASE_URL"].rstrip("/"),
            amap_api_key=source.get("AMAP_API_KEY", "").strip(),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            amap_mcp_command=amap_mcp_command,
            amap_mcp_timeout_seconds=amap_mcp_timeout_seconds,
        )

    @staticmethod
    def _read_positive_float(
        source: Mapping[str, str], name: str, default: float
    ) -> float:
        raw_value = source.get(name)
        if raw_value is None or not raw_value.strip():
            return default
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ConfigurationError(f"{name} 必须是数字") from exc
        if value <= 0:
            raise ConfigurationError(f"{name} 必须大于 0")
        return value

    @staticmethod
    def _read_non_negative_int(
        source: Mapping[str, str], name: str, default: int
    ) -> int:
        raw_value = source.get(name)
        if raw_value is None or not raw_value.strip():
            return default
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ConfigurationError(f"{name} 必须是整数") from exc
        if value < 0:
            raise ConfigurationError(f"{name} 不能小于 0")
        return value
