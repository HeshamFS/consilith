# Initial mathematical contract, 2026-10-21

One solid phase, N distinct grains, 2D square cell-centered grid, constant isotropic
boundary energy gamma and mobility M. No orientation-dependent energy, composition,
elasticity, nucleation, or physical prediction from uncalibrated images.

At each material cell phi belongs to the probability simplex: phi_i >= 0, sum phi_i = 1.
The energy per unit out-of-plane thickness is

    F = integral sum_i [kappa/2 |grad phi_i|^2 + W/2 phi_i(1-phi_i)] dA
    kappa = 4 gamma delta / pi^2; W = 4 gamma / delta
    L = pi^2 M / (4 delta)

The evolution is the constrained L2 gradient flow of F, discretized by a projected
explicit gradient step onto the simplex. Projection is the obstacle constraint in
the discrete variational method, not unreported clipping after independent evolution.
For a binary interface, phi=(1+sin(pi d/delta))/2 within |d|<delta/2, with constant
extensions outside. Its energy is gamma and its limiting inward velocity is
M gamma curvature. Thus R^2=R0^2-2 M gamma t for an isolated circle in the
thin-interface limit. Triple junction accuracy is a separate qualification gate.

The discrete gradient uses nearest-neighbor edge differences; the Laplacian is its
negative adjoint. Missing exterior edges implement zero normal flux. Periodic
boundaries include wrapping edges. We do not impose grain-area conservation.
An explicit step is capped by 0.8/[L(8 kappa/h^2+W)], a conservative bound on the
discrete energy-gradient Lipschitz constant. Each accepted step checks energy descent.
Float64 fields and independent energy/finite-difference derivative tests are required.

Physical convention: h, delta in m; gamma in J/m^2; M in m^4/(J s); t in s;
F in J/m. Dimensionless mode must be labeled as such and cannot claim seconds.
The grid must resolve delta with >=4 cells; practical accuracy is determined by
separate grid, time-step, and width studies rather than that lower bound.

Initialization takes accepted integer labels, estimates signed distance to the
pixel-cell boundary, applies the compact sinusoidal profile, then normalizes weights
at multi-grain junctions. This junction construction is an initial approximation,
not an equilibrium solution. The original labels remain immutable. The initializer
reports exact argmax mismatch, per-grain diffuse area changes and grains without a
resolved pure interior. It rejects unknown pixels and does not silently delete grains.

Acceptance targets frozen before runs: field bounds/partition <=1e-10; discrete
energy gradient relative error <=1e-6; circle slope relative error <=2% on the
declared resolved thin-interface fixture; spatial order >1.7 and temporal order >0.8
on a smooth periodic binary solution with an analytic growth rate. Initialization
must recover all accepted labels by argmax on the tested fixtures. Real image
segmentation accuracy, junction equilibrium and experimental kinetics are NOT
established by those gates. Unknown outcomes remain unqualified.

Trajectory extension (0.2.0): sampling observes accepted solver states without changing
the timestep sequence. Bounded outputs include label maps, diffuse-interface density,
per-grain diffuse areas and initial/final full fields. Standard image evolution requires
zero label/topology mismatch, resolved interiors and <=5% per-grain initial diffuse-area
change. This engineering transfer gate is distinct from scientific model accuracy.
Unknown pixels inside the material domain remain invalid. An excluded-pixel mask used
to mark missing observations does not make those pixels a known nonmaterial region.


Full-domain storage extension (0.4.0): the mathematical energy and timestep remain
unchanged. Remove the common candidate offset -dt*L*W/2 before simplex projection.
The remaining candidates sum to 1+dt*L*W; all phases absent from a cell and its four
stencil neighbors have candidate zero. The projection threshold is positive, hence
those absent phases cannot enter the result. Evaluating the exact union is sufficient.
Local storage expands and retries on overflow, with no cutoff or truncated grain set.
Initialization evaluates the same compact distance profile in finite-support boxes;
these are internal storage windows, not simulation crops or independently evolved tiles.
Global neighbors remain coupled across the full original grid. Tests compare dense and
local fields, energy, boundaries and timestep convergence independently.

Material-domain extension (0.5.0): an explicit boolean domain mask distinguishes
material from nonmaterial annotation pixels on the unchanged original image canvas.
All phase fields are exactly zero outside this mask; excluded pixels have label zero,
zero active slots and zero interface density. Inside it, the ordinary simplex constraint
and all transfer gates remain unchanged. Unknown label zero inside material is refused.
The mask is preserved in provenance, grain-map digests, trajectory arrays and sparse
initial/final checkpoints. Neither the source raster nor material pixel spacing changes.

The discrete energy contains material-cell potentials and edges joining two material
cells only. An edge into an excluded cell is absent, giving the same zero-flux condition
as an exterior edge. The implementation reflects the current cell in that stencil
position. Excluded cells cannot acquire phases. This masked graph energy and gradient
are checked against an independent graph calculation, and a rectangular material region
embedded in a larger canvas reproduces the dense solver on the identical rectangle.

The label-to-field initializer currently accepts rectangular connected material
components only. Components are initialized independently, preventing fields from
crossing an excluded gap. Distance to another grain ignores excluded cells; compact
profiles remain on material pixels. Nonrectangular components fail closed because
Euclidean distances can cross holes; a separately qualified reconstruction would be
needed. The evolution kernel itself supports arbitrary explicit masks when valid
initial fields are provided directly, with no-flux hole boundaries tested independently.

For the reference image, rows 720 through 775 contain the confirmed annotation footer.
The canvas remains 960 by 776 pixels, while all 960 by 720 material pixels are segmented
and evolved at native spacing. The annotation pixels are preserved for display and
never counted as grains. The corrected reconstruction has 141 grains with resolved
interiors; the app uses the strict transfer gates and no boundary-grain exception.

These checks establish numerical implementation and faithful transfer of accepted
labels, not independent segmentation ground truth or calibrated experimental kinetics.
