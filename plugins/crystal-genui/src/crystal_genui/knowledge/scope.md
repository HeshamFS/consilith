# Crystal coordination scope

This plugin computes geometric coordination, symmetry-distinct polyhedra,
bond-length and angular metrics, and crystal-chemical diagnostics. It uses the
Crystal-GENUI implementation with pymatgen, NumPy and SciPy.

Platform input is a bundled example or supplied UTF-8 CIF: at most 1 MiB, 256
fully occupied ordered sites, cell lengths >= 2 angstrom and volume >= 8 A^3.
The service refuses unsupported inputs rather than reducing or resampling them.

Neighbor methods are Shannon radii, CrystalNN and explicit cutoff. Report which
method and parameters were used. Lengths are angstrom, volumes cubic angstrom,
and angles degrees. Null metrics mean unavailable or inapplicable.

SrTiO3, BaTiO3, CaTiO3 and Bi2Se3 are approximate bundled examples. They are not
DFT-relaxed results. Input CIF coordinates are not relaxed by this workflow.
Pauling-rule agreement, bond-valence sums and distortion do not prove stability.

Consilith checks provenance and output shape, not independent scientific replay.
The standalone MCP server retains Materials Project/OPTIMADE tools and its
interactive viewer. The platform uses existing text, JSON and table components.
No automatic conversion to phase-field parameters is established.
