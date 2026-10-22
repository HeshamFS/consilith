"""A stand-in for a collaborator's phase-field MCP server.

It shows the receiving side of the contract: a tool that accepts a
``crystallography/material-descriptor@1`` document and uses its units. The physics is a toy
2D Allen–Cahn model; replace it with the real phase-field code.

    python examples/mock_phasefield_server.py          # stdio
"""

from __future__ import annotations

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

EV = 1.602176634e-19
AVOGADRO = 6.02214076e23

mcp = MCPServer(
    name="phasefield-mock",
    instructions="Toy Allen–Cahn solver fed by crystallography material descriptors.",
)


@mcp.tool(
    description="Run a toy 2D Allen–Cahn simulation parameterised by a crystallography material descriptor."
)
def run_phase_field(descriptor: dict, grid: int = 64, steps: int = 300, seed: int = 0) -> dict:
    if not str(descriptor.get("schema", "")).startswith("crystallography/material-descriptor@1"):
        raise ToolError("expected a crystallography/material-descriptor@1 document")
    vm = descriptor["molar_volume"]  # m^3 per mole of formula units
    atoms_per_fu = descriptor["atoms_per_formula_unit"]
    e_form = descriptor.get("formation_energy_per_atom")
    # bulk free-energy density scale: |ΔH_f| (eV/atom) → J/m^3 of the phase
    f0 = abs(e_form) * atoms_per_fu * EV * AVOGADRO / vm if e_form is not None else None

    rng = np.random.default_rng(seed)
    phi = 0.5 + 0.05 * rng.standard_normal((grid, grid))
    dt, kappa = 0.1, 1.0
    for _ in range(steps):
        lap = sum(np.roll(phi, s, a) for s in (1, -1) for a in (0, 1)) - 4 * phi
        phi += dt * (kappa * lap - 2 * phi * (1 - phi) * (1 - 2 * phi))
    return {
        "material": descriptor["formula"],
        "free_energy_density_scale_J_per_m3": f0,
        "bulk_modulus_GPa": (descriptor.get("elastic") or {}).get("bulk_modulus_vrh"),
        "polyhedral_distortion": {
            t["type_id"]: t["metrics"]["distortion_index"] for t in descriptor["polyhedron_types"]
        },
        "global_instability_index": descriptor.get("global_instability_index"),
        "phase_fraction": float((phi > 0.5).mean()),
        "interface_fraction": float(((phi > 0.1) & (phi < 0.9)).mean()),
    }


if __name__ == "__main__":
    mcp.run("stdio")
