<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/consilith-banner-dark.svg">
    <img alt="Consilith: where agents do science together" src="docs/assets/consilith-banner-light.svg" width="100%">
  </picture>
</h1>

A multiplayer agentic workspace with independently packaged capabilities. People and their agents collaborate through owned inputs, proposals, approvals, recorded execution and signed evidence. An agent composes a workspace from approved UI components; plugins provide the task knowledge and implementation.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/consilith-workflow-dark.svg">
  <img alt="How a result is made: bring an agent, take a seat, propose, owner approves, solver runs, referee checks, signed result" src="docs/assets/consilith-workflow-light.svg" width="100%">
</picture>

## What is included

- `apps/room`: accounts, collaboration, agent orchestration, permissions and execution records.
- `apps/referee`: independently configured checking and signed evidence.
- `apps/web`: the branded React workspace and generative UI renderer.
- `apps/siwc`: the containerized Sign in with ChatGPT bridge.
- `packages/contracts` and `packages/plugin-runtime`: public schemas and isolated plugin transport.
- `examples/agentic-plugin-template`: starter 1.1.2 with MCP tools, schemas, skills, knowledge, workflow, checker and an owner-approved proposal policy.
- `plugins/microstructure`: independent experimental 2D reconstruction and grain-growth plugin, with MCP services, skills, knowledge and a standalone solver.
- `plugins/crystallography`: independent crystal coordination analysis package, with a standalone MCP App and optional Consilith workflow. See the [plugin guide](plugins/crystallography/README.md).
- `docker`, `tools` and deployment manifests: reproducible container builds and plugin lifecycle operations.

The starter produces text, structured JSON and downloadable files without a numeric placeholder. An optional text-count operation demonstrates numeric compatibility. The optional microstructure plugin is included as experimental research software; its segmentation candidates require human review and its default kinetics are dimensionless scenarios, not experimentally calibrated predictions.

## Run locally with Docker

Install Python 3.12, uv and Docker with Linux containers enabled. From this repository root:

```sh
python tools/docker_stack.py up
python tools/docker_stack.py check
```

Open [Consilith](http://127.0.0.1:8800). The development deployment uses a local sign-in mailbox. Connect your own account at `/settings/agent` to enable ChatGPT; the bridge container being healthy does not mean an account is authorized. Account sessions and generated secrets never belong in Git.

The initial build fetches pinned dependencies and the separately licensed ChatGPT DevKit. Read [Docker operations](docs/docker.md) and [the bridge contract](docs/siwc.md), including its usage terms. This is a local development configuration, not an internet-facing production deployment.

## Run the microstructure showcase

The default deployment remains domain-neutral with the starter plugin. To include the independently packaged microstructure plugin:

```sh
python tools/docker_stack.py up --manifest examples/deployment.microstructure.json
python tools/docker_stack.py check --manifest examples/deployment.microstructure.json
```

Create a room using Microstructure grain growth, upload a micrograph, run preparation, inspect the full image and material-domain mask, approve the result, then run evolution. Stored frames support playback and scrubbing. Image assembly and job activity effects are reusable GenUI features. Read the [plugin guide](plugins/microstructure/README.md) and [model assumptions](plugins/microstructure/MODEL.md) before interpreting results. No SAM weights or external solver are required.

After approval, duration, interface energy and mobility can change without repeating segmentation; every run records its own settings and restarts from the approved initial map. Image, segmentation, material-mask and interface-width changes require a fresh review. The app currently limits each run to 20,000 stable solver steps and a bounded native-grid work budget. Longer continuation and automatic migration of existing pinned rooms are not implemented.

## Build your own plugin

Copy the [starter package](examples/agentic-plugin-template/README.md) into its own repository and follow the [wiring guide](examples/agentic-plugin-template/DOMAIN_WIRING.md). Supply its manifest, input schema, MCP services, tools, workflow, checker, skills and knowledge. The platform discovers installed capabilities and exposes active, pinned resources to agents.

```sh
python tools/docker_stack.py plugin-update --manifest deployment.json --plugin starter
```

Updates stage and validate candidates before publication. Existing rooms keep their admitted release identity; incompatible replacement requires explicit migration or a new room. New frontend primitives and host permissions require a platform contract extension.

## Documentation

- [Architecture and boundaries](docs/architecture.md)
- [Plugin authoring](docs/plugin-authoring.md)
- [Web API](docs/web-api.md)
- [Development and builds](docs/development.md)
- [Dependency notices](DEPENDENCIES.md)

The source snapshot is prepared for publication. Private specifications, agent guidance, test suites, credentials, run receipts and build products are intentionally excluded. No repository-wide open-source license has been selected; dependency licenses remain applicable.
