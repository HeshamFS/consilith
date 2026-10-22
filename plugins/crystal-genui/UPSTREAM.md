# Crystal-GENUI

VESTA-style coordination polyhedron analysis of DFT relaxed Materials Project structures, served
as an **MCP server with an interactive 3D viewer** (an MCP App). Other generative-UI projects, such as
a separate atomistic simulation server, can consume its output over MCP through a versioned JSON contract.

```
Materials Project ──► crystal-genui MCP server ──► 3D polyhedra viewer (MCP App, in the chat)
   (mp-api)              │  analyze_polyhedra
                         │  get_material_descriptor ──► crystal-genui/material-descriptor@1
                         ▼                                         │
                  MCP host (Claude, …) ◄──────────────────────────┘
                         │
                         ▼
                 Other MCP server (collaborator's)
```

## Workflow

`analyze_polyhedra` (and the `crystal-genui` command) runs the same four steps on any structure:

1. **Coordination environment of every Wyckoff site.** Sites are grouped into symmetry orbits with
   spglib (Wyckoff letter, multiplicity, site symmetry). Each orbit's environment is found from
   **Shannon ionic radii** (Shannon, *Acta Cryst.* A32, 751 (1976); the table behind
   abulafia.mt.ic.ac.uk/shannon, as shipped with pymatgen), self-consistently: for each
   coordination number *n* tabulated for the ion, a neighbour is bonded if
   d ≤ (r_centre(n) + r_ligand)(1 + tolerance), and the environment is the *n* for which exactly *n*
   neighbours qualify. Oxidation states come from charge balance. Bonds are found from the cation
   side and read backwards for the anions, so the bond graph is symmetric and anions get their own
   environments (e.g. O in an OSr4Ti2 octahedron in SrTiO3).
2. **Which positions can form a polyhedron.** A site is a polyhedron centre only if at least four
   ligands enclose a volume with the centre inside it, and no ligand lies inside the hull of the others.
   Sites that fail are listed with the reason (for example the outer Se of Bi2Se3, bonded to three Bi).
3. **Analyses of every polyhedron**, built around every symmetry-equivalent copy in the cell (below).
4. **Crystal-chemical checks**: Pauling's rules, bond valence and symmetry (below).

`method="crystalnn"` (Voronoi solid angles) and `method="cutoff"` (fixed bond window, as in a VESTA
bond search) are alternatives to Shannon radii. `include_element_shells=true` also builds the nearest
shell of each element around each site, such as the Ti8 cube around Sr, closed at the first jump of
more than 10 % in distance.

### Analyses per polyhedron

| Quantity | Definition |
|---|---|
| Average bond length | l_av = (1/n) Σ l_i |
| Polyhedral volume | volume of the convex hull of the ligands |
| Distortion index (bond length) | Baur (1974): D = (1/n) Σ \|l_i − l_av\| / l_av |
| Effective coordination number | Hoppe (1979), as VESTA does it: l_w = Σ l_i e^{1−(l_i/l_min)^6} / Σ e^{1−(l_i/l_min)^6}, ECoN = Σ e^{1−(l_i/l_w)^6} |
| Quadratic elongation | Robinson et al. (1971): ⟨λ⟩ = (1/n) Σ (l_i/l_0)², l_0 from the regular polyhedron of equal volume (tetrahedra, octahedra, cubes) |
| Bond-angle variance | Robinson et al. (1971): σ² = Σ (θ_i − θ_0)² / (m − 1) over polyhedron edges |
| Shape | from the hull's vertex count and face polygons (octahedron, trigonal prism, square antiprism, cuboctahedron, …) |
| Bond-valence sum | Brown; R_0 from Brese & O'Keeffe (1991), b = 0.37 Å |
| Off-centring | distance of the centre from the ligand centroid (e.g. Ti in BaTiO3) |
| Connectivity | corner/edge/face sharing with polyhedra of the same type, across periodic boundaries, with bridging M–X–M angles (octahedral tilts) |

### Crystal-chemical checks

| Check | What is reported |
|---|---|
| Pauling 1 | radius ratio r+/r− → predicted CN vs observed |
| Pauling 2 | electrostatic valence Σ z_c/CN_c at each anion vs its charge |
| Pauling 3 | corner/edge/face sharing between cation polyhedra |
| Pauling 4 | edge/face sharing between high-valence (≥4), low-CN cations |
| Pauling 5 | number of distinct environments per element |
| Brown valence-sum rule | BVS per site and the global instability index GII |
| Bond-count balance | Σ_cations m·CN = Σ_anions m·CN |
| Site symmetry | every polyhedron is invariant under its centre's site-symmetry group |

## Structure sources

| Spec | Source |
|---|---|
| `optimade:<db>:<id>` | any open database through [OPTIMADE](https://www.optimade.org), no key needed |
| `mp-5229` | Materials Project native API (needs `MP_API_KEY`; also gives elastic data) |
| `SrTiO3`, `BaTiO3`, `CaTiO3`, `Bi2Se3` | bundled approximate experimental structures, for offline use |
| CIF / POSCAR path or CIF text | your own file |

OPTIMADE databases built in (`search_open_databases` queries them in parallel; any other OPTIMADE
base URL also works, see <https://providers.optimade.org>):

| Key | Database | Level of theory |
|---|---|---|
| `mp` | Materials Project | PBE / PBE+U, r2SCAN |
| `oqmd` | OQMD | PBE / PBE+U |
| `alexandria`, `alexandria_pbesol`, `alexandria_scan` | Alexandria | PBE, PBEsol, SCAN |
| `aflow` | AFLOW | PBE / PBE+U |
| `jarvis` | JARVIS-DFT (NIST) | OptB88vdW |
| `nomad` | NOMAD | various (uploaded calculations) |
| `mc3d`, `mc2d` | Materials Cloud 3D / 2D | PBEsol / PBE |
| `odbx` | open database of xtals | PBE |

Entries with partial occupancy or fewer than three periodic dimensions are flagged in the source note.

## Downloading the tables (CSV)

Every analysis can be downloaded as CSV in four tables:

* `polyhedra`: one row per polyhedron in the cell, with Wyckoff site, fractional position, every metric and the bond lengths
* `types`: one row per symmetry-distinct polyhedron
* `sites`: the coordination environment of each Wyckoff site and whether it forms a polyhedron
* `bonds`: one row per centre–ligand bond

From the viewer, use the **Download tables** buttons. From the command line:

```bash
crystal-genui optimade:oqmd:<id> --format csv --table polyhedra > polyhedra.csv
crystal-genui my.cif --format csv --table all -o results/my     # my_polyhedra.csv, my_types.csv, …
```

Over MCP, `export_polyhedra_csv` returns the table as a `text/csv` resource.

## Install

```bash
git clone <this repo> && cd crystal-genui
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Python ≥ 3.10. The MCP server uses the official Python SDK v2 (`mcp>=2.3`).

### Materials Project API key

Get a key at <https://next-gen.materialsproject.org/api> and put it in the server's environment,
never in a chat:

```bash
export MP_API_KEY=your_key_here
```

The key is only needed for the native Materials Project API. OPTIMADE databases (including
Materials Project's own OPTIMADE endpoint), CIF/POSCAR files and the bundled examples need no key.

## Command line

```bash
crystal-genui mp-5229                         # sites → polyhedra → analyses → checks, as text
crystal-genui optimade:alexandria:<id>        # any OPTIMADE database
crystal-genui my_structure.cif --element-shells
crystal-genui my_structure.cif --method cutoff --max-bond 3.2 --centers Bi --ligands Se
crystal-genui CaTiO3 --format html -o CaTiO3.html   # standalone viewer, open in a browser
crystal-genui mp-5229 --format json           # crystal-genui/polyhedra@1
crystal-genui mp-5229 --format descriptor     # crystal-genui/material-descriptor@1
```

## MCP server

Tools:

| Tool | Returns |
|---|---|
| `analyze_polyhedra(structure, method?, radius_tolerance?, include_element_shells?, …)` | `polyhedra@1` + **interactive 3D viewer** |
| `export_polyhedra_csv(structure, table)` | CSV of polyhedra, types, sites or bonds |
| `search_open_databases(formula? \| elements?, databases?)` | DFT-relaxed structures across OPTIMADE databases |
| `get_material_descriptor(structure)` | `material-descriptor@1` for coupled models |
| `get_structure(structure)` | `structure@1` |
| `search_materials(formula? \| chemsys? \| elements?, max_energy_above_hull?)` | Materials Project ids, most stable first |
| `list_example_structures()` | bundled examples, and whether an API key is configured |

`structure` is `optimade:<db>:<id>`, a Materials Project id (`mp-5229`), an example name, or CIF text.
Resources: `ui://crystal-genui/viewer.html` (the viewer) and
`schema://crystal-genui/*.v1.schema.json` (the JSON Schemas).

The viewer follows the MCP Apps extension (`text/html;profile=mcp-app`): hosts that support it show
it inline when `analyze_polyhedra` runs. Hosts without MCP Apps get a compact text summary plus the
full `structuredContent`.

**The viewer** is black with white ink (cell grid, bonds, polyhedra, text); only the atoms keep element
colours, and a *white atoms* switch removes those too.

* **Atoms in a 3D grid of unit cells.** Every site is drawn in every cell of an a×b×c block
  (default: a roughly equiaxed block of up to 3 cells per axis; change it with *cells*), with images on
  the outer faces, edges and corners. Bonds come from the analysed bond graph. Hovering an atom shows its
  site, Wyckoff position, fractional coordinates, which cell of the grid it sits in, and its radius.
* **Atom size = ionic size.** Each sphere's radius is the Shannon (1976) ionic radius of that ion at its
  oxidation state and observed coordination (`ionic_radius` in each site environment), times the *size*
  slider. At ×1 the spheres are the ions themselves, so cations and anions touch along each bond.
  Sites without a tabulated ionic radius fall back to the atomic radius.
* **Polyhedra.** The selected polyhedron is drawn in the cell you clicked; tick a type to draw it around
  every centre in the grid. Click an atom or polyhedron for the VESTA-style readout.
* **Move atoms.** Turn on *Move atoms* and drag any atom (Alt for fine steps); every periodic image moves
  with it. Bond lengths, volume, distortion index, ECoN and quadratic elongation update live. *Reset*
  restores the analysed structure, *Export CIF* saves the edited cell (P1), and *Re-run full analysis*
  (inside an MCP host) redoes coordination, symmetry, bond valence and Pauling checks on it.
* Below the canvas: the site-environment table, the analyses of every polyhedron type, the
  crystal-chemical checks, every polyhedron in the cell, CSV downloads, and recompute with other settings.

### If using Claude Desktop / Claude Code (stdio)

```json
{
  "mcpServers": {
    "crystal-genui": {
      "command": "/path/to/crystal-genui/.venv/bin/crystal-genui-mcp",
      "env": { "MP_API_KEY": "your_key_here" }
    }
  }
}
```

Claude Code: `claude mcp add crystal-genui --env MP_API_KEY=... -- /path/to/.venv/bin/crystal-genui-mcp`

### Remote (Streamable HTTP)

```bash
crystal-genui-mcp --http --host 0.0.0.0 --port 8765    # endpoint: http://<host>:8765/mcp
```

Put it behind HTTPS (a reverse proxy or a tunnel) before sharing the URL with someone else.

## Combining with another generative UI (e.g. a phase-field server)

There are two ways to connect the servers. Neither needs the two projects to share code; they share
the JSON contract in [`schemas/`](schemas/).

**1. Both servers in one MCP host.** Register both, and the model passes data between them:

```json
{
  "mcpServers": {
    "crystal-genui": { "command": "/path/to/crystal-genui/.venv/bin/crystal-genui-mcp",
                       "env": { "MP_API_KEY": "..." } },
    "phasefield":    { "command": "python", "args": ["/path/to/phasefield/server.py"] }
  }
}
```

or, for a server running in another lab, add its URL as a remote MCP server (`"url": "https://…/mcp"`).
Then ask: *"Get the material descriptor for mp-5229 and run the phase-field simulation with it."*
Each server renders its own UI in the conversation.

**2. Direct server-to-server pipeline**, with no LLM in the loop:

```bash
python examples/combine_servers.py BaTiO3
```

[`examples/combine_servers.py`](examples/combine_servers.py) calls `get_material_descriptor` on this
server and passes the result to `run_phase_field` on
[`examples/mock_phasefield_server.py`](examples/mock_phasefield_server.py), a toy Allen–Cahn stand-in
for your collaborator's server that shows how to read the descriptor. Swap its
`StdioServerParameters` for their command or URL.

### The data contract

Every document carries a `schema` tag; consumers should check it and refuse unknown major versions.

| Schema | Purpose |
|---|---|
| `crystal-genui/structure@1` | lattice (Å, degrees, row-vector matrix), space group, sites with VESTA-style labels |
| `crystal-genui/polyhedra@1` | structure with Wyckoff sites + site environments + polyhedron types + every polyhedron (ligands with lattice images, metrics, hull faces, connectivity) + crystal-chemical checks |
| `crystal-genui/material-descriptor@1` | composition, atoms per formula unit, molar volume (m³/mol), density, DFT formation energy and energy above hull (eV/atom), band gap, elastic tensor and VRH moduli (GPa) when Materials Project has them, global instability index, and the coordination polyhedron types |

Units: Å, Å³, degrees, eV/atom, GPa, g/cm³. The JSON Schema files are generated from the Pydantic
models in `src/crystal_genui/schema.py`; regenerate with `python -m crystal_genui.schema`
(a test fails if they are stale). Additive changes keep `@1`; breaking changes bump to `@2`.

## Layout

```
src/crystal_genui/
  structures.py   structure loading (Materials Project, OPTIMADE, CIF, examples)
  optimade.py     OPTIMADE client for open databases
  polyhedra.py    symmetry, Shannon coordination, feasibility, shapes, metrics
  rules.py        Pauling's rules, bond valence, bond-count and symmetry checks
  tables.py       CSV tables
  core.py         builds the schema documents
  schema.py       Pydantic models = the data contract
  server.py       MCP server (tools, viewer resource, schema resources)
  viewer.html     the MCP App (dependency-free canvas renderer)
  cli.py          command line
schemas/          generated JSON Schemas
examples/         two-server pipeline and a mock phase-field server
tests/
```

## References

* K. Momma & F. Izumi, *J. Appl. Cryst.* 44, 1272 (2011), VESTA 3.
* W. H. Baur, *Acta Cryst.* B30, 1195 (1974), distortion index.
* R. Hoppe, *Z. Kristallogr.* 150, 23 (1979), effective coordination number.
* K. Robinson, G. V. Gibbs & P. H. Ribbe, *Science* 172, 567 (1971), quadratic elongation, bond-angle variance.
* N. E. Brese & M. O'Keeffe, *Acta Cryst.* B47, 192 (1991), bond-valence parameters.
* H. Pan et al., *Inorg. Chem.* 60, 1590 (2021), CrystalNN.
* R. D. Shannon, *Acta Cryst.* A32, 751 (1976), ionic radii.
* L. Pauling, *J. Am. Chem. Soc.* 51, 1010 (1929), Pauling's rules.
* A. Salinas-Sanchez et al., *J. Solid State Chem.* 100, 201 (1992), global instability index.
* C. W. Andersen et al., *Sci. Data* 8, 217 (2021), OPTIMADE.
