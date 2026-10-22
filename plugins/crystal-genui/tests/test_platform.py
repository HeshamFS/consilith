"""Exercise the actual Consilith contracts and the independent scientific package."""

import base64
import hashlib
import importlib.resources
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

pytest.importorskip("consilith_contracts")

from consilith_contracts import ResultEnvelope
from consilith_contracts.base import digest
from consilith_contracts.plugins import PluginManifest
from consilith_contracts.results import ArtifactRef, RunManifest
from consilith_contracts.review import ReviewItem, ReviewRequest
from crystal_genui.adapter import MANIFEST, checker, extension, workflow
from crystal_genui.configuration import CASE, MAX_CIF_BYTES, TOOL
from crystal_genui.platform_server import analyze, build_server, load_input
from crystal_genui.structures import load_structure
from mcp import Client


@pytest.fixture(scope="module")
def result():
    return ResultEnvelope.model_validate(analyze("SrTiO3"))


def test_crystal_outputs_use_platform_envelope(result):
    outputs = {item.name: item for item in result.outputs}
    report = outputs["polyhedra_report"].value
    titanium = next(p for p in report["polyhedra"] if p["center_element"] == "Ti")
    assert titanium["metrics"]["coordination_number"] == 6
    assert titanium["metrics"]["average_bond_length"] == pytest.approx(1.9525)
    assert outputs["analysis_receipt"].value["reportDigest"] == digest(report)
    assert result.quantities == []
    assert result.illustrative
    assert json.loads(outputs["polyhedra_json"].artifacts[0].bytes()) == report
    assert outputs["types_csv"].artifacts[0].bytes().startswith(b"structure,formula")


def test_uploaded_cif_has_byte_identity():
    raw = load_structure("SrTiO3").structure.to(fmt="cif").encode()
    loaded = load_input("", base64.b64encode(raw).decode())
    assert loaded.source_kind == "cif"
    assert loaded.source_id == hashlib.sha256(raw).hexdigest()
    envelope = ResultEnvelope.model_validate(analyze(cif_base64=base64.b64encode(raw).decode()))
    assert not envelope.illustrative


@pytest.mark.parametrize(
    "spec", ["/tmp/secret.cif", "mp-5229", "https://example.com/x.cif", "optimade:oqmd:1"]
)
def test_platform_rejects_paths_and_network_sources(spec, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unsupported input reached the standalone path/network loader")

    monkeypatch.setattr("crystal_genui.platform_server.load_structure", unexpected)
    with pytest.raises(ValueError, match="paths and database IDs"):
        load_input(spec)


def test_bounded_and_unambiguous_inputs():
    with pytest.raises(ValueError, match="1 MiB"):
        load_input("x" * (MAX_CIF_BYTES + 1))
    with pytest.raises(ValueError, match="either"):
        load_input("SrTiO3", "YQ==")
    with pytest.raises(ValueError, match="max_bond"):
        analyze("SrTiO3", configuration={"method": "cutoff"})
    with pytest.raises(ValueError, match="Set method"):
        analyze("SrTiO3", configuration={"max_bond": 2.2})
    with pytest.raises(ValueError):
        analyze("SrTiO3", configuration={"radius_tolerance": float("nan")})


def test_registry_manifest_and_packaged_resources():
    sections = extension()
    manifest = PluginManifest.model_validate(sections["plugin"]["manifest"])
    assert manifest.capabilities == [CASE]
    case = sections["cases"][CASE]
    assert case.lane_ids() == ["crystal"]
    root = importlib.resources.files("crystal_genui")
    for resource in [*manifest.skills, *manifest.knowledge]:
        assert root.joinpath(resource.path).read_text()
    assert root.joinpath("schemas/polyhedra.v1.schema.json").is_file()


def test_isolated_plugin_registration():
    pytest.importorskip("consilith_plugin_runtime")
    from consilith_contracts.extensions import (
        all_extensions,
        clear_extensions,
        configure_extension_loader,
        load_extensions,
    )
    from consilith_plugin_runtime.client import load

    configure_extension_loader(load)
    try:
        load_extensions(["crystal_genui.adapter:extension"])
        assert CASE in all_extensions("cases")
        assert "crystal-analyze" in all_extensions("skill_documents")
        assert "crystal.scope" in all_extensions("knowledge_documents")
    finally:
        clear_extensions()


def test_deployment_manifest_matches_plugin():
    # Available when this package is checked out under the Consilith source tree.
    root = Path(__file__).resolve().parents[3]
    path = root / "examples/deployment.crystal-genui.json"
    if not path.is_file():
        pytest.skip("Standalone checkout has no Consilith deployment example")
    document = json.loads(path.read_text())
    plugin = next(p for p in document["plugins"] if p["id"] == MANIFEST["id"])
    assert plugin["entryPoint"] == "crystal_genui.adapter:extension"
    assert plugin["mcpServices"] == [s["id"] for s in MANIFEST["mcpServers"]]
    for service in [document["platform"], *document["plugins"], *document["mcpServices"]]:
        assert all((root / p / "pyproject.toml").is_file() for p in service["build"]["packages"])


def test_platform_mcp_discovery_and_execution():
    async def run():
        async with Client(build_server()) as client:
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == MANIFEST["tools"]
            response = await client.call_tool(TOOL, {"structure": "SrTiO3"})
            assert not response.is_error
            envelope = ResultEnvelope.model_validate(response.structured_content)
            assert envelope.provenance.tool == TOOL

    anyio.run(run)


def test_workflow_sends_owned_upload_with_snapshot():
    raw = b"data_example\n_cell_length_a 3.905\n"
    sha = hashlib.sha256(raw).hexdigest()
    calls = AsyncMock(return_value=("run-1", None, [], None))
    context = SimpleNamespace(
        snapshot=lambda lanes: (
            {"crystal": {"source": {"artifact": sha}}},
            {"crystal": "revision"},
        ),
        artifact=lambda key: raw if key == sha else None,
        run_tool=calls,
    )
    outcome = anyio.run(workflow, context)
    assert outcome.runs == ["run-1"]
    args = calls.call_args.args
    assert args[0] == TOOL
    assert args[1]["structure"] == ""
    assert base64.b64decode(args[1]["cif_base64"]) == raw
    assert calls.call_args.kwargs["snapshot"] == {"crystal": "revision"}


def test_workflow_rejects_artifact_hash_mismatch():
    calls = AsyncMock()
    context = SimpleNamespace(
        snapshot=lambda lanes: ({"crystal": {"source": {"artifact": "0" * 64}}}, {}),
        artifact=lambda key: b"different bytes",
        run_tool=calls,
    )
    outcome = anyio.run(workflow, context)
    assert "Upload a CIF" in outcome.error
    calls.assert_not_called()


def review_request(result):
    manifest = RunManifest(
        tool=TOOL,
        server="solver-crystal",
        arguments={"structure": "SrTiO3", "cif_base64": "", "configuration": None},
        spec_revision="rev-1",
        input_revisions={"crystal": "rev-1"},
    )
    items = []
    for output in result.outputs:
        refs = [
            ArtifactRef(
                sha256=hashlib.sha256(artifact.bytes()).hexdigest(),
                media_type=artifact.media_type,
                size_bytes=len(artifact.bytes()),
            )
            for artifact in output.artifacts
        ]
        items.append(
            ReviewItem(
                result_id=output.name,
                quantity=output.name,
                kind=output.kind,
                value=getattr(output, "value", None),
                artifacts=refs,
                result_digest="digest",
                manifest_digest="manifest",
                input_revisions={},
                tier=2,
            )
        )
    return ReviewRequest(room="room", case=CASE, run_id="run", manifest=manifest, items=items)


def test_checker_accepts_real_review_items_and_blocks_tampered_receipt(result):
    request = review_request(result)
    for item in request.items:
        findings, _ = anyio.run(checker, request, item, None)
        assert findings[0].check.outcome == "pass", item.quantity
        assert findings[1].check.outcome == "warn"
    receipt = request.items[0].model_copy(deep=True)
    receipt.value["inputDigest"] = "tampered"
    findings, _ = anyio.run(checker, request, receipt, None)
    assert findings[0].check.outcome == "fail"


def test_core_cli_and_html_work_without_optional_imports():
    code = """
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mcp', 'consilith_contracts', 'mp_api'}:
            raise ImportError('optional dependency imported: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
from crystal_genui.cli import standalone_html
from crystal_genui.core import analyze_spec
assert 'window.__CRYSTAL_GENUI_DATA__' in standalone_html(analyze_spec('SrTiO3'))
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_adapter_does_not_import_science():
    code = """
import sys
import consilith_contracts
before = set(sys.modules)
from crystal_genui.adapter import extension
extension()
assert not any(name.split('.')[0] in {'pymatgen', 'mcp'} for name in sys.modules)
assert not any(name.startswith('crystal_genui.') and name.rsplit('.', 1)[-1] in
               {'core', 'polyhedra', 'rules', 'structures', 'server', 'platform_server'}
               for name in set(sys.modules) - before)
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
