# Agentic plugin starter

Copy this directory to begin an independent plugin. It contains a shared task with two roles, typed inputs, MCP tools, a workflow,
a checker, an opt-in proposal policy, a portable skill and a knowledge resource.
It imports the public contracts and MCP SDK only. It does not import the platform
application, referee implementation, or any domain package.

## Run and package

Install the published `consilith-contracts` wheel alongside this package. From the
platform workspace, build with `uv build --wheel examples/agentic-plugin-template`.
The wheel includes the skill and knowledge files.
See [the domain wiring guide](DOMAIN_WIRING.md#7-build-and-verify-outside-the-application)
for exact clean-environment installation and worker startup commands. Run installed
services with that environment's Python. When using the platform workspace after a
separate plugin installation, pass `--no-sync` to `uv run` so it preserves the plugin.

Start the tool service with:

```sh
uvicorn consilith_agentic_starter.server:app --factory --host 0.0.0.0 --port 8100
```

Configure the isolated plugin runtime entry point as
`consilith_agentic_starter:extension`. Register its MCP endpoint under `solver-starter`.
The platform operator chooses which plugin supplies the referee checker independently.
Create or activate the `starter` plugin in a room, choose the `starter.operator` role,
claim the `request` input lane and provide its `text` field. A second user can claim
the `review` lane as `starter.reviewer`; a solo owner may claim both. The generic UI reads the
manifest and capability descriptor; the agent can compose a workspace before execution.

The default service accepts loopback and `solver-starter` Host names. Change that explicit
allowlist for your deployment hostname; do not disable transport validation.

## Replace the example

1. Rename the distribution, Python module, plugin, capability, role, skill and tool IDs.
2. Replace the input fields and the MCP function. Keep result values in tools, never in
   model-generated prose. The versioned result envelope supports numbers, text, categories, JSON and files.
3. Replace the workflow with calls through the supplied host ports. Declare exactly the
   ports, tools and dependencies it needs; never import the host to bypass these gates.
4. Write a checker suited to the new task and configure it in the referee deployment.
   A producer manifest does not grant itself trust.
5. Update the skill, knowledge, versions, schemas and tests. Build and install the wheel
   in an isolated environment, then register its runtime endpoint in the operator catalog.

Package updates are pinned: existing rooms cannot silently switch implementations.
Use a new room or an explicit supported activation/migration path after changing a release.

## Example results

The default `all` output format returns `prepared_text` (exact text), `task_document`
(structured JSON) and `task_file` (a UTF-8 JSON download). It returns **no quantities**.
Choose `text`, `structured` or `artifact` to exercise each independently. All outputs
carry provenance, signed review and replay bindings. The checker reconstructs the
expected document from recorded inputs and checks the file's exact content hash.

The optional `numeric` format retains the Unicode code-point count as a compatibility
control. It is not required for any nonnumeric output. Duration is a generic optional
input stored with its unit; it does not turn the task into a scientific calculation.

Set request metadata to `{"requestReview": true}` to request an owner-approved review
note after execution. The plugin policy proposes `Reviewed` in the review lane and
cannot accept its own proposal. Approval changes inputs, makes previous results stale,
and requires another run. This is deterministic policy execution, not live LLM evidence.
