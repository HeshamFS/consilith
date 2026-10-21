"""Local explicit file I/O; the server exposes no arbitrary path access."""

import argparse
import json
from pathlib import Path

from .pipeline import bundle_arrays, png_labels, reconstruct, run
from .trajectory import evolution_gif, trajectory, trajectory_npz


def main():
    parser = argparse.ArgumentParser(description="Micrograph / EBSD to a 2D grain-field checkpoint")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=["image", "ang", "ctf"], default="image")
    parser.add_argument("--crop", nargs=4, type=int)
    parser.add_argument("--factor", type=int, default=1)
    parser.add_argument("--spacing", type=float, default=1.0)
    parser.add_argument("--units", choices=["pixel", "m"], default="pixel")
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--minimum-region", type=int, default=12)
    parser.add_argument("--gamma", type=float, default=1.0)
    parser.add_argument("--mobility", type=float, default=1.0)
    parser.add_argument("--misorientation", type=float, default=10.0)
    parser.add_argument(
        "--method", choices=["global", "adaptive", "ridge", "guided"], default="global"
    )
    parser.add_argument("--boundary", choices=["dark", "bright"], default="dark")
    parser.add_argument("--markers", type=Path, help="JSON list of cropped-image [x,y] markers")
    parser.add_argument("--excluded-mask", type=Path, help="Nonzero image pixels remain unknown")
    parser.add_argument("--frames", type=int, default=0, help="2..201 actual trajectory frames")
    args = parser.parse_args()
    if args.kind != "image" and (args.markers or args.excluded_mask or args.method != "global"):
        parser.error("image segmentation options require --kind image")
    excluded = None
    if args.excluded_mask:
        import numpy as np
        from PIL import Image

        excluded = np.asarray(Image.open(args.excluded_mask).convert("L")) > 0
    options = (
        {
            "crop": args.crop,
            "spacing": args.spacing,
            "units": args.units,
            "threshold": args.threshold,
            "minimum_region": args.minimum_region,
            "method": args.method,
            "boundary": args.boundary,
            "markers": json.loads(args.markers.read_text()) if args.markers else None,
            "excluded_mask": excluded,
        }
        if args.kind == "image"
        else {"threshold_deg": args.misorientation}
    )
    original, grains = reconstruct(
        args.input.read_bytes(), kind=args.kind, factor=args.factor, **options
    )
    arrays = None
    if args.frames:
        arrays, report = trajectory(
            grains,
            args.width,
            args.duration,
            gamma=args.gamma,
            mobility=args.mobility,
            frame_count=args.frames,
        )
        initial, final, ids = arrays["initial"], arrays["final"], arrays["grain_ids"]
    else:
        initial, final, ids, report = run(
            grains, args.width, duration=args.duration, gamma=args.gamma, mobility=args.mobility
        )
    # Never overwrite an existing result bundle.
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / ("source" + args.input.suffix)).write_bytes(args.input.read_bytes())
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (args.output / "grain-map.json").write_text(json.dumps(grains.wire(), allow_nan=False))
    (args.output / "source-labels.png").write_bytes(png_labels(original.labels))
    (args.output / "final-labels.png").write_bytes(png_labels(ids[final.argmax(axis=0)]))
    (args.output / "fields.npz").write_bytes(bundle_arrays(grains.labels, ids, initial, final))
    if arrays is not None:
        (args.output / "trajectory.npz").write_bytes(trajectory_npz(arrays))
        (args.output / "evolution.gif").write_bytes(
            evolution_gif(arrays, units=report["parameters"]["units"])
        )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "grains": len(ids),
                "argmaxMismatchPixels": report["initialization"]["argmaxMismatchPixels"],
                "physicalValidation": "not_performed",
            }
        )
    )


if __name__ == "__main__":
    main()
