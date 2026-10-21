# Wire a new task domain into the platform

Start by copying this entire package into its own repository. Its wheel is the unit you
build, version and install independently. No backend, agent, referee or UI source edit is
required for the demonstrated contract. Novel UI primitives or new host permissions still
require an explicit platform contract change; a manifest cannot silently add either.

## 1. Name the independent package

In `pyproject.toml`, replace `consilith-agentic-starter` and set your release version. Rename
`src/consilith_agentic_starter` and update the entry point and imports. Keep the public
contracts dependency. Add only your operation's dependencies; never depend on the room
or referee application. Choose globally distinct plugin, role, tool and resource IDs.

| Contract | Starter example | Replace here |
|---|---|---|
| Distribution | consilith-agentic-starter | pyproject.toml, MANIFEST.distribution |
| Plugin and release | starter, 1.1.2 | MANIFEST.id/version, __version__ |
| Worker entry point | consilith_agentic_starter:extension | deployment plugins[].entryPoint |
| Capability and input lane | tasks, request | extension() CaseDef and workflow snapshot |
| Role and default permissions | starter.operator | MANIFEST.roles, role(), LaneDef.default_role |
| Tool and MCP server | starter.inspect, solver-starter | MANIFEST.tools/mcpServers/schemaRefs, server.py |
| Instruction skill | text-inspection | MANIFEST.skills, role skills, skills/ folder |
| Knowledge resource | starter.scope | MANIFEST.knowledge and knowledge/ folder |

Use portable IDs (1–128 ASCII letters, digits, `_`, `.` or `-`), without slash/tilde, dot-only path segments or JavaScript prototype keys. Keep human-readable labels separate. A capability registry key must equal its CaseDef ID; lane IDs and field names within a lane must be unique.

`dependencies` names other activatable plugin IDs. `packageDependencies` documents Python
distributions; the actual installer requirements remain in `pyproject.toml`. Do not confuse them.

## 2. Describe inputs and the task UI

Edit the `CaseDef` and its lanes/fields. The starter has request and review lanes, with text, choice, number, list and object fields. Number
fields use stored values shaped as `{"v": value, "unit": unit}`; text fields store strings.
Each lane names its default role, but a signed-in user explicitly claims ownership. Names,
seat position and model prompts grant no permission.

The platform sends registered input, result, artifact and evidence bindings to the model.
The model selects approved components and composes their tree for the request. It does
not write operation results. `presentation` supplies descriptive metadata; it cannot
register arbitrary frontend code or unsupported actions. Preview and actual model-created
workspaces remain visibly distinguished. A user supplies `request.text` before execution;
there are no hidden demonstration inputs.

## 3. Implement a tool and publish its schema

Replace `server.prepare_task` with your operation, validate all inputs, and return the
standard result envelope. Keep actual result values inside the tool. The starter rejects
text above 10,000 code points and produces text, JSON and/or file outputs without a numeric surrogate. The
`MCPServer` function signature supplies its live JSON schema; `schemaRefs` points at that
tool, not at a hand-maintained duplicate schema. Update the MCP service Host allowlist for
your explicit hostname.

Return real errors for unavailable dependencies or invalid input. Do not fill failures
with plausible values. Result identity, arguments, artifacts, implementation version and
input revisions are recorded by the host.

## 4. Orchestrate through granted ports

The `workflow` gets a public `WorkflowContext`, not a database, room object or credentials.
Take one input snapshot, call the registered tool with that snapshot and request review.
Declare `workflow_required_tools` and only the `policyPorts` the plugin uses. This starter
needs snapshot, run_tool, review and result for execution; its optional strategy also uses agent_for and propose, and declares the read-only model, owners and proposals attributes. Do not declare every port speculatively.

Agents discover active capabilities, live MCP tool schemas, role permissions, skills and
knowledge. The generic structured dispatcher may read active pinned resources or request
a workflow using stored inputs. It cannot install packages, activate new ones, invent
numerical tool arguments, or bypass the owner's autonomy, deny list and compute budget.
Optional task policies can use additional documented host ports for proposals and watches;
add them explicitly with negative authorization tests. Personal lessons remain owner-scoped.

## 5. Supply an independently selected checker

Replace `checker` with checks justified for the new task. It receives recorded inputs and
one typed result, then returns findings. The starter reconstructs the expected document
from recorded inputs and compares text, JSON and exact artifact hashes. File bytes use the platform RFC 8785 canonical JSON representation so compatible integer/float transport normalization does not change the file. Its optional numeric
control independently recounts Unicode code points. Altered kinds, payloads and files fail. This is a useful
contract example, not validation of an unrelated task domain.

The referee operator chooses installed checker plugins. The producer's request does not
select or bless its own checker. Successful findings become signed evidence after host
binding checks; missing approved checking cannot yield a verified result. For independent
checker release cycles, publish the checker in a separate wheel and deploy it to the
referee's operator-selected catalog.

## 6. Package instructions and knowledge

Edit `skills/text-inspection/SKILL.md` and `knowledge/scope.md`. A skill contains behavior
instructions: when to run, which resources to read, missing-input handling and limits.
Knowledge describes the task's domain facts and assumptions. Keep paths inside the wheel;
worker discovery returns portable content rather than host filesystem access. `MANIFEST.instructions` supplies bounded plugin-specific behavior to the agent.
`promptKnowledge` selects a small owned primer (`starter.scope` here) to include eagerly
in that plugin's agent context. Other knowledge and full skill bodies are fetched on demand
through active, pinned resource IDs. Keep the primer concise; do not turn discovery into
an unbounded document dump. The host
pins source and packaged assets together, so editing a data file is a release change too.

## 7. Package outside the application

Build the public contracts, transport and plugin as independent wheels. Choose an output directory outside your source checkout:

```sh
uv build --wheel --out-dir ../build/wheels packages/contracts
uv build --wheel --out-dir ../build/wheels packages/plugin-runtime
uv build --wheel --out-dir ../build/wheels examples/agentic-plugin-template
```

Install those wheels into a separate Python 3.12 environment. The plugin worker entry point is `consilith_agentic_starter:extension`; the MCP application is `consilith_agentic_starter.server:app`. Docker provisions the worker capability token and private service wiring. Never put that token in source, a prompt or a manifest.

Write your own task-specific tests in your development workspace. Check invalid inputs, output tampering, exact file bytes, permissions, independent checking, replay and failed replacement. This source snapshot does not include the maintainer's private qualification suite. The starter is an integration template, not validation for your new domain.

## 8. Register and activate deliberately

Add plugin and MCP entries to the operator deployment manifest; use the existing starter
entries in the root `deployment.json` as a concrete example. The worker entry names the
factory above. The MCP entry advertises `http://solver-starter:8080/mcp`. Build package
requirements into those services, not the platform image.

```sh
uv run --frozen --no-sync --no-default-groups --group platform python tools/docker_stack.py up --manifest deployment.json
uv run --frozen --no-sync --no-default-groups --group platform python tools/docker_stack.py check --manifest deployment.json
```

Read `GET /plugins` to discover the installed catalog. A seated user activates an installed
plugin with `POST /rooms/{room_id}/plugins/{plugin_id}/activate`. The model cannot do this
itself. Choose the capability/role and claim lanes through the product's normal room flow.
Inspect `GET /rooms/{room_id}/knowledge/{knowledge_id}` with that user's authentication to
verify active, pinned knowledge delivery.

## 9. Replace a release without rewriting the platform

Change the plugin version/code/resources, build new wheels, then update only its deployment
entries. After initial deployment, the generic lifecycle command is:

```sh
uv run --frozen --no-sync --no-default-groups --group platform python tools/docker_stack.py plugin-update --manifest deployment.json
```

The lifecycle stages isolated candidate services, requires health and full host-registry
and live MCP validation, then atomically switches the operator catalog. A failed update
restores the previous catalog and removes its staging resources. New rooms can use it; existing rooms
retain pinned schema/package identities and refuse silent incompatible execution. Use an
explicit supported activation/migration path or create a new room. Keep the old release
available when continued execution of old rooms is required. Capture unchanged platform
image identity, plugin identities and actual tool/evidence results in replacement tests.

## 10. Apply the template to future domains

A future mechanics, microstructure or crystal package would replace the task fields,
operation, checker, instructions and knowledge in this package. Those domain packages
are intentionally not implemented or exercised by this starter. Their own numerical
methods, validation datasets and correctness criteria belong to that later work.

## Typed output contract

Use `ResultEnvelope(output_version="1", outputs=[...], ...)` and leave `quantities=[]`
for nonnumeric tasks. Public output types are `NumberOutput`, `TextOutput`,
`CategoryOutput`, `JsonOutput` and `ArtifactOutput`. Each has a stable `name` and `kind`.
Files use `ArtifactContent(media_type=..., content_base64=...)`; the host validates the
bounded bytes and assigns content-addressed `ArtifactRef`s. Never return arbitrary
credential-bearing download URLs. Declare permitted artifact media types in the manifest.

The checker receives `ReviewItem.kind`, `value`, `unit` and `artifacts`. Review and replay
bind every payload and file reference. Numeric tolerance applies only to numbers; other
payloads and file hashes compare exactly. Legacy `quantities` remains supported.
New output kinds or executable UI primitives require a versioned platform change.
