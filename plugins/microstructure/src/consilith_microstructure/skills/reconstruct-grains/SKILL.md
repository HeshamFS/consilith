---
name: reconstruct-grains
description: Reconstruct a 2D grain map from a micrograph or raw square-grid EBSD, inspect ambiguity, and initialize diffuse grain fields before simulation.
---

1. Read the scope resource and identify whether the input is an image or raw EBSD.
2. Preserve source bytes. Ask for explicit crop/calibration when labels or scale bars are present.
3. Use microstructure.segment_image for visible boundaries; use microstructure.segment_ebsd
   for measured orientations. Never infer orientations from IPF screenshot colors.
4. Compare global, adaptive and ridge methods with explicit bright/dark boundary polarity.
   Use guided markers for reviewed corrections; preserve pore/unknown masks. Do not claim
   method agreement or a processed reference mask is independent grain ground truth.
5. Show the candidate map and its unknown/phase/size diagnostics. Request review or correction.
6. Use microstructure.initialize_fields with recorded interface width. Inspect label recovery,
   diffuse area changes and unresolved grains. Never hide small-grain loss or replace the map
   with a synthetic tessellation.
7. Simulation requires accepted reconstruction, supported single-phase data and sourced or
   explicitly hypothetical gamma/mobility. Report dimensionless time when uncalibrated.
8. Use microstructure.trajectory to show the complete sampled evolution: stable grain IDs,
   diffuse interfaces, time, grain areas, disappearance and energy. Call it a hypothetical
   scenario when mobility/energy are not calibrated. Unknown/pore masks and mixed phases
   must not be evolved by silently filling or relabeling them.
9. Distinguish a successful computation, numerical verification and experimental validation.
