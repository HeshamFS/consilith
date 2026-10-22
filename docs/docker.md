# Docker operations

Requirements: Docker with Linux containers, Python 3.12, uv, and network access for pinned dependencies. Run every command from the repository root.

```sh
python tools/docker_stack.py up            # build, validate plugins, start
python tools/docker_stack.py check         # health and sign-in smoke test
python tools/docker_stack.py logs          # follow service logs
python tools/docker_stack.py stop          # stop and keep all data
python tools/docker_stack.py plugin-update --plugin microstructure   # stage and publish one plugin release
```

## What runs

`deployment.json` selects the plugins, their images, entry points, MCP endpoints and health checks. The default stack runs the room, referee, web gateway and ChatGPT bridge, plus the microstructure and crystallography plugins (workers, MCP servers and solvers). Other manifests are in `examples/`:

| Manifest | Stack |
|---|---|
| `deployment.json` | platform + microstructure + crystallography |
| `examples/deployment.microstructure.json` | platform + microstructure |
| `examples/deployment.crystallography.json` | platform + crystallography |
| `examples/deployment.starter.json` | platform + the plugin starter |
| `examples/deployment.empty.json` | platform only |

Use one with `--manifest <file>` on every command.

## Ports and keys

- The gateway listens on `127.0.0.1:8800` (`CONSILITH_HTTP_PORT` changes it); the ChatGPT callback uses `127.0.0.1:8787`.
- Model keys are added per person in the app, never in files.
- Service keys are read from your shell at `up`: `VALENCY_API_KEY` (literature search), `ADAPTION_API_KEY` (Adaption Labs). Leave them unset to run without those tools.

## State

- Generated secrets, catalogs and Compose files live in `.docker/`; local evidence lives in `var/`. Both are ignored by Git. Never commit them.
- `stop` keeps every volume. Removing volumes deletes accounts, rooms and results.
- Cleanup removes only resources this project owns; it never prunes Docker globally.
- To run a second, separate instance (for example a clean demo), give it another project name in a copy of the manifest and another `CONSILITH_DEPLOYMENT_STATE_DIR`.

## Before exposing it

This is a local development deployment: a dev mailbox for sign-in, loopback ports and generated secrets. An internet-facing deployment needs your own authentication, mail, TLS, secret management and network exposure. Read the [ChatGPT bridge terms](siwc.md) first.
