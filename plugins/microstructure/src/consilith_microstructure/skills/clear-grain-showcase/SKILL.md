---
name: clear-grain-showcase
description: Upload a clear-grain image, review the segmentation and play a bounded computed phase-field evolution.
---

1. Collect an uploaded PNG, JPEG or single-frame TIFF into image.source. Never invent or fetch image paths.
2. Show the entire native-resolution image. This workflow never crops or resamples; crop must remain empty. Review all boundaries, including grains touching the outer image edge.
3. Expose segmentation method, contrast, optional threshold, minimum region size and width, plus dimensionless duration, energy and mobility.
4. Run with reviewed_result blank. Present segmentation_preview, material_domain, resolution_notice and segmentation_review initialization diagnostics before further work. Explain the proposed annotation exclusion; no pixels are cropped, and excluded pixels remain unchanged in playback.
5. Wait for the human-only approval action. Never populate reviewed_result yourself or claim that a model's inspection is human approval.
6. Run with the approved result. If any bound image or parameter changed, clear approval and repeat segmentation review.
7. Present evolution_sequence using the generic image-sequence player, plus report and native initial/final checkpoint downloads. Present the actual time, grain count and energy history. Use flexible GenUI composition with upload, review and evolution areas.
8. Describe results as hypothetical dimensionless 2D grain growth. No experimental accuracy or calibration claim is allowed.


If annotation detection is uncertain, stop before evolution and request a reviewed
non-material footer height (image.footer_rows, original pixels). Zero means automatic.
Do not silently set annotation_policy to none to bypass the domain-review block.


Version 0.5.2 separates segmentation approval from evolution settings. After approving a map, duration, interface energy (gamma), and mobility can change without repeating segmentation. Each run starts from the approved map, not the preceding final state. Image, segmentation, domain-mask, and interface-width changes still require a new review. Run receipts include the executed configuration and its fingerprint. Existing rooms retain their pinned release; this source change is not an automatic room migration.
