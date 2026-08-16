"""Small, side-effect-free helpers shared by the Sea Power generator."""

from __future__ import annotations

import re
from typing import Any


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def scalar(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def ini_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


ROLE_PREFIXES = {"friendly": "Taskforce1", "enemy": "Taskforce2", "neutral": "Neutral"}


def role_prefix(role: str) -> str:
    return ROLE_PREFIXES[role]
