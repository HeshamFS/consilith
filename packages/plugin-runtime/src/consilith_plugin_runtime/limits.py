"""Shared bounded execution limits for the host and worker supervisor."""

from __future__ import annotations

import math
import os


def plugin_timeout_s() -> float:
    """Allow the 600 s queued tool deadline plus callback and review cleanup."""
    try:
        value = float(os.environ.get("CONSILITH_PLUGIN_TIMEOUT", "660"))
    except ValueError as exc:
        raise ValueError("CONSILITH_PLUGIN_TIMEOUT must be between 1 and 7200 seconds") from exc
    if not math.isfinite(value) or not 1 <= value <= 7200:
        raise ValueError("CONSILITH_PLUGIN_TIMEOUT must be between 1 and 7200 seconds")
    return value
