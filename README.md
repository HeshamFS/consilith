# Consilith

**A multiplayer workspace where people and their AI agents work on the same problem, and every step can be traced.**

A Consilith room brings together several people, each with their own agent. Everyone owns their inputs. Agents make proposals, people approve them, each execution is recorded, and an independent referee checks the results and signs the evidence. The screen is not hand-built for each task: an agent composes the workspace from approved UI components, and plugins supply the task knowledge, tools and implementation.

## How it works

1. **A room is opened.** Members join with their own roles and lanes, and each member brings an agent: a hosted model, a ChatGPT plan through the sign-in bridge, or their own A2A agent.
2. **Plugins provide the capability.** An installed plugin declares its input schema, MCP tools, workflow, checker, skills and knowledge. The platform discovers it and exposes only active, pinned resources to agents.
3. **Agents compose the workspace.** The generative UI renderer receives A2UI surfaces and AG-UI streams and draws them only from the platform's approved components.
4. **People decide.** Agents propose and owners approve. Nothing runs on an input without its owner's consent.
5. **Execution is recorded and checked.** Runs are journaled and can be replayed. The referee checks the results independently and signs the evidence.

## Repository layout

| Path | What it holds |
|---|---|
| `apps/room` | Room server: accounts, collaboration, permissions, agent orchestration, protocols (A2A, MCP, AG-UI, A2UI) and execution records |
| `apps/referee` | Independently configured checking and signed evidence |
| `apps/web` | React workspace and generative UI renderer |
| `apps/siwc` | Containerized Sign in with ChatGPT bridge |
| `packages/contracts` | Public schemas shared by every service and plugin |
| `packages/plugin-runtime` | Isolated plugin transport: server, client, worker and catalog |
| `examples/agentic-plugin-template` | Starter plugin 1.1.2 with MCP tools, schemas, skills, knowledge, workflow, checker and an owner-approved proposal policy |
| `docker`, `tools`, `deployment.json` | Reproducible container builds and plugin lifecycle operations |
| `docs` | Architecture, plugin authoring, web API, Docker, development and bridge guides |

The starter produces text, structured JSON and downloadable files, and an optional text-count operation shows numeric compatibility. Domain-specific scientific implementations are not included.

## Run locally with Docker

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker with Linux containers. From the repository root:

```sh
python tools/docker_stack.py up
python tools/docker_stack.py check
```

Then open [http://127.0.0.1:8800](http://127.0.0.1:8800). The development deployment signs you in through a local mailbox. To enable ChatGPT, connect your own account at `/settings/agent`. A healthy bridge container does not mean an account is authorized.

The first build fetches pinned dependencies and the separately licensed ChatGPT DevKit. Read [Docker operations](docs/docker.md) and [the bridge contract](docs/siwc.md), including its usage terms. This is a local development configuration, not an internet-facing production deployment. Account sessions and generated secrets never belong in Git.

## Build your own plugin

Copy the [starter package](examples/agentic-plugin-template/README.md) into its own repository and follow the [wiring guide](examples/agentic-plugin-template/DOMAIN_WIRING.md). Supply the manifest, input schema, MCP services, tools, workflow, checker, skills and knowledge, then roll it out:

```sh
python tools/docker_stack.py plugin-update --manifest deployment.json --plugin starter
```

Each update is staged and validated before it is published. Existing rooms keep the release they were admitted with, so an incompatible replacement needs an explicit migration or a new room. New frontend primitives and host permissions need a platform contract extension.

## Documentation

- [Architecture and boundaries](docs/architecture.md)
- [Plugin authoring](docs/plugin-authoring.md)
- [Web API](docs/web-api.md)
- [Development and builds](docs/development.md)
- [Docker operations](docs/docker.md)
- [Sign in with ChatGPT bridge](docs/siwc.md)
- [Dependency notices](DEPENDENCIES.md)

## License

The license will be defined later. Until then, all rights are reserved. Third-party dependencies remain under their own licenses; see [DEPENDENCIES.md](DEPENDENCIES.md).

---

Copyright © 2026 Hesham Salama. All rights reserved.
