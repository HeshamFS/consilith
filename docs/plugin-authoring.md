# Building a plugin independently

A plugin is an independently packaged implementation of the Consilith contracts. The platform owns identity, collaboration, authority, jobs, artifacts, UI composition and signed evidence. A plugin owns its domain schemas, methods, workflows, checks and supporting resources. This repository ships two plugins (microstructure and crystallography) and a domain-neutral starter to copy.

## Package contents

Start with [`examples/agentic-plugin-template`](../examples/agentic-plugin-template/README.md) and its adjacent [domain wiring guide](../examples/agentic-plugin-template/DOMAIN_WIRING.md). It supplies a capability, two roles, typed form fields, MCP tools, a workflow, a checker, an owner-gated proposal strategy, a portable skill and packaged knowledge. The starter is the installed product example; the package is intentionally domain-neutral.

An installed entry point exports `CONTRACT_VERSION = "1"`, its version and a factory returning extension mappings. Include a `plugin.manifest` validated by `PluginManifest` in `packages/contracts/src/consilith_contracts/plugins.py`. Declare:

- A stable plugin ID, distribution and version.
- Capability IDs and namespaced roles.
- Tool IDs and associated MCP service declarations.
- Skill and knowledge resource IDs, with paths relative to the package.
- Bounded `instructions` and selected owned `promptKnowledge` IDs for initial agent context.
- Additional input MIME types in `artifactMediaTypes`, snapshotted with the active room.
- Required plugin IDs and Python distribution dependencies.
- The host callback ports required by its policy code.
- Explicit replay tolerances appropriate to its own method.

Identifiers for capabilities, lanes, fields and extension keys use 1–128 ASCII letters, digits, `_`, `.` or `-`. Dot-only path segments and JavaScript prototype keys are rejected. Labels remain free text. Lane IDs and field IDs within a lane must be unique, and a case mapping key must equal its descriptor ID. Built-in view names such as `agents` are valid capability IDs because navigation uses a separate namespace.

Relevant mappings include `cases`, `workflows`, `formulas`, `checkers`, `roles`, `presentation`, `workflow_required_tools` and `workflow_dependencies`. Existing package factories show the complete concrete interfaces. Plugin dependencies are activation dependencies; distribution dependencies are installation dependencies.

Keep implementation and resource files inside the packaged distribution. Identity includes those files and installed dependency code. Changing a CSV, skill, knowledge file or dependency can therefore change the admitted release even when the declared version is unchanged.

## Host ports and execution

Workflows receive `WorkflowContext`; proposals and policies receive their corresponding neutral ports from `packages/contracts/src/consilith_contracts/execution.py`. A different product can implement these ports or use the isolated runtime client. Do not import room, referee or UI implementations from a plugin.

Callbacks carry values or scoped capabilities. A plugin cannot obtain authority by declaring a tool: the host also checks the active case dependency closure, room pins, requesting actor, explicit denials, autonomy and compute budget. A policy-created workflow retains its bound requesting actor; a context without a bound actor cannot run tools or obtain an executable workflow. Personal lessons are visible only to the bound agent; shared room and bound-case lessons and collaborative agent configuration remain available to case policies. Review resolves canonical stored records and enforces their case and run before contacting the referee. Review returns an updated outcome by value. Signing keys and room storage stay in their host services.

MCP supplies tool descriptions and input schemas. Agent discovery exposes active metadata. Initial context includes active, pinned plugin instructions and selected `promptKnowledge` text; full skills and other knowledge are retrieved by ID when needed. Primers have a 48,000-character aggregate budget, individual knowledge fetches a 24,000-character limit, and truncation is explicit. Plugin context cannot grant permissions. Structured agent dispatch can select a declared workflow and mode using the room's stored inputs. The model cannot install packages, activate arbitrary plugins, invent endpoints or insert numerical results.

## UI contract

Declare input fields in `CaseDef` lanes and presentation bindings in the plugin descriptor. The generative composer chooses a layout from the platform's branded component catalog. The plugin does not own the application shell or executable frontend code.

Contract v1 supports the canonical Run action, field edits and approval decisions. Unsupported presentation action or input-schema overrides are rejected at registration instead of being advertised and silently ignored. Typed output version 1 supports number, text, category, JSON and artifact-only records. Nonnumeric results do not require dummy quantities. Signatures and replay bind kind, payload and content-addressed artifact references. Images, label maps, scalar meshes, XY plots, tables and JSON use existing generic renderers. Adding a new visual primitive requires a versioned platform catalog extension; adding a new domain using existing primitives does not.

Review and sequence support extends these generic contracts:

- `FieldDef.default` supplies a validated initial value, copied into the stored room inputs
  and revision hashes. Numeric defaults use the usual `{v, unit}` representation.
- A text field with `human_only=True` and `review_quantity` binds explicit user approval
  to a canonical result ID. Direct agent edits are refused; the owner can accept a
  proposal explicitly. The generic form offers the current matching result for review.
  Plugins must check the selected immutable result against the current source and
  parameters before executing. Approval alone is not a scientific-validity claim.
- Approved result artifacts and sibling artifacts from that same run remain visible as
  reviewed snapshots when an approval edit makes the original run historical.
- A JSON artifact shaped as `{kind: "image-sequence", frames: [{sha256, label, time}],
  timeUnit, description}` uses generic stored-frame playback. Include each raster as a
  normal content-addressed output artifact with role `sequence-frame`; the room still
  authorizes access to every frame. The renderer accepts 2–201 PNG/JPEG/WebP frames,
  permits pause, replay and scrubbing, and never interpolates computed data. The output
  contract allows 64 MiB of aggregate inline artifact bytes, at most 16 MiB per artifact,
  and up to 128 artifacts per output. Plugin RPC messages remain bounded at 128 MiB,
  including base64 expansion. Keep large arrays compressed and image frames individually
  content addressed so clients can retrieve only the frame they need.

  Plugin host and worker timeouts default to 660 seconds, allowing the queued tool's
  600-second deadline plus callback and review cleanup. Operators can set
  `CONSILITH_PLUGIN_TIMEOUT` consistently on host and worker (1–7200 seconds). This does
  not increase the tool deadline or the requesting agent's compute allowance.
- XY JSON artifacts may include escaped `title`, `xLabel` and `yLabel` strings.

These fields require the matching platform build. They do not change an older room's pinned schema; replacing an admitted release still requires explicit migration or a new room.

### Charts, tables and apps as artifacts

A plugin shows data as well-designed generative UI by storing plain artifacts. It ships no layout and no frontend code. The workspace groups a result's artifacts like a report: apps and reports, then charts in a responsive grid, then tables, then raw files. In chat the agent shows one artifact through `ui.show` by artifact handle.

- **Chart.** A JSON artifact `{"kind": "chart", "chart": <ChartData>}`. `ChartData` is the type in `apps/web/src/shared/a2ui/types.ts`: `kind` is `line`, `scatter` or `bar`, then `xLabel`, `yLabel`, `xUnit`, `yUnit`, optional `title` and `stamp`, and up to 12 `series` of `{name, x, y, unit?}`. A long line or scatter series is thinned by picking points (at most 2000) and marked as decimated. A histogram is a `bar` chart over bins: `x` holds the bin labels as strings (for example the bin centres) and `y` the counts, with the unit in the axis label.
- **Table.** A JSON artifact `{"kind": "table", "columns": [...], "rows": [...], "units"?: {column: unit}, "labels"?: {column: header}, "caption"?: "..."}`. A column is a name or `{key, label, unit}`, a row is an array in column order or an object by name. The table sorts on screen, aligns numbers, shows units in the header, keeps a sticky header and bounded height, and offers a CSV download of every stored row.
- **Older shapes keep working.** `{columns, rows}` without `kind` is a table (a trailing `(unit)` in a column name becomes its unit), and `{x, y, title?, xLabel?, yLabel?}` is one line.
- **App or report.** An artifact with media type `text/html` and role `app` or `report` is a self-contained page, shown in a sandboxed frame. An app that draws another artifact names it by a stem: `<stem>_viewer` draws `<stem>_json`. The frame's "Data table" toggle shows `<stem>_table`, or the first table.

Intake choices are identified by capability and input kind together, so independent plugins can both accept a file or text. The server checks uploads against generic platform formats and the room's pinned plugin MIME declarations. Text knowledge can be read in the workspace; binary knowledge is offered as an explicit download. A removed installed plugin remains visible through its historical room descriptor, with execution availability reported separately.

## Install, activate and replace

1. Build and test the wheel without product application dependencies.
2. Select its wheel bundle or immutable image, worker entry point and associated MCP services in an operator deployment manifest. The root `deployment.json` is the starter-only example.
3. Run `python tools/docker_stack.py plugin-update --manifest <manifest> --plugin <id>`.
4. The deployment tool stages separate candidate services, requires health, validates the full registry in both hosts and checks live MCP discovery before atomically publishing the catalog. Failure restores the previous catalog and removes owned candidate resources. The room and referee refresh discovery without a platform restart.
5. Create a room with the capability, or explicitly activate the plugin in an existing room. Installation alone does not activate it everywhere.
6. Replace the selected plugin through the same command. New rooms use the new release. Existing rooms retain their original schemas and pins; changed releases cannot silently execute against those rooms. Use the pinned release or an explicit future migration.

The platform containers contain contracts, the generic runtime and host applications. Each plugin and MCP service runs in its own restricted environment. This is an operator-managed installation boundary, not permission for a model to upload and execute arbitrary code.

## Evidence required before a release claim

Run `python tools/check.py` after installing the source-check dependencies described in [development](development.md). It checks this public source snapshot; regression suites and private qualification receipts are maintained separately.

Scientific rigor is a separate gate: convergence, reference data, method independence, uncertainty and validity domains are not established by these architecture tests.


## Image presentation

The trusted GenUI renderer presents supplied image bytes with optional tile assembly and indeterminate scanning bound to the surface's JobStatus data path. Queued, running, failed and completed states remain distinct. No progress percentage or scientific geometry is inferred by the UI. Stored artifacts can be selected with keyboard-accessible tabs, and a visual replay action does not start execution. Reduced-motion preferences disable moving effects. Domain algorithms and image interpretation remain in their plugin.
