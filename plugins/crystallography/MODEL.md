# Scientific scope

The numerical implementation is imported from Crystal-GENUI commit
`a49515fa8c0d5fa980834b4998d1b24e823d95fb`. This adaptation adds packaging and platform
interfaces; it does not establish new scientific validation.

- Bundled structures are approximate examples, not DFT-relaxed structures.
- CIF analysis uses the supplied coordinates. It does not relax the structure.
- Coordination depends on Shannon radii, CrystalNN or the user's cutoff.
  Oxidation-state inference and ionic-radius availability affect the result.
- Bond lengths are in angstrom, volumes in cubic angstrom, angles in degrees;
  other metrics carry their schema-defined units. Material descriptors use
  additional explicit units, including cubic metres per mole.
- Pauling-rule and bond-valence checks are diagnostics, not proof of stability.
- Missing geometric metrics remain null; they are not replaced with invented values.
- Consilith receipt checking verifies input binding and output shape. It is not
  an independent calculation or experimental validation.
- Atomistic coordination metrics alone do not supply grain-boundary mobility,
  interface energy or a calibrated phase-field model. No automatic coupling to
  the microstructure grain-growth plugin is implemented.

Consult [the upstream guide](UPSTREAM.md) for method descriptions and source references.
