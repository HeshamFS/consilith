"""Standalone MCP service. Only supplied bytes/arrays; no host path or URL access."""

import base64
from importlib.resources import files
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import __version__
from .data import GrainMap, positive
from .pipeline import artifact, bundle_arrays, png_labels, reconstruct, run
from .solver import Parameters
from .trajectory import evolution_gif, trajectory, trajectory_npz


class GrainMapInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    labels: list[list[StrictInt]]
    spacing: float = Field(default=1.0, gt=0)
    units: Literal["pixel", "m"] = "pixel"
    provenance: dict[str, Any] = Field(default_factory=dict)


def decode_file(value):
    if len(value) > 28 * 1024 * 1024:
        raise ValueError("encoded input exceeds budget")
    return base64.b64decode(value, validate=True)


def segment_micrograph(
    image_base64: str,
    crop: list[int] | None = None,
    spacing: float = 1.0,
    units: Literal["pixel", "m"] = "pixel",
    factor: int = 1,
    threshold: float | None = None,
    minimum_region: int = 12,
    boundary: Literal["dark", "bright"] = "dark",
    method: Literal["global", "adaptive", "ridge", "guided"] = "global",
    smoothing: float = 1.0,
    local_window: int = 51,
    local_offset: float = 0.02,
    ridge_threshold: float = 0.15,
    closing_radius: int = 1,
    markers: list[list[StrictInt]] | None = None,
    excluded_mask: list[list[StrictBool]] | None = None,
) -> dict[str, Any]:
    """Reconstruct candidate grains from a visible boundary image.

    Requires review; does not imply universal segmentation accuracy.
    """
    original, g = reconstruct(
        decode_file(image_base64),
        crop=crop,
        spacing=spacing,
        units=units,
        factor=factor,
        threshold=threshold,
        minimum_region=minimum_region,
        boundary=boundary,
        method=method,
        smoothing=smoothing,
        local_window=local_window,
        local_offset=local_offset,
        ridge_threshold=ridge_threshold,
        closing_radius=closing_radius,
        markers=markers,
        excluded_mask=excluded_mask,
    )
    return {
        "grainMap": g.wire(),
        "report": g.report(),
        "sourceReport": original.report(),
        "preview": artifact(png_labels(g.labels), "image/png"),
    }


def segment_ebsd(
    file_base64: str,
    format: Literal["ang", "ctf"],
    threshold_deg: float = 10.0,
    symmetry: Literal["cubic", "triclinic"] = "cubic",
    coordinate_unit: Literal["um", "m"] = "um",
) -> dict[str, Any]:
    """Reconstruct grains from raw square-grid EBSD; retain phases and unknown pixels."""
    _, g = reconstruct(
        decode_file(file_base64),
        kind=format,
        threshold_deg=threshold_deg,
        symmetry=symmetry,
        length_unit=coordinate_unit,
    )
    return {
        "grainMap": g.wire(),
        "report": g.report(),
        "preview": artifact(png_labels(g.labels), "image/png"),
    }


def initialize_fields(grain_map: GrainMapInput, width: float) -> dict[str, Any]:
    """Convert labels into diffuse fields with label recovery and area diagnostics."""
    g = GrainMap.from_wire(grain_map.model_dump())
    initial, final, ids, report = run(g, width)
    return {
        "report": report,
        "checkpoint": artifact(bundle_arrays(g.labels, ids, initial, final), "application/x-npz"),
        "preview": artifact(png_labels(ids[final.argmax(axis=0)]), "image/png"),
    }


def simulate_grain_growth(
    grain_map: GrainMapInput,
    width: float,
    duration: float,
    gamma: float,
    mobility: float,
    reviewed: bool = False,
    parameter_basis: str = "",
    dt: float | None = None,
) -> dict[str, Any]:
    """Run bounded isotropic 2D growth with reviewed labels and explicit parameter basis."""
    if not reviewed or not parameter_basis.strip():
        raise ValueError("reviewed reconstruction and parameter basis are required")
    g = GrainMap.from_wire(grain_map.model_dump())
    p = Parameters(g.spacing, width, gamma, mobility)
    from math import ceil, isfinite

    if not isfinite(duration) or duration < 0:
        raise ValueError("duration must be finite and nonnegative")
    selected_dt = p.dt_limit if dt is None else positive(dt, "dt")
    steps = 0 if mobility == 0 else ceil(duration / selected_dt)
    if len(set(g.labels.ravel().tolist())) * g.labels.size * steps > 200_000_000:
        raise ValueError(
            "server work budget exceeded; choose a smaller reviewed ROI or shorter duration"
        )
    initial, final, ids, report = run(
        g, width, duration=duration, gamma=gamma, mobility=mobility, dt=dt
    )
    report["parameterBasis"] = parameter_basis
    return {
        "report": report,
        "checkpoint": artifact(bundle_arrays(g.labels, ids, initial, final), "application/x-npz"),
        "preview": artifact(png_labels(ids[final.argmax(axis=0)]), "image/png"),
    }


def simulate_trajectory(
    grain_map: GrainMapInput,
    width: float,
    duration: float,
    gamma: float,
    mobility: float,
    reviewed: bool = False,
    parameter_basis: str = "",
    frame_count: int = 41,
) -> dict[str, Any]:
    """Return actual phase-field trajectory, area history and a grain-growth GIF."""
    from math import ceil

    import numpy as np

    if not reviewed or not parameter_basis.strip():
        raise ValueError("reviewed reconstruction and parameter basis are required")
    g = GrainMap.from_wire(grain_map.model_dump())
    duration = positive(duration, "duration", True)
    p = Parameters(g.spacing, width, gamma, mobility)
    steps = 0 if mobility == 0 else ceil(duration / p.dt_limit)
    if np.unique(g.labels).size * g.labels.size * steps > 200_000_000:
        raise ValueError("server work budget exceeded; reduce ROI or duration")
    if not 2 <= frame_count <= 61 or frame_count * g.labels.size > 2_000_000:
        raise ValueError("server trajectory output budget exceeded")
    arrays, report = trajectory(
        g, width, duration, gamma=gamma, mobility=mobility, frame_count=frame_count
    )
    report["parameterBasis"] = parameter_basis
    return {
        "report": report,
        "trajectory": artifact(trajectory_npz(arrays), "application/x-npz"),
        "animation": artifact(
            evolution_gif(arrays, units=report["parameters"]["units"]), "image/gif"
        ),
    }


def build_server():
    server = MCPServer(
        "consilith-microstructure",
        version=__version__,
        instructions=(
            "Reconstruct and review before evolving. "
            "Respect unknown pixels, phases and calibration. "
            "A candidate segmentation is not experimental ground truth. "
            "Never invent scientific values."
        ),
    )
    for name, fn in [
        ("segment_image", segment_micrograph),
        ("segment_ebsd", segment_ebsd),
        ("initialize_fields", initialize_fields),
        ("simulate", simulate_grain_growth),
        ("trajectory", simulate_trajectory),
    ]:
        server.tool(name="microstructure." + name, structured_output=True)(fn)

    @server.resource("microstructure://knowledge/scope", mime_type="text/markdown")
    def scope():
        return files("consilith_microstructure").joinpath("knowledge/scope.md").read_text()

    @server.resource("microstructure://skills/reconstruct-grains", mime_type="text/markdown")
    def skill():
        return (
            files("consilith_microstructure")
            .joinpath("skills/reconstruct-grains/SKILL.md")
            .read_text()
        )

    return server


def app():
    return build_server().streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "microstructure:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
