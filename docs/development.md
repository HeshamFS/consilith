# Source development and build output

The repository contains product source and public usage documentation. Private plans, agent instructions, audit receipts and qualification suites are maintained outside the publication boundary.

Install source-check dependencies and build the frontend:

```sh
uv sync --frozen --no-default-groups --group platform
npm --prefix apps/web ci
npm --prefix apps/siwc ci
python tools/check.py
```

The public check performs lint, formatting, type checks, dependency-boundary checks and frontend/bridge builds. It does not claim to run the private regression or browser qualification suites. Generated environments, `node_modules`, caches, `dist`, `.docker` and `var` are ignored.

To keep a publication snapshot byte-clean, copy it into a separate build workspace before installing dependencies or running commands. The maintainer's enclosing workspace uses sibling `public`, `build` and `_internal` directories; only the contents of `public` are intended for GitHub.

The API server owns authentication and events. Vite's optional development proxy forwards `/api` to the room service; the full supported local setup is the Docker gateway. Generated UI assets are checked in, so private design-generation scripts are not required to build.
