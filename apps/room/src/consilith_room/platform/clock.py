"""Times on the wire are ISO 8601 UTC with milliseconds."""

from __future__ import annotations

import time


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts * 1000) % 1000:03d}Z"
