"""Local authenticated supervisor healthcheck, without importing plugin code."""

import json
import os

from .client import exchange

reference = "tcp://127.0.0.1:8840"
os.environ["CONSILITH_PLUGIN_TOKENS"] = json.dumps(
    {reference: os.environ["CONSILITH_PLUGIN_TOKEN"]}
)
assert exchange(reference, {"op": "health"}, 5)["ready"]
