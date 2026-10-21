"""Standalone MCP execution service for the starter task."""

import base64
from typing import Any, Literal

from consilith_contracts import ResultEnvelope, Tier, Validity, canonical_bytes
from consilith_contracts.solver import envelope, implementation_identity
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__


def prepare_task(
    text: str,
    format: Literal["all", "text", "structured", "artifact"] = "all",
    note: str = "",
    labels: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
    duration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare exact task text, a structured document and/or a downloadable UTF-8 file."""
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("text must contain at most 10000 Unicode code points")
    if format not in {"all", "text", "structured", "artifact"}:
        raise ValueError("unsupported output format")
    document = {
        "text": text,
        "note": note,
        "labels": labels or [],
        "metadata": metadata or {},
        "duration": duration,
    }
    raw = canonical_bytes(document)
    arguments = {
        "text": text,
        "format": format,
        "note": note,
        "labels": labels or [],
        "metadata": metadata or {},
        "duration": duration,
    }
    outputs = []
    if format in {"all", "text"}:
        outputs.append({"name": "prepared_text", "kind": "text", "value": text})
    if format in {"all", "structured"}:
        outputs.append({"name": "task_document", "kind": "json", "value": document})
    if format in {"all", "artifact"}:
        outputs.append(
            {
                "name": "task_file",
                "kind": "artifact",
                "artifacts": [
                    {
                        "mediaType": "application/json",
                        "contentBase64": base64.b64encode(raw).decode("ascii"),
                    }
                ],
            }
        )
    from consilith_contracts.solver import inputs_hash

    return ResultEnvelope.model_validate(
        {
            "outputVersion": "1",
            "outputs": outputs,
            "provenance": {
                "tool": "starter.prepare",
                "solverVersion": __version__,
                "inputsHash": inputs_hash(arguments),
                "wallTimeS": 0,
            },
            "validity": {
                "range": "Exact task serialization; text at most 10000 Unicode code points",
                "inside": True,
            },
            "tier": 1,
        }
    ).wire()


def inspect_text(text: str) -> dict[str, Any]:
    """Count the exact text's Unicode code points, including whitespace; maximum 10000."""
    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("text must contain at most 10000 Unicode code points")
    count = len(text)
    return envelope(
        tool="starter.inspect",
        version=__version__,
        arguments={"text": text},
        quantities=[("character_count", float(count), "")],
        wall_time_s=0,
        validity=Validity(range="Unicode code-point count; not display width", inside=True),
        tier=Tier.QUICK,
        fields={"columns": ["Measure", "Count"], "rows": [["Unicode code points", count]]},
        notes=["Deterministic text operation; the language model supplies no result values."],
    ).wire()


def build_server():
    server = MCPServer(
        "consilith-agentic-starter",
        version=implementation_identity(__file__, __version__),
        instructions="Inspect the supplied text. Return recorded results without inventing values.",
    )
    server.tool(name="starter.inspect")(inspect_text)
    server.tool(name="starter.prepare")(prepare_task)
    return server


def app():
    return build_server().streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "solver-starter:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
