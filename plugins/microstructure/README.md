# Microstructure: standalone first implementation

An independent CPU package for candidate grain reconstruction, label-to-field conversion
and isotropic 2D grain growth. No platform application or external multiphysics library
is imported. This is an experimental research package, not a scientifically qualified
general micrograph interpreter.

## What works

The app uses the complete native-resolution clear-grain image: upload, inspect
candidate segmentation and transfer diagnostics, approve the immutable result, then watch
computed 2D grain growth. No SAM, Torch, model download or GPU is required. Cropping and
resampling are refused by the full-image app workflow. Difficult SEM and learned
segmentation remain separate work. Qualification uses controlled numerical and image-transfer checks; it does not establish general SEM accuracy.

- PNG/JPEG/single-frame TIFF with visible dark or bright boundaries: deterministic
  threshold, connected interior markers and watershed. Explicit ROI, marker threshold,
  calibration and optional grid resampling are recorded.
- Square-grid ANG and a constrained CTF subset: measured orientations, explicit cubic
  or triclinic symmetry, disorientation threshold and connected grains. Unknown points,
  phase IDs, source identity and representative measured orientations are retained.
- Accepted integer grain maps become constrained diffuse fields. Exact argmax recovery,
  diffuse area drift, underresolved grains and grid-resampling changes are reported.
- A small float64 constrained multiphase solver with no-flux or periodic kernel boundaries,
  stable-step limits, energy diagnostics, bounded chunks and reproducible checkpoints.
- Four independent MCP tools plus packaged scope and reconstruction skill resources.

Read `MODEL.md` before interpreting output. Numerical verification covers selected
analytic and invariance tests; experimental validation is not established. Image
segmentation produces candidates requiring inspection, not ground truth. A color EBSD
screenshot does not supply raw orientations. Hexagonal grids, mixed-phase growth,
general noisy SEM segmentation and interactive mask editing are not implemented.

## Local use

Install this package in its own environment: `pip install '.[server]'`.
The core CLI works without MCP: `pip install .`.

```sh
microstructure micrograph.png --output results/run-001 --width 8
```

That command uses pixel units and initializes only. Inspect `report.json`, the label
preview and the NPZ fields before evolution. Physical spacing requires `--units m`
and `--spacing <metres-per-pixel>`; width then also uses metres. Physical gamma and
mobility must be supplied with a justified parameter basis; CLI defaults are hypothetical
scenario values, not a material calibration. Uncalibrated runs remain dimensionless.
`--duration` runs an explicit scenario; unresolved grains fail closed. The original
source file is preserved in the output bundle. An existing output directory is never
overwritten. NPZ files contain plain numeric arrays and load with `allow_pickle=False`.

```sh
uvicorn consilith_microstructure.server:app --factory --host 127.0.0.1 --port 8080
```

MCP endpoint: `/mcp`. Tools: `microstructure.segment_image`, `microstructure.segment_ebsd`,
`microstructure.initialize_fields`, `microstructure.simulate`. The simulation tool
requires a reviewed flag and an explicit parameter-basis description. Those assertions
do not replace platform authorization or independent scientific checking. This standalone
server is intended for a trusted local/container network, not unauthenticated public exposure.
Inputs are supplied bytes or validated grain-map objects; tools accept no filesystem
paths or arbitrary URLs. Outputs contain JSON reports and hashed PNG/NPZ artifacts.

## Docker

```sh
docker build -f docker/Dockerfile -t consilith-microstructure:local .
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,noexec,size=64m --cap-drop=ALL \
  --security-opt no-new-privileges --memory 1g --cpus 2 \
  -p 127.0.0.1:8080:8080 consilith-microstructure:local
```

The base image and server dependencies are pinned. The image contains no tests, datasets,
private plans or platform source. Linux/amd64 was tested on Docker Desktop; other hosts
and ARM64 require their own execution checks.

## Deliberate limits

The dense reference kernel remains available for small verification cases. Larger grain
partitions use exact local active-field storage on the original grid. Float64 values,
no-flux/periodic stencils, simplex projection and the energy check are unchanged. Dynamic
slot expansion never truncates grain IDs. Every native pixel is retained. Compute and
artifact limits remain bounded; the application reports unsupported work explicitly.


The pixel initializer currently uses nonperiodic distances. Periodic initial fields must
be supplied explicitly to the numerical kernel; the high-level image pipeline refuses
periodic initialization rather than creating an artificial seam.

The standalone kernel and MCP service work without platform services. The optional
platform adapter described below connects the clear-grain workflow to the internal app.
Independent scientific checking and generalized segmentation adapters remain future work.

## Segmentation and actual evolution (0.2.0)

Choose `--method global`, `adaptive`, `ridge` or `guided`, and `--boundary dark` or
`bright`. Global is the original comparator. Adaptive handles spatial illumination changes;
ridge uses multiscale boundary evidence. No automatic winner is selected. Guided requires
`--markers seeds.json`, a list of integer `[x,y]` points in the cropped image. Markers
are review input, not a claim of independent validation.

`--excluded-mask mask.png` preserves nonzero mask pixels as unknown. It must match the
cropped image. This avoids silently filling pores; unknown pixels prevent the current
single-solid-phase initializer from running. Real SEM contrast can reflect phases or
pores rather than grain boundaries, even when a candidate looks plausible.

```sh
microstructure image.png --output results/ridge-review --method ridge --boundary dark --width 4
microstructure image.png --output results/evolution --width 8 --duration 100 --frames 61
```

The second command produces `evolution.gif`, `trajectory.npz` and a JSON report. Use a
reviewed, supported reconstruction and justified parameters; defaults are a hypothetical
dimensionless scenario. Frames contain actual solver states, with no interpolation.
The default transfer gate rejects unresolved grains, topology changes and per-grain
diffuse-area changes above 5%. The CLI never overwrites an existing result directory.

MCP exposes the same segmentation controls and the additional `microstructure.trajectory`
tool. It returns the GIF and numeric trajectory as hashed artifacts, with bounded work
and output size. The returned schemas let a future agent workspace expose controls
without science logic in the platform. The separate platform adapter exposes canonical
result envelopes for the app.

Neither SAM nor a trained
SEM model was tested in this CPU-only comparison; such methods require separate data
and qualification, not replacement of the present limitations with unsupported claims.

## Platform adapter

Install the `platform` extra alongside the platform contracts distribution. The worker
entry point is `consilith_microstructure.adapter:extension`; the MCP service is
`consilith_microstructure.platform_server:app` (uvicorn factory, `/mcp`). No platform
application package is imported by this plugin, and the platform does not import the
scientific implementation.

The `grain-growth` capability declares the uploaded `image.source`, segmentation settings
and dimensionless evolution controls. Run with approval blank to prepare the whole image.
Inspect source, boundary overlay, grain identities and the visible resolution notice.
Approve the immutable result, then run evolution. Changing the source, segmentation settings, material mask or transfer width
requires a fresh preparation and review. Duration, gamma and mobility can be changed
for a new run while retaining the approved initial map. Both preparation and evolution use the bounded
600-second accurate-tool deadline, including cold-start compilation.

Use the [microstructure deployment manifest](../../examples/deployment.microstructure.json)
from the repository root. Upload your own clear-grain image, starting with global
segmentation, dark boundaries, width 4, duration 600, energy 1 and mobility 1. No crop
field is exposed. A reviewed non-material mask can exclude a supported annotation strip
while preserving the full source canvas. Always inspect boundaries and the mask;
reported grain counts are candidate measurements, not experimental ground truth.
All per-grain area histories are retained; the workspace also plots energy and a shrinking
grain. Numerical receipt checks remain distinct from experimental validation.

## Full-domain internal app (0.4.0)

The image workflow rejects nonempty crop coordinates. Review payloads carry a losslessly
compressed native int32 label map with bounded decoding and an immutable digest. The solver
uses the reviewed map without resegmentation. Full-resolution identity/interface PNGs are
stored as content-addressed frames; browser playback keeps only the current frame and two
ahead. Downloads contain original pixels. Initial/final NPZ checkpoints use numeric arrays
and load with `allow_pickle=False`; local field indices index the stored `grain_ids` array.

Version 0.5.0 adds conservative annotation-strip detection and an explicit, digest-bound
material-domain mask. Review the amber exclusion overlay before approval. Inactive pixels
have zero phase fields and labels; the material edge has no flux. The original annotation
pixels are copied unchanged into every evolution frame. The full canvas is never cropped.
All unresolved grains, label mismatch, changed adjacency or more than 5% initial diffuse
area error block app evolution. Masked initialization currently supports rectangular
connected material components; ambiguous black areas are not automatically excluded.

Deployment sets `NUMBA_NUM_THREADS=2` and writable `NUMBA_CACHE_DIR=/tmp/numba` in the
scientific MCP container. The platform does not import Numba or the scientific package.

Version 0.5.1 corrects annotation detection on cropped inputs whose scale bar spans a
large fraction of the row. The complete supported edge band is assessed rather than
stopping at the bright bar. Ambiguous annotation candidates block app evolution.
`image.footer_rows` is an explicit footer-height fallback (0 means automatic); it is
reviewed and fingerprint-bound, and never crops or resamples the source canvas.
Qualification includes the exact user-supplied 525 x 420 crop, nearby real grain
boundaries, wide scale bars and the original 960 x 776 input.


Version 0.5.2 separates segmentation approval from evolution settings. After approving a map, duration, interface energy (gamma), and mobility can change without repeating segmentation. Each run starts from the approved map, not the preceding final state. Image, segmentation, domain-mask, and interface-width changes still require a new review. Run receipts include the executed configuration and its fingerprint. Existing rooms retain their pinned release; this source change is not an automatic room migration.
