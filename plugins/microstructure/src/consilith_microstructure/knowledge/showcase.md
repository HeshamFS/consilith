# Micrograph to phase-field grain growth

This optional platform adapter accepts visible, connected grain boundaries in a single
2D micrograph. It uses classical threshold or ridge segmentation and marker watershed,
never SAM. Use the full original image. Cropping is rejected by this workflow; no resizing is performed.

Source bytes, segmentation settings, interface width, duration, energy and mobility are
bound to the displayed segmentation receipt. The human approves that immutable result
through the platform's human-only review field. Changing any bound input requires a new
segmentation and approval. The reviewed labels, not a new segmentation, initialize evolution.

The model is isotropic 2D grain growth, no-flux boundaries, one solid phase, dimensionless
units. Width must resolve at least four pixels. Transfer gates require preserved argmax
labels and adjacency and at most 5% initial diffuse area change. Any unresolved grain blocks evolution.
The solver records up to 61 actual states; playback does not interpolate states. Large
partitions use exact locally active fields instead of a dense field for every grain at
every pixel. The original grid is retained. Native PNG frames load on demand in the
workspace; initial/final numeric field checkpoints are available separately. The service
bounds pixel-step work at 12 billion and solver steps at 20000; a long requested duration
can be refused without preventing full-image segmentation. No silent crop, downsampling,
grain deletion or state interpolation is permitted. Runtime qualification is recorded
separately; never promise a duration without measurement.


Segmentation accuracy and experimental kinetics are not validated. Referee receipt checks
verify bindings and reported diagnostics; they are not independent scientific validation.
Keep the warning visible. User-provided data must remain in its authorized room.

Annotation handling (0.5.0): a conservative edge-strip detector proposes an explicit
non-material mask for black annotation bands with text or scale marks. Present
material_domain and resolution_notice beside segmentation_preview before approval.
Keep the full canvas and original annotation pixels unchanged in playback. Excluded
pixels have no grain labels or phase fields; the material edge is no-flux. The
initializer currently requires rectangular connected material components. Ambiguous
dark regions are not automatically excluded. The proposal still needs human review.


Annotation fallback: if automatic detection reports uncertainty, evolution is blocked.
Set image.footer_rows to the footer height in original pixels and review material_domain.
Zero means automatic. This excludes rows from the physics while preserving the full
canvas and source pixels. Do not choose annotation_policy=none merely to bypass a warning;
that selection explicitly declares that the dark edge should be treated as material.


Version 0.5.2 separates segmentation approval from evolution settings. After approving a map, duration, interface energy (gamma), and mobility can change without repeating segmentation. Each run starts from the approved map, not the preceding final state. Image, segmentation, domain-mask, and interface-width changes still require a new review. Run receipts include the executed configuration and its fingerprint. Existing rooms retain their pinned release; this source change is not an automatic room migration.

Report, training set and Adaption (follow-ups to an evolution): mode `report` runs
`microstructure.report`, a marimo notebook exported headless to one HTML page (title, the
hypothetical and dimensionless disclaimer, free-energy, grain-count, mean-area and grain-area
charts, the state table and a short summary). Mode `dataset` runs `microstructure.dataset`,
which writes 40 to 200 prompt and completion rows from the run's own tables with the same
rounding as the report, then `adaption.estimate`, which uploads the rows to Adaption Labs and
returns a credit estimate; nothing is spent. Mode `adapt-spends-credits` runs `adaption.adapt`:
it spends credits, adapts the rows, evaluates them and returns the score before and after, a
table and the adapted dataset. The service refuses an estimate above `ADAPTION_MAX_CREDITS`
(default 50), more than 200 rows, and any adapt that is not confirmed. Always estimate first.
The report is presentation. The rows are derived and illustrative. The evaluation is the
vendor's score of text quality, not a check of the physics, and may be skipped.
