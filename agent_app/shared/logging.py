"""Shared console logging helpers."""

import logging
from typing import Any


def preview(value: Any, max_characters: int = 300) -> str:
    """Return a compact, length-bounded value suitable for console logs."""
    if value is None:
        return "-"
    text = value if isinstance(value, str) else repr(value)
    compact = " ".join(text.split())
    if len(compact) <= max_characters:
        return compact
    return compact[: max_characters - 3] + "..."


def configure_logging(level: str = "INFO") -> None:
    """Configure readable console logs for CLI and end-to-end runs."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format=(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        ),
        datefmt="%H:%M:%S",
        force=True,
    )
