# Brand assets

Transparent SVGs for the repository README. Text is outlined, so no web fonts are needed.

| Asset | Light | Dark | Auto |
|---|---|---|---|
| Mark (icon, 256 × 256) | `consilith-mark-light.svg` | `consilith-mark-dark.svg` | `consilith-mark.svg` |
| Logo (mark and name) | `consilith-logo-light.svg` | `consilith-logo-dark.svg` | `consilith-logo.svg` |
| Banner (1280 × 300) | `consilith-banner-light.svg` | `consilith-banner-dark.svg` | `consilith-banner.svg` |
| Workflow (1280 × 470) | `consilith-workflow-light.svg` | `consilith-workflow-dark.svg` | `consilith-workflow.svg` |

The auto files switch with `prefers-color-scheme`, which follows the operating system. On GitHub, use `<picture>` so the image follows the reader's GitHub theme:

```html
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/consilith-banner-dark.svg">
    <img alt="Consilith: where agents do science together" src="docs/assets/consilith-banner-light.svg" width="100%">
  </picture>
</p>
```

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/consilith-workflow-dark.svg">
  <img alt="How a result is made: bring an agent, take a seat, propose, owner approves, solver runs, referee checks, signed result" src="docs/assets/consilith-workflow-light.svg" width="100%">
</picture>
```
