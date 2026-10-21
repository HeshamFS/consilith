# Scope

This plugin reconstructs candidate 2D grain maps and evolves an isotropic single-solid-phase
grain network. Images do not supply crystallographic orientation or physical mobility.
Raw EBSD requires explicit symmetry and boundary threshold. Unknown pixels and multiple
solid phases are not silently filled or collapsed. A 2D section is not a complete model
of 3D grain dynamics. Interface width is a numerical model parameter with measured
reconstruction error; grain labels and diffuse fields are distinct artifacts.

Image adapters: global threshold watershed, adaptive local threshold watershed, multiscale
ridge watershed and explicitly guided watershed. Choose boundary polarity from the source,
compare candidates and review overlays. No automatic method is a universal grain detector.
The guided method takes interior [x,y] points in cropped-image coordinates. An explicit
boolean excluded_mask preserves pores/unknown regions as zero; the single-solid-phase
initializer then refuses them. Filling these pixels is a model change, not image cleanup.

Every conversion measures argmax labels, grain IDs, adjacency, diffuse area and centroid
changes. Evolution requires zero label/topology mismatch, resolved grain interiors, and at
most 5% initial diffuse-area change per grain in the standard workflow. This 5% is an
engineering acceptance budget, not a physical accuracy theorem. Real segmentation can still
be wrong despite a perfectly faithful conversion.

microstructure.trajectory returns actual solver frames, PNG-compatible density/label arrays,
a GIF, grain-area histories and energy history. Frame sampling does not alter timestepping.
The GIF has constant playback time per frame; scientific times are recorded explicitly.
All outputs come from bounded CPU tools. There is no GPU or pretrained-model dependency.
SEM phase morphology, pores and indistinct boundaries still require explicit review.

