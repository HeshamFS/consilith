---
name: crystal-analyze
description: Analyze a supplied crystal in Consilith and inspect coordination metrics with source and units.
---

1. Use a bundled example name or CIF text in `crystal.structure`, or a room-owned
   uploaded CIF in `crystal.source`. An upload overrides the text until removed.
2. Confirm the neighbor method. Cutoff needs an explicit maximum bond distance
   in angstrom; Shannon tolerance is a fraction. Do not silently switch methods.
3. Run the `crystal-polyhedra` workflow with `crystal.analyze` through the host.
4. Present `analysis_summary`, `polyhedra_table`, source and method settings.
   Offer `polyhedra_json` and the CSV artifacts for detailed inspection.
5. Explain missing metrics and limitations. Bundled examples are approximate,
   and coordination checks are not thermodynamic or experimental validation.
6. For a 3D viewer, describe the standalone MCP App or CLI HTML export. The
   Consilith room currently shows native text, JSON and tables.

Never use a server filesystem path, remote database identifier or API key as a
platform structure input. Do not infer grain-growth parameters from these metrics.
