# Optional local segmentation models

Author: Hesham Salama

SAM inference belongs to this plugin. The platform, agent, referee and generic UI must
not import Torch, SAM, or image-specific model code. The existing base plugin remains
CPU-only with no mandatory neural-network runtime. A deployment can enable an optional
segmentation dependency in its existing plugin worker; it does not need a SAM web app,
training repository, additional server, or dedicated Docker image.

## Local file design

Keep a model checkpoint in a plugin-owned local model directory, outside the source
wheel and public snapshot. Pass its absolute path to `TinySegmenter`. The adapter
checks the pinned SHA256 before loading, imports inference dependencies only on use,
and never downloads a model or sends an image over the network. Missing or modified
weights produce an explicit error. Unload the worker/model when the feature is disabled.

```python
from consilith_microstructure.sam2_adapter import TinySegmenter

segmenter = TinySegmenter(checkpoint_path, device="cpu")
candidate_masks = segmenter.automatic(rgb_uint8_image)
```

Pinned initial candidate: SAM 2.1 Hiera Tiny, 156,008,466 bytes, SHA256
`7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69`.
Official source revision: `2b90b9f5ceec907a1c18123530e92e794ad901a4`.
Official checkpoint:
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt

The qualification uses Python 3.12, Torch 2.5.1+cpu, TorchVision 0.20.1+cpu,
Hydra 1.3.2, iopath 0.1.10, and the pinned official `sam2` package/configuration files.
The official optional CUDA extension is disabled (`SAM2_BUILD_CUDA=0`), with no
small-hole/sprinkle postprocessing. Installation is an explicit deployment step,
not a request-time action. Install the correct CPU or accelerator Torch distribution
for the target environment. Windows CPU execution is qualified in the private report;
Mac, Linux, CUDA, MPS, ONNX and CoreML execution are not established by that receipt.
Preserve the upstream Apache-2.0 notices with any distributed code and weights.

## Masks and simulation are different contracts

The adapter returns object-mask candidates, with overlaps and uncovered pixels intact.
The network resizes images internally to its 1024-pixel input resolution; returned masks
use the original crop grid. No downstream resampling is implied.
It does not claim that an object is a grain, convert pores into material, or choose a
material model. Automatic inference uses a fixed 32x32 prompt grid; prompted inference
uses supplied positive points and must record their origin.
The model's predicted IoU and stability are filtering scores, not measured agreement
with true grains. They must not be presented as scientific accuracy or confidence in
material identity.

Private qualification keeps raw masks and an explicitly named SAM-marker plus ridge
watershed hybrid separately. Reconciliation and completion can change boundaries.
Every accepted complete grain partition still needs review, calibration and the
existing phase-field transfer gates. Unknown pixels block initialization; no silent
filling is allowed. A correct label-to-field conversion cannot establish that the
image segmentation was correct.

EdgeTAM is evaluated as a smaller alternative in the private qualification directory.
Its official Python package also uses the `sam2` namespace; do not install both
upstream packages into one environment. The comparison selects one pinned source tree
per process. A future release must choose one implementation or isolate their runtimes.
No new MCP method, default segmentation change, platform release or public promotion
is implied by this experimental adapter.
