# Development

## Set up

```sh
uv sync --frozen --no-default-groups --group platform   # Python workspace and tools
npm --prefix apps/web ci                                # web app
npm --prefix apps/siwc ci                               # ChatGPT bridge
```

## Check

```sh
python tools/check.py                 # ruff lint and format, pyright, import boundaries, web and bridge builds
npm --prefix apps/web run typecheck   # TypeScript
npm --prefix apps/web run lint        # Biome
```

Run all three before every pull request.

## Run

The supported local setup is the Docker stack ([docker.md](docker.md)). For frontend work, `npm --prefix apps/web run dev` starts Vite with a proxy that forwards `/api` to a running room service.

## Boundaries

- Platform code (`apps/`, `packages/`) never imports a plugin. Plugins use only `packages/contracts` and the runtime ports.
- New UI primitives or host permissions are platform contract changes; a new domain built from existing primitives is not.
- Generated environments, `node_modules`, caches, `dist`, `.docker` and `var` are ignored. Never commit secrets, account stores or generated catalogs.
