---
name: crystal-analyze
description: Analyze a supplied crystal in Consilith and inspect coordination metrics with source and units.
---

1. Use a bundled example name or CIF text in `crystal.structure`, or a room-owned
   uploaded CIF in `crystal.source`. An upload overrides the text until removed.
2. Confirm the neighbor method. Cutoff needs an explicit maximum bond distance
   in angstrom; Shannon tolerance is a fraction. Do not silently switch methods.
3. Run the `crystal-polyhedra` workflow with `crystal.analyze` through the host.
4. Present `analysis_summary`, `structure_summary` (formula, space group, cell, source and
   method), `polyhedra_table`, `site_environments_table`, `all_polyhedra_table` and
   `crystal_checks_table` (pass, warn, fail or info per check). Offer `polyhedra_json` and the
   CSV artifacts for detailed inspection.
5. Explain missing metrics and limitations. Bundled examples are approximate,
   and coordination checks are not thermodynamic or experimental validation.
6. Show `polyhedra_viewer` for the 3D scene. It draws only the structure; every table and
   number above is a native Consilith output, not part of the viewer.

Never use a server filesystem path, remote database identifier or API key as a
platform structure input. Do not infer grain-growth parameters from these metrics.
