# Contributing

Thanks for helping. Bug reports, fixes, docs and new plugins are all welcome.

## Before you start

- **Bugs:** open an issue with steps to reproduce, what you expected and what happened. Include the browser, the provider and model, and the plugin.
- **Larger changes:** open an issue first so we can agree on the approach before you write code.
- **Security problems:** do not open a public issue. Email hesham.salama@rub.de.

## Workflow

1. Fork the repository and create a branch from `main` (`feat/...`, `fix/...`, `docs/...`).
2. Keep commits focused, with messages in the imperative ("Add the lane release card").
3. Run the checks:
   ```sh
   python tools/check.py
   npm --prefix apps/web run typecheck
   npm --prefix apps/web run lint
   ```
4. For behavior changes, run the stack and try the change in the browser ([docker.md](docs/docker.md)).
5. Open a pull request against `main` that says what changed, why, and how you checked it.

`main` keeps a linear history: pull requests are merged by rebase or squash after review.

## Code

- Python 3.12, formatted and linted with ruff, typed with pyright.
- TypeScript and React in `apps/web`, formatted and linted with Biome.
- Keep the platform domain-neutral. Domain logic belongs in a plugin, never in `apps/` or `packages/`.
- Never commit keys, account data, `.docker/`, `var/` or generated catalogs.

## Plugins

A plugin can live in its own repository. Start from the [plugin starter](examples/agentic-plugin-template/README.md) and the [authoring guide](docs/plugin-authoring.md). To add one to this repository, put it under `plugins/<name>` with its README, its license and a model or assumptions note, and add an example manifest under `examples/`.

## License

By contributing, you agree that your contribution is licensed under the [Apache License 2.0](LICENSE), the license of this repository. Code you bring from elsewhere must keep its original license and notice.
