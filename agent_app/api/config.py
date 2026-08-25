"""Environment configuration owned by the HTTP API layer."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping, Tuple


@dataclass(frozen=True)
class APISettings:
    """Non-secret HTTP and task-runtime settings."""

    database_path: Path = Path("var/travel_plan.db")
    cors_origins: Tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    max_workers: int = 2
    task_timeout_seconds: int = 600
    sse_poll_interval_seconds: float = 0.5
    sse_heartbeat_seconds: float = 15.0
    auth_cookie_name: str = "travel_session"
    auth_session_days: int = 7
    auth_cookie_secure: bool = False
    memory_context_window_tokens: int = 1_000_000
    memory_input_limit_tokens: int = 650_000
    memory_target_tokens: int = 500_000
    memory_recent_assistant_tokens: int = 150_000

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "APISettings":
        source = os.environ if environ is None else environ
        raw_origins = source.get(
            "API_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        )
        origins = tuple(
            origin.strip()
            for origin in raw_origins.split(",")
            if origin.strip()
        )
        return cls(
            database_path=Path(
                source.get(
                    "TRAVEL_API_DB_PATH",
                    "var/travel_plan.db",
                )
            ),
            cors_origins=origins,
            max_workers=_positive_int(
                source,
                "TRAVEL_API_MAX_WORKERS",
                2,
            ),
            task_timeout_seconds=_positive_int(
                source,
                "TRAVEL_API_TASK_TIMEOUT_SECONDS",
                600,
            ),
            sse_poll_interval_seconds=_positive_float(
                source,
                "TRAVEL_API_SSE_POLL_SECONDS",
                0.5,
            ),
            sse_heartbeat_seconds=_positive_float(
                source,
                "TRAVEL_API_SSE_HEARTBEAT_SECONDS",
                15.0,
            ),
            auth_cookie_name=(
                source.get("AUTH_COOKIE_NAME", "travel_session").strip()
                or "travel_session"
            ),
            auth_session_days=_positive_int(
                source,
                "AUTH_SESSION_DAYS",
                7,
            ),
            auth_cookie_secure=_boolean(
                source,
                "AUTH_COOKIE_SECURE",
                False,
            ),
            memory_context_window_tokens=_positive_int(
                source,
                "MEMORY_CONTEXT_WINDOW_TOKENS",
                1_000_000,
            ),
            memory_input_limit_tokens=_positive_int(
                source,
                "MEMORY_INPUT_LIMIT_TOKENS",
                650_000,
            ),
            memory_target_tokens=_positive_int(
                source,
                "MEMORY_TARGET_TOKENS",
                500_000,
            ),
            memory_recent_assistant_tokens=_positive_int(
                source,
                "MEMORY_RECENT_ASSISTANT_TOKENS",
                150_000,
            ),
        )


def _positive_int(
    source: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = source.get(name, "").strip()
    value = default if not raw else int(raw)
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _positive_float(
    source: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw = source.get(name, "").strip()
    value = default if not raw else float(raw)
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _boolean(
    source: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = source.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")
