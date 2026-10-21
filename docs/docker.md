# Docker operations

Run lifecycle commands from the source repository or a disposable working copy. Python 3.12, uv, network access for pinned dependencies, and a Docker Linux engine are required.

```sh
python tools/docker_stack.py up
python tools/docker_stack.py check
python tools/docker_stack.py plugin-update --manifest deployment.json --plugin starter
python tools/docker_stack.py logs
python tools/docker_stack.py stop
```

The default stack contains room, referee, web, starter worker, starter MCP server and the ChatGPT bridge. The worker executes plugin policies through host ports; the MCP server executes the plugin's registered tools. Their dependencies and releases are isolated from the core.

`deployment.json` selects images, wheel packages, entry points, MCP endpoints and health checks. `examples/deployment.empty.json` describes a core-only catalog. Adding a plugin changes its package and deployment entries, not domain branches in the host.

The gateway listens on loopback port 8800 and the ChatGPT callback uses loopback port 8787. The development mailbox is local-only. A second stack cannot bind the same occupied ports. Do not run a second deployment against the same project/data namespace accidentally.

Generated `.docker/` contains secrets, catalogs, wheel caches and generated Compose configuration. `var/` contains local evidence. Both are ignored by Git and excluded from public source. Never commit generated catalogs, environment files, account stores, private keys or signing material.

`stop` preserves volumes. Do not remove volumes unless deliberately deleting application state. Cleanup uses exact project ownership and live references; it does not prune globally. Empty networks referenced by a running container's Docker configuration must remain until that container is deliberately replaced.

The ChatGPT bridge stores encrypted credentials and the Linux keyring in a dedicated volume. Moving from an operating-system-specific host store can require fresh user authorization. Do not bake personal credentials into images. Read [bridge behavior and terms](siwc.md).

This repository ships a local development deployment. Internet-facing deployment requires an operator's authentication, mail, TLS, secret-management and exposure configuration.
# Internal source builds against existing Docker state

An operator can set `CONSILITH_DEPLOYMENT_STATE_DIR` to an absolute directory containing
the existing `.docker` deployment state. Build inputs still come from the selected source
tree; catalog files, active service aliases, scoped environment files and bridge secret
paths remain in the selected state directory. Keep the Compose project name unchanged
when updating that deployment. This does not copy account credentials into the source
tree, install a plugin automatically, or authorize a public release.
