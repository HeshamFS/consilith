"""MCP server exposing crystal structures, VESTA-style polyhedra and an interactive viewer.

Run over stdio (Claude Desktop / Claude Code):   crystallography-mcp
Run over Streamable HTTP for remote clients:     crystallography-mcp --http --port 8765
"""

from __future__ import annotations

import argparse
import functools
import json
from typing import Annotated, Literal

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, EmbeddedResource, TextContent, TextResourceContents

from . import __version__, optimade
from .cli import vesta_text
from .core import analyze_spec, material_descriptor, structure_record
from .schema import MaterialDescriptor, PolyhedraReport, StructureRecord, json_schemas
from .structures import get_mp_api_key, list_examples, load_structure, search_mp
from .tables import to_csv
from .viewer import viewer_html

VIEWER_URI = "ui://crystallography/viewer.html"

INSTRUCTIONS = """\
Crystal-structure analysis with VESTA-style coordination polyhedra.

`structure` arguments accept a Materials Project id (mp-5229; needs MP_API_KEY on the server),
a bundled example name (SrTiO3, BaTiO3, CaTiO3, Bi2Se3), or CIF text.
Use analyze_polyhedra to find every polyhedron (per Wyckoff site, Shannon radii) and show the interactive 3D viewer. Use material_descriptor to get a
compact, unit-explicit record for other MCP servers (e.g. phase-field simulation); its JSON
Schema is the resource schema://crystallography/material-descriptor.v1.schema.json.
"""


def _user_errors(fn):
    """Report bad input and missing API keys to the model as readable tool errors."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, RuntimeError, KeyError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def build_server() -> MCPServer:
    apps = Apps()
    apps.add_html_resource(
        VIEWER_URI,
        viewer_html(),
        name="Crystal structure and coordination viewer",
        description="Interactive 3D crystal structure with coordination polyhedra and VESTA-style metrics",
        prefers_border=True,
    )

    @apps.tool(
        resource_uri=VIEWER_URI,
        name="analyze_polyhedra",
        title="Find and analyze all coordination polyhedra",
        description=(
            "Workflow on a crystal structure: (1) coordination environment of every symmetry-distinct "
            "(Wyckoff) site, cations and anions, from self-consistent Shannon ionic radii by default; "
            "(2) which sites can form a polyhedron (≥4 ligands enclosing the centre); (3) VESTA's analyses "
            "for every polyhedron: average bond length, polyhedral volume, distortion index (Baur), effective "
            "coordination number (Hoppe), quadratic elongation and bond-angle variance (Robinson), plus "
            "bond-valence sums, off-centring, shape, corner/edge/face sharing and bridging angles; (4) checks of "
            "Pauling's rules 1-5, global instability index, bond-count balance and site-symmetry invariance. "
            "Renders an interactive 3D viewer. method: 'shannon' (default), 'crystalnn' or 'cutoff' "
            "(needs max_bond, Å). include_element_shells=true also builds the nearest shell of every element "
            "around every site (e.g. the Ti8 cube around Sr)."
        ),
    )
    @_user_errors
    def analyze_polyhedra(
        structure: str,
        centers: list[str] | None = None,
        ligands: list[str] | None = None,
        method: Literal["shannon", "crystalnn", "cutoff"] = "shannon",
        max_bond: float | None = None,
        min_bond: float = 0.0,
        radius_tolerance: float = 0.15,
        include_element_shells: bool = False,
    ) -> Annotated[CallToolResult, PolyhedraReport]:
        report = analyze_spec(
            structure,
            centers=centers,
            ligands=ligands,
            method=method,
            max_bond=max_bond,
            min_bond=min_bond,
            radius_tolerance=radius_tolerance,
            include_element_shells=include_element_shells,
        )
        return CallToolResult(
            content=[TextContent(type="text", text=vesta_text(report))],
            structured_content=report.model_dump(mode="json", by_alias=True),
        )

    mcp = MCPServer(
        name="consilith-crystallography",
        title="Crystal structure and coordination GenUI",
        instructions=INSTRUCTIONS,
        version=__version__,
        extensions=[apps],
    )

    @mcp.tool(
        title="Search Materials Project",
        description=(
            "Search Materials Project for DFT-relaxed materials by formula (e.g. 'SrTiO3'), chemical system "
            "('Ca-Ti-O') or required elements. Returns ids sorted by energy above hull. Needs MP_API_KEY."
        ),
    )
    @_user_errors
    def search_materials(
        formula: str | None = None,
        chemsys: str | None = None,
        elements: list[str] | None = None,
        max_energy_above_hull: float = 0.1,
        limit: int = 20,
    ) -> list[dict]:
        return search_mp(formula, chemsys, elements, max_energy_above_hull, limit)

    @mcp.tool(
        title="Search open databases (OPTIMADE)",
        description=(
            "Search DFT-relaxed structures across open databases through OPTIMADE, in parallel: Materials Project, "
            "OQMD, Alexandria (PBE/PBEsol/SCAN), AFLOW, JARVIS-DFT, NOMAD, Materials Cloud 3D/2D, odbx. "
            "Give a formula ('CaTiO3') or elements (['Ca','Ti','O']). databases: provider keys "
            f"({', '.join(optimade.PROVIDERS)}) or OPTIMADE base URLs; default {', '.join(optimade.DEFAULT_PROVIDERS)}. "
            "Each result's 'structure' string (optimade:<provider>:<id>) can be passed to the other tools. No API key needed."
        ),
    )
    @_user_errors
    def search_open_databases(
        formula: str | None = None,
        elements: list[str] | None = None,
        databases: list[str] | None = None,
        limit_per_database: int = 10,
    ) -> dict:
        return optimade.search(formula, elements, databases, limit_per_database)

    @mcp.tool(
        title="Download polyhedra table (CSV)",
        description=(
            "All polyhedral information for one structure as CSV. table: 'polyhedra' (one row per polyhedron in the "
            "cell, with Wyckoff site, fractional position and every VESTA metric), 'types' (one row per "
            "symmetry-distinct polyhedron), 'sites' (coordination environment of each Wyckoff site), or 'bonds'. "
            "Takes the same analysis options as analyze_polyhedra."
        ),
    )
    @_user_errors
    def export_polyhedra_csv(
        structure: str,
        table: Literal["polyhedra", "types", "sites", "bonds"] = "polyhedra",
        method: Literal["shannon", "crystalnn", "cutoff"] = "shannon",
        max_bond: float | None = None,
        radius_tolerance: float = 0.15,
        include_element_shells: bool = False,
    ) -> CallToolResult:
        report = analyze_spec(
            structure,
            method=method,
            max_bond=max_bond,
            radius_tolerance=radius_tolerance,
            include_element_shells=include_element_shells,
        )
        text = to_csv(report, table)
        name = f"{report.structure.reduced_formula}_{table}.csv"
        n = max(text.count("\n") - 1, 0)
        return CallToolResult(
            content=[
                TextContent(
                    type="text", text=f"{name}: {n} rows. Full CSV attached as a text/csv resource."
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=f"file:///{name}", mime_type="text/csv", text=text
                    ),
                ),
            ]
        )

    @mcp.tool(
        title="Get crystal structure",
        description="Return lattice, space group and all sites (fractional and Cartesian) for a structure.",
    )
    @_user_errors
    def get_structure(structure: str, conventional: bool = True) -> StructureRecord:
        return structure_record(load_structure(structure, conventional=conventional))

    @mcp.tool(
        title="Material descriptor for coupled models",
        description=(
            "Compact, unit-explicit record for mesoscale models such as phase-field: composition, molar volume, "
            "lattice, DFT formation energy and energy above hull, elastic constants when Materials Project has "
            "them, and a per-element polyhedral distortion summary. Schema: crystallography/material-descriptor@1."
        ),
    )
    @_user_errors
    def get_material_descriptor(structure: str) -> MaterialDescriptor:
        return material_descriptor(structure)

    @mcp.tool(
        title="List bundled examples",
        description="Offline example structures that need no API key.",
    )
    @_user_errors
    def list_example_structures() -> dict[str, str]:
        return {
            **list_examples(),
            "_materials_project_key_configured": "yes" if get_mp_api_key() else "no",
        }

    for fname, schema in json_schemas().items():
        _register_schema(mcp, fname, schema)

    return mcp


def _register_schema(mcp: MCPServer, fname: str, schema: dict) -> None:
    @mcp.resource(
        f"schema://crystallography/{fname}", name=fname, mime_type="application/schema+json"
    )
    def _schema() -> str:
        return json.dumps(schema, indent=2)


def app():
    """ASGI factory, matching the standalone microstructure service entry point."""
    return build_server().streamable_http_app(json_response=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crystal structure and coordination GenUI MCP server")
    parser.add_argument(
        "--http", action="store_true", help="serve Streamable HTTP instead of stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = build_server()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
