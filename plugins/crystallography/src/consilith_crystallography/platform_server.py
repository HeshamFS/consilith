"""Consilith result envelopes around the independent crystal analysis package."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Any

from consilith_contracts import ResultEnvelope, canonical_bytes
from consilith_contracts.base import digest
from consilith_contracts.solver import implementation_identity, inputs_hash
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pymatgen.core import Structure

from . import __version__
from . import report as report_html
from .charts import bond_length_distribution, coordination_histogram
from .cli import vesta_text
from .configuration import EXAMPLE_NAMES, MAX_CIF_BYTES, MAX_SITES, TOOL, AnalysisConfiguration
from .core import analyze_structure
from .structures import LoadedStructure, load_structure
from .tables import TABLES, structure_summary, to_csv, typed_table
from .viewer import viewer_html

logger = logging.getLogger(__name__)

# Output name and typed table of each native table; ``polyhedra_table`` stays the distinct polyhedra.
TYPED_OUTPUTS = (
    ("polyhedra_table", "types"),
    ("site_environments_table", "sites"),
    ("all_polyhedra_table", "polyhedra"),
    ("crystal_checks_table", "checks"),
)


def load_input(structure: str, cif_base64: str = "") -> LoadedStructure:
    """Accept bundled examples or supplied CIF bytes, never paths or remote lookups."""
    if cif_base64:
        if structure:
            raise ValueError("Choose either a structure name/CIF text or an uploaded CIF")
        if len(cif_base64) > 4 * ((MAX_CIF_BYTES + 2) // 3):
            raise ValueError("CIF exceeds the 1 MiB input limit")
        raw = base64.b64decode(cif_base64, validate=True)
    else:
        if len(structure) > MAX_CIF_BYTES:
            raise ValueError("CIF exceeds the 1 MiB input limit")
        raw = structure.strip().encode("utf-8")
    if len(raw) > MAX_CIF_BYTES:
        raise ValueError("CIF exceeds the 1 MiB input limit")
    text = raw.decode("utf-8")
    example = next((name for name in EXAMPLE_NAMES if name.lower() == text.lower()), None)
    if example and not cif_base64:
        loaded = load_structure(example)
    elif "_cell_length_a" in text or "data_" in text[:200]:
        loaded = LoadedStructure(
            Structure.from_str(text, fmt="cif"),
            "cif",
            hashlib.sha256(raw).hexdigest(),
            "Supplied CIF; source identity is the SHA-256 of the supplied bytes",
        )
    else:
        raise ValueError(
            "Supply a bundled example or CIF content; paths and database IDs are unsupported"
        )
    if not 1 <= len(loaded.structure) <= MAX_SITES:
        raise ValueError(f"Platform analysis supports 1–{MAX_SITES} sites per cell")
    if not loaded.structure.is_ordered:
        raise ValueError("Platform analysis requires a fully occupied, ordered structure")
    if min(loaded.structure.lattice.abc) < 2 or loaded.structure.volume < 8:
        raise ValueError(
            "Platform analysis requires cell lengths >= 2 angstrom and volume >= 8 A^3"
        )
    return loaded


def artifact(name: str, raw: bytes, media_type: str, role: str = "output") -> dict:
    return {
        "name": name,
        "kind": "artifact",
        "artifacts": [
            {
                "mediaType": media_type,
                "contentBase64": base64.b64encode(raw).decode(),
                "role": role,
            }
        ],
    }


def json_artifact(name: str, value: dict) -> dict:
    return artifact(name, canonical_bytes(value), "application/json")


def html_report(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    """The marimo report as an artifact, or None and the reason it was left out.

    It needs marimo (the ``platform`` extra); set ``CRYSTAL_GENUI_REPORT=0`` to skip it.
    """
    if os.environ.get("CRYSTAL_GENUI_REPORT", "1") == "0" or not report_html.available():
        return None, None
    try:
        raw = report_html.export_report(payload)
    except RuntimeError as exc:
        logger.warning("crystal report left out: %s", exc)
        return None, "The HTML report could not be produced for this run."
    return artifact("crystal_report", raw, "text/html", role="report"), None


def analyze(
    structure: str = "",
    cif_base64: str = "",
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze an offline crystal; return provenance, JSON reports, table and CSV downloads.

    Supply exactly one of structure (example name or CIF text) and cif_base64 (uploaded CIF).
    All distances are in angstrom. This does not run a phase-field simulation.
    """
    started = time.perf_counter()
    arguments = {"structure": structure, "cif_base64": cif_base64, "configuration": configuration}
    cfg = AnalysisConfiguration.model_validate(configuration or {})
    loaded = load_input(structure, cif_base64)
    report = analyze_structure(loaded, **cfg.model_dump())
    payload = report.model_dump(mode="json", by_alias=True)
    receipt = {
        "inputDigest": digest(arguments),
        "configuration": cfg.model_dump(),
        "source": payload["structure"]["source"],
        "reportDigest": digest(payload),
        "siteCount": len(loaded.structure),
        "experimentalValidation": "not_performed",
    }
    tables = {name: to_csv(report, name) for name in TABLES}
    # The viewer reads the report as ``polyhedra_json`` (the host sends it as structuredContent).
    # Charts and the table are typed artifacts the interface draws; the report is a marimo page.
    drawn = [
        ("coordination_histogram", coordination_histogram(payload)),
        ("bond_length_distribution", bond_length_distribution(payload)),
    ]
    page, report_note = html_report(payload)
    outputs = [
        {"name": "analysis_receipt", "kind": "json", "value": receipt},
        {"name": "polyhedra_report", "kind": "json", "value": payload},
        {"name": "analysis_summary", "kind": "text", "value": vesta_text(report)},
        artifact("polyhedra_json", canonical_bytes(payload), "application/json"),
        artifact("polyhedra_viewer", viewer_html().encode("utf-8"), "text/html", role="app"),
        *[json_artifact(name, chart) for name, chart in drawn if chart is not None],
        {"name": "structure_summary", "kind": "json", "value": structure_summary(report)},
        *[json_artifact(name, typed_table(report, table)) for name, table in TYPED_OUTPUTS],
        *([page] if page is not None else []),
        *[
            artifact(f"{name}_csv", text.encode("utf-8"), "text/csv")
            for name, text in tables.items()
        ],
    ]
    return ResultEnvelope.model_validate(
        {
            "outputVersion": "1",
            "outputs": outputs,
            "provenance": {
                "tool": TOOL,
                "solverVersion": __version__,
                "inputsHash": inputs_hash(arguments),
                "wallTimeS": time.perf_counter() - started,
            },
            "validity": {
                "range": "Geometric coordination analysis of bounded ordered crystal cells",
                "inside": True,
            },
            "tier": 2,
            "illustrative": loaded.source_kind == "example",
            "notes": [
                "Bundled examples are approximate structures, not DFT-relaxed predictions.",
                "Coordination depends on the selected neighbor method and parameters.",
                "Receipt review is not independent scientific or experimental validation.",
                *([report_note] if report_note else []),
            ],
        }
    ).wire()


def build_server():
    server = MCPServer(
        "crystallography-platform",
        version=implementation_identity(__file__, __version__),
        instructions="Analyze supplied crystals and preserve source, units and method limitations.",
    )
    server.tool(name=TOOL)(analyze)
    return server


def app():
    return build_server().streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "solver-crystal:*", "crystal:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
