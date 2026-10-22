# Crystal-GENUI: standalone package and Consilith plugin

Crystal coordination and polyhedra analysis, adapted from
[Anny-tech/Crystal-GENUI](https://github.com/Anny-tech/Crystal-GENUI) at commit
`a49515fa8c0d5fa980834b4998d1b24e823d95fb`. The upstream scientific implementation,
CLI, MCP App viewer, examples and MIT license are retained. Version 0.2.0 adds the
same standalone-first boundaries as [microstructure](../microstructure/README.md).
See [UPSTREAM.md](UPSTREAM.md) for the original feature guide and [MODEL.md](MODEL.md)
for interpretation limits.

## Layout

```text
crystal-genui/
  pyproject.toml                 # independent distribution and optional extras
  requirements.lock             # resolved standalone server dependencies
  docker/Dockerfile              # standalone HTTP MCP service
  README.md / MODEL.md / LICENSE
  src/crystal_genui/
    core.py / polyhedra.py       # existing scientific API
    rules.py / structures.py     # chemistry checks and structure sources
    schema.py / tables.py        # typed outputs and CSV exports
    cli.py                      # standalone command line
    server.py                   # original MCP App, stdio and HTTP
    viewer.py / viewer.html      # independent interactive viewer
    configuration.py            # bounded platform input validation
    adapter.py                  # Consilith manifest, case, workflow, checker
    platform_server.py          # canonical Consilith result envelopes
    schemas/                    # JSON schemas shipped in the wheel
    skills/analyze-crystal/      # agent workflow instructions
    knowledge/                  # scope and interpretation guidance
  tests/                        # upstream and integration regression checks
  examples/                     # upstream standalone server composition
```

The core and CLI import no Consilith applications or MCP server. The optional
adapter imports contracts and input validation; scientific computation runs in
the separate MCP service. This folder can also be copied to its own repository.
Consilith integration then needs the contracts/runtime wheel bundle separately.

## Standalone use

From this folder, install into your own environment:

```sh
pip install .
crystal-genui SrTiO3 --format text
crystal-genui CaTiO3 --format html -o crystal.html
pip install '.[server]'
crystal-genui-mcp
uvicorn crystal_genui.server:app --factory --host 127.0.0.1 --port 8080
```

HTTP MCP uses `/mcp`. The original stdio command, tool names and MCP App viewer
remain available. HTML export works without MCP installed. The viewer loads its
JavaScript libraries from the URLs in the original HTML, so it needs network
access on first load; the bundled structure analyses work offline.

Materials Project access is optional: install `.[materials-project]` and provide
`MP_API_KEY` through the process environment. Do not place API keys in source or
room inputs. The existing standalone OPTIMADE searches are retained.

## Consilith use

From the Consilith repository root:

```sh
python tools/docker_stack.py up --manifest examples/deployment.crystal-genui.json
python tools/docker_stack.py check --manifest examples/deployment.crystal-genui.json
```

Create a room with **Crystal polyhedra**, choose a bundled example (`SrTiO3`,
`BaTiO3`, `CaTiO3`, `Bi2Se3`) or paste CIF text, select the analysis method and Run.
Alternatively upload a UTF-8 CIF file; an upload takes precedence over the text
field until removed. Results include a provenance receipt, full JSON report,
readable analysis, a polyhedron-types table and all four CSV downloads.

The worker entry point is `crystal_genui.adapter:extension`. The service factory
is `crystal_genui.platform_server:app`; it exposes `crystal.analyze` on `/mcp`.
Its inputs are bundled example names or supplied CIF contents, not file paths,
Materials Project IDs or OPTIMADE URLs. Remote database tools and material
descriptors remain in the standalone server. The platform service does not need
an API key or network access for analysis.

The room uses Consilith's existing JSON, text and table renderers. The interactive
3D MCP App remains available in the standalone server and HTML export; displaying
it inside Consilith would require a separate platform renderer extension.

Platform limits: 1 MiB CIF, 256 ordered fully occupied sites, cell lengths at least
2 angstrom, volume at least 8 cubic angstrom, cutoff at most 6 angstrom, and the
host's bounded accurate-tool deadline. No silent structure reduction or resampling
is performed. Cutoff mode requires an explicit upper bond distance. Parameters
are recorded in the receipt. Checks cover the receipt and output structure;
they do not certify experimental accuracy or independently replay the science.

## Docker

From this folder:

```sh
docker build -f docker/Dockerfile -t crystal-genui:local .
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  --cap-drop=ALL --security-opt no-new-privileges --memory 2g --cpus 2 \
  -p 127.0.0.1:8080:8080 crystal-genui:local
```

This image runs the standalone MCP server with Materials Project support. Use
the Consilith deployment manifest for the separate platform service and worker.
The Docker build uses the resolved dependency lock; regenerate it after changing
dependencies with `uv pip compile pyproject.toml --extra server --extra materials-project
--python-version 3.12 --output-file requirements.lock`.

## Development

For standalone tests, install `.[dev]` and run `pytest`. For integration tests use
Python 3.12, install Consilith contracts/runtime and this package's platform extra:

```sh
pip install ../../packages/contracts ../../packages/plugin-runtime '.[platform,test]'
pytest
```

Tests requiring an MP API key are skipped when it is absent. Schemas live inside
the installed package and can be regenerated with `python -m crystal_genui.schema`
from a writable source installation. The original schemas directory moved there
so wheels carry the same resources that the server advertises.

The deployment example is opt-in; the default Consilith deployment is unchanged.
Existing rooms retain their admitted plugin release pins.
