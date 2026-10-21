"""Bounded trajectories sampled from solver states, never interpolated animation."""

from io import BytesIO
from math import ceil

import numpy as np
from PIL import Image, ImageDraw

from .data import positive
from .pipeline import run
from .solver import Parameters, energy, evolve
from .sparse import SparseFields, arrays_for_fields


def trajectory(
    grains,
    width,
    duration,
    *,
    gamma=1.0,
    mobility=1.0,
    dt=None,
    frame_count=41,
    maximum_area_change=0.05,
    allow_unresolved_boundary=False,
):
    duration = positive(duration, "duration", True)
    maximum_area_change = positive(maximum_area_change, "maximum_area_change", True)
    if type(frame_count) is not int or not 2 <= frame_count <= 201:
        raise ValueError("frame_count must be an integer in 2..201")
    if grains.labels.size * frame_count > 256_000_000:
        raise ValueError(
            "trajectory exceeds the explicit 256 million native-pixel frame budget; "
            "reduce frame count"
        )
    initial, _, ids, report = run(grains, width, gamma=gamma, mobility=mobility)
    info = report["initialization"]
    unresolved = set(info["unresolvedGrains"])
    boundary_ids = {item["id"] for item in report["grainMap"]["grains"] if item["touchesBorder"]}
    if unresolved and (not allow_unresolved_boundary or not unresolved <= boundary_ids):
        raise ValueError("unresolved grains cannot evolve; refine width/grid or correct labels")
    if unresolved:
        report["boundaryResolutionWarning"] = {
            "grainIds": sorted(unresolved),
            "explicitlyAccepted": True,
            "meaning": (
                "All original pixels and grain IDs retained; boundary grains lack "
                "a resolved pure interior. Their kinetics are not qualified."
            ),
        }
    if info["argmaxMismatchPixels"] or not info["adjacencyPreserved"]:
        raise ValueError("label topology changed during initialization")
    if info["maximumRelativeAreaChange"] > maximum_area_change:
        raise ValueError("initial diffuse area change exceeds the declared transfer tolerance")
    p = Parameters(
        grains.spacing,
        width,
        gamma,
        mobility,
        units="SI" if grains.units == "m" else "dimensionless",
    )
    step = p.dt_limit if dt is None else positive(dt, "dt")
    steps = 0 if mobility == 0 else ceil(duration / step)
    # Save at a solver-step cadence: changing frame count cannot change the dynamics.
    every = max(1, ceil(steps / (frame_count - 1)))
    labels, interfaces, areas, times, energies, step_numbers = [], [], [], [], [], []

    def snapshot(a, time, step_number):
        image = ids[np.maximum(a.argmax(axis=0), 0)].astype(np.int32)
        if isinstance(a, SparseFields) and a.domain_mask is not None:
            image = np.where(a.domain_mask, image, 0).astype(np.int32)
        labels.append(image)
        interfaces.append(
            (a.density() if isinstance(a, SparseFields) else 1 - np.sum(a * a, axis=0)).astype(
                np.float32
            )
        )
        areas.append(
            (a.areas() if isinstance(a, SparseFields) else a.sum(axis=(1, 2))) * p.spacing**2
        )
        times.append(time)
        energies.append(energy(a, p))
        step_numbers.append(step_number)

    final, diagnostics = evolve(initial, p, duration, dt, record_every=every, observer=snapshot)
    report["simulation"] = diagnostics
    report["trajectory"] = {
        "requestedFrameCount": frame_count,
        "actualFrameCount": len(times),
        "times": times,
        "solverSteps": step_numbers,
        "energy": energies,
        "visibleGrainCounts": [len(np.unique(a[a > 0])) for a in labels],
        "grainIds": ids.tolist(),
        "diffuseAreas": [a.tolist() for a in areas],
        "interpolatedFrames": False,
        "maximumInitialRelativeAreaChange": maximum_area_change,
        "extinctionDefinition": "grain disappears from argmax labels; diffuse area also reported",
    }
    arrays = {
        "times": np.array(times),
        "grain_ids": ids,
        "labels": np.stack(labels),
        "interface_density": np.stack(interfaces),
        "diffuse_areas": np.stack(areas),
        **(
            {}
            if getattr(grains, "domain_mask", None) is None
            else {"domain_mask": grains.domain_mask}
        ),
        **arrays_for_fields("initial", initial),
        **arrays_for_fields("final", final),
    }
    return arrays, report


def trajectory_npz(arrays):
    b = BytesIO()
    np.savez_compressed(b, **arrays)
    return b.getvalue()


def evolution_gif(arrays, *, title="Hypothetical 2D grain growth", units="dimensionless"):
    """Two views of actual states: stable grain IDs and diffuse interface density."""
    frames = []
    for k, (labels, density) in enumerate(
        zip(arrays["labels"], arrays["interface_density"], strict=True)
    ):
        rgb = np.stack(
            ((labels * 71) % 223 + 32, (labels * 137) % 223 + 32, (labels * 193) % 223 + 32),
            axis=-1,
        ).astype("uint8")
        rgb[labels == 0] = 0
        z = np.clip(density * 1.5, 0, 1)
        heat = np.stack((255 * z, 180 * z * z, 220 * np.sqrt(z)), axis=-1).astype("uint8")
        left = Image.fromarray(rgb)
        right = Image.fromarray(heat)
        scale = min(3.0, 480 / max(labels.shape))
        size = (max(1, round(left.width * scale)), max(1, round(left.height * scale)))
        left = left.resize(size, Image.Resampling.NEAREST)
        right = right.resize(size, Image.Resampling.NEAREST)
        frame = Image.new("RGB", (size[0] * 2 + 30, size[1] + 100), (17, 23, 32))
        frame.paste(left, (10, 70))
        frame.paste(right, (20 + size[0], 70))
        draw = ImageDraw.Draw(frame)
        draw.text((10, 8), title, fill="white")
        draw.text(
            (10, 28),
            f"t={arrays['times'][k]:.3f} {units} | "
            f"visible grains: {len(np.unique(labels[labels > 0]))}",
            fill="white",
        )
        draw.text((10, 49), "Grain identities", fill="white")
        draw.text((20 + size[0], 49), "Diffuse interfaces: 1 - sum(phi^2)", fill="white")
        draw.text(
            (10, size[1] + 78),
            "Computed states; no frame interpolation; no experimental kinetics claim.",
            fill="white",
        )
        frames.append(frame)
    b = BytesIO()
    frames[0].save(b, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return b.getvalue()
