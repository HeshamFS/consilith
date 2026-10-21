"""Optional local SAM 2.1 Tiny inference. No download, service or platform dependency.

Author: Hesham Salama
Masks are candidate objects, not validated grains or a complete phase-field partition.
"""

import hashlib
from pathlib import Path

import numpy as np

from .data import MAX_PIXELS

TINY_SHA256 = "7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69"
TINY_CONFIG = "configs/sam2.1/sam2.1_hiera_t.yaml"


class TinySegmenter:
    """Lazy optional dependency; construct only when the user selects SAM segmentation."""

    def __init__(self, checkpoint, *, device="cpu"):
        path = Path(checkpoint)
        if not path.is_file():
            raise ValueError("Install the pinned Tiny checkpoint locally before selecting SAM")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != TINY_SHA256:
            raise ValueError("SAM 2.1 Tiny checkpoint checksum mismatch")
        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda; other devices are not qualified")
        try:
            import torch
            from sam2.build_sam import build_sam2
        except ImportError as exc:
            raise RuntimeError("Optional SAM 2 inference runtime is not installed") from exc
        self.torch = torch
        self.model = build_sam2(TINY_CONFIG, str(path), device=device, apply_postprocessing=False)
        self.provenance = {"model": "SAM 2.1 Hiera Tiny", "checkpointSha256": digest,
                           "device": device, "customCudaPostprocessing": False}

    @staticmethod
    def _image(image):
        a = np.asarray(image)
        if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 3:
            raise ValueError("SAM image must be uint8 RGB")
        if min(a.shape[:2]) < 2 or a.shape[0] * a.shape[1] > MAX_PIXELS:
            raise ValueError("SAM image exceeds the plugin pixel budget")
        return np.array(a, copy=True, order="C")

    def automatic(self, image):
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

        image = self._image(image)
        generator = SAM2AutomaticMaskGenerator(
            self.model, points_per_side=32, points_per_batch=8,
            pred_iou_thresh=0.8, stability_score_thresh=0.95,
            box_nms_thresh=0.7, crop_n_layers=0,
            min_mask_region_area=0, output_mode="binary_mask",
        )
        with self.torch.inference_mode():
            return generator.generate(image)

    def prompted(self, image, points):
        """One positive point per candidate; provenance must identify the seed source."""
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        image = self._image(image)
        points = np.asarray(points, dtype=float)
        if (points.ndim != 2 or points.shape[1] != 2 or not 1 <= len(points) <= 512
                or not np.all(np.isfinite(points)) or np.any(points < 0)
                or np.any(points[:, 0] >= image.shape[1]) or np.any(points[:, 1] >= image.shape[0])):
            raise ValueError("Provide 1..512 finite points inside the image")
        predictor = SAM2ImagePredictor(self.model)
        out = []
        with self.torch.inference_mode():
            predictor.set_image(image)
            for point in points:
                logits, scores, _ = predictor.predict(
                    point_coords=point[None], point_labels=np.array([1]),
                    multimask_output=True, return_logits=True,
                )
                k = int(np.argmax(scores))
                z = logits[k]
                stability = float((z > 1).sum() / max(1, (z > -1).sum()))
                if scores[k] >= 0.8 and stability >= 0.95:
                    out.append({"segmentation": z > 0, "predicted_iou": float(scores[k]),
                                "stability_score": stability, "point_coords": [point.tolist()]})
        return out
