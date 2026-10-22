import json
import os
from importlib.resources import files

import anyio
import jsonschema
import pytest
from mcp import Client

from crystal_genui.cli import standalone_html
from crystal_genui.core import analyze_spec, material_descriptor
from crystal_genui.schema import json_schemas
from crystal_genui.server import VIEWER_URI, build_server

SCHEMA_DIR = files("crystal_genui").joinpath("schemas")


def test_schema_files_are_current():
    for name, schema in json_schemas().items():
        on_disk = json.loads((SCHEMA_DIR / name).read_text())
        assert on_disk == schema, f"{name} is stale: run python -m crystal_genui.schema"


def test_outputs_validate_against_schemas():
    schemas = json_schemas()
    report = json.loads(analyze_spec("CaTiO3", include_element_shells=True).model_dump_json(by_alias=True))
    jsonschema.validate(report, schemas["polyhedra.v1.schema.json"])
    jsonschema.validate(report["structure"], schemas["structure.v1.schema.json"])
    desc = json.loads(material_descriptor("BaTiO3").model_dump_json(by_alias=True))
    jsonschema.validate(desc, schemas["material-descriptor.v1.schema.json"])
    assert desc["schema"] == "crystal-genui/material-descriptor@1"
    assert desc["molar_volume"] == pytest.approx(3.87e-5, rel=0.01)  # m^3/mol for BaTiO3


def test_mcp_server_in_process():
    async def run():
        async with Client(build_server()) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert tools["analyze_polyhedra"].meta["ui"]["resourceUri"] == VIEWER_URI
            assert {"search_materials", "search_open_databases", "export_polyhedra_csv", "get_structure", "get_material_descriptor"} <= set(tools)

            res = await c.read_resource(VIEWER_URI)
            assert res.contents[0].mime_type == "text/html;profile=mcp-app"

            r = await c.call_tool("analyze_polyhedra", {"structure": "SrTiO3"})
            assert not r.is_error
            assert r.structured_content["schema"] == "crystal-genui/polyhedra@1"
            assert "Average bond length" in r.content[0].text

            r = await c.call_tool("analyze_polyhedra", {"structure": "SrTiO3", "method": "cutoff"})
            assert r.is_error and "max_bond" in r.content[0].text

            r = await c.call_tool("export_polyhedra_csv", {"structure": "SrTiO3", "table": "types"})
            assert not r.is_error
            res = [c for c in r.content if c.type == "resource"][0].resource
            assert res.mime_type == "text/csv" and res.text.startswith("structure,formula")

    anyio.run(run)


def test_standalone_html_embeds_data():
    html = standalone_html(analyze_spec("SrTiO3"))
    assert "window.__CRYSTAL_GENUI_DATA__" in html and "crystal-genui/polyhedra@1" in html


@pytest.mark.skipif(not os.environ.get("MP_API_KEY"), reason="needs MP_API_KEY")
def test_materials_project_live():
    r = analyze_spec("mp-5229")  # SrTiO3, DFT-relaxed
    ti = next(p for p in r.polyhedra if p.center_element == "Ti")
    assert ti.metrics.coordination_number == 6
    assert r.structure.source.kind == "materials_project"


def test_csv_tables():
    import csv
    import io

    from crystal_genui.tables import TABLES, to_csv

    r = analyze_spec("CaTiO3")
    for t in TABLES:
        rows = list(csv.DictReader(io.StringIO(to_csv(r, t))))
        assert rows, t
    poly = list(csv.DictReader(io.StringIO(to_csv(r, "polyhedra"))))
    assert len(poly) == len(r.polyhedra) == 20
    assert {"wyckoff", "x", "y", "z", "polyhedral_volume_A3", "distortion_index", "effective_coordination_number"} <= set(poly[0])


def test_optimade_entry_to_structure():
    """Parser check on a minimal OPTIMADE v1 structures entry (spec-shaped, not real database output)."""
    from crystal_genui.optimade import build_filter, optimade_formula, to_structure

    entry = {
        "id": "test-1",
        "type": "structures",
        "attributes": {
            "lattice_vectors": [[3.905, 0, 0], [0, 3.905, 0], [0, 0, 3.905]],
            "cartesian_site_positions": [[0, 0, 0], [1.9525, 1.9525, 1.9525], [0, 1.9525, 1.9525], [1.9525, 0, 1.9525], [1.9525, 1.9525, 0]],
            "species_at_sites": ["Sr", "Ti", "O", "O", "O"],
            "species": [{"name": n, "chemical_symbols": [n], "concentration": [1.0]} for n in ("Sr", "Ti", "O")],
            "dimension_types": [1, 1, 1],
        },
    }
    s, warnings = to_structure(entry)
    assert s.composition.reduced_formula == "SrTiO3" and not warnings
    assert optimade_formula("CaTiO3") == "CaO3Ti"
    assert build_filter(elements=["Ca", "Ti", "O"]) == 'elements HAS ALL "Ca","Ti","O" AND nelements=3'
