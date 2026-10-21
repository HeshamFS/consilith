# Consilith web

React, TypeScript and Vite implement the collaborative workspace and the approved generative UI component contract. Theme tokens live in `src/shared/theme/tokens.css`.

From the repository root, use `python tools/docker_stack.py up` for the complete local application. For frontend-only development, install dependencies with `npm --prefix apps/web ci` and run `npm --prefix apps/web run dev` against a configured room server. Build with `npm --prefix apps/web run build`.

Product source and pre-generated assets are included. Private design inputs, regression suites, screenshots and run artifacts are not part of this public snapshot. See [development](../../docs/development.md) and [architecture](../../docs/architecture.md).
