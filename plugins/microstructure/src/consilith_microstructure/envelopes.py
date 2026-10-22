"""Result envelopes for the derived tools (report, training set, Adaption bridge).

Light on purpose: no numpy, imaging or solver imports, so the bridge service starts without
them. ``platform_server`` keeps its own envelope for the solver tools.
"""

import base64
import time
from typing import Any

from consilith_contracts import ResultEnvelope
from consilith_contracts.solver import inputs_hash

from . import __version__


def content(raw: bytes, media_type: str, role: str = "output") -> dict[str, str]:
    return {"mediaType": media_type, "contentBase64": base64.b64encode(raw).decode(), "role": role}


def file_output(name: str, raw: bytes, media_type: str, role: str = "output") -> dict[str, Any]:
    return {"name": name, "kind": "artifact", "artifacts": [content(raw, media_type, role)]}


def json_output(name: str, value: Any) -> dict[str, Any]:
    return {"name": name, "kind": "json", "value": value}


def derived_envelope(
    tool: str,
    arguments: dict[str, Any],
    outputs: list[dict[str, Any]],
    started: float,
    *,
    validity: str,
    notes: list[str],
) -> dict[str, Any]:
    """A tier-2, illustrative envelope. ``arguments`` are hashed, never echoed."""
    return ResultEnvelope.model_validate(
        {
            "outputVersion": "1",
            "outputs": outputs,
            "provenance": {
                "tool": tool,
                "solverVersion": __version__,
                "inputsHash": inputs_hash(arguments),
                "wallTimeS": time.perf_counter() - started,
            },
            "validity": {"range": validity, "inside": True},
            "tier": 2,
            "illustrative": True,
            "notes": notes,
        }
    ).wire()
