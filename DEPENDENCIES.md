# Dependency and publication notes

`plugins/crystallography` includes Crystal-GENUI from Anny-tech/Crystal-GENUI at
commit `a49515fa8c0d5fa980834b4998d1b24e823d95fb`, adapted for Consilith. Its MIT
license and copyright notice are preserved in [the plugin license](plugins/crystallography/LICENSE).
Its standalone dependency lock is separate from the platform workspace lock.

Python and JavaScript dependency versions are pinned in `uv.lock` and the application npm lockfiles. Their respective licenses and notices apply. Fonts are consumed through their pinned npm packages; do not remove those packages' license notices from redistributed dependency material.

The Docker ChatGPT bridge builds a separately sourced, pinned Sign in with ChatGPT DevKit. It is not vendored in this repository. Its upstream license and notices are copied by the image build. The DevKit's noncommercial/local-use terms are separate from this project's software licensing; see `apps/siwc/README.md` and `docs/siwc.md` for the configured source and behavior.

A repository-wide software license has not been selected. Preparing this source tree does not grant a new open-source license or publish the repository.
