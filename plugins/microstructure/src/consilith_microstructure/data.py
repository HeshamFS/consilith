"""Bounded, explicit grain maps and provenance."""

import base64
import hashlib
import json
import zlib
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

MAX_PIXELS = 4_194_304
MAX_FIELD_VALUES = 16_777_216


def positive(value, name, allow_zero=False):
    value = float(value)
    if not np.isfinite(value) or (value < 0 if allow_zero else value <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}")
    return value


def labels_array(labels):
    a = np.asarray(labels)
    if a.ndim != 2 or min(a.shape) < 2 or a.size > MAX_PIXELS:
        raise ValueError("labels must be a bounded 2D raster of at least 2 by 2")
    if not np.issubdtype(a.dtype, np.integer) or np.any(a < 0) or np.any(a > 2**31 - 1):
        raise ValueError("labels must be nonnegative int32 IDs; zero means unknown")
    return a.astype(np.int32, copy=True)


@dataclass
class GrainMap:
    labels: np.ndarray
    spacing: float = 1.0
    units: str = "pixel"
    provenance: dict = field(default_factory=dict)
    domain_mask: np.ndarray | None = None

    def __post_init__(self):
        self.labels = labels_array(self.labels)
        self.spacing = positive(self.spacing, "spacing")
        if self.units not in {"pixel", "m"}:
            raise ValueError("spacing units must be pixel or m")
        if self.domain_mask is not None:
            mask = np.asarray(self.domain_mask)
            if mask.dtype != bool or mask.shape != self.labels.shape or not mask.any():
                raise ValueError("domain mask must be a nonempty boolean raster matching labels")
            if np.any(self.labels[~mask] != 0):
                raise ValueError("excluded domain pixels cannot contain grain labels")
            self.domain_mask = mask.copy()

    @property
    def digest(self):
        h = hashlib.sha256(self.labels.astype("<i4").tobytes())
        h.update(
            json.dumps(
                {
                    "shape": self.labels.shape,
                    "spacing": self.spacing,
                    "units": self.units,
                    "provenance": self.provenance,
                },
                sort_keys=True,
                allow_nan=False,
            ).encode()
        )
        if self.domain_mask is not None:
            h.update(b"explicit-domain-mask-v1")
            h.update(self.domain_mask.astype(np.uint8).tobytes())
        return h.hexdigest()

    def report(self):
        ids, counts = np.unique(self.labels, return_counts=True)
        edge = set(
            np.concatenate(
                (self.labels[0], self.labels[-1], self.labels[:, 0], self.labels[:, -1])
            ).tolist()
        )
        valid = (
            np.ones(self.labels.shape, dtype=bool) if self.domain_mask is None else self.domain_mask
        )
        if self.domain_mask is not None:
            border = valid & ~ndi.binary_erosion(valid, border_value=0)
            edge.update(self.labels[border].tolist())
        pairs = set()
        for a, b in [
            (self.labels[:-1], self.labels[1:]),
            (self.labels[:, :-1], self.labels[:, 1:]),
        ]:
            mask = (a != b) & (a > 0) & (b > 0)
            pairs.update(
                tuple(sorted(pair)) for pair in zip(a[mask].tolist(), b[mask].tolist(), strict=True)
            )
        return {
            "schemaVersion": "1",
            "digest": self.digest,
            "shape": list(self.labels.shape),
            "spacing": self.spacing,
            "units": self.units,
            "unknownPixels": int(((self.labels == 0) & valid).sum()),
            "excludedPixels": int((~valid).sum()),
            "grains": [
                {
                    "id": int(i),
                    "pixels": int(n),
                    "area": float(n * self.spacing**2),
                    "touchesBorder": int(i) in edge,
                    "connectedComponents": int(ndi.label(self.labels == i)[1]),
                }
                for i, n in zip(ids, counts, strict=True)
                if i > 0
            ],
            "adjacency": [list(p) for p in sorted(pairs)],
            "provenance": self.provenance,
        }

    def wire(self, *, compressed=False):
        if compressed:
            value = {
                "encoding": "int32-le-zlib-base64",
                "shape": list(self.labels.shape),
                "labelsBase64": base64.b64encode(
                    zlib.compress(self.labels.astype("<i4").tobytes())
                ).decode(),
                "spacing": self.spacing,
                "units": self.units,
                "provenance": self.provenance,
            }
            if self.domain_mask is not None:
                value["domainMaskBase64"] = base64.b64encode(
                    zlib.compress(self.domain_mask.astype(np.uint8).tobytes())
                ).decode()
            return value
        value = {
            "labels": self.labels.tolist(),
            "spacing": self.spacing,
            "units": self.units,
            "provenance": self.provenance,
        }
        if self.domain_mask is not None:
            value["domain_mask"] = self.domain_mask.tolist()
        return value

    @classmethod
    def from_wire(cls, value):
        if "encoding" in value:
            if set(value) - {"domainMaskBase64"} != {
                "encoding",
                "shape",
                "labelsBase64",
                "spacing",
                "units",
                "provenance",
            }:
                raise ValueError("invalid compressed grain map fields")
            shape = value["shape"]
            if (
                value["encoding"] != "int32-le-zlib-base64"
                or not isinstance(shape, list)
                or len(shape) != 2
                or any(type(n) is not int or n < 2 for n in shape)
                or shape[0] * shape[1] > MAX_PIXELS
            ):
                raise ValueError("invalid compressed grain map shape or encoding")
            encoded = value["labelsBase64"]
            if not isinstance(encoded, str) or len(encoded) > 24_000_000:
                raise ValueError("compressed grain map exceeds encoded byte budget")
            expected = shape[0] * shape[1] * 4
            decoder = zlib.decompressobj()
            raw = decoder.decompress(base64.b64decode(encoded, validate=True), expected + 1)
            if (
                len(raw) != expected
                or not decoder.eof
                or decoder.unused_data
                or decoder.unconsumed_tail
            ):
                raise ValueError("compressed grain map length or stream mismatch")
            domain_mask = None
            if "domainMaskBase64" in value:
                mask_encoded = value["domainMaskBase64"]
                if not isinstance(mask_encoded, str) or len(mask_encoded) > 6_000_000:
                    raise ValueError("compressed domain mask exceeds byte budget")
                size = shape[0] * shape[1]
                mask_decoder = zlib.decompressobj()
                mask_raw = mask_decoder.decompress(
                    base64.b64decode(mask_encoded, validate=True), size + 1
                )
                if (
                    len(mask_raw) != size
                    or not mask_decoder.eof
                    or mask_decoder.unused_data
                    or mask_decoder.unconsumed_tail
                ):
                    raise ValueError("compressed domain mask length or stream mismatch")
                mask_values = np.frombuffer(mask_raw, dtype=np.uint8).reshape(shape)
                if np.any(mask_values > 1):
                    raise ValueError("domain mask values must be zero or one")
                domain_mask = mask_values.astype(bool)
            return cls(
                np.frombuffer(raw, dtype="<i4").reshape(shape),
                value["spacing"],
                value["units"],
                value["provenance"],
                domain_mask,
            )
        return cls(**value)
