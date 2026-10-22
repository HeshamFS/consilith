# Licenses and dependencies

- **This repository:** Apache License 2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).
- **`plugins/crystallography`:** adapted from [Crystal-GENUI](https://github.com/Anny-tech/Crystal-GENUI) at commit `a49515fa8c0d5fa980834b4998d1b24e823d95fb`. Its MIT license and copyright notice are kept in [its license](plugins/crystallography/LICENSE), and its dependency lock is separate from the platform lock.
- **Python and JavaScript dependencies:** pinned in `uv.lock` and the npm lockfiles under `apps/`; their own licenses apply. Fonts come from pinned npm packages; keep their notices when redistributing.
- **Sign in with ChatGPT DevKit:** not included. The bridge image build fetches a pinned copy and keeps its license and notices. Its noncommercial and local-use terms are separate from this project's license; see [apps/siwc/README.md](apps/siwc/README.md) and [docs/siwc.md](docs/siwc.md).
